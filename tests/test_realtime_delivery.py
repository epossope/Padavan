import asyncio
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot


class RealtimeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_new_canonical_user_is_visible_before_and_after_usage(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE bot_users(user_number INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER UNIQUE,username TEXT NOT NULL DEFAULT '',display_name TEXT NOT NULL DEFAULT '',first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL);
            CREATE TABLE usage_events(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL,source TEXT NOT NULL,model TEXT NOT NULL,input_tokens INTEGER NOT NULL DEFAULT 0,output_tokens INTEGER NOT NULL DEFAULT 0,cost REAL NOT NULL DEFAULT 0,provider TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
            CREATE TABLE user_settings(chat_id INTEGER PRIMARY KEY,primary_model TEXT NOT NULL DEFAULT '',fallback_model TEXT NOT NULL DEFAULT '',vision_model TEXT NOT NULL DEFAULT '');
            CREATE TABLE user_api_keys(chat_id INTEGER PRIMARY KEY,encrypted_key TEXT NOT NULL,key_hint TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
            CREATE TABLE managed_api_keys(chat_id INTEGER PRIMARY KEY,encrypted_key TEXT NOT NULL,key_hash TEXT NOT NULL DEFAULT '',key_hint TEXT NOT NULL DEFAULT '',limit_usd REAL NOT NULL DEFAULT 2,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE app_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by INTEGER);
        """)
        config = {"fast_model": "qwen/test", "fast_model_providers": [], "fast_model_allow_provider_fallback": False,
                  "strong_model": "deepseek/test", "strong_model_providers": [], "strong_model_allow_provider_fallback": True}
        with patch.object(bot, "conn", return_value=database), patch.object(bot, "runtime_config_values", return_value=config):
            bot.register_bot_user(6999, SimpleNamespace(username="existing", first_name="Старый", last_name="Пользователь"))
            bot.record_usage(6999, "shared", "qwen/test", {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.001}})
            bot.register_bot_user(7001, SimpleNamespace(username="new_user", first_name="Новый", last_name="Пользователь"))
            before = bot.admin_usage_users()
            fresh = next(row for row in before if row["chat_id"] == 7001)
            self.assertEqual(fresh["requests"], 0)
            self.assertEqual(fresh["cost"], 0)
            self.assertEqual(fresh["effective_model"], "qwen/test")
            self.assertEqual(next(row for row in before if row["chat_id"] == 6999)["requests"], 1)

            bot.record_usage(7001, "shared", "qwen/test", {"usage": {"prompt_tokens": 12, "completion_tokens": 8, "cost": 0.004}})
            bot.record_usage(7001, "managed", "deepseek/test", {"usage": {"prompt_tokens": 5, "completion_tokens": 3, "cost": 0.002}})
            after = bot.admin_usage_users()
            accounted = next(row for row in after if row["chat_id"] == 7001)
            self.assertEqual((accounted["input_tokens"], accounted["output_tokens"], accounted["requests"]), (17, 11, 2))
            self.assertAlmostEqual(accounted["cost"], 0.006)
            self.assertEqual(next(row for row in after if row["chat_id"] == 6999)["requests"], 1)
            rows = bot.admin_user_usage_rows(7001)
            self.assertEqual({row["provider"] for row in rows}, {"openrouter"})
            self.assertEqual({row["source"] for row in rows}, {"shared", "managed"})
        database.close()

    def test_admin_runtime_config_has_admin_precedence_and_safe_metadata(self):
        defaults = {"fast_model": ("env/model", "ENV")}
        with patch.object(bot, "_runtime_env_defaults", return_value=defaults), \
             patch.object(bot, "_runtime_config_record", return_value={"overrides": {"fast_model": "admin/model"}, "updated_at": "2026-01-01T00:00:00+00:00", "updated_by": 77}):
            snapshot = bot.runtime_config_snapshot()
        self.assertEqual(snapshot["fields"]["fast_model"], {"value": "admin/model", "source": "ADMIN"})
        self.assertEqual(snapshot["updated_by"], 77)

    def test_admin_runtime_config_validates_safe_values_only(self):
        with self.assertRaises(ValueError):
            bot._normalise_runtime_config_value("fast_model", "bad model value")
        with self.assertRaises(ValueError):
            bot._normalise_runtime_config_value("tts_provider", "unknown-provider")
        self.assertEqual(bot._normalise_runtime_config_value("model_catalog", "one/model, two/model"), ["one/model", "two/model"])

    def test_batch_stt_env_prefers_canonical_name_then_one_release_alias(self):
        values = {"BATCH_STT_MODEL": "canonical-model", "STT_MODEL": "legacy-model"}
        with patch.object(bot.os, "getenv", side_effect=lambda name: values.get(name)):
            self.assertEqual(bot.env_first("BATCH_STT_MODEL", "STT_MODEL", default="default"), "canonical-model")
        values = {"STT_MODEL": "legacy-model"}
        with patch.object(bot.os, "getenv", side_effect=lambda name: values.get(name)):
            self.assertEqual(bot.env_first("BATCH_STT_MODEL", "STT_MODEL", default="default"), "legacy-model")

    def test_batch_stt_runtime_uses_canonical_configuration_only(self):
        source = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
        example = (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")
        self.assertIn("BATCH_STT_MODEL = env_first(\"BATCH_STT_MODEL\", \"STT_MODEL\"", source)
        self.assertIn("timeout=BATCH_STT_TIMEOUT_SEC", source)
        self.assertIn("BATCH_STT_MODEL=mistralai/voxtral-mini-transcribe", example)
        for removed in ("VOICE_CONVERSATION_ENABLED=", "VOICE_MODE=", "VAD_SPEECH_THRESHOLD="):
            self.assertNotIn(removed, example)

    def test_ab_router_uses_configured_provider_chains_only(self):
        with patch.object(bot, "FAST_MODEL_PROVIDERS", ("fast-one",)), \
             patch.object(bot, "STRONG_MODEL_PROVIDERS", ("strong-one", "strong-two")), \
             patch.object(bot, "FAST_MODEL_ALLOW_PROVIDER_FALLBACK", False), \
             patch.object(bot, "STRONG_MODEL_ALLOW_PROVIDER_FALLBACK", True):
            self.assertEqual(bot.provider_preferences_for(bot.FAST_MODEL), {
                "only": ["fast-one"], "order": ["fast-one"],
                "allow_fallbacks": False, "require_parameters": True,
            })
            self.assertEqual(bot.provider_preferences_for(bot.STRONG_MODEL), {
                "only": ["strong-one", "strong-two"], "order": ["strong-one", "strong-two"],
                "allow_fallbacks": True, "require_parameters": True,
            })
        self.assertIsNone(bot.provider_preferences_for("custom/model"))

    def test_empty_provider_env_uses_normal_openrouter_routing(self):
        with patch.object(bot, "FAST_MODEL_PROVIDERS", ()), patch.object(bot, "STRONG_MODEL_PROVIDERS", ()):
            self.assertIsNone(bot.provider_preferences_for(bot.FAST_MODEL))
            self.assertIsNone(bot.provider_preferences_for(bot.STRONG_MODEL))

    def test_streaming_request_adds_provider_preferences_for_fast_model(self):
        response = Mock()
        with patch.object(bot, "api_key_for_chat", return_value=("test-key", "")), \
             patch.object(bot.requests, "post", return_value=response) as post:
            with patch.object(bot, "FAST_MODEL_PROVIDERS", ("configured-fast",)):
                bot.request_chat_stream(42, bot.FAST_MODEL, [{"role": "user", "content": "тест"}])
        self.assertEqual(post.call_args.kwargs["json"]["provider"]["only"], ["configured-fast"])

    def test_mistral_session_mints_scoped_token(self):
        response = Mock(ok=True)
        response.json.return_value = {"client_secret": {"value": "rt_short", "expires_at": "soon"}}
        with patch.object(bot, "MISTRAL_API_KEY", "server-secret"), patch.object(bot.requests, "post", return_value=response) as post:
            result = bot.mint_mistral_realtime_session()
        self.assertEqual(result["token"], "rt_short")
        self.assertNotIn("server-secret", repr(result))
        self.assertEqual(post.call_args.kwargs["json"]["purpose"], "realtime")

    def test_cancelled_stream_never_starts_openrouter_request(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="system"), \
             patch.object(bot, "request_chat_stream") as request:
            events = list(bot.stream_agent_response(42, "тест", cancel))
        self.assertEqual(events, [{"type": "cancelled"}])
        request.assert_not_called()

    def test_reasoning_never_reaches_shared_stream_or_canonical_history(self):
        response = Mock(ok=True, status_code=200)
        response.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"reasoning":"private","content":"<thi"}}]}',
            b'data: {"choices":[{"delta":{"content":"nk>Need answer</think>Visible "}}]}',
            b'data: {"choices":[{"delta":{"content":"answer."},"finish_reason":"stop"}]}',
            b'data: [DONE]',
        ]
        router = SimpleNamespace(resolve=lambda *_: {"primary": "test-model", "fallback": ""})
        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="system"), \
             patch.object(bot, "model_router", return_value=router), \
             patch.object(bot, "runtime_config_values", return_value={"strong_model": "", "model_catalog": []}), \
             patch.object(bot, "request_chat_stream", return_value=response), \
             patch.object(bot, "add_message") as add:
            events = list(bot.stream_agent_response(42, "test"))
        deltas = "".join(event["text"] for event in events if event["type"] == "delta")
        self.assertEqual(deltas, "Visible answer.")
        self.assertEqual(events[-1]["text"], "Visible answer.")
        add.assert_any_call(42, "assistant", "Visible answer.")
        self.assertNotIn("think", repr(events).lower())

    def test_stop_during_partial_tool_call_never_executes_tool(self):
        cancel = threading.Event()
        response = Mock(ok=True, status_code=200)

        def partial_tool_stream():
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"add_task","arguments":"{\\"text\\":\\""}}]}}]}'
            cancel.set()
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"unfinished"}}]}}]}'

        response.iter_lines.return_value = partial_tool_stream()
        router = SimpleNamespace(resolve=lambda *_: {"primary": "test-model", "fallback": ""})
        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="system"), \
             patch.object(bot, "model_router", return_value=router), \
             patch.object(bot, "request_chat_stream", return_value=response), \
             patch.object(bot, "execute_tool") as execute:
            events = list(bot.stream_agent_response(42, "добавь задачу", cancel))
        self.assertEqual(events, [{"type": "cancelled"}])
        execute.assert_not_called()

    def test_cancel_stream_closes_active_http_response(self):
        cancel = threading.Event()
        response = Mock()
        with bot.ACTIVE_STREAM_RESPONSES_LOCK:
            bot.ACTIVE_STREAM_RESPONSES[cancel] = response
        try:
            bot.cancel_stream(cancel)
            self.assertTrue(cancel.is_set())
            response.close.assert_called_once_with()
        finally:
            with bot.ACTIVE_STREAM_RESPONSES_LOCK:
                bot.ACTIVE_STREAM_RESPONSES.pop(cancel, None)

    async def test_stop_update_cancels_matching_draft(self):
        event = threading.Event()
        bot.register_active_draft(42, 77, event)
        update = SimpleNamespace(api_kwargs={"stopped_message_generation": {"chat": {"id": 42}, "draft_id": 77}})
        try:
            await bot.stopped_generation_handler(update, None)
            self.assertTrue(event.is_set())
        finally:
            bot.unregister_active_draft(42, 77)

    async def test_telegram_draft_finishes_as_persistent_message(self):
        telegram = SimpleNamespace(send_message_draft=AsyncMock(return_value=True))
        context = SimpleNamespace(bot=telegram)
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42))
        events = iter([
            {"type": "delta", "text": "При"},
            {"type": "delta", "text": "вет"},
            {"type": "done", "text": "Привет"},
        ])
        with patch.object(bot, "stream_agent_response", return_value=events), \
             patch.object(bot, "send_answer", new=AsyncMock()) as final_send:
            completed = await bot.stream_answer_to_telegram(update, context, "тест")
        self.assertTrue(completed)
        self.assertGreaterEqual(telegram.send_message_draft.await_count, 2)
        final_send.assert_awaited_once()

    async def test_output_modes_keep_text_voice_and_combined_contracts(self):
        message = SimpleNamespace(reply_text=AsyncMock(), reply_voice=AsyncMock())
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42), effective_message=message)

        async def voice_file(_text):
            handle = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            handle.write(b"ID3")
            handle.close()
            return Path(handle.name)

        for mode, text_count, voice_count in (("text", 1, 0), ("voice", 0, 1), ("voice_and_text", 1, 1)):
            message.reply_text.reset_mock()
            message.reply_voice.reset_mock()
            with patch.object(bot, "get_mode", return_value=mode), \
                 patch.object(bot, "make_voice", new=AsyncMock(side_effect=voice_file)):
                await bot.send_answer(update, "Готово.")
            self.assertEqual(message.reply_text.await_count, text_count, mode)
            self.assertEqual(message.reply_voice.await_count, voice_count, mode)

    def test_effective_model_user_override_and_auto_share_one_setting(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.execute("CREATE TABLE user_settings(chat_id INTEGER PRIMARY KEY,primary_model TEXT NOT NULL DEFAULT '',fallback_model TEXT NOT NULL DEFAULT '',vision_model TEXT NOT NULL DEFAULT '')")
        router = bot.ModelRouter(lambda: database, "admin-fast", ["admin-strong"], "admin-vision")
        fields = {
            "fast_model": {"value": "admin-fast", "source": "ADMIN"},
            "strong_model": {"value": "env-strong", "source": "ENV"},
            "vision_model": {"value": "env-vision", "source": "ENV"},
            "tts_provider": {"value": "edge", "source": "ENV"},
            "tts_voice": {"value": "ru-RU-DmitryNeural", "source": "ENV"},
        }
        with patch.object(bot, "conn", return_value=database), \
             patch.object(bot, "runtime_config_snapshot", return_value={"fields": fields}), \
             patch.object(bot, "vision_models_for", return_value=["env-vision"]), \
             patch.object(bot, "has_personal_api_key", return_value=False):
            router.set_primary(42, "user-model")
            selected = bot.effective_user_ai_config(42)
            self.assertEqual(selected["effective_model"], {"value": "user-model", "source": "USER"})
            self.assertEqual(selected["fast_default"], {"value": "admin-fast", "source": "ADMIN"})
            self.assertEqual(selected["strong_fallback"], {"value": "env-strong", "source": "ENV"})
            router.set_primary(42, "")
            automatic = bot.effective_user_ai_config(42)
            self.assertEqual(automatic["effective_model"], {"value": "admin-fast", "source": "ADMIN"})
        database.close()

    def test_canonical_history_never_stores_or_returns_reasoning(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,role TEXT,content TEXT,created_at TEXT);
            CREATE TABLE conversation_summaries(chat_id INTEGER PRIMARY KEY,summary TEXT,through_message_id INTEGER,version INTEGER,updated_at TEXT);
        """)
        with patch.object(bot, "conn", return_value=database):
            bot.add_message(42, "assistant", "<think>Нужно ответить</think>Готово")
            stored = database.execute("SELECT content FROM messages").fetchone()["content"]
            self.assertEqual(stored, "Готово")
            self.assertEqual(bot.history(42), [{"role": "assistant", "content": "Готово"}])
        database.close()


if __name__ == "__main__":
    unittest.main()
