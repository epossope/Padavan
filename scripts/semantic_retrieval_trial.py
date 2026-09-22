"""Offline production-runtime retrieval smoke trial.

Uses a disposable SQLite database and deterministic planner/responder doubles.
It deliberately never contacts a provider or the production database.
"""
from __future__ import annotations

import asyncio
import gc
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot
from grounded_response import EvidenceAssembler, GroundedClaim, GroundedResponse
from knowledge_store import KnowledgeItem, KnowledgeStore
from plan_runtime import BotDomainServices, PlanExecutor, PlanValidator
from semantic_core import EntityReference, ReadRequest, SemanticPlan
from semantic_runtime import SemanticProductionRuntime


OWNER, OTHER = 71_001, 71_002


class TrialPlanner:
    def __init__(self, plans):
        self.plans = iter(plans)

    async def plan(self, *args, **kwargs):
        return next(self.plans)


class TrialResponder:
    async def respond(self, question, packet):
        visible = [item for item in packet.items if item.source in {"exact_current", "exact_historical"}]
        if not visible:
            return GroundedResponse([], 0, "В текущих данных ничего не найдено.", "NO_DATA")
        return GroundedResponse([GroundedClaim("Подтверждено сохранёнными данными.", "personal_fact", [visible[0].evidence_id])], 1)


def _turn(runtime, utterance, request_id, context):
    return asyncio.run(runtime.handle_turn(
        trusted_owner=OWNER, request_id=request_id, utterance=utterance,
        now=datetime.now(), timezone="Europe/Moscow", conversation_context=context,
        memory_context=bot.semantic_memory_context(OWNER, utterance),
    ))


def run_trial() -> dict[str, bool]:
    old_db, old_pipeline = bot.DB, bot._pipeline
    try:
        with tempfile.TemporaryDirectory(prefix="noema-semantic-retrieval-", ignore_cleanup_errors=True) as directory:
            bot.DB = Path(directory) / "trial.sqlite3"
            bot._pipeline = None
            bot.init_db()
            anna = bot.person_upsert(OWNER, "Анна", relationship="друг")
            bot.person_upsert(OWNER, "Игорь", relationship="коллега")
            bot.person_upsert(OTHER, "Чужая", relationship="друг")
            bot.person_interaction(OWNER, interaction="Обсуждали Noema", person_id=anna["id"])
            store = KnowledgeStore(bot.DB)
            store.insert_item(KnowledgeItem(chat_id=OWNER, title="Noema", summary="Материал о Noema", searchable_text="Noema semantic", content_hash="trial-owner"))
            store.insert_item(KnowledgeItem(chat_id=OTHER, title="Чужой материал", summary="private", searchable_text="Noema", content_hash="trial-other"))
            bot.add_message(OWNER, "user", "Расскажи про Анну")
            plans = [
                SemanticPlan("people", "read", reads=[ReadRequest("person", "list", read_id="friends", filters={"relationship": "friend"})]),
                SemanticPlan("people", "read", reads=[ReadRequest("person", "list", read_id="all")]),
                SemanticPlan("knowledge", "read", reads=[ReadRequest("knowledge", "search", read_id="noema", filters={"query": "Noema"})]),
                SemanticPlan("knowledge", "read", reads=[ReadRequest("knowledge", "search", read_id="broad", filters={"query": ""})]),
                SemanticPlan("person", "read", reads=[
                    ReadRequest("person", "resolve", read_id="anna", entity_refs=[EntityReference("person", "Анна")]),
                    ReadRequest("person", "interactions_list", read_id="history", entity_refs=[EntityReference("person", "Анна")]),
                ]),
                SemanticPlan("person", "read", reads=[ReadRequest("person", "interactions_list", read_id="follow", entity_refs=[EntityReference("person", "с ней")])]),
            ]
            services = BotDomainServices(bot)
            runtime = SemanticProductionRuntime(
                TrialPlanner(plans), PlanValidator(bot.person_entity_resolver()), services,
                PlanExecutor(services, bot.conn), EvidenceAssembler(), TrialResponder(),
                mode_getter=lambda: "read", owners_getter=lambda: frozenset({OWNER}),
            )
            context = bot.semantic_conversation_context(OWNER)
            results = [
                _turn(runtime, "Какие у меня есть друзья?", "trial-1", context),
                _turn(runtime, "Какие люди у меня сохранены?", "trial-2", context),
                _turn(runtime, "Что я сохранял про Noema?", "trial-3", context),
                _turn(runtime, "Какие материалы у меня сохранены?", "trial-4", context),
                _turn(runtime, "Что ты знаешь про Анну из моих данных?", "trial-5", context),
            ]
            context["recent_entities"] = [{"type": "person", "id": anna["id"]}]
            results.append(_turn(runtime, "А что ты про неё знаешь?", "trial-6", context))
            other_people = bot.person_list(OTHER, relationship="friend")
            other_knowledge = bot.knowledge_search_tool(OTHER, query="Noema")
            report = {
                "friends": [row["name"] for row in results[0].execution.reads[0].result["items"]] == ["Анна"],
                "all_people": {row["name"] for row in results[1].execution.reads[0].result["items"]} == {"Анна", "Игорь"},
                "saved_noema": [row["title"] for row in results[2].execution.reads[0].result["results"]] == ["Noema"],
                "broad_materials": bool(results[3].execution.reads[0].result["results"]),
                "named_person": results[4].status == "READ_ANSWER",
                "pronoun_follow_up": results[5].status == "READ_ANSWER",
                "isolation": [row["name"] for row in other_people["items"]] == ["Чужая"] and [row["title"] for row in other_knowledge["results"]] == ["Чужой материал"],
            }
            bot._pipeline = None
            gc.collect()
            return report
    finally:
        bot.DB, bot._pipeline = old_db, old_pipeline


if __name__ == "__main__":
    report = run_trial()
    for name, passed in report.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    raise SystemExit(0 if all(report.values()) else 1)
