import ast
import asyncio
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import Forbidden, TimedOut

import bot


class AsyncResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_send_has_one_bounded_retry(self):
        telegram = SimpleNamespace(send_message=AsyncMock(side_effect=[TimedOut("slow"), SimpleNamespace(message_id=9)]))
        with patch.object(bot, "TELEGRAM_SEND_RETRIES", 1), patch.object(bot.secrets, "randbelow", return_value=0), patch.object(bot.asyncio, "sleep", new=AsyncMock()) as sleep:
            sent = await bot.telegram_send_with_retry(telegram, source="test", chat_id=42, text="ok")
        self.assertEqual(sent.message_id, 9)
        self.assertEqual(telegram.send_message.await_count, 2)
        sleep.assert_awaited_once()

    async def test_replace_active_ui_absorbs_final_timeout(self):
        telegram = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock(side_effect=TimedOut("slow")))
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42))
        context = SimpleNamespace(bot=telegram)
        with patch.object(bot, "TELEGRAM_SEND_RETRIES", 0), patch.object(bot, "active_ui_message_id", return_value=0):
            result = await bot.replace_active_ui(update, context, "Меню", None)
        self.assertIsNone(result)
        telegram.send_message.assert_awaited_once()

    async def test_replace_active_ui_absorbs_forbidden(self):
        telegram = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock(side_effect=Forbidden("blocked")))
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=42))
        context = SimpleNamespace(bot=telegram)
        with patch.object(bot, "active_ui_message_id", return_value=0), patch.object(bot, "set_app_setting") as unavailable:
            result = await bot.replace_active_ui(update, context, "Меню", None)
        self.assertIsNone(result)
        unavailable.assert_called_once()

    async def test_event_loop_lag_metric_is_recorded(self):
        loop = asyncio.get_running_loop()
        context = SimpleNamespace(job=SimpleNamespace(data={"expected": loop.time() - 0.02}))
        await bot.event_loop_lag_tick(context)
        self.assertGreaterEqual(bot.RUNTIME_METRICS["event_loop_lag_ms"]["value_ms"], 15)

    async def test_voice_callback_and_reminder_work_do_not_starve_loop(self):
        async def slow_send(**_):
            await asyncio.sleep(0.03)
            return SimpleNamespace(message_id=10)

        telegram = SimpleNamespace(send_message=AsyncMock(side_effect=slow_send))
        reminder_context = SimpleNamespace(bot=telegram)
        rows = [{"id": index, "chat_id": 42 + index, "text": "Проверка"} for index in range(8)]
        query = SimpleNamespace(
            answer=AsyncMock(), data="ui:close",
            message=SimpleNamespace(chat_id=42, message_id=1, delete=AsyncMock()),
        )
        update = SimpleNamespace(callback_query=query, effective_user=None)
        callback_context = SimpleNamespace()
        loop_delays = []

        async def heartbeat():
            expected = asyncio.get_running_loop().time()
            for _ in range(12):
                expected += 0.01
                await asyncio.sleep(0.01)
                loop_delays.append(max(0, asyncio.get_running_loop().time() - expected))

        with patch.object(bot, "due_reminder_rows", return_value=(rows, [])), \
             patch.object(bot, "mark_reminder_delivered"), \
             patch.object(bot, "adopt_active_ui", new=AsyncMock()), \
             patch.object(bot, "register_bot_user"), \
             patch.object(bot, "set_active_ui_message_id"):
            await asyncio.gather(
                bot.reminder_tick(reminder_context),
                asyncio.to_thread(time.sleep, 0.08),
                bot.callback(update, callback_context),
                heartbeat(),
            )
        query.answer.assert_awaited_once()
        self.assertLess(max(loop_delays), 0.05)

    def test_async_functions_do_not_call_requests_or_time_sleep(self):
        tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
        violations = []
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)):
            for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
                target = call.func
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    if (target.value.id, target.attr) in {("requests", "get"), ("requests", "post"), ("requests", "request"), ("time", "sleep")}:
                        violations.append((function.name, call.lineno))
        self.assertEqual(violations, [])

    def test_reminder_tick_is_not_hot_loop(self):
        self.assertGreaterEqual(bot.REMINDER_TICK_SECONDS, 15)
        self.assertLessEqual(bot.REMINDER_TICK_SECONDS, 30)


class TelemetrySeriesTests(unittest.TestCase):
    def setUp(self):
        self.original_enabled = bot.TELEMETRY_ENABLED
        bot.TELEMETRY_ENABLED = True
        bot.reset_runtime_metric_series()

    def tearDown(self):
        bot.reset_runtime_metric_series()
        bot.TELEMETRY_ENABLED = self.original_enabled

    def test_export_has_bounded_numeric_latency_aggregates_only(self):
        for value in (10, 20, 30, 40, 50):
            bot.record_runtime_metric("llm_total_ms", value, chat_id=42, text="never exported")
        bot.record_runtime_metric("untracked_metric", 999)

        exported = bot.runtime_metric_export()
        llm = exported["metrics"]["llm_total_ms"]
        self.assertEqual(llm, {"count": 5, "p50": 30.0, "p95": 48.0, "max": 50.0})
        self.assertEqual(exported["metrics"]["tool_execution_ms"]["count"], 0)
        self.assertNotIn("untracked_metric", exported["metrics"])
        serialized = repr(exported).lower()
        self.assertNotIn("chat_id", serialized)
        self.assertNotIn("never exported", serialized)

    def test_disabled_telemetry_does_not_append_samples(self):
        bot.TELEMETRY_ENABLED = False
        bot.record_runtime_metric("llm_total_ms", 12)
        self.assertEqual(bot.runtime_metric_export()["metrics"]["llm_total_ms"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
