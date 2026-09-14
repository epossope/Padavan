import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot


class P1ProductionUxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with bot.TELEGRAM_FINAL_CHUNK_LOCK:
            bot.TELEGRAM_FINAL_CHUNK_KEYS.clear()
            bot.TELEGRAM_FINAL_CHUNK_ORDER.clear()

    def test_history_contract_has_stable_ids_timestamps_and_full_content(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,role TEXT,content TEXT,created_at TEXT)")
        long_text = "x" * 2200
        database.execute("INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)", (42, "assistant", long_text, "2026-09-14T10:00:00+00:00"))
        with patch.object(bot, "conn", return_value=database):
            self.assertEqual(bot.history(42), [{
                "message_id": 1, "role": "assistant", "content": long_text,
                "created_at": "2026-09-14T10:00:00+00:00",
            }])
        database.close()

    def test_tool_runtime_state_is_truthful_and_follows_actual_execution(self):
        tool_frame = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"get_today_plan","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}'
        answer_frame = 'data: {"choices":[{"delta":{"content":"Готово"},"finish_reason":"stop"}]}'.encode("utf-8")
        responses = []
        for frames in ([tool_frame, b"data: [DONE]"], [answer_frame, b"data: [DONE]"]):
            response = Mock(ok=True, status_code=200)
            response.iter_lines.return_value = frames
            responses.append(response)
        calls = []
        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="system"), \
             patch.object(bot, "chat_model_candidates", return_value=["model"]), \
             patch.object(bot, "request_chat_stream", side_effect=responses), \
             patch.object(bot, "execute_tool", side_effect=lambda cid, name, args: calls.append((cid, name, args)) or {"ok": True}), \
             patch.object(bot, "record_usage"), patch.object(bot, "record_runtime_metric"), \
             patch.object(bot, "add_message", side_effect=[71, 72]):
            events = list(bot.stream_agent_response(42, "Что у меня сегодня?"))
        self.assertEqual(calls, [(42, "get_today_plan", {})])
        tool_index = next(i for i, event in enumerate(events) if event.get("type") == "tool")
        state_index = next(i for i, event in enumerate(events) if event.get("state") == "TOOL")
        self.assertLess(state_index, tool_index)
        self.assertEqual(events[state_index]["text"], "Проверяю задачи…")
        self.assertEqual(events[-1]["canonical_user_message_id"], 71)
        self.assertEqual(events[-1]["canonical_message_id"], 72)

    def test_legacy_auto_is_read_normalized_without_database_mutation(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.execute("CREATE TABLE settings(chat_id INTEGER PRIMARY KEY,response_mode TEXT NOT NULL)")
        database.execute("INSERT INTO settings(chat_id,response_mode) VALUES(42,'auto')")
        with patch.object(bot, "conn", return_value=database), \
             patch.object(bot, "runtime_config_values", return_value={"default_voice_reply_mode": "text"}):
            self.assertEqual(bot.get_mode(42), "text")
        self.assertEqual(database.execute("SELECT response_mode FROM settings WHERE chat_id=42").fetchone()["response_mode"], "auto")
        database.close()

    def test_new_runtime_config_rejects_auto_voice_mode(self):
        with self.assertRaises(ValueError):
            bot._normalise_runtime_config_value("default_voice_reply_mode", "auto")
        self.assertEqual(bot.canonical_voice_reply_mode("auto"), "text")

    async def test_tts_requires_identity_or_resolved_preferences(self):
        with patch.object(bot, "runtime_config_values", return_value={
            "tts_provider": "edge", "tts_fallback_provider": "none",
            "tts_male_voice": "male", "tts_female_voice": "female",
        }):
            with self.assertRaisesRegex(RuntimeError, "TTS_IDENTITY_REQUIRED"):
                await bot.make_voice("Ответ")

    async def test_gender_preferences_resolve_live_admin_voices_for_telegram(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.execute("CREATE TABLE app_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,updated_at TEXT,updated_by INTEGER)")
        runtime = {
            "tts_provider": "browser", "tts_fallback_provider": "none",
            "tts_male_voice": "male-v1", "tts_female_voice": "female-v1",
            "tts_default_speed": 1.0, "tts_default_pitch": 1.0, "tts_default_volume": 1.0,
        }
        voices = []

        class FakeCommunicate:
            def __init__(self, _text, voice, **_kwargs):
                voices.append(voice)

            async def save(self, filename):
                Path(filename).write_bytes(b"ID3")

        async def send_for(chat_id, gender):
            bot.set_voice_preferences(chat_id, gender)
            message = SimpleNamespace(reply_text=AsyncMock(), reply_voice=AsyncMock())
            update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_message=message)
            with patch.object(bot, "get_mode", return_value="voice"):
                await bot.send_answer(update, "Готово")
            self.assertEqual(message.reply_voice.await_count, 1)

        with patch.object(bot, "conn", return_value=database), \
             patch.object(bot, "runtime_config_values", side_effect=lambda: dict(runtime)), \
             patch.object(bot.edge_tts, "Communicate", FakeCommunicate):
            await send_for(101, "female")
            await send_for(102, "male")
            runtime["tts_female_voice"] = "female-v2"
            await send_for(101, "female")
        self.assertEqual(voices, ["female-v1", "male-v1", "female-v2"])
        self.assertEqual(bot.server_tts_provider(runtime), "edge")
        database.close()

    async def test_telegram_maps_shared_states_to_ephemeral_chat_actions_and_status_card(self):
        telegram = SimpleNamespace(
            send_chat_action=AsyncMock(),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=91)),
        )
        message = SimpleNamespace(reply_voice=AsyncMock())
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42), effective_message=message)

        def events(*_args):
            yield bot.runtime_state_event("REQUESTING", text="Думаю…")
            yield {"type": "tool", "name": "get_today_plan", "ok": True}
            yield bot.runtime_state_for_tool("get_today_plan")
            yield bot.runtime_state_event("STREAMING")
            yield {"type": "delta", "text": "Готово"}
            yield {"type": "done", "text": "Готово", "canonical_message_id": 77}

        with patch.object(bot, "stream_agent_response", side_effect=events), \
             patch.object(bot, "get_mode", return_value="text"), \
             patch.object(bot, "reply_emoji_prefix", return_value=""), \
             patch.object(bot, "main_keyboard", return_value=None):
            self.assertTrue(await bot.stream_answer_to_telegram(update, SimpleNamespace(bot=telegram), "test"))
        self.assertGreaterEqual(telegram.send_chat_action.await_count, 3)
        self.assertTrue(all(call.kwargs["action"] == "typing" for call in telegram.send_chat_action.await_args_list))


    def test_user_voice_preferences_store_only_gender_and_use_admin_tuning(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.execute("CREATE TABLE app_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT,updated_at TEXT,updated_by INTEGER)")
        runtime = {
            "tts_provider": "edge", "tts_fallback_provider": "browser",
            "tts_male_voice": "male-admin", "tts_female_voice": "female-admin",
            "tts_default_speed": 1.12, "tts_default_pitch": .9, "tts_default_volume": .8,
        }
        with patch.object(bot, "conn", return_value=database), patch.object(bot, "runtime_config_values", return_value=runtime):
            result = bot.set_voice_preferences(42, "female", speed=.8, pitch=1.5, volume=.2)
            self.assertTrue(result["ok"])
            stored = json.loads(database.execute("SELECT setting_value FROM app_settings WHERE setting_key='voice_preferences:42'").fetchone()[0])
            self.assertEqual(stored, {"gender": "female"})
            prefs = bot.get_voice_preferences(42)
            self.assertEqual((prefs["voice"], prefs["speed"], prefs["pitch"], prefs["volume"]), ("female-admin", 1.12, .9, .8))
        database.close()

    def test_telegram_admin_voice_page_exposes_all_global_controls(self):
        runtime = {
            "tts_provider": "edge", "tts_male_voice": "male-admin", "tts_female_voice": "female-admin",
            "tts_default_speed": .85, "tts_default_pitch": .95, "tts_default_volume": .8,
        }
        prefs = {"gender": "male"}
        with patch.object(bot, "ADMIN_CHAT_IDS", {42}), \
             patch.object(bot, "runtime_config_values", return_value=runtime), \
             patch.object(bot, "get_voice_preferences", return_value=prefs):
            text, markup = bot.voice_settings_page(42)
        self.assertIn("Мужской голос", text)
        self.assertIn("Женский голос", text)
        self.assertIn("Скорость", text)
        self.assertIn("Тон", text)
        self.assertIn("Громкость", text)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("voice:admin:edit:tts_default_speed", callbacks)

    async def test_three_voice_modes_have_same_channel_semantics(self):
        app_source = (Path(__file__).parent.parent / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("speak=mode==='voice'||mode==='voice_and_text'", app_source)
        self.assertIn("render:mode!=='voice'", app_source)
        for mode, text_count, voice_count in (("text", 1, 0), ("voice", 0, 1), ("voice_and_text", 1, 1)):
            message = SimpleNamespace(reply_text=AsyncMock(), reply_voice=AsyncMock())
            update = SimpleNamespace(effective_chat=SimpleNamespace(id=42), effective_message=message)

            async def voice_file(_text, *, chat_id):
                self.assertEqual(chat_id, 42)
                handle = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                handle.write(b"ID3")
                handle.close()
                return Path(handle.name)

            with patch.object(bot, "get_mode", return_value=mode), patch.object(bot, "make_voice", new=AsyncMock(side_effect=voice_file)):
                await bot.send_answer(update, "Ответ")
            self.assertEqual(message.reply_text.await_count, text_count, mode)
            self.assertEqual(message.reply_voice.await_count, voice_count, mode)


if __name__ == "__main__":
    unittest.main()
