import asyncio
import json
import unittest
from datetime import datetime

from semantic_planner import PLANNER_PROMPT, SemanticPlanner, parse_semantic_plan


NOW = datetime.fromisoformat("2026-09-17T10:00:00+03:00")
ZONE = "Europe/Moscow"


def entity(entity_type, mention, **extra):
    return {"type": entity_type, "mention": mention, **extra}


def read(domain, operation, **extra):
    return {"domain": domain, "operation": operation, **extra}


def action(domain, operation, **extra):
    return {"domain": domain, "operation": operation, **extra}


def plan(intent, disposition, **extra):
    return {"intent": intent, "disposition": disposition, **extra}


class FakePlannerBackend:
    def __init__(self, responses=None, *, error=None, delay=0):
        self.responses = responses or {}
        self.error = error
        self.delay = delay
        self.calls = []

    async def generate_structured(self, *, system_prompt, input_payload, output_schema):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "input_payload": input_payload,
                "output_schema": output_schema,
            }
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.responses[input_payload["utterance"]]


MEETING_ACTIONS = [
    action(
        "person",
        "upsert",
        fields={"name": "Иван"},
        entity_refs=[entity("person", "Иван")],
        action_id="resolve_person",
    ),
    action(
        "event",
        "create",
        fields={"kind": "meeting", "title": "Встреча с Иваном", "local_datetime": "2026-09-18T15:00:00"},
        entity_refs=[entity("person", "Иван")],
        depends_on=["resolve_person"],
        action_id="create_event",
    ),
]


NATURAL_LANGUAGE_CASES = {
    "Завтра в 15 встреча с Иваном.": plan(
        "schedule_event", "commit", entities=[entity("person", "Иван")], actions=MEETING_ACTIONS
    ),
    "Завтра в три встречаюсь с Иваном.": plan(
        "schedule_event", "commit", entities=[entity("person", "Иван")], actions=MEETING_ACTIONS
    ),
    "С Иваном завтра пересечёмся в 15:00.": plan(
        "schedule_event", "commit", entities=[entity("person", "Иван")], actions=MEETING_ACTIONS
    ),
    "Познакомился с Артёмом, он дизайнер.": plan(
        "remember_person",
        "commit",
        entities=[entity("person", "Артём", attributes={"notes": "дизайнер"})],
        actions=[
            action(
                "person",
                "upsert",
                fields={"name": "Артём", "notes": "дизайнер"},
                entity_refs=[entity("person", "Артём")],
            )
        ],
    ),
    "Он завтра сможет в четыре, поставь встречу.": plan(
        "schedule_event",
        "commit",
        entities=[entity("person", "Он")],
        actions=[
            action(
                "event",
                "create",
                fields={"kind": "meeting", "local_datetime": "2026-09-18T16:00:00"},
                entity_refs=[entity("person", "Он")],
            )
        ],
    ),
    "Возможно завтра встречусь с Сергеем.": plan(
        "possible_event",
        "answer",
        entities=[entity("person", "Сергей"), entity("event", "возможно завтра встречусь")],
        confidence=0.55,
    ),
    "Когда я встречаюсь с Сергеем?": plan(
        "find_event",
        "read",
        entities=[entity("person", "Сергей")],
        reads=[
            read("person", "resolve", entity_refs=[entity("person", "Сергей")]),
            read("event", "search", read_id="events", filters={"query": "встреч"}, entity_refs=[entity("person", "Сергей")]),
        ],
    ),
    "Что мы с ним обсуждали?": plan(
        "interaction_history",
        "read",
        entities=[entity("person", "с ним")],
        reads=[
            read("person", "resolve", entity_refs=[entity("person", "с ним")]),
            read("person", "interactions_list", entity_refs=[entity("person", "с ним")]),
        ],
    ),
    "Потратил 850 рублей на такси.": plan(
        "record_expense",
        "commit",
        entities=[entity("transaction", "850 рублей на такси")],
        actions=[action("transaction", "create", fields={"amount": 850, "currency": "RUB", "category": "такси", "kind": "expense"})],
    ),
    "Сколько ушло на такси за неделю?": plan(
        "expense_summary",
        "read",
        reads=[read("finance", "summary", filters={"category": "такси", "period": "week"})],
    ),
    "Напомни позвонить маме завтра в 12.": plan(
        "create_reminder",
        "commit",
        entities=[entity("person", "маме")],
        actions=[
            action(
                "reminder",
                "create",
                fields={"title": "Позвонить маме", "local_datetime": "2026-09-18T12:00:00"},
                entity_refs=[entity("person", "маме")],
            )
        ],
    ),
    "Запиши: сервер переехал на новый IP.": plan(
        "save_note",
        "commit",
        actions=[action("note", "create", fields={"text": "Сервер переехал на новый IP"})],
    ),
    "Удали встречу с Иваном завтра.": plan(
        "delete_event",
        "commit",
        entities=[entity("person", "Иван")],
        reads=[
            read(
                "event",
                "search",
                read_id="target", filters={"query": "встреч", "date_from": "2026-09-18", "date_to": "2026-09-18"},
                entity_refs=[entity("person", "Иван")],
            )
        ],
        actions=[
            action(
                "event",
                "delete",
                fields={"semantic_target": "встреча с Иваном завтра"},
                entity_refs=[entity("person", "Иван")],
                action_id="delete_event", depends_on=["target"],
            )
        ],
    ),
    "Да": plan("acknowledge", "answer"),
    "В пятницу Иван.": plan(
        "ambiguous_person_date",
        "clarify",
        entities=[entity("person", "Иван")],
        clarification="Что запланировать с Иваном на пятницу?",
    ),
    "Кто такой Иван Грозный?": plan(
        "general_knowledge_question", "answer", entities=[entity("knowledge", "Иван Грозный")]
    ),
    "Создай человека Иван": plan(
        "create_person",
        "commit",
        entities=[entity("person", "Иван")],
        actions=[action("person", "upsert", fields={"name": "Иван"}, entity_refs=[entity("person", "Иван")])],
    ),
}


class SemanticPlannerNaturalLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_natural_language_regression_set(self):
        backend = FakePlannerBackend(NATURAL_LANGUAGE_CASES)
        planner = SemanticPlanner(backend)
        results = {}
        for utterance in NATURAL_LANGUAGE_CASES:
            results[utterance] = await planner.plan(
                utterance,
                now=NOW,
                timezone=ZONE,
                conversation_context={"recent_entities": [{"type": "person", "name": "Артём", "person_id": 17}]},
            )

        equivalent = [
            results["Завтра в 15 встреча с Иваном."],
            results["Завтра в три встречаюсь с Иваном."],
            results["С Иваном завтра пересечёмся в 15:00."],
        ]
        self.assertTrue(all(item.intent == "schedule_event" and item.disposition == "commit" for item in equivalent))
        self.assertTrue(all(item.entities[0].mention == "Иван" for item in equivalent))
        self.assertTrue(all([request.domain for request in item.actions] == ["person", "event"] for item in equivalent))

        contextual = results["Он завтра сможет в четыре, поставь встречу."]
        self.assertEqual(contextual.entities[0].mention, "Он")
        self.assertIsNone(contextual.entities[0].resolved_id)
        self.assertEqual(contextual.actions[0].domain, "event")
        self.assertEqual(contextual.actions[0].fields["local_datetime"], "2026-09-18T16:00:00")

        self.assertFalse(results["Возможно завтра встречусь с Сергеем."].actions)
        self.assertEqual(results["Когда я встречаюсь с Сергеем?"].disposition, "read")
        self.assertEqual([item.operation for item in results["Что мы с ним обсуждали?"].reads], ["resolve", "interactions_list"])
        self.assertEqual(results["Потратил 850 рублей на такси."].actions[0].domain, "transaction")
        self.assertEqual(results["Сколько ушло на такси за неделю?"].reads[0].domain, "finance")
        self.assertEqual(results["Напомни позвонить маме завтра в 12."].actions[0].domain, "reminder")
        self.assertEqual(results["Запиши: сервер переехал на новый IP."].actions[0].domain, "note")
        destructive = results["Удали встречу с Иваном завтра."]
        self.assertTrue(destructive.reads)
        self.assertTrue(destructive.actions[0].depends_on)
        self.assertEqual(results["Да"].actions, [])
        self.assertEqual(results["В пятницу Иван."].disposition, "clarify")
        self.assertEqual(results["Кто такой Иван Грозный?"].actions, [])
        self.assertEqual(results["Создай человека Иван"].actions[0].operation, "upsert")

    async def test_time_and_context_are_supplied_without_exact_ids(self):
        backend = FakePlannerBackend({"С ним завтра встреча.": plan("possible_event", "answer")})
        planner = SemanticPlanner(backend)
        await planner.plan(
            "С ним завтра встреча.",
            now=NOW,
            timezone=ZONE,
            conversation_context={
                "chat_id": 99,
                "recent_entities": [{"type": "person", "name": "Артём", "person_id": 17, "resolved_id": 17}],
            },
            memory_context={"owner_id": 99, "summary": "Рабочий контекст"},
        )
        payload = backend.calls[0]["input_payload"]
        self.assertEqual(payload["now"], NOW.isoformat())
        self.assertEqual(payload["timezone"], ZONE)
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("chat_id", "person_id", "resolved_id", "owner_id"):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("Артём", serialized)


class SemanticPlannerValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_fields_are_rejected_and_model_ids_are_discarded(self):
        faulty = plan(
            "create_person",
            "commit",
            actions=[action("person", "upsert", fields={"name": "Иван", "chat_id": 999})],
        )
        backend = FakePlannerBackend({"bad": faulty})
        events = []
        result = await SemanticPlanner(backend, observer=lambda event, **fields: events.append((event, fields))).plan(
            "bad", now=NOW, timezone=ZONE, conversation_context=[]
        )
        self.assertEqual(result.intent, "planner_failure")
        self.assertIn(("semantic_planner_invalid_output", {"failure_category": "owner_field"}), events)

        clean = parse_semantic_plan(
            plan(
                "find_person",
                "read",
                entities=[entity("person", "Иван", resolved_id=999)],
                reads=[read("person", "resolve", entity_refs=[entity("person", "Иван", resolved_id=999)])],
            )
        )
        self.assertIsNone(clean.entities[0].resolved_id)
        self.assertIsNone(clean.reads[0].entity_refs[0].resolved_id)

    async def test_malformed_empty_oversized_unknown_and_unsafe_outputs_fail_closed(self):
        responses = {
            "malformed": "{not json",
            "empty": "  ",
            "oversized": "x" * (64 * 1024 + 1),
            "unknown": plan("x", "read", reads=[read("event", "teleport")]),
            "unsafe": plan("x", "commit", actions=[action("event", "delete", fields={"target": "tomorrow"})]),
            "wrong disposition": plan("x", "dance"),
        }
        for utterance, response in responses.items():
            with self.subTest(utterance=utterance):
                events = []
                result = await SemanticPlanner(
                    FakePlannerBackend({utterance: response}),
                    observer=lambda event, **fields: events.append(event),
                ).plan(utterance, now=NOW, timezone=ZONE, conversation_context=[])
                self.assertEqual(result.disposition, "clarify")
                self.assertEqual(result.actions, [])
                self.assertIn("semantic_planner_invalid_output", events)

    async def test_timeout_and_provider_error_are_controlled(self):
        for backend, category in (
            (FakePlannerBackend(delay=0.05), "timeout"),
            (FakePlannerBackend(error=RuntimeError("private provider detail")), "provider_error"),
        ):
            with self.subTest(category=category):
                events = []
                result = await SemanticPlanner(
                    backend,
                    timeout_seconds=0.005,
                    observer=lambda event, **fields: events.append((event, fields)),
                ).plan("request", now=NOW, timezone=ZONE, conversation_context=[])
                self.assertEqual(result.intent, "planner_failure")
                self.assertIn(("semantic_planner_failed", {"failure_category": category}), events)

    async def test_observability_is_safe_and_latency_is_recorded(self):
        events = []
        metrics = []
        utterance = "Секретная частная фраза"
        result = await SemanticPlanner(
            FakePlannerBackend({utterance: plan("general_answer", "answer")}),
            observer=lambda event, **fields: events.append((event, fields)),
            metric_recorder=lambda name, value: metrics.append((name, value)),
        ).plan(utterance, now=NOW, timezone=ZONE, conversation_context=[])
        self.assertEqual(result.intent, "general_answer")
        self.assertEqual(events[0], ("semantic_planner_started", {}))
        self.assertEqual(events[-1][0], "semantic_planner_completed")
        self.assertEqual(metrics[0][0], "planner_latency_ms")
        self.assertGreaterEqual(metrics[0][1], 0)
        self.assertNotIn(utterance, repr(events) + repr(metrics))

    def test_prompt_is_compact_and_planning_only(self):
        self.assertLess(len(PLANNER_PROMPT), 2000)
        for instruction in ("do not answer", "never invent", "exact reads", "explicit committed", "JSON only"):
            self.assertIn(instruction.casefold(), PLANNER_PROMPT.casefold())

    def test_caps_and_nested_junk_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "entities_limit"):
            parse_semantic_plan(plan("x", "answer", entities=[entity("person", "x")] * 17))
        nested = {}
        cursor = nested
        for _ in range(7):
            cursor["next"] = {}
            cursor = cursor["next"]
        with self.assertRaisesRegex(ValueError, "nested_limit"):
            parse_semantic_plan(
                plan("x", "commit", actions=[action("note", "create", fields=nested)])
            )

    def test_overwrite_and_replace_are_not_canonical_operations(self):
        for operation in ("overwrite", "replace"):
            with self.subTest(operation=operation):
                unsafe = plan(
                    "replace_note",
                    "commit",
                    actions=[action("note", operation, fields={"text": "new value"})],
                )
                with self.assertRaisesRegex(ValueError, "unknown_operation"):
                    parse_semantic_plan(unsafe)

class SemanticPlannerTransportTests(unittest.TestCase):
    def test_missing_local_ids_are_normalized_and_fenced_json_is_accepted(self):
        parsed = parse_semantic_plan("```json\n{\"intent\":\"meeting\",\"disposition\":\"commit\",\"reads\":[{\"domain\":\"event\",\"operation\":\"search\"}],\"actions\":[{\"domain\":\"event\",\"operation\":\"create\",\"fields\":{\"title\":\"x\",\"local_datetime\":\"2026-09-20T15:00:00\"}}]}\n```")
        self.assertEqual(("r1", "a1"), (parsed.reads[0].read_id, parsed.actions[0].action_id))

    def test_duplicate_local_ids_and_prose_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate_local_id"):
            parse_semantic_plan({"intent": "x", "disposition": "read", "reads": [{"domain": "event", "operation": "search", "read_id": "r1"}, {"domain": "event", "operation": "list", "read_id": "r1"}]})
        with self.assertRaisesRegex(ValueError, "malformed_json"):
            parse_semantic_plan("Here is JSON: {\"intent\":\"x\"}")


if __name__ == "__main__":
    unittest.main()
