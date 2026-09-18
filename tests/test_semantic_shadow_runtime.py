import asyncio
import os
import threading
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot
from grounded_response import EvidenceAssembler
from plan_runtime import BotDomainServices, PlanValidator
from semantic_core import ActionRequest, ReadRequest, SemanticPlan
from semantic_orchestrator import LegacyExecutionTrace, SemanticShadowOrchestrator


class _RecordingOrchestrator:
    def __init__(self):
        self.calls = []

    def schedule(self, **kwargs):
        self.calls.append((asyncio.get_running_loop(), kwargs))


class SemanticShadowRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_stream_schedules_once_on_the_application_loop(self):
        loop = asyncio.get_running_loop()
        shadow = _RecordingOrchestrator()
        events = []

        def worker():
            events.extend(bot.stream_agent_response(7, "hello", request_id="trusted-turn", shadow_loop=loop))

        with patch.object(bot, "get_semantic_shadow_orchestrator", return_value=shadow), \
             patch.object(bot, "direct_live_request", return_value="legacy reply"), \
             patch.object(bot, "record_user_request"), \
             patch.object(bot, "add_message", side_effect=[1, 2]):
            thread = threading.Thread(target=worker)
            thread.start()
            await asyncio.to_thread(thread.join)

        self.assertEqual("legacy reply", events[-1]["text"])
        self.assertEqual(1, len(shadow.calls))
        scheduled_loop, kwargs = shadow.calls[0]
        self.assertIs(loop, scheduled_loop)
        self.assertEqual("trusted-turn", kwargs["request_id"])
        self.assertTrue(kwargs["legacy_trace"].finalized)

    async def test_same_trusted_request_id_is_delivered_to_the_shadow_scheduler(self):
        loop = asyncio.get_running_loop()
        shadow = _RecordingOrchestrator()
        with patch.object(bot, "get_semantic_shadow_orchestrator", return_value=shadow):
            first = bot.schedule_semantic_shadow_turn(7, "one", request_id="same", loop=loop)
            second = bot.schedule_semantic_shadow_turn(7, "two", request_id="same", loop=loop)
            await asyncio.wrap_future(first)
            await asyncio.wrap_future(second)
        self.assertEqual(["same", "same"], [item[1]["request_id"] for item in shadow.calls])

    def test_execute_tool_records_names_and_outcomes_without_arguments_or_results(self):
        trace = LegacyExecutionTrace()
        bot._LEGACY_SHADOW_TRACE.trace = trace
        try:
            with patch.object(bot, "finance_summary", return_value={"ok": True, "amount": 42}), \
                 patch.object(bot, "save_reminder", side_effect=RuntimeError("private detail")):
                bot.execute_tool(1, "finance_summary", {"amount": 42})
                with self.assertRaises(RuntimeError):
                    bot.execute_tool(1, "set_reminder", {"note": "private detail"})
        finally:
            del bot._LEGACY_SHADOW_TRACE.trace
        trace.finalize()
        self.assertEqual(["finance_summary", "set_reminder"], trace.tool_names)
        self.assertEqual((1, 1), (trace.success_count, trace.failure_count))
        self.assertFalse(hasattr(trace, "args"))

    def test_flag_off_performs_no_shadow_factory_work(self):
        with patch.dict(os.environ, {"SEMANTIC_SHADOW_ENABLED": "0"}), \
             patch.object(bot, "_SEMANTIC_SHADOWS", {}), \
             patch.object(bot, "_SemanticRuntimeBackend", side_effect=AssertionError("model backend")):
            self.assertIsNone(bot.get_semantic_shadow_orchestrator(99))

    def test_semantic_backend_uses_the_existing_model_fallback_order(self):
        failed = SimpleNamespace(ok=False, status_code=400, close=Mock())
        succeeded = SimpleNamespace(
            ok=True, status_code=200, close=Mock(),
            json=lambda: {"choices": [{"message": {"content": "{}"}}]},
        )
        with patch.object(bot, "chat_model_candidates", return_value=["primary", "fallback"]), \
             patch.object(bot, "request_chat", side_effect=[failed, succeeded]) as request, \
             patch.object(bot, "record_usage"), \
             patch.object(bot, "recover_missing_managed_key", return_value=False):
            value = asyncio.run(bot._SemanticRuntimeBackend(8)._generate("system", {"x": 1}))
        self.assertEqual("{}", value)
        self.assertEqual(["primary", "fallback"], [call.args[1] for call in request.call_args_list])

    def test_validated_delete_shadow_reads_target_but_never_writes(self):
        class Planner:
            async def plan(self, *args, **kwargs):
                return SemanticPlan(
                    "meeting", "commit",
                    reads=[ReadRequest("event", "search", read_id="target", filters={"query": "Keep me"})],
                    actions=[ActionRequest("event", "delete", action_id="delete", depends_on=["target"])],
                )
        class Responder:
            async def respond(self, *args, **kwargs):
                return type("Response", (), {"status": "OK"})()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, patch.object(bot, "DB", Path(directory) / "shadow.db"):
            bot.init_db()
            owner = 714
            event = bot.event_create(owner, "Keep me", "2026-09-20T15:00:00+00:00")
            shadow = SemanticShadowOrchestrator(
                Planner(), PlanValidator(bot.person_entity_resolver()), BotDomainServices(bot),
                EvidenceAssembler(), Responder(), enabled=True,
            )
            result = asyncio.run(shadow.run(
                trusted_owner=owner, request_id="shadow-delete", utterance="delete it",
                now=datetime.now(), timezone="Europe/Moscow", conversation_context={},
            ))
            self.assertEqual(("VALIDATED_COMMIT", 1), (result.status, result.read_count))
            self.assertEqual(event["id"], bot.event_list(owner)["events"][0]["id"])
            with bot.conn() as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM semantic_executions").fetchone()[0])
