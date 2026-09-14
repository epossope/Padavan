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
    def setUp(self):
        with bot.TELEGRAM_FINAL_CHUNK_LOCK:
            bot.TELEGRAM_FINAL_CHUNK_KEYS.clear()
            bot.TELEGRAM_FINAL_CHUNK_ORDER.clear()

    def test_new_canonical_user_is_visible_before_and_after_usage(self):
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE bot_users(user_number INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER UNIQUE,username TEXT NOT NULL DEFAULT '',display_name TEXT NOT NULL DEFAULT '',first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL);
            CREATE TABLE usage_events(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL,source TEXT NOT NULL,model TEXT NOT NULL,input_tokens INTEGER NOT NULL DEFAULT 0,output_tokens INTEGER NOT NULL DEFAULT 0,cost REAL NOT NULL DEFAULT 0,provider TEXT NOT NULL DEFAULT '',call_type TEXT NOT NULL DEFAULT 'llm',created_at TEXT NOT NULL);
            CREATE TABLE user_request_events(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL,channel TEXT NOT NULL DEFAULT 'chat',created_at TEXT NOT NULL);
            CREATE TABLE user_settings(chat_id INTEGER PRIMARY KEY,primary_model TEXT NOT NULL DEFAULT '',fallback_model TEXT NOT NULL DEFAULT '',vision_model TEXT NOT NULL DEFAULT '');
            CREATE TABLE user_api_keys(chat_id INTEGER PRIMARY KEY,encrypted_key TEXT NOT NULL,key_hint TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
            CREATE TABLE managed_api_keys(chat_id INTEGER PRIMARY KEY,encrypted_key TEXT NOT NULL,key_hash TEXT NOT NULL DEFAULT '',key_hint TEXT NOT NULL DEFAULT '',limit_usd REAL NOT NULL DEFAULT 2,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE app_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by INTEGER);
        """)
        config = {"fast_model": "qwen/test", "fast_model_providers": [], "fast_model_allow_provider_fallback": False,
                  "strong_model": "deepseek/test", "strong_model_providers": [], "strong_model_allow_provider_fallback": True}
        with patch.object(bot, "conn", return_value=database), patch.object(bot, "runtime_config_values", return_value=config):
            bot.register_bot_user(6999, SimpleNamespace(username="existing", first_name="Старый", last_name="Пользователь"))
            bot.record_user_request(6999)
            bot.record_usage(6999, "shared", "qwen/test", {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.001}})
            bot.register_bot_user(7001, SimpleNamespace(username="new_user", first_name="Новый", last_name="Пользователь"))
            before = bot.admin_usage_users()
            fresh = next(row for row in before if row["chat_id"] == 7001)
            self.assertEqual(fresh["requests"], 0)
            self.assertEqual(fresh["cost"], 0)
            self.assertEqual(fresh["effective_model"], "qwen/test")
            self.assertEqual(next(row for row in before if row["chat_id"] == 6999)["requests"], 1)

            for _ in range(3):
                bot.record_user_request(7001)
            bot.record_usage(7001, "shared", "qwen/test", {"usage": {"prompt_tokens": 12, "completion_tokens": 8, "cost": 0.004}})
            bot.record_usage(7001, "managed", "deepseek/test", {"usage": {"prompt_tokens": 5, "completion_tokens": 3, "cost": 0.002}})
            bot.record_usage(7001, "personal", "qwen/test", {})
            database.execute("INSERT INTO user_api_keys(chat_id,encrypted_key,key_hint,active,updated_at) VALUES(7001,'encrypted','hint',1,'now')")
            after = bot.admin_usage_users()
            accounted = next(row for row in after if row["chat_id"] == 7001)
            self.assertEqual((accounted["input_tokens"], accounted["output_tokens"], accounted["requests"], accounted["llm_calls"]), (17, 11, 3, 3))
            self.assertAlmostEqual(accounted["cost"], 0.006)
            self.assertEqual(accounted["key_type"], "personal")
            self.assertEqual(next(row for row in after if row["chat_id"] == 6999)["requests"], 1)
            rows = bot.admin_user_usage_rows(7001)
            self.assertEqual({row["provider"] for row in rows}, {"openrouter"})
            self.assertEqual({row["source"] for row in rows}, {"shared", "managed", "personal"})
            self.assertEqual(sum(row["llm_calls"] for row in rows), 3)
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
        self.assertEqual(bot._normalise_runtime_config_value("fast_model", bot.MODEL_CATALOG[0]), bot.MODEL_CATALOG[0])
        with self.assertRaisesRegex(ValueError, "MODEL_CATALOG"):
            bot._normalise_runtime_config_value("fast_model", "vendor/not-canonical")

    def test_global_primary_routes_real_multiuser_outbound_requests_without_user_overrides(self):
        database = sqlite3.connect(":memory:", check_same_thread=False)
        database.row_factory = sqlite3.Row
        database.execute("CREATE TABLE user_settings(chat_id INTEGER PRIMARY KEY,primary_model TEXT NOT NULL DEFAULT '',fallback_model TEXT NOT NULL DEFAULT '',vision_model TEXT NOT NULL DEFAULT '')")
        database.executemany("INSERT INTO user_settings(chat_id,primary_model) VALUES(?,?)", [(101, "qwen/user-a"), (102, "deepseek/user-b")])
        config = {"fast_model": "router/fast", "strong_model": "router/strong"}
        fields = {name: {"value": value, "source": "ADMIN"} for name, value in config.items()}
        outbound = []

        def response_for(_chat_id, model, _messages, _tools=None, _tool_choice="auto"):
            outbound.append(model)
            response = Mock(ok=True, status_code=200)
            response.iter_lines.return_value = [b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}', b'data: [DONE]']
            return response

        common = [patch.object(bot, "runtime_config_snapshot", side_effect=lambda: {"fields": dict(fields)}),
                  patch.object(bot, "direct_live_request", return_value=None),
                  patch.object(bot, "conversation_context", return_value=[]),
                  patch.object(bot, "system_prompt", return_value="system"),
                  patch.object(bot, "request_chat_stream", side_effect=response_for),
                  patch.object(bot, "record_usage"), patch.object(bot, "record_user_request"),
                  patch.object(bot, "add_message", side_effect=range(1, 100))]
        with common[0], common[1], common[2], common[3], common[4], common[5], common[6], common[7]:
            for user in (100, 101, 102):
                list(bot.stream_agent_response(user, "test"))
            self.assertEqual(outbound, ["router/fast"] * 3)
            outbound.clear();fields["fast_model"] = {"value": "global/model-x", "source": "ADMIN"}
            for user in (100, 101, 102):
                list(bot.stream_agent_response(user, "test"))
            self.assertEqual(outbound, ["global/model-x"] * 3)
        database.close()

    def test_global_vision_force_is_used_by_real_outbound_request_for_every_user(self):
        config = {"global_vision_mode": "force", "global_force_vision_model": "vision/global-y",
                  "vision_model": "vision/default", "vision_fallback_models": []}
        outbound = []

        def vision_response(chat_id, model, _messages):
            outbound.append((chat_id, model))
            response = Mock(ok=True, status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": "Описание"}}]}
            return response

        handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        handle.write(b"image");handle.close()
        try:
            with patch.object(bot, "runtime_config_values", side_effect=lambda: dict(config)), \
                 patch.object(bot, "request_vision", side_effect=vision_response), \
                 patch.object(bot, "api_key_for_chat", return_value=("key", "shared")), \
                 patch.object(bot, "record_usage"):
                for user in (100, 101, 102):
                    self.assertEqual(bot.describe_image(user, handle.name), "Описание")
            self.assertEqual(outbound, [(100, "vision/global-y"), (101, "vision/global-y"), (102, "vision/global-y")])
        finally:
            Path(handle.name).unlink(missing_ok=True)

    def test_global_vision_force_is_used_by_real_request_then_auto_restores(self):
        config = {"global_vision_mode": "force", "global_force_vision_model": "vision/global-y",
                  "vision_fallback_models": [], "vision_model": "vision/default"}
        outbound = []
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "Описание"}}], "usage": {}}
        image = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        image.write(b"image");image.close()
        try:
            with patch.object(bot, "runtime_config_values", side_effect=lambda: dict(config)), \
                 patch.object(bot, "request_vision", side_effect=lambda cid, model, messages: outbound.append(model) or response), \
                 patch.object(bot, "record_usage"), patch.object(bot, "api_key_for_chat", return_value=("key", "shared")):
                self.assertEqual(bot.describe_image(101, image.name), "Описание")
                self.assertEqual(outbound, ["vision/global-y"])
                outbound.clear();config["global_vision_mode"] = "auto"
                with patch.object(bot, "has_personal_api_key", return_value=False), \
                     patch.object(bot, "shared_vision_model", return_value="vision/default"):
                    self.assertEqual(bot.describe_image(102, image.name), "Описание")
                self.assertEqual(outbound, ["vision/default"])
        finally:
            Path(image.name).unlink(missing_ok=True)

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
        self.assertEqual(response.noema_key_source, "")

    def test_outbound_response_keeps_the_key_source_that_was_actually_used(self):
        response = Mock()
        with patch.object(bot, "api_key_for_chat", return_value=("test-key", "personal")), \
             patch.object(bot.requests, "post", return_value=response):
            returned = bot.request_chat_stream(42, bot.FAST_MODEL, [{"role": "user", "content": "test"}])
        self.assertIs(returned, response)
        self.assertEqual(bot.response_key_source(response, 42), "personal")

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
        self.assertEqual(events[-1], {"type": "cancelled"})
        request.assert_not_called()

    def test_reasoning_never_reaches_shared_stream_or_canonical_history(self):
        violating = Mock(ok=True, status_code=200)
        violating.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"reasoning":"private","content":"<thi"}}]}',
            b'data: {"choices":[{"delta":{"content":"nk>Need answer</think>Visible "}}]}',
            b'data: {"choices":[{"delta":{"content":"answer."},"finish_reason":"stop"}]}',
            b'data: [DONE]',
        ]
        compliant = Mock(ok=True, status_code=200)
        compliant.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"content":"Visible "}}]}',
            b'data: {"choices":[{"delta":{"content":"answer."},"finish_reason":"stop"}]}',
            b'data: [DONE]',
        ]
        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="system"), \
             patch.object(bot, "chat_model_candidates", return_value=["test-model", "safe-model"]), \
             patch.object(bot, "request_chat_stream", side_effect=[violating, compliant]), \
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
        self.assertEqual(events[-1], {"type": "cancelled"})
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

    async def test_experimental_telegram_draft_finishes_as_persistent_message(self):
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
            completed = await bot.stream_answer_to_telegram_draft(update, context, "тест")
        self.assertTrue(completed)
        self.assertGreaterEqual(telegram.send_message_draft.await_count, 2)
        final_send.assert_awaited_once()

    async def test_production_stream_sends_once_then_edits_same_message(self):
        final = "A" * 200
        telegram = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
            edit_message_text=AsyncMock(return_value=True),
            delete_message=AsyncMock(return_value=True), send_message_draft=AsyncMock(),
        )
        context = SimpleNamespace(bot=telegram)
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=42),
            effective_message=SimpleNamespace(reply_voice=AsyncMock()),
        )
        events = iter([
            *({"type": "delta", "text": final[index:index + 20]}
              for index in range(0, len(final), 20)),
            {"type": "done", "text": final, "canonical_message_id": 9},
        ])
        throttle = Mock(); throttle.should_send.return_value = True
        with patch.object(bot, "stream_agent_response", return_value=events), \
             patch.object(bot, "get_mode", return_value="text"), \
             patch.object(bot, "AdaptiveDraftThrottle", return_value=throttle), \
             patch.object(bot, "reply_emoji_prefix", return_value=""):
            completed = await bot.stream_answer_to_telegram(update, context, "тест")
        self.assertTrue(completed)
        telegram.send_message.assert_awaited_once()
        telegram.send_message_draft.assert_not_awaited()
        self.assertGreaterEqual(telegram.edit_message_text.await_count, 2)
        self.assertTrue(all(call.kwargs["message_id"] == 77
                            for call in telegram.edit_message_text.await_args_list))
        self.assertEqual(telegram.edit_message_text.await_args.kwargs["text"], final)
        telegram.delete_message.assert_not_awaited()
        self.assertEqual(bot.TELEGRAM_DELIVERY_CORRELATIONS[-1]["canonical_message_id"], 9)
        self.assertEqual(bot.TELEGRAM_DELIVERY_CORRELATIONS[-1]["telegram_message_id"], 77)
        self.assertEqual(bot.TELEGRAM_DELIVERY_CORRELATIONS[-1]["final_message_ids"], [77])

    async def test_3000_char_preview_is_promoted_to_the_same_message(self):
        final = "B" * 3000
        telegram = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=78)),
            edit_message_text=AsyncMock(return_value=True), delete_message=AsyncMock(),
            send_message_draft=AsyncMock(),
        )
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_voice=AsyncMock()))
        events = iter([
            {"type": "delta", "text": final[:40]},
            {"type": "delta", "text": final[40:1500]},
            {"type": "delta", "text": final[1500:]},
            {"type": "done", "text": final, "canonical_message_id": 10},
        ])
        throttle = Mock(); throttle.should_send.return_value = True
        with patch.object(bot, "stream_agent_response", return_value=events), \
             patch.object(bot, "get_mode", return_value="text"), \
             patch.object(bot, "AdaptiveDraftThrottle", return_value=throttle), \
             patch.object(bot, "reply_emoji_prefix", return_value=""):
            self.assertTrue(await bot.stream_answer_to_telegram(
                update, SimpleNamespace(bot=telegram), "test"
            ))
        telegram.send_message.assert_awaited_once()
        self.assertGreaterEqual(telegram.edit_message_text.await_count, 3)
        self.assertEqual(telegram.edit_message_text.await_args.kwargs["message_id"], 78)
        self.assertEqual(telegram.edit_message_text.await_args.kwargs["text"], final)
        telegram.delete_message.assert_not_awaited()
        self.assertEqual(bot.TELEGRAM_DELIVERY_CORRELATIONS[-1]["final_message_ids"], [78])

    async def test_production_stream_never_exposes_reasoning(self):
        telegram = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=88)),
            edit_message_text=AsyncMock(return_value=True),
            send_message_draft=AsyncMock(),
        )
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_voice=AsyncMock()))
        violating = Mock(ok=True, status_code=200)
        violating.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"reasoning":"private","content":"<thi"}}]}',
            b'data: {"choices":[{"delta":{"content":"nk>internal</think>Visible"}}]}',
            b'data: [DONE]',
        ]
        compliant = Mock(ok=True, status_code=200)
        compliant.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"content":"Visible"},"finish_reason":"stop"}]}',
            b'data: [DONE]',
        ]
        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="system"), \
             patch.object(bot, "chat_model_candidates", return_value=["test-model", "safe-model"]), \
             patch.object(bot, "request_chat_stream", side_effect=[violating, compliant]), \
             patch.object(bot, "record_usage"), patch.object(bot, "add_message", side_effect=[1, 2]), \
             patch.object(bot, "get_mode", return_value="text"):
            await bot.stream_answer_to_telegram(update, SimpleNamespace(bot=telegram), "AAA")
        delivered = " ".join(call.kwargs["text"] for call in telegram.send_message.await_args_list)
        delivered += " " + " ".join(call.kwargs["text"] for call in telegram.edit_message_text.await_args_list)
        self.assertIn("Visible", delivered)
        self.assertNotIn("internal", delivered)
        self.assertNotIn("think", delivered.casefold())

    async def test_long_canonical_answer_is_delivered_as_every_telegram_chunk(self):
        final = "A" * 9000
        ids = iter(range(100, 120))
        telegram = SimpleNamespace(
            send_message=AsyncMock(side_effect=lambda **_kwargs: SimpleNamespace(message_id=next(ids))),
            edit_message_text=AsyncMock(return_value=True), delete_message=AsyncMock(return_value=True),
            send_message_draft=AsyncMock(),
        )
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_voice=AsyncMock()))
        events = iter([
            {"type": "delta", "text": final[:40]},
            {"type": "delta", "text": final[40:]},
            {"type": "done", "text": final, "canonical_message_id": 501},
        ])
        throttle = Mock(); throttle.should_send.return_value = True
        with patch.object(bot, "stream_agent_response", return_value=events), \
             patch.object(bot, "get_mode", return_value="text"), \
             patch.object(bot, "AdaptiveDraftThrottle", return_value=throttle), \
             patch.object(bot, "reply_emoji_prefix", return_value=""):
            self.assertTrue(await bot.stream_answer_to_telegram(update, SimpleNamespace(bot=telegram), "test"))
            expected = bot._telegram_final_chunks(42, final)
        delivered_tail = [call.kwargs["text"] for call in telegram.send_message.await_args_list[1:]]
        self.assertEqual(telegram.edit_message_text.await_args.kwargs["text"], expected[0])
        self.assertEqual(delivered_tail, expected[1:])
        self.assertEqual(telegram.send_message.await_count, len(expected))
        self.assertEqual(telegram.edit_message_text.await_args.kwargs["text"] + "".join(delivered_tail),
                         final)
        telegram.delete_message.assert_not_awaited()
        self.assertEqual(bot.TELEGRAM_DELIVERY_CORRELATIONS[-1]["final_message_ids"],
                         list(range(100, 100 + len(expected))))

    async def test_canonical_chunk_correlation_guard_prevents_duplicate_final_send(self):
        telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=700)))
        with patch.object(bot, "reply_emoji_prefix", return_value=""):
            expected = bot._telegram_final_chunks(42, "canonical final")
            first = await bot._deliver_telegram_final(
                telegram, chat_id=42, final="canonical final", request_id="request-a",
                canonical_message_id=900,
            )
            second = await bot._deliver_telegram_final(
                telegram, chat_id=42, final="canonical final", request_id="request-b",
                canonical_message_id=900,
            )
        self.assertEqual(first, [700])
        self.assertEqual(second, [])
        self.assertEqual(telegram.send_message.await_count, len(expected))

    async def test_edit_failures_still_deliver_complete_immutable_final(self):
        failures = [
            ("timeout", lambda: [bot.TimedOut("slow"), bot.TimedOut("slow")]),
            ("retry-after", lambda: [bot.RetryAfter(0.01), bot.RetryAfter(0.01)]),
            ("bad-request", lambda: [bot.BadRequest("invalid edit")]),
        ]
        for offset, (name, errors) in enumerate(failures):
            with self.subTest(name=name):
                with bot.TELEGRAM_FINAL_CHUNK_LOCK:
                    bot.TELEGRAM_FINAL_CHUNK_KEYS.clear(); bot.TELEGRAM_FINAL_CHUNK_ORDER.clear()
                final = (name + "-final-") * 20
                ids = iter(range(200 + offset * 20, 220 + offset * 20))
                telegram = SimpleNamespace(
                    send_message=AsyncMock(side_effect=lambda **_kwargs: SimpleNamespace(message_id=next(ids))),
                    edit_message_text=AsyncMock(side_effect=errors()),
                    delete_message=AsyncMock(return_value=True), send_message_draft=AsyncMock(),
                )
                update = SimpleNamespace(effective_chat=SimpleNamespace(id=42),
                                         effective_message=SimpleNamespace(reply_voice=AsyncMock()))
                events = iter([
                    {"type": "delta", "text": final[:30]},
                    {"type": "done", "text": final, "canonical_message_id": 600 + offset},
                ])
                throttle = Mock(); throttle.should_send.return_value = True
                with patch.object(bot, "stream_agent_response", return_value=events), \
                     patch.object(bot, "get_mode", return_value="text"), \
                     patch.object(bot, "AdaptiveDraftThrottle", return_value=throttle), \
                     patch.object(bot, "reply_emoji_prefix", return_value=""), \
                     patch.object(bot.asyncio, "sleep", new=AsyncMock()):
                    self.assertTrue(await bot.stream_answer_to_telegram(update, SimpleNamespace(bot=telegram), "test"))
                    expected = bot._telegram_final_chunks(42, final)
                delivered = [call.kwargs["text"] for call in telegram.send_message.await_args_list[1:]]
                self.assertEqual(delivered, expected)
                telegram.delete_message.assert_awaited_once_with(
                    chat_id=42, message_id=200 + offset * 20
                )
                self.assertEqual(bot.TELEGRAM_DELIVERY_CORRELATIONS[-1]["final_message_ids"],
                                 list(range(201 + offset * 20, 201 + offset * 20 + len(expected))))

    async def test_progress_status_is_removed_when_first_stream_bubble_appears(self):
        ids = iter((50, 51))
        telegram = SimpleNamespace(
            send_message=AsyncMock(side_effect=lambda **_kwargs: SimpleNamespace(message_id=next(ids))),
            edit_message_text=AsyncMock(return_value=True), delete_message=AsyncMock(return_value=True),
            send_chat_action=AsyncMock(), send_message_draft=AsyncMock(),
        )
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_voice=AsyncMock()))
        final = "Visible streamed answer"
        events = iter([
            {"type": "state", "state": "REQUESTING", "text": "Думаю…"},
            {"type": "delta", "text": final[:12]},
            {"type": "delta", "text": final[12:]},
            {"type": "done", "text": final, "canonical_message_id": 700},
        ])
        throttle = Mock(); throttle.should_send.return_value = True
        with patch.object(bot, "stream_agent_response", return_value=events), \
             patch.object(bot, "get_mode", return_value="text"), \
             patch.object(bot, "AdaptiveDraftThrottle", return_value=throttle), \
             patch.object(bot, "reply_emoji_prefix", return_value=""):
            self.assertTrue(await bot.stream_answer_to_telegram(
                update, SimpleNamespace(bot=telegram), "test"
            ))
        self.assertEqual(telegram.send_message.await_count, 2)
        telegram.delete_message.assert_awaited_once_with(chat_id=42, message_id=50)
        self.assertTrue(all(call.kwargs["message_id"] == 51
                            for call in telegram.edit_message_text.await_args_list))
        self.assertEqual(bot.TELEGRAM_DELIVERY_CORRELATIONS[-1]["final_message_ids"], [51])

    async def test_live_production_sequence_promotes_preview_without_second_final(self):
        """Regression: a real progress + fast deltas path never reaches send_answer."""
        operations, ids = [], iter((50, 51))

        def sent(**kwargs):
            operations.append(("send", kwargs["text"]))
            return SimpleNamespace(message_id=next(ids))

        def edited(**kwargs):
            operations.append(("edit", kwargs["message_id"], kwargs["text"]))
            return True

        def deleted(**kwargs):
            operations.append(("delete", kwargs["message_id"]))
            return True

        telegram = SimpleNamespace(
            send_message=AsyncMock(side_effect=sent),
            edit_message_text=AsyncMock(side_effect=edited),
            delete_message=AsyncMock(side_effect=deleted),
            send_chat_action=AsyncMock(), send_message_draft=AsyncMock(),
        )
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_voice=AsyncMock()))
        first, second, third, final_tail = "a" * 10, "b" * 50, "c" * 200, "d" * 20
        final = first + second + third + final_tail
        events = iter([
            {"type": "state", "state": "STREAMING", "text": "Готовлю ответ…"},
            {"type": "delta", "text": first},
            {"type": "delta", "text": second},
            {"type": "delta", "text": third},
            {"type": "done", "text": final, "canonical_message_id": 701},
        ])
        with patch.object(bot, "stream_agent_response", return_value=events), \
             patch.object(bot, "get_mode", return_value="text"), \
             patch.object(bot, "reply_emoji_prefix", return_value=""):
            self.assertTrue(await bot.stream_answer_to_telegram(
                update, SimpleNamespace(bot=telegram), "ordinary request"
            ))

        self.assertEqual(telegram.send_message.await_count, 2)  # status + preview only
        self.assertEqual(operations[0], ("send", "✍️ Готовлю ответ…"))
        self.assertEqual(operations[1], ("send", first + second))
        self.assertEqual(operations[2], ("delete", 50))
        self.assertGreaterEqual(telegram.edit_message_text.await_count, 2)
        self.assertEqual(telegram.edit_message_text.await_args_list[0].kwargs["message_id"], 51)
        self.assertEqual(telegram.edit_message_text.await_args_list[0].kwargs["text"], first + second + third)
        self.assertEqual(telegram.edit_message_text.await_args.kwargs["message_id"], 51)
        self.assertEqual(telegram.edit_message_text.await_args.kwargs["text"], final)
        self.assertEqual(bot.TELEGRAM_DELIVERY_CORRELATIONS[-1]["final_message_ids"], [51])

    async def test_private_text_handler_ignores_legacy_draft_transport(self):
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_message=SimpleNamespace(text="ordinary request", entities=[]),
            effective_user=SimpleNamespace(),
        )
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace())
        with patch.object(bot, "register_bot_user"), \
             patch.object(bot, "TELEGRAM_DRAFT_STREAMING_ENABLED", True), \
             patch.object(bot, "stream_answer_to_telegram", new=AsyncMock(return_value=True)) as persistent, \
             patch.object(bot, "stream_answer_to_telegram_draft", new=AsyncMock()) as legacy_draft, \
             patch.object(bot, "drain_media_outbox", new=AsyncMock()):
            await bot.text_handler(update, context)
        persistent.assert_awaited_once_with(update, context, "ordinary request")
        legacy_draft.assert_not_awaited()

    async def test_short_final_removes_status_before_immutable_send(self):
        operations, ids = [], iter((60, 61))

        def sent(**kwargs):
            operations.append(("send", kwargs["text"]))
            return SimpleNamespace(message_id=next(ids))

        def deleted(**kwargs):
            operations.append(("delete", kwargs["message_id"]))
            return True

        telegram = SimpleNamespace(
            send_message=AsyncMock(side_effect=sent), edit_message_text=AsyncMock(),
            delete_message=AsyncMock(side_effect=deleted), send_chat_action=AsyncMock(),
        )
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_voice=AsyncMock()))
        events = iter([
            {"type": "state", "state": "REQUESTING", "text": "Думаю…"},
            {"type": "delta", "text": "коротко"},
            {"type": "done", "text": "коротко", "canonical_message_id": 702},
        ])
        with patch.object(bot, "stream_agent_response", return_value=events), \
             patch.object(bot, "get_mode", return_value="text"), \
             patch.object(bot, "reply_emoji_prefix", return_value=""):
            self.assertTrue(await bot.stream_answer_to_telegram(
                update, SimpleNamespace(bot=telegram), "ordinary request"
            ))
        self.assertEqual(operations, [
            ("send", "🧠 Думаю…"), ("delete", 60), ("send", "коротко"),
        ])
        telegram.edit_message_text.assert_not_awaited()

    async def test_output_modes_keep_text_voice_and_combined_contracts(self):
        message = SimpleNamespace(reply_text=AsyncMock(), reply_voice=AsyncMock())
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42), effective_message=message)

        async def voice_file(_text, *, chat_id=None):
            self.assertEqual(chat_id, 42)
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

    async def test_telegram_speech_prefetch_delivers_before_stream_completion_and_in_order(self):
        played = []
        first_played = asyncio.Event()

        async def synthesize(text, *, chat_id=None, preferences=None):
            if text == "first":
                await asyncio.sleep(.01)
            handle = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            handle.write(text.encode()); handle.close()
            return Path(handle.name)

        async def deliver(*, voice):
            played.append(voice.read().decode())
            first_played.set()

        telegram = SimpleNamespace(send_chat_action=AsyncMock())
        update = SimpleNamespace(effective_message=SimpleNamespace(reply_voice=AsyncMock(side_effect=deliver)))
        preferences = {"voice": "voice", "speed": 1, "pitch": 1, "volume": 1}
        with patch.object(bot, "get_voice_preferences", return_value=preferences), \
             patch.object(bot, "make_voice", new=AsyncMock(side_effect=synthesize)):
            queue = bot.TelegramSpeechQueue(update, telegram, 42)
            queue.enqueue("first")
            queue.enqueue("second")
            await asyncio.wait_for(first_played.wait(), 1)
            self.assertEqual(played[0], "first")
            self.assertFalse(queue.worker.done())
            await queue.finish()
        self.assertEqual(played, ["first", "second"])
        for metric in ("tts_prepare_start_ms", "tts_first_audio_ready_ms", "tts_playback_start_ms"):
            self.assertIn(metric, bot.RUNTIME_METRICS)

    def test_effective_model_is_global_and_legacy_user_override_is_ignored(self):
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
            self.assertEqual(selected["effective_model"], {"value": "admin-fast", "source": "ADMIN"})
            self.assertEqual(selected["fast_default"], {"value": "admin-fast", "source": "ADMIN"})
            self.assertEqual(selected["strong_fallback"], {"value": "env-strong", "source": "ENV"})
            self.assertEqual(selected["personal"]["model_override"], "")
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
            item = bot.history(42)[0]
            self.assertEqual(item["message_id"], 1)
            self.assertEqual(item["role"], "assistant")
            self.assertEqual(item["content"], "Готово")
            self.assertTrue(item["created_at"])
        database.close()


if __name__ == "__main__":
    unittest.main()
