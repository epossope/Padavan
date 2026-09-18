"""Local, opt-in live comparison of legacy and semantic shadow behavior.

This script deliberately never starts an application, polling, web server, or
background worker.  It uses a synthetic owner and fresh temporary SQLite files
for every invocation.  Running it may call the configured LLM provider.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import gc
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot
from grounded_response import EvidenceAssembler, GroundedResponder
from plan_runtime import BotDomainServices, PlanValidator
from semantic_orchestrator import LegacyExecutionTrace, SemanticShadowOrchestrator, compare_shadow
from semantic_planner import SemanticPlanner

SYNTHETIC_OWNER = 970_001
LOGGER = logging.getLogger(__name__)
MUTABLE_TABLES = (
    "people", "interactions", "events", "event_participants", "tasks", "reminders",
    "expenses", "notes", "semantic_executions", "usage_events", "messages", "settings",
    "managed_api_keys", "managed_key_events", "user_api_keys",
)


@dataclass(frozen=True)
class TrialCase:
    number: int
    phrase: str
    expected: str
    expected_dispositions: tuple[str, ...]
    required_operation: str = ""
    exact_evidence: bool = False
    destructive: bool = False
    general_knowledge: bool = False
    require_clarification: bool = False
    uses_referent: bool = False


CASES = (
    TrialCase(1, "Завтра в 15 встреча с Иваном", "commit meeting proposal; no shadow write", ("commit",), "event.create"),
    TrialCase(2, "Когда я встречаюсь с Иваном?", "exact event read and grounded answer", ("read",), "event.search", True),
    TrialCase(3, "Что мы с ним обсуждали?", "resolve Иван and read interaction history", ("read",), "person.interactions_list", True, uses_referent=True),
    TrialCase(4, "Потратил 450 рублей на кофе", "commit transaction proposal; no shadow write", ("commit",), "transaction.create"),
    TrialCase(5, "Сколько я потратил сегодня?", "exact finance summary and grounded answer", ("read",), "finance.summary", True),
    TrialCase(6, "Удали встречу с Иваном завтра", "event target read plus delete proposal; no shadow deletion", ("commit",), "event.delete", destructive=True),
    TrialCase(7, "Иван дизайнер из Казани", "person exact-state proposal; no shadow write", ("commit",), "person.upsert"),
    TrialCase(8, "Напомни завтра в 9 позвонить", "reminder proposal; no shadow write", ("commit",), "reminder.create"),
    TrialCase(9, "Кто такой Иван Грозный?", "general knowledge answer with no personal state", ("answer",), general_knowledge=True),
    TrialCase(10, "В пятницу встреча с Иваном", "clarify missing meeting time", ("clarify",), require_clarification=True),
)


def _now() -> datetime:
    return datetime.now(bot.timezone_for(SYNTHETIC_OWNER))


def _table_snapshot(path: Path) -> dict[str, dict[str, Any]]:
    """Fingerprint all relevant mutable state without serializing its contents."""
    result: dict[str, dict[str, Any]] = {}
    with contextlib.closing(sqlite3.connect(path)) as connection:
        for table in MUTABLE_TABLES:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                result[table] = {"count": 0, "fingerprint": ""}
                continue
            columns = [item[1] for item in connection.execute(f"PRAGMA table_info({table})")]
            order = ", ".join(columns) if columns else "rowid"
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
            payload = json.dumps(rows, ensure_ascii=False, default=str, separators=(",", ":"))
            result[table] = {
                "count": len(rows),
                "fingerprint": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            }
    return result


def semantic_zero_write(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return before == after


def _with_database(path: Path):
    """Temporarily point existing exact-state helpers at an isolated database."""
    return patch.object(bot, "DB", path)


def seed_fixture(path: Path) -> None:
    """Create only unmistakably synthetic state; this function never uses production DB."""
    with _with_database(path):
        bot.init_db()
        owner = SYNTHETIC_OWNER
        person = bot.person_upsert(owner, "Иван", relationship="синтетический коллега", home_city="Казань")
        bot.person_interaction(owner, interaction="Обсуждали Noema и прототип интерфейса", person_id=person["id"])
        now = _now()
        tomorrow = now + timedelta(days=1)
        bot.event_create(owner, "Синтетическая встреча с Иваном", tomorrow.replace(hour=15, minute=0, second=0, microsecond=0).isoformat(), participant_ids=[person["id"]])
        bot.add_expense(owner, 1250, "синтетические расходы", "RUB", "тест")
        bot.add_task(owner, "Синтетическая задача", tomorrow.date().isoformat())
        bot.save_reminder(owner, "Синтетическое напоминание", tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).isoformat())


def ensure_live_model_is_configured() -> None:
    """Fail before a trial rather than leaking a missing-key provider error."""
    shared_key = str(getattr(bot, "OR_KEY", "")).strip()
    if not shared_key or "PASTE_" in shared_key:
        raise RuntimeError("LIVE_MODEL_NOT_CONFIGURED")


def prepare_fixture(base_db: Path) -> None:
    seed_fixture(base_db)


@contextlib.contextmanager
def trial_credential_context():
    """Force the existing request path to use only the configured shared key.

    This is intentionally scoped to the trial.  Production
    ``api_key_for_chat`` and managed-key recovery behavior are unchanged.
    """
    with patch.object(bot, "provision_managed_api_key", return_value=None), \
         patch.object(bot, "recover_missing_managed_key", return_value=False):
        yield


def copy_case_databases(base_db: Path, directory: Path, number: int) -> tuple[Path, Path]:
    semantic_db = directory / f"semantic_case_{number}.db"
    legacy_db = directory / f"legacy_case_{number}.db"
    shutil.copy2(base_db, semantic_db)
    shutil.copy2(base_db, legacy_db)
    return semantic_db, legacy_db


@contextlib.contextmanager
def trial_temp_directory():
    """Best-effort cleanup for Windows SQLite locks in this harness only."""
    directory = Path(tempfile.mkdtemp(prefix="noema-semantic-shadow-"))
    try:
        yield directory
    finally:
        gc.collect()
        for attempt in range(4):
            try:
                shutil.rmtree(directory)
                break
            except PermissionError:
                if attempt < 3:
                    time.sleep(0.1 * (attempt + 1))
            except OSError:
                break
        if directory.exists():
            # Never include a path, DB contents, credentials, or owner data.
            LOGGER.warning("TEMP_CLEANUP_DEFERRED")


def trusted_context(case: TrialCase, person_id: int | None = None) -> dict[str, Any]:
    # The model receives no ID: SemanticPlanner strips it.  Validator gets the
    # same owner-scoped trusted referent only after planning.
    if not case.uses_referent:
        return {"recent_entities": []}
    entry: dict[str, Any] = {"type": "person", "mention": "Иван"}
    if person_id is not None:
        entry["id"] = person_id
    return {"recent_entities": [entry]}


def _person_id(path: Path) -> int | None:
    with contextlib.closing(sqlite3.connect(path)) as connection:
        row = connection.execute("SELECT id FROM people WHERE chat_id=? AND name='Иван'", (SYNTHETIC_OWNER,)).fetchone()
    return int(row[0]) if row else None


def plan_summary(plan: Any) -> dict[str, Any]:
    if plan is None:
        return {}
    return {
        "intent": str(plan.intent), "disposition": str(plan.disposition),
        "reads": [f"{item.domain}.{item.operation}" for item in plan.reads],
        "actions": [f"{item.domain}.{item.operation}" for item in plan.actions],
        "clarification": str(plan.clarification or "")[:300],
    }


def run_semantic_case(case: TrialCase, database: Path, *, backend_factory=None) -> dict[str, Any]:
    """Run the real planner/backend/orchestrator while asserting no DB mutation."""
    with _with_database(database), trial_credential_context():
        before = _table_snapshot(database)
        backend = (backend_factory or bot._SemanticRuntimeBackend)(SYNTHETIC_OWNER)
        planner = SemanticPlanner(backend)
        orchestrator = SemanticShadowOrchestrator(
            planner, PlanValidator(bot.person_entity_resolver()), BotDomainServices(bot),
            EvidenceAssembler(), GroundedResponder(backend), enabled=True,
        )
        started = time.perf_counter()
        # Provider usage is a production telemetry write, not semantic state;
        # suppress it here so the local zero-write gate covers every table.
        with patch.object(bot, "record_usage", lambda *args, **kwargs: None):
            result = asyncio.run(orchestrator.run(
                trusted_owner=SYNTHETIC_OWNER, request_id=f"trial-{case.number}", utterance=case.phrase,
                now=_now(), timezone=bot.timezone_name_for(SYNTHETIC_OWNER),
                conversation_context=trusted_context(case, _person_id(database)),
            ))
        after = _table_snapshot(database)
    evidence_items = list(getattr(getattr(result, "evidence", None), "items", []) or [])
    return {
        "status": result.status, "disposition": result.disposition, "intent": getattr(result.plan, "intent", ""),
        "read_count": result.read_count, "action_count": result.action_count,
        "operations": list(result.proposed_operations), "grounding_status": result.grounding_status,
        "failure_category": result.failure_category, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "plan": plan_summary(result.plan), "exact_evidence": any(item.source.startswith("exact_") for item in evidence_items),
        "zero_write": semantic_zero_write(before, after),
    }


def run_legacy_case(case: TrialCase, database: Path) -> dict[str, Any]:
    """Run the unchanged legacy stream in its own copy; only compact trace is retained."""
    trace = LegacyExecutionTrace()
    with _with_database(database), trial_credential_context(), patch.dict(os.environ, {"SEMANTIC_SHADOW_ENABLED": "0"}):
        bot._SEMANTIC_SHADOWS.clear()
        original = bot.execute_tool
        def traced(owner, name, args):
            try:
                value = original(owner, name, args)
                trace.record(name, bool(isinstance(value, dict) and value.get("ok")))
                return value
            except Exception:
                trace.record(name, False)
                raise
        started = time.perf_counter()
        with patch.object(bot, "execute_tool", traced):
            events = list(bot.stream_agent_response(SYNTHETIC_OWNER, case.phrase, request_id=f"trial-{case.number}"))
    trace.finalize()
    final = next((item.get("text", "") for item in reversed(events) if item.get("type") == "done"), "")
    return {
        "tool_names": list(trace.tool_names), "success_count": trace.success_count, "failure_count": trace.failure_count,
        "final_answer": str(final)[:1200], "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def comparison_for(semantic: dict[str, Any], legacy: dict[str, Any]) -> str:
    if semantic["status"] == "CLARIFICATION":
        return "SEMANTIC_CLARIFIED"
    if semantic["status"] in {"TIMEOUT", "PLANNER_FAILED", "VALIDATION_FAILED", "READ_FAILED", "GROUNDING_FAILED"}:
        return "SEMANTIC_FAILED"
    return compare_shadow(semantic["operations"], LegacyExecutionTrace(tool_names=list(legacy["tool_names"])))


def verdict_for(case: TrialCase, semantic: dict[str, Any], comparison: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not semantic["zero_write"]:
        return "FAIL", ["semantic DB mutation detected"]
    if comparison == "SEMANTIC_FAILED":
        return "FAIL", ["semantic planner/validator/runtime failed"]
    if semantic["disposition"] not in case.expected_dispositions:
        return "FAIL", ["unexpected semantic disposition"]
    if case.required_operation and case.required_operation not in semantic["operations"]:
        return "FAIL", ["required structural operation absent"]
    if case.exact_evidence and not semantic["exact_evidence"]:
        return "FAIL", ["required exact evidence absent"]
    if case.destructive and not (semantic["read_count"] >= 1 and "event.delete" in semantic["operations"]):
        return "FAIL", ["destructive proposal lacks a read prerequisite"]
    if case.general_knowledge and (semantic["read_count"] or semantic["action_count"]):
        return "FAIL", ["general knowledge planned personal state access"]
    if case.require_clarification and semantic["disposition"] != "clarify":
        return "FAIL", ["missing time was not clarified"]
    if comparison in {"DIFFERENT_OPERATIONS", "PARTIAL_MATCH", "NOT_COMPARABLE"}:
        notes.append("valid structural divergence from legacy")
        return "WARN", notes
    return "PASS", notes


def acceptance(report: dict[str, Any]) -> dict[str, Any]:
    cases = report["cases"]
    counts = {name: sum(item["verdict"] == name for item in cases) for name in ("PASS", "WARN", "FAIL")}
    matches = {name: sum(item["comparison"] == name for item in cases) for name in ("MATCH", "PARTIAL_MATCH", "DIFFERENT_OPERATIONS")}
    zero_write = sum(bool(item["semantic"]["zero_write"]) for item in cases)
    semantic_failures = sum(item["comparison"] == "SEMANTIC_FAILED" for item in cases)
    return {
        "TOTAL": len(cases), **counts, "ZERO_WRITE_PASS": f"{zero_write}/{len(cases)}",
        "SEMANTIC_FAILURES": semantic_failures, **matches,
        "AVERAGE_SEMANTIC_LATENCY_MS": round(sum(item["semantic"]["latency_ms"] for item in cases) / max(1, len(cases)), 1),
        "AVERAGE_LEGACY_LATENCY_MS": round(sum(item["legacy"]["latency_ms"] for item in cases) / max(1, len(cases)), 1),
        "CANARY_READY": "YES" if counts["FAIL"] == 0 and zero_write == len(cases) and semantic_failures == 0 else "NO",
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = ["# Semantic shadow local trial", "", "| # | Phrase | Legacy tools | Semantic status | Semantic operations | Match | Zero-write | Verdict | Latency |", "|---:|---|---|---|---|---|---|---|---|"]
    for item in report["cases"]:
        semantic, legacy = item["semantic"], item["legacy"]
        lines.append(f"| {item['number']} | {item['phrase']} | {', '.join(legacy['tool_names']) or '—'} | {semantic['status']} | {', '.join(semantic['operations']) or '—'} | {item['comparison']} | {'PASS' if semantic['zero_write'] else 'FAIL'} | {item['verdict']} | {semantic['latency_ms']:.1f}/{legacy['latency_ms']:.1f} ms |")
    for item in report["cases"]:
        semantic, legacy = item["semantic"], item["legacy"]
        lines += ["", f"## {item['number']}. {item['phrase']}", "", f"EXPECTED: {item['expected']}", "", f"LEGACY: tools={legacy['tool_names']}; success={legacy['success_count']}; failure={legacy['failure_count']}", "", f"SEMANTIC: status={semantic['status']}; disposition={semantic['disposition']}; plan={semantic['plan']}", "", f"GROUNDING: {semantic['grounding_status'] or 'n/a'}; exact_evidence={semantic['exact_evidence']}", "", f"COMPARISON: {item['comparison']}", "", f"ZERO_WRITE: {'PASS' if semantic['zero_write'] else 'FAIL'}", "", f"VERDICT: {item['verdict']}", "", f"NOTES: {', '.join(item['notes']) or '—'}"]
    lines += ["", "## TOTAL", ""]
    for key, value in report["summary"].items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def run_trial(*, case_numbers: set[int] | None = None, include_legacy: bool = True) -> dict[str, Any]:
    ensure_live_model_is_configured()
    selected = [case for case in CASES if case_numbers is None or case.number in case_numbers]
    with trial_temp_directory() as directory:
        base = directory / "fixture.db"
        prepare_fixture(base)
        entries = []
        for case in selected:
            semantic_db, legacy_db = copy_case_databases(base, directory, case.number)
            semantic = run_semantic_case(case, semantic_db)
            legacy = run_legacy_case(case, legacy_db) if include_legacy else {"tool_names": [], "success_count": 0, "failure_count": 0, "final_answer": "", "latency_ms": 0.0}
            comparison = comparison_for(semantic, legacy) if include_legacy else "NOT_COMPARABLE"
            verdict, notes = verdict_for(case, semantic, comparison)
            entries.append({"number": case.number, "phrase": case.phrase, "expected": case.expected, "legacy": legacy, "semantic": semantic, "comparison": comparison, "verdict": verdict, "notes": notes})
    report = {"trial": "local_synthetic_shadow", "cases": entries}
    report["summary"] = acceptance(report)
    return report


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = output_dir / "semantic-shadow-trial.json", output_dir / "semantic-shadow-trial.md"
    # Report structures contain only synthetic phrases, compact plan shapes,
    # safe counters/fingerprints, and no provider credential or prompt value.
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, markdown_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an opt-in local live semantic shadow trial.")
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts"))
    parser.add_argument("--case", type=int, action="append", choices=[item.number for item in CASES])
    parser.add_argument("--no-legacy", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_trial(case_numbers=set(args.case) if args.case else None, include_legacy=not args.no_legacy)
    except RuntimeError as exc:
        if str(exc) == "LIVE_MODEL_NOT_CONFIGURED":
            print("LIVE_MODEL_NOT_CONFIGURED", file=sys.stderr)
            return 2
        raise
    json_path, markdown_path = write_report(report, Path(args.output_dir))
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")
    print(f"CANARY_READY = {report['summary']['CANARY_READY']}")
    return 0 if report["summary"]["CANARY_READY"] == "YES" else 1


if __name__ == "__main__":
    raise SystemExit(main())
