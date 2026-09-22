import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bot
from grounded_response import EvidenceAssembler
from knowledge_store import KnowledgeItem, KnowledgeStore
from plan_runtime import BotDomainServices, PlanExecutor, PlanValidator
from semantic_core import ActionRequest, EntityReference, ReadRequest, SemanticPlan
from semantic_runtime import (SemanticProductionRuntime, SemanticRuntimeResult,
                              canary_owners, runtime_mode, semantic_owner_allowed)


class Planner:
    def __init__(self, result): self.result = result; self.calls = 0
    async def plan(self, *args, **kwargs): self.calls += 1; return self.result


class Responder:
    async def respond(self, question, evidence):
        return type("Response", (), {"status": "OK", "render": lambda self: "Точный ответ."})()


class SemanticRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.temp.name) / "runtime.db"
        self.patch = patch.object(bot, "DB", self.db); self.patch.start(); bot.init_db()
        self.original_pipeline = bot._pipeline
        self.original_recent_entities = dict(bot._SEMANTIC_RECENT_ENTITIES)
        self.original_last_retrieval = dict(bot._LAST_RETRIEVAL)
        bot._pipeline = None
        bot._SEMANTIC_RECENT_ENTITIES.clear()
        bot._LAST_RETRIEVAL.clear()
        self.owner = 902
        self.mode = "safe_write"; self.allowed = frozenset({self.owner})
        self.services = BotDomainServices(bot)
        self.executor = PlanExecutor(self.services, bot.conn)

    def tearDown(self):
        bot._pipeline = self.original_pipeline
        bot._SEMANTIC_RECENT_ENTITIES.clear(); bot._SEMANTIC_RECENT_ENTITIES.update(self.original_recent_entities)
        bot._LAST_RETRIEVAL.clear(); bot._LAST_RETRIEVAL.update(self.original_last_retrieval)
        self.patch.stop(); self.temp.cleanup()

    def runtime(self, plan, *, executor=None, owner_allowed_getter=None):
        return SemanticProductionRuntime(
            Planner(plan), PlanValidator(bot.person_entity_resolver()), self.services, executor or self.executor,
            EvidenceAssembler(), Responder(), mode_getter=lambda: self.mode,
            owners_getter=lambda: self.allowed, owner_allowed_getter=owner_allowed_getter,
        )

    def turn(self, runtime, request="request-1"):
        return asyncio.run(runtime.handle_turn(
            trusted_owner=self.owner, request_id=request, utterance="private text", now=datetime.now(),
            timezone="Europe/Moscow", conversation_context={},
        ))

    def test_off_and_unlisted_do_not_call_planner(self):
        plan = SemanticPlan("finance", "read", reads=[ReadRequest("finance", "summary", read_id="f")])
        self.mode = "off"; runtime = self.runtime(plan)
        self.assertEqual("FALLBACK_TO_LEGACY", self.turn(runtime).status)
        self.assertEqual(0, runtime.planner.calls)
        self.mode = "read"; self.allowed = frozenset()
        self.assertEqual("FALLBACK_TO_LEGACY", self.turn(runtime).status)
        self.assertEqual(0, runtime.planner.calls)

    def test_mode_and_allowlist_defaults_fail_closed(self):
        self.assertEqual("off", runtime_mode({}))
        self.assertEqual(frozenset(), canary_owners({}))
        self.assertFalse(semantic_owner_allowed(1, {}))
        self.assertEqual("off", runtime_mode({"SEMANTIC_RUNTIME_MODE": "unexpected"}))
        self.assertEqual(frozenset({1, 2}), canary_owners({"SEMANTIC_CANARY_USER_IDS": "1, invalid, 2"}))

    def test_global_rollout_wildcard_is_exact_and_malformed_values_fail_closed(self):
        self.assertTrue(semantic_owner_allowed(1, {"SEMANTIC_CANARY_USER_IDS": "*"}))
        self.assertTrue(semantic_owner_allowed(999_999, {"SEMANTIC_CANARY_USER_IDS": " * "}))
        numeric = {"SEMANTIC_CANARY_USER_IDS": "1,2"}
        self.assertTrue(semantic_owner_allowed(1, numeric))
        self.assertTrue(semantic_owner_allowed(2, numeric))
        self.assertFalse(semantic_owner_allowed(3, numeric))
        for malformed in ("1,*", "*,2", "**", "all", "true"):
            with self.subTest(malformed=malformed):
                self.assertFalse(semantic_owner_allowed(999_999, {"SEMANTIC_CANARY_USER_IDS": malformed}))
        self.assertTrue(semantic_owner_allowed(1, {"SEMANTIC_CANARY_USER_IDS": "1,*"}))

    def test_off_kill_switch_overrides_global_rollout(self):
        plan = SemanticPlan("finance", "read", reads=[ReadRequest("finance", "summary", read_id="f")])
        self.mode = "off"
        runtime = self.runtime(plan, owner_allowed_getter=lambda owner: semantic_owner_allowed(owner, {"SEMANTIC_CANARY_USER_IDS": "*"}))
        self.assertEqual("FALLBACK_TO_LEGACY", self.turn(runtime).status)
        self.assertEqual(0, runtime.planner.calls)

    def test_full_global_rollout_allows_existing_safe_action(self):
        self.mode = "full"
        plan = SemanticPlan("write", "commit", actions=[ActionRequest("task", "create", fields={"text": "global rollout"}, action_id="a")])
        runtime = self.runtime(plan, owner_allowed_getter=lambda owner: semantic_owner_allowed(owner, {"SEMANTIC_CANARY_USER_IDS": "*"}))
        self.assertEqual("ACTION_RECEIPT", self.turn(runtime, "global-full").status)
        with bot.conn() as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM tasks WHERE chat_id=?", (self.owner,)).fetchone()[0])

    def test_allowlisted_read_is_exact_and_handled(self):
        self.mode = "read"
        plan = SemanticPlan("finance", "read", reads=[ReadRequest("finance", "summary", read_id="f")])
        result = self.turn(self.runtime(plan))
        self.assertEqual(("READ_ANSWER", True, "Точный ответ."), (result.status, result.handled, result.reply))

    def test_exact_empty_event_search_is_still_handled(self):
        self.mode = "read"
        plan = SemanticPlan("event", "read", reads=[ReadRequest("event", "search", read_id="e", filters={"query": "none"})])
        result = self.turn(self.runtime(plan))
        self.assertEqual("READ_ANSWER", result.status)
        self.assertEqual(1, len(result.execution.reads))

    def test_person_interactions_read(self):
        self.mode = "read"; person = bot.person_upsert(self.owner, "Иван")
        bot.person_interaction(self.owner, interaction="Обсуждали", person_id=person["id"])
        plan = SemanticPlan("person", "read", reads=[ReadRequest("person", "interactions_list", read_id="p", entity_refs=[EntityReference("person", "Иван")])])
        self.assertEqual("READ_ANSWER", self.turn(self.runtime(plan)).status)

    def test_people_aggregate_uses_owner_scoped_person_list_without_resolve(self):
        bot.person_upsert(self.owner, "Анна", relationship="друг")
        bot.person_upsert(self.owner, "Игорь", relationship="коллега")
        bot.person_upsert(self.owner + 1, "Чужая", relationship="друг")
        plan = SemanticPlan("people", "read", reads=[ReadRequest("person", "list", read_id="people", filters={"relationship": "friend"})])
        runtime = self.runtime(plan)
        self.assertEqual("READ_ANSWER", self.turn(runtime).status)
        validated = runtime.validator.validate(self.owner, plan, conversation_context={}, now=datetime.now(), timezone="Europe/Moscow", request_id="people-direct")
        item = runtime.services.read(self.owner, validated.reads[0])
        self.assertEqual(["Анна"], [row["name"] for row in item["items"]])
        self.assertNotIn("person_id", item["items"][0])

    def test_knowledge_search_is_owner_scoped_and_grounded(self):
        store = KnowledgeStore(bot.DB)
        store.insert_item(KnowledgeItem(chat_id=self.owner, title="Noema", summary="Сохранённый материал Noema", searchable_text="Noema semantic", content_hash="owner-noema"))
        store.insert_item(KnowledgeItem(chat_id=self.owner + 1, title="Чужой Noema", summary="private", searchable_text="Noema", content_hash="other-noema"))
        plan = SemanticPlan("knowledge", "read", reads=[ReadRequest("knowledge", "search", read_id="knowledge", filters={"query": "Noema"})])

        class CapturingResponder(Responder):
            async def respond(inner_self, question, evidence):
                inner_self.evidence = evidence
                return await super().respond(question, evidence)

        runtime = self.runtime(plan); runtime.responder = CapturingResponder()
        self.assertEqual("READ_ANSWER", self.turn(runtime).status)
        exact = [item for item in runtime.responder.evidence.items if item.source == "exact_current" and item.domain == "knowledge"]
        self.assertEqual(["Noema"], [item.fields["title"] for item in exact])
        self.assertEqual(["Noema"], [item["title"] for item in bot.knowledge_search_tool(self.owner, query="")["results"]])

    def test_memory_context_is_bounded_owner_scoped_and_has_no_database_ids(self):
        store = KnowledgeStore(bot.DB)
        for index in range(6):
            store.insert_item(KnowledgeItem(chat_id=self.owner, title=f"Noema {index}", summary="saved", searchable_text="Noema", content_hash=f"memory-{index}"))
        store.insert_item(KnowledgeItem(chat_id=self.owner + 1, title="Чужое", summary="private", searchable_text="Noema", content_hash="memory-other"))
        context = bot.semantic_memory_context(self.owner, "Что сохранено про Noema?", limit=10)
        self.assertEqual(4, len(context))
        self.assertTrue(all(item["domain"] == "knowledge" for item in context))
        self.assertNotIn("Чужое", str(context))
        self.assertNotIn("id", str(context).casefold())

    def test_empty_knowledge_search_remains_honest_exact_empty(self):
        self.mode = "read"
        plan = SemanticPlan("knowledge", "read", reads=[ReadRequest("knowledge", "search", read_id="knowledge", filters={"query": "nothing"})])

        class NoDataResponder:
            async def respond(self, question, evidence):
                self.evidence = evidence
                return type("Response", (), {"status": "OK", "render": lambda self: "В текущих данных ничего не найдено."})()

        runtime = self.runtime(plan); runtime.responder = NoDataResponder()
        result = self.turn(runtime)
        self.assertEqual("READ_ANSWER", result.status)
        self.assertIn("knowledge", runtime.responder.evidence.exact_empty_domains)

    def test_bridge_passes_bounded_memory_and_conversation_context(self):
        captured = {}
        class CaptureRuntime:
            async def handle_turn(self, **kwargs):
                captured.update(kwargs)
                return SemanticRuntimeResult("FALLBACK_TO_LEGACY", failure_category="test")
        with patch.dict(os.environ, {"SEMANTIC_RUNTIME_MODE": "read", "SEMANTIC_CANARY_USER_IDS": str(self.owner)}), \
             patch.object(bot, "get_semantic_production_runtime", return_value=CaptureRuntime()), \
             patch.object(bot, "conversation_context", return_value=[{"role": "user", "content": "old"}] * 12), \
             patch.object(bot, "semantic_memory_context", return_value=[{"domain": "knowledge", "summary": "saved"}]):
            bot.run_semantic_production_turn(self.owner, "Что сохранено?", request_id="bridge-context")
        self.assertEqual([{"domain": "knowledge", "summary": "saved"}], captured["memory_context"])
        self.assertEqual(9, len(captured["conversation_context"]["messages"]))
        self.assertEqual([], captured["conversation_context"]["recent_entities"])

    def test_pronoun_follow_up_resolves_only_trusted_recent_person(self):
        person = bot.person_upsert(self.owner, "Анна")
        bot.person_interaction(self.owner, interaction="Обсуждали Noema", person_id=person["id"])
        bot._SEMANTIC_RECENT_ENTITIES[self.owner] = [{"type": "person", "id": person["id"]}]
        plan = SemanticPlan("person", "read", reads=[ReadRequest(
            "person", "interactions_list", read_id="follow", entity_refs=[EntityReference("person", "с ней")]
        )])
        runtime = self.runtime(plan)
        result = asyncio.run(runtime.handle_turn(
            trusted_owner=self.owner, request_id="pronoun", utterance="А что ты про неё знаешь?",
            now=datetime.now(), timezone="Europe/Moscow", conversation_context=bot.semantic_conversation_context(self.owner),
            memory_context=[],
        ))
        self.assertEqual("READ_ANSWER", result.status)

    def test_safe_writes_and_replay_are_deterministic(self):
        cases = [
            ("reminder", "create", {"title": "Позвонить", "local_datetime": "2026-09-22T09:00:00"}, "Готово. Напоминание создано."),
            ("transaction", "create", {"amount": 10, "currency": "RUB"}, "Готово. Расход сохранён."),
            ("event", "create", {"title": "Встреча", "local_datetime": "2026-09-22T15:00:00"}, "Готово. Встреча сохранена."),
            ("person", "upsert", {}, "Готово. Информация сохранена."),
            ("note", "create", {"text": "Заметка"}, "Готово. Заметка сохранена."),
            ("task", "create", {"text": "Задача"}, "Готово. Задача создана."),
        ]
        for index, (domain, operation, fields, receipt) in enumerate(cases):
            with self.subTest(domain=domain):
                refs = [EntityReference("person", "Пётр")] if domain == "person" else []
                plan = SemanticPlan("write", "commit", actions=[ActionRequest(domain, operation, fields=fields, entity_refs=refs, action_id="a")])
                runtime = self.runtime(plan); request = f"write-{index}"
                first, replay = self.turn(runtime, request), self.turn(runtime, request)
                self.assertEqual(("ACTION_RECEIPT", receipt), (first.status, first.reply))
                self.assertEqual(("ACTION_RECEIPT", receipt, "REPLAYED"), (replay.status, replay.reply, replay.execution.status))

    def test_pre_execution_gates_fallback(self):
        self.mode = "read"
        commit = SemanticPlan("write", "commit", actions=[ActionRequest("note", "create", fields={"text": "x"}, action_id="a")])
        self.assertEqual("FALLBACK_TO_LEGACY", self.turn(self.runtime(commit)).status)
        self.mode = "safe_write"
        destructive = SemanticPlan("delete", "commit", actions=[ActionRequest("event", "delete", action_id="a", depends_on=["r"])], reads=[ReadRequest("event", "search", read_id="r")])
        self.assertEqual("FALLBACK_TO_LEGACY", self.turn(self.runtime(destructive)).status)
        multi = SemanticPlan("many", "commit", actions=[ActionRequest("note", "create", fields={"text": "x"}, action_id="a"), ActionRequest("task", "create", fields={"text": "x"}, action_id="b")])
        self.assertEqual("FALLBACK_TO_LEGACY", self.turn(self.runtime(multi)).status)
        self.assertEqual("FALLBACK_TO_LEGACY", self.turn(self.runtime(SemanticPlan("answer", "answer"))).status)

    def test_failure_after_executor_claim_never_falls_back(self):
        class FailingExecutor:
            def execute(self, plan, *, timezone_name):
                from plan_runtime import ExecutionResult
                return ExecutionResult("FAILED", plan.request_id, failure_category="domain_failure")
        plan = SemanticPlan("write", "commit", actions=[ActionRequest("note", "create", fields={"text": "x"}, action_id="a")])
        result = self.turn(self.runtime(plan, executor=FailingExecutor()))
        self.assertEqual(("FAILURE_AFTER_EXECUTION_STARTED", True, "domain_failure"), (result.status, result.execution_started, result.failure_category))

    def test_executor_exception_after_start_never_falls_back(self):
        class RaisingExecutor:
            def execute(self, plan, *, timezone_name):
                raise RuntimeError("private detail")
        plan = SemanticPlan("write", "commit", actions=[ActionRequest("note", "create", fields={"text": "x"}, action_id="a")])
        result = self.turn(self.runtime(plan, executor=RaisingExecutor()))
        self.assertEqual(("FAILURE_AFTER_EXECUTION_STARTED", True, "unexpected_execution_error"), (result.status, result.execution_started, result.failure_category))

    def test_bridge_fallback_stops_after_authoritative_execution_claim(self):
        request = "bridge-request"
        with patch.dict(os.environ, {"SEMANTIC_RUNTIME_MODE": "safe_write", "SEMANTIC_CANARY_USER_IDS": str(self.owner)}), \
             patch.object(bot, "get_semantic_production_runtime", side_effect=RuntimeError("private failure")):
            before = bot.run_semantic_production_turn(self.owner, "text", request_id=request)
            self.assertEqual(("FALLBACK_TO_LEGACY", False), (before.status, before.handled))
            with bot.conn() as connection:
                connection.execute("INSERT INTO semantic_executions(chat_id,request_id,plan_fingerprint,status,result_json,created_at) VALUES(?,?,?,?,?,?)", (self.owner, request, "x", "RUNNING", "", "now"))
            after = bot.run_semantic_production_turn(self.owner, "text", request_id=request)
        self.assertEqual(("FAILURE_AFTER_EXECUTION_STARTED", True, True), (after.status, after.handled, after.execution_started))

    def test_stable_telegram_transport_ids_ignore_text(self):
        first = SimpleNamespace(effective_chat=SimpleNamespace(id=self.owner), effective_message=SimpleNamespace(message_id=11), update_id=101)
        duplicate = SimpleNamespace(effective_chat=SimpleNamespace(id=self.owner), effective_message=SimpleNamespace(message_id=11), update_id=999)
        next_message = SimpleNamespace(effective_chat=SimpleNamespace(id=self.owner), effective_message=SimpleNamespace(message_id=12), update_id=102)
        self.assertEqual("tg:902:11", bot.telegram_semantic_request_id(first))
        self.assertEqual(bot.telegram_semantic_request_id(first), bot.telegram_semantic_request_id(duplicate))
        self.assertNotEqual(bot.telegram_semantic_request_id(first), bot.telegram_semantic_request_id(next_message))

    def test_same_transport_request_replays_but_same_text_new_turn_writes_again(self):
        fields = {"amount": 450, "currency": "RUB", "description": "coffee"}
        plan = SemanticPlan("expense", "commit", actions=[ActionRequest("transaction", "create", fields=fields, action_id="a")])
        runtime = self.runtime(plan)
        self.assertEqual("ACTION_RECEIPT", self.turn(runtime, "transport-x").status)
        self.assertEqual("REPLAYED", self.turn(runtime, "transport-x").execution.status)
        self.assertEqual("ACTION_RECEIPT", self.turn(runtime, "transport-y").status)
        with bot.conn() as connection:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM expenses WHERE chat_id=?", (self.owner,)).fetchone()[0])
        reminder = SemanticPlan("reminder", "commit", actions=[ActionRequest("reminder", "create", fields={"title": "call", "local_datetime": "2026-09-22T09:00:00"}, action_id="a")])
        reminder_runtime = self.runtime(reminder)
        self.turn(reminder_runtime, "reminder-x"); self.turn(reminder_runtime, "reminder-x")
        with bot.conn() as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM reminders WHERE chat_id=?", (self.owner,)).fetchone()[0])

    def test_completed_replay_skips_planner_even_if_new_plan_would_differ(self):
        plan = SemanticPlan("expense", "commit", actions=[ActionRequest("transaction", "create", fields={"amount": 1, "currency": "RUB"}, action_id="a")])
        runtime = self.runtime(plan)
        self.turn(runtime, "replay-key")
        class NeverPlanner:
            async def plan(self, *args, **kwargs): raise AssertionError("planner must not run on replay")
        runtime.planner = NeverPlanner()
        replay = self.turn(runtime, "replay-key")
        self.assertEqual(("ACTION_RECEIPT", "REPLAYED"), (replay.status, replay.execution.status))

    def test_channel_neutral_stream_stops_legacy_when_semantic_handles(self):
        handled = SemanticRuntimeResult("READ_ANSWER", handled=True, reply="Точный ответ.")
        with patch.object(bot, "run_semantic_production_turn", return_value=handled), \
             patch.object(bot, "_stream_agent_response_legacy", side_effect=AssertionError("legacy called")), \
             patch.object(bot, "record_user_request"), patch.object(bot, "add_message", side_effect=[1, 2]):
            events = list(bot.stream_agent_response(self.owner, "text", request_id="channel"))
        self.assertEqual("Точный ответ.", events[-1]["text"])

    def test_rendering_exception_after_handled_execution_never_enters_legacy(self):
        handled = SemanticRuntimeResult("ACTION_RECEIPT", handled=True, reply="Готово.", execution_started=True)
        with patch.object(bot, "run_semantic_production_turn", return_value=handled), \
             patch.object(bot, "_stream_agent_response_legacy", side_effect=AssertionError("legacy called")), \
             patch.object(bot, "record_user_request"), patch.object(bot, "add_message", side_effect=RuntimeError("render failure")):
            with self.assertRaisesRegex(RuntimeError, "render failure"):
                list(bot.stream_agent_response(self.owner, "text", request_id="claimed"))

    def test_channel_neutral_stream_uses_legacy_once_on_fallback(self):
        fallback = SemanticRuntimeResult("FALLBACK_TO_LEGACY", failure_category="disabled")
        legacy = [{"type": "done", "text": "legacy"}]
        with patch.object(bot, "run_semantic_production_turn", return_value=fallback), \
             patch.object(bot, "schedule_semantic_shadow_turn"), \
             patch.object(bot, "_stream_agent_response_legacy", return_value=iter(legacy)) as legacy_call:
            events = list(bot.stream_agent_response(self.owner, "text", request_id="channel"))
        self.assertEqual("legacy", events[-1]["text"])
        legacy_call.assert_called_once()

    def test_full_delete_uses_exact_target_and_zero_or_ambiguous_is_safe(self):
        self.mode = "full"
        event = bot.event_create(self.owner, "Удалить", "2026-09-22T15:00:00+00:00")
        plan = SemanticPlan("delete", "commit", reads=[ReadRequest("event", "search", read_id="r", filters={"query": "Удалить"})], actions=[ActionRequest("event", "delete", action_id="a", depends_on=["r"])])
        result = self.turn(self.runtime(plan), "delete-one")
        self.assertEqual("ACTION_RECEIPT", result.status)
        self.assertFalse(any(item["id"] == event["id"] for item in bot.event_list(self.owner)["events"]))
        no_target = self.turn(self.runtime(plan), "delete-zero")
        self.assertEqual(("FAILURE_AFTER_EXECUTION_STARTED", "target_not_found"), (no_target.status, no_target.failure_category))
        bot.event_create(self.owner, "Дубликат", "2026-09-22T15:00:00+00:00"); bot.event_create(self.owner, "Дубликат", "2026-09-23T15:00:00+00:00")
        ambiguous_plan = SemanticPlan("delete", "commit", reads=[ReadRequest("event", "search", read_id="r", filters={"query": "Дубликат"})], actions=[ActionRequest("event", "delete", action_id="a", depends_on=["r"])])
        ambiguous = self.turn(self.runtime(ambiguous_plan), "delete-many")
        self.assertEqual(("FAILURE_AFTER_EXECUTION_STARTED", "ambiguous_target"), (ambiguous.status, ambiguous.failure_category))
