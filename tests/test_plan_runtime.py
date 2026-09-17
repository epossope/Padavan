import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import bot
from plan_runtime import BotDomainServices, PlanExecutor, PlanValidationError, PlanValidator
from semantic_core import ActionRequest, EntityReference, ReadRequest, SemanticPlan


class PlanRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patch = patch.object(bot, "DB", Path(self.temp.name) / "semantic.db")
        self.patch.start(); bot.init_db(); self.owner, self.other = 801, 802
        self.validator = PlanValidator(bot.person_entity_resolver())
        self.executor = PlanExecutor(BotDomainServices(bot), bot.conn)

    def tearDown(self): self.patch.stop(); self.temp.cleanup()
    def validate(self, plan, request="request-1", context=None):
        return self.validator.validate(self.owner, plan, conversation_context=context or {}, now=datetime.now(), timezone="Europe/Moscow", request_id=request)

    def test_meeting_alias_context_date_only_and_replay(self):
        person = bot.person_upsert(self.owner, "Иван Петров", aliases=["Ваня"])
        plan = SemanticPlan("meeting", "commit", actions=[ActionRequest("event", "create", fields={"title": "Встреча", "local_date": "2026-09-20"}, entity_refs=[EntityReference("person", "Ваня")], action_id="event")])
        validated = self.validate(plan)
        self.assertEqual(person["id"], validated.actions[0].entity_refs[0].resolved_id)
        first = self.executor.execute(validated, timezone_name="Europe/Moscow")
        again = self.executor.execute(validated, timezone_name="Europe/Moscow")
        self.assertEqual("EXECUTED", first.status); self.assertEqual("REPLAYED", again.status)
        event = bot.event_list(self.owner)["events"][0]
        self.assertTrue(event["all_day"]); self.assertEqual(person["id"], event["participants"][0]["person_id"])

    def test_event_reminder_link_and_person_context(self):
        person = bot.person_upsert(self.owner, "Артём")
        event = ActionRequest("event", "create", fields={"title": "Созвон", "local_datetime": "2026-09-20T15:00:00"}, entity_refs=[EntityReference("person", "с ним")], action_id="event")
        reminder = ActionRequest("reminder", "create", fields={"title": "Напомнить", "local_datetime": "2026-09-20T14:00:00"}, depends_on=["event"], action_id="reminder")
        result = self.executor.execute(self.validate(SemanticPlan("meeting", "commit", actions=[event, reminder]), context={"recent_entities": [{"type": "person", "id": person["id"]}]}), timezone_name="Europe/Moscow")
        self.assertEqual("EXECUTED", result.status)
        with bot.conn() as c: self.assertEqual(result.actions[0].result["id"], c.execute("SELECT event_id FROM reminders WHERE id=?", (result.actions[1].result["id"],)).fetchone()["event_id"])

    def test_rejects_model_ids_dependencies_ambiguity_and_preflight(self):
        foreign = bot.person_upsert(self.other, "Иван")
        bad = SemanticPlan("bad", "commit", actions=[ActionRequest("event", "create", fields={"title": "x", "event_id": foreign["id"], "local_datetime": "2026-09-20T15:00:00"}, action_id="event")])
        with self.assertRaisesRegex(PlanValidationError, "model_exact_id"): self.validate(bad)
        future = SemanticPlan("bad", "commit", actions=[ActionRequest("note", "create", fields={"text": "x"}, depends_on=["later"], action_id="first"), ActionRequest("note", "create", fields={"text": "y"}, action_id="later")])
        with self.assertRaisesRegex(PlanValidationError, "invalid_dependency"): self.validate(future)
        bot.person_upsert(self.owner, "Александр Иванов", aliases=["Саша"]); bot.person_upsert(self.owner, "Александр Петров", aliases=["Саша"])
        ambiguous = SemanticPlan("meeting", "commit", actions=[ActionRequest("event", "create", fields={"title": "x", "local_datetime": "2026-09-20T15:00:00"}, entity_refs=[EntityReference("person", "Саша")], action_id="event")])
        with self.assertRaisesRegex(PlanValidationError, "ambiguous_person"): self.validate(ambiguous)
        self.assertEqual(0, bot.event_list(self.owner)["count"])

    def test_read_only_finance_has_no_write(self):
        plan = SemanticPlan("finance", "read", reads=[ReadRequest("finance", "summary", read_id="finance")])
        result = self.executor.execute(self.validate(plan), timezone_name="Europe/Moscow")
        self.assertEqual("EXECUTED", result.status); self.assertEqual(1, len(result.reads)); self.assertFalse(result.actions)

    def test_new_person_validation_is_pure_and_fields_persist_on_execution(self):
        plan = SemanticPlan("person", "commit", actions=[ActionRequest(
            "person", "upsert", fields={"name": "Артём", "relationship": "дизайнер", "home_city": "Казань"},
            entity_refs=[EntityReference("person", "Артём")], action_id="person")])
        validated = self.validate(plan)
        self.assertEqual([], bot.get_people(self.owner)["people"])
        result = self.executor.execute(validated, timezone_name="Europe/Moscow")
        person = bot.get_people(self.owner)["people"][0]
        self.assertEqual("EXECUTED", result.status)
        self.assertEqual(("дизайнер", "Казань"), (person["relationship"], person["home_city"]))

    def test_invalid_second_action_is_rejected_before_any_write(self):
        plan = SemanticPlan("bad", "commit", actions=[ActionRequest("transaction", "create", fields={}, action_id="bad")])
        with self.assertRaisesRegex(PlanValidationError, "invalid_transaction_amount"):
            self.validate(plan, request="failed-1")
        self.assertEqual([], bot.get_people(self.owner)["people"])

    def test_reminder_without_time_and_top_level_model_id_fail_preflight(self):
        plan = SemanticPlan("bad", "commit", entities=[EntityReference("person", "Иван", attributes={"person_id": 99})], actions=[ActionRequest("reminder", "create", fields={"title": "x"}, action_id="reminder")])
        with self.assertRaisesRegex(PlanValidationError, "model_exact_id"):
            self.validate(plan)
        plan.entities = []
        with self.assertRaisesRegex(PlanValidationError, "missing_time"):
            self.validate(plan)
        self.assertEqual([], bot.get_people(self.owner)["people"])

    def test_execution_metric_is_safe(self):
        metrics = []
        executor = PlanExecutor(BotDomainServices(bot), bot.conn, metric_recorder=lambda name, value: metrics.append((name, value)))
        plan = SemanticPlan("finance", "read", reads=[ReadRequest("finance", "summary", read_id="finance")])
        executor.execute(self.validate(plan, request="metric"), timezone_name="Europe/Moscow")
        self.assertEqual("plan_execution_ms", metrics[0][0])
