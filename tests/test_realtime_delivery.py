import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot


class RealtimeDeliveryTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
