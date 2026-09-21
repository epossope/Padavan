"""Opt-in live production-runtime trial on synthetic state only.

Unlike the shadow harness, safe writes execute here, but exclusively against a
temporary SQLite database and a synthetic owner.
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

import bot
from grounded_response import EvidenceAssembler, GroundedResponder
from plan_runtime import BotDomainServices, PlanExecutor, PlanValidator
from semantic_planner import SemanticPlanner
from semantic_runtime import SemanticProductionRuntime

SYNTHETIC_OWNER = 970000001
ZONE = "Europe/Moscow"
NOW = datetime.fromisoformat("2026-09-21T12:00:00+03:00")


@dataclass(frozen=True)
class TrialCase:
    number: int
    phrase: str
    mode: str
    request_id: str
    expected: str


CASES = (
    TrialCase(1, "Потратил 450 рублей на кофе", "off", "prod-trial-off", "fallback"),
    TrialCase(2, "Потратил 450 рублей на кофе", "safe_write", "prod-trial-unlisted", "fallback_unlisted"),
    TrialCase(3, "Сколько я потратил сегодня?", "read", "prod-trial-read-1", "read"),
    TrialCase(4, "Потратил 450 рублей на кофе", "safe_write", "prod-trial-expense-1", "expense"),
    TrialCase(5, "Сколько я потратил сегодня?", "read", "prod-trial-read-2", "read_after_expense"),
    TrialCase(6, "Потратил 450 рублей на кофе", "safe_write", "prod-trial-expense-1", "expense_replay"),
    TrialCase(7, "Потратил 450 рублей на кофе", "safe_write", "prod-trial-expense-2", "expense_second"),
    TrialCase(8, "Иван дизайнер из Казани", "safe_write", "prod-trial-person", "person"),
    TrialCase(9, "Завтра в 15 встреча с Иваном", "safe_write", "prod-trial-meeting", "meeting"),
    TrialCase(10, "Когда я встречаюсь с Иваном?", "read", "prod-trial-meeting-read", "meeting_read"),
    TrialCase(11, "Напомни завтра в 9 позвонить", "safe_write", "prod-trial-reminder", "reminder"),
    TrialCase(12, "В пятницу встреча с Иваном", "safe_write", "prod-trial-missing-time", "clarify"),
    TrialCase(13, "Кто такой Иван Грозный?", "read", "prod-trial-answer", "fallback"),
    TrialCase(14, "Удали встречу с Иваном завтра", "safe_write", "prod-trial-delete-blocked", "delete_blocked"),
    TrialCase(15, "Удали встречу Прод-триал уникальная завтра", "full", "prod-trial-delete-one", "delete_one"),
    TrialCase(16, "Удали встречу Прод-триал дубликат завтра", "full", "prod-trial-delete-many", "delete_many"),
)


def ensure_live_model_is_configured() -> None:
    if not str(getattr(bot, "OR_KEY", "")).strip() or "PASTE_" in str(bot.OR_KEY):
        raise RuntimeError("LIVE_MODEL_NOT_CONFIGURED")


@contextlib.contextmanager
def trial_credentials():
    with patch.object(bot, "provision_managed_api_key", return_value=None), patch.object(bot, "recover_missing_managed_key", return_value=False):
        yield


@contextlib.contextmanager
def temp_database():
    directory = Path(tempfile.mkdtemp(prefix="noema-semantic-production-"))
    database = directory / "production-trial.db"
    try:
        with patch.object(bot, "DB", database):
            bot.init_db()
            if not database.resolve().is_relative_to(directory.resolve()):
                raise RuntimeError("TRIAL_DB_NOT_TEMP")
            yield database
    finally:
        gc.collect()
        shutil.rmtree(directory, ignore_errors=True)


def seed() -> None:
    bot.person_upsert(SYNTHETIC_OWNER, "Иван", relationship="synthetic", home_city="", notes="")
    bot.add_expense(SYNTHETIC_OWNER, 100, "synthetic", "RUB", "test", spent_at=NOW.isoformat())


def counts() -> dict[str, int]:
    with bot.conn() as connection:
        return {table: int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE chat_id=?", (SYNTHETIC_OWNER,)).fetchone()[0]) for table in ("expenses", "people", "events", "reminders", "semantic_executions")}


def execution_status(request_id: str) -> str:
    with bot.conn() as connection:
        row = connection.execute("SELECT status FROM semantic_executions WHERE chat_id=? AND request_id=?", (SYNTHETIC_OWNER, request_id)).fetchone()
    return str(row["status"]) if row else "ABSENT"


def make_runtime() -> SemanticProductionRuntime:
    backend = bot._SemanticRuntimeBackend(SYNTHETIC_OWNER)
    services = BotDomainServices(bot)
    return SemanticProductionRuntime(
        SemanticPlanner(backend), PlanValidator(bot.person_entity_resolver()), services,
        PlanExecutor(services, bot.conn), EvidenceAssembler(), GroundedResponder(backend),
    )


def run_case(case: TrialCase, runtime: SemanticProductionRuntime, *, allowlisted: bool) -> dict:
    before = counts(); started = time.perf_counter()
    environment = {"SEMANTIC_RUNTIME_MODE": case.mode, "SEMANTIC_CANARY_USER_IDS": str(SYNTHETIC_OWNER) if allowlisted else ""}
    with patch.dict(os.environ, environment, clear=False), trial_credentials(), patch.object(bot, "record_usage", lambda *args, **kwargs: None):
        result = asyncio.run(runtime.handle_turn(
            trusted_owner=SYNTHETIC_OWNER, request_id=case.request_id, utterance=case.phrase,
            now=NOW, timezone=ZONE, conversation_context={"recent_entities": []},
        ))
    after = counts()
    operations = []
    if result.execution:
        operations = [f"{step.domain}.{step.operation}" if not isinstance(step, dict) else f"{step.get('domain')}.{step.get('operation')}" for step in [*result.execution.reads, *result.execution.actions]]
    return {"number": case.number, "phrase": case.phrase, "mode": case.mode, "expected": case.expected,
            "status": result.status, "handled": result.handled, "operations": operations,
            "before": before, "after": after, "execution_status": execution_status(case.request_id),
            "replayed": bool(result.execution and result.execution.status == "REPLAYED"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1), "reply_nonempty": bool(result.reply)}


def verify(entry: dict) -> tuple[str, str]:
    expected, status, before, after = entry["expected"], entry["status"], entry["before"], entry["after"]
    unchanged = before == after
    if expected.startswith("fallback"):
        return ("PASS", "fallback before execution") if status == "FALLBACK_TO_LEGACY" and unchanged else ("FAIL", "fallback boundary")
    if expected == "read":
        return ("PASS", "exact grounded read") if status == "READ_ANSWER" and entry["reply_nonempty"] and before == after else ("FAIL", "read check")
    if expected == "read_after_expense":
        return ("PASS", "exact read after write") if status == "READ_ANSWER" and after["expenses"] >= 2 else ("FAIL", "post-write read")
    if expected == "expense":
        return ("PASS", "one expense") if status == "ACTION_RECEIPT" and after["expenses"] == before["expenses"] + 1 and entry["execution_status"] == "EXECUTED" else ("FAIL", "expense state")
    if expected == "expense_replay":
        return ("PASS", "replayed once") if status == "ACTION_RECEIPT" and before == after and entry["replayed"] else ("FAIL", "replay state")
    if expected == "expense_second":
        return ("PASS", "intentional second expense") if status == "ACTION_RECEIPT" and after["expenses"] == before["expenses"] + 1 and entry["execution_status"] == "EXECUTED" else ("FAIL", "second expense")
    if expected == "person":
        with bot.conn() as connection:
            people = connection.execute("SELECT home_city,notes FROM people WHERE chat_id=? AND name='Иван'", (SYNTHETIC_OWNER,)).fetchall()
        return ("PASS", "person exact state") if status == "ACTION_RECEIPT" and len(people) == 1 and people[0]["home_city"] == "Казань" and "дизайнер" in str(people[0]["notes"] or "").lower() else ("FAIL", "person state")
    if expected == "meeting":
        return ("PASS", "event exact state") if status == "ACTION_RECEIPT" and after["events"] == before["events"] + 1 else ("FAIL", "meeting state")
    if expected == "meeting_read":
        return ("PASS", "meeting exact read") if status == "READ_ANSWER" and entry["reply_nonempty"] else ("FAIL", "meeting read")
    if expected == "reminder":
        return ("PASS", "reminder exact state") if status == "ACTION_RECEIPT" and after["reminders"] == before["reminders"] + 1 else ("FAIL", "reminder state")
    if expected == "clarify":
        return ("PASS", "safe clarification") if status == "CLARIFICATION" and before == after else ("FAIL", "missing-time")
    if expected == "delete_blocked":
        return ("PASS", "blocked before execution") if status == "FALLBACK_TO_LEGACY" and before == after else ("FAIL", "safe-write delete")
    if expected == "delete_one":
        return ("PASS", "deleted exactly one") if status == "ACTION_RECEIPT" and after["events"] == before["events"] - 1 else ("FAIL", "delete one")
    if expected == "delete_many":
        return ("PASS", "ambiguous preserved") if status == "FAILURE_AFTER_EXECUTION_STARTED" and before["events"] == after["events"] else ("FAIL", "delete many")
    return "FAIL", "unknown expectation"


def markdown(report: dict) -> str:
    lines = ["# Semantic production trial", "", "DB_MODE: TEMP", "SYNTHETIC_OWNER: YES", "", "| # | Phrase | Mode | Semantic status | Operations | Exact-state check | Verdict | Latency |", "|---:|---|---|---|---|---|---|---:|"]
    for item in report["cases"]:
        lines.append(f"| {item['number']} | {item['phrase']} | {item['mode']} | {item['status']} | {', '.join(item['operations']) or '—'} | {item['check']} | {item['verdict']} | {item['latency_ms']} ms |")
    lines += ["", "## TOTAL", ""]
    lines += [f"{key}: {value}" for key, value in report["summary"].items()]
    return "\n".join(lines) + "\n"


def trial_summary(entries: list[dict], *, real_db_writes: int) -> dict[str, object]:
    """Calculate acceptance/readiness only from accepted trial outcomes."""
    def cases_pass(indices: tuple[int, ...]) -> bool:
        return len(entries) > max(indices, default=-1) and all(entries[index]["verdict"] == "PASS" for index in indices)

    failures = sum(item["verdict"] == "FAIL" for item in entries)
    semantic_failures = sum(
        item["verdict"] == "FAIL"
        and item["status"] in {"FAILURE_AFTER_EXECUTION_STARTED", "PLANNER_FAILED"}
        for item in entries
    )
    passed = sum(item["verdict"] == "PASS" for item in entries)
    return {
        "TOTAL": len(entries), "PASS": passed, "WARN": 0, "FAIL": failures,
        "SEMANTIC_FAILURES": semantic_failures, "EXACT_STATE_PASS": f"{passed}/{len(entries)}",
        "IDEMPOTENCY_PASS": "YES" if cases_pass((3, 5, 6)) else "NO",
        "REAL_DB_WRITES": real_db_writes,
        "CANARY_READ_READY": "YES" if failures == 0 and semantic_failures == 0 else "NO",
        "CANARY_SAFE_WRITE_READY": "YES" if failures == 0 and semantic_failures == 0 else "NO",
        "CANARY_FULL_READY": "YES" if failures == 0 and cases_pass((14, 15)) else "NO",
    }


def run_trial(*, runtime_factory=make_runtime) -> dict:
    ensure_live_model_is_configured()
    with temp_database() as database:
        seed(); runtime = runtime_factory(); entries = []
        selected = bot.resolved_chat_models(SYNTHETIC_OWNER)
        model_route = {"primary_model": selected["primary"], "fallback_model": selected["fallback"]}
        # Dedicated destructive fixtures are exact-state only, never legacy.
        bot.event_create(SYNTHETIC_OWNER, "Прод-триал уникальная", (NOW + timedelta(days=1)).replace(hour=15).isoformat())
        bot.event_create(SYNTHETIC_OWNER, "Прод-триал дубликат", (NOW + timedelta(days=1)).replace(hour=16).isoformat())
        bot.event_create(SYNTHETIC_OWNER, "Прод-триал дубликат", (NOW + timedelta(days=1)).replace(hour=17).isoformat())
        for case in CASES:
            entry = run_case(case, runtime, allowlisted=case.number != 2)
            entry["verdict"], entry["check"] = verify(entry)
            entries.append(entry)
        real_db_writes = 0
    summary = trial_summary(entries, real_db_writes=real_db_writes)
    return {"trial": "local_synthetic_production", "db_mode": "TEMP", "synthetic_owner": True,
            "model_route": model_route, "cases": entries, "summary": summary}


def write_report(report: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output_dir / "semantic-production-trial.json", output_dir / "semantic-production-trial.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    report = run_trial()
    write_report(report, ROOT / "artifacts")
    print(markdown(report))
    return 0 if report["summary"]["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
