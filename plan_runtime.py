"""Validated, deterministic Stage 4 execution path (not wired into production)."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from entity_resolver import EntityResolver, ResolutionStatus
from semantic_core import ActionRequest, EntityReference, ReadRequest, SemanticPlan

EXACT_ID_KEYS = frozenset({"id", "person_id", "event_id", "task_id", "reminder_id", "transaction_id", "note_id", "file_id", "project_id", "source_turn_id", "resolved_id"})
WRITE_OPS = frozenset({"create", "upsert", "update", "delete", "cancel", "overwrite", "replace", "resolve_or_create"})
DESTRUCTIVE = frozenset({"delete", "cancel", "overwrite", "replace", "update"})
READ_OPERATIONS = {"person": {"resolve"}, "event": {"list", "search", "get"}, "finance": {"summary"}, "transaction": {"list"}, "task": {"list"}, "reminder": {"list"}, "note": {"list", "search"}}
WRITE_OPERATIONS = {"person": {"upsert", "resolve_or_create"}, "event": {"create", "update", "delete", "cancel"}, "reminder": {"create"}, "transaction": {"create"}, "note": {"create"}, "task": {"create"}}


@dataclass(slots=True)
class ValidatedRead:
    read_id: str
    domain: str
    operation: str
    filters: dict[str, Any]
    entity_refs: list[EntityReference]


@dataclass(slots=True)
class ValidatedAction:
    action_id: str
    domain: str
    operation: str
    fields: dict[str, Any]
    entity_refs: list[EntityReference]
    depends_on: list[str]


@dataclass(slots=True)
class ValidatedPlan:
    owner: int
    request_id: str
    source_intent: str
    reads: list[ValidatedRead] = field(default_factory=list)
    actions: list[ValidatedAction] = field(default_factory=list)
    status: str = "VALIDATED"


@dataclass(slots=True)
class ExecutionStep:
    step_id: str
    kind: str
    status: str
    result: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionResult:
    status: str
    request_id: str
    reads: list[ExecutionStep] = field(default_factory=list)
    actions: list[ExecutionStep] = field(default_factory=list)
    created_entities: list[dict[str, Any]] = field(default_factory=list)
    updated_entities: list[dict[str, Any]] = field(default_factory=list)
    deleted_entities: list[dict[str, Any]] = field(default_factory=list)
    clarification: str = ""
    failure_category: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlanValidationError(ValueError):
    def __init__(self, category: str, clarification: str = ""):
        super().__init__(category); self.category = category; self.clarification = clarification


def _has_exact_id(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).casefold() in EXACT_ID_KEYS or _has_exact_id(item) for key, item in value.items())
    if isinstance(value, list): return any(_has_exact_id(item) for item in value)
    return False


def _time_fields(fields: dict[str, Any], zone_name: str, *, event: bool) -> dict[str, Any]:
    result = dict(fields)
    if any(key in result for key in ("fuzzy_time", "daypart", "relative_time")):
        raise PlanValidationError("fuzzy_time", "Уточни точное время.")
    local_datetime, local_date = result.pop("local_datetime", ""), result.pop("local_date", "")
    if local_datetime:
        try:
            parsed = datetime.fromisoformat(str(local_datetime).replace("Z", "+00:00"))
            if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=ZoneInfo(zone_name))
            result["starts_at" if event else "remind_at"] = parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, ZoneInfoNotFoundError): raise PlanValidationError("invalid_time") from None
    elif local_date and event:
        try: datetime.fromisoformat(str(local_date))
        except ValueError: raise PlanValidationError("invalid_date") from None
        result["starts_at"] = str(local_date) + "T00:00:00"; result["all_day"] = True
    elif event:
        raise PlanValidationError("missing_time", "Укажи дату или время события.")
    return result


class PlanValidator:
    def __init__(self, resolver: EntityResolver, *, observer=None, metric_recorder=None): self.resolver, self.observer, self.metric_recorder = resolver, observer, metric_recorder
    def _emit(self, event, **fields):
        try:
            if self.observer: self.observer(event, **fields)
        except Exception: pass

    def validate(self, trusted_owner: int, plan: SemanticPlan, *, conversation_context: dict | None, now: Any, timezone: str, request_id: str) -> ValidatedPlan:
        started = time.perf_counter(); self._emit("semantic_plan_validation_started")
        try:
            return self._validate(trusted_owner, plan, conversation_context=conversation_context, now=now, timezone=timezone, request_id=request_id)
        except PlanValidationError as exc:
            self._emit("semantic_plan_rejected", failure_category=exc.category); raise
        finally:
            if self.metric_recorder:
                try: self.metric_recorder("plan_validation_ms", (time.perf_counter() - started) * 1000)
                except Exception: pass

    def _validate(self, trusted_owner: int, plan: SemanticPlan, *, conversation_context: dict | None, now: Any, timezone: str, request_id: str) -> ValidatedPlan:
        if not request_id or not isinstance(request_id, str): raise PlanValidationError("invalid_request_id")
        if plan.disposition in {"answer", "clarify"}:
            if plan.reads or plan.actions: raise PlanValidationError("disposition_content")
            raise PlanValidationError("clarification_required", plan.clarification)
        if plan.disposition == "read" and plan.actions: raise PlanValidationError("disposition_content")
        if plan.disposition == "commit" and not plan.actions: raise PlanValidationError("commit_without_actions")
        namespace: set[str] = set(); reads: list[ValidatedRead] = []; actions: list[ValidatedAction] = []
        ref_cache: dict[str, EntityReference] = {}

        def resolve(ref: EntityReference, allow_create: bool) -> EntityReference:
            if ref.type != "person": return replace(ref, resolved_id=None)
            key = ref.mention.casefold().strip()
            if key in ref_cache: return ref_cache[key]
            # Validation is read-only.  A missing explicitly-created person is
            # carried as a pending semantic reference and created by executor.
            resolved, outcome = self.resolver.resolve_reference(trusted_owner, ref, conversation_context=conversation_context, allow_create=False)
            if outcome.status == ResolutionStatus.AMBIGUOUS: raise PlanValidationError("ambiguous_person", "Уточни, о каком человеке речь.")
            if outcome.status == ResolutionStatus.NOT_FOUND:
                if not allow_create: raise PlanValidationError("person_not_found", "Не удалось точно определить человека.")
                resolved = replace(ref, resolved_id=None)
            ref_cache[key] = resolved; return resolved

        create_mentions = {r.mention.casefold().strip() for a in plan.actions if a.domain == "person" and a.operation in {"upsert", "resolve_or_create"} for r in a.entity_refs}
        for item in plan.reads:
            if not item.read_id or item.read_id in namespace: raise PlanValidationError("invalid_read_id")
            if _has_exact_id(item.filters): raise PlanValidationError("model_exact_id")
            if item.domain not in READ_OPERATIONS or item.operation not in READ_OPERATIONS[item.domain]: raise PlanValidationError("unsupported_read")
            if any(_has_exact_id(ref.attributes) for ref in item.entity_refs): raise PlanValidationError("model_exact_id")
            if item.domain == "event" and item.operation == "get": raise PlanValidationError("event_get_requires_trusted_target")
            namespace.add(item.read_id)
            reads.append(ValidatedRead(item.read_id, item.domain, item.operation, dict(item.filters), [resolve(r, False) for r in item.entity_refs]))
        for index, item in enumerate(plan.actions):
            if not item.action_id or item.action_id in namespace: raise PlanValidationError("invalid_action_id")
            if _has_exact_id(item.fields) or any(_has_exact_id(ref.attributes) for ref in item.entity_refs): raise PlanValidationError("model_exact_id")
            if any(dep not in namespace for dep in item.depends_on) or item.action_id in item.depends_on: raise PlanValidationError("invalid_dependency")
            if item.operation in DESTRUCTIVE and not item.depends_on: raise PlanValidationError("unsafe_destructive")
            namespace.add(item.action_id)
            fields = dict(item.fields)
            if item.domain not in {"person", "event", "reminder", "transaction", "note", "task"}: raise PlanValidationError("unsupported_action")
            if item.operation not in WRITE_OPERATIONS.get(item.domain, set()): raise PlanValidationError("unsupported_action")
            if item.domain == "person" and item.operation in {"upsert", "resolve_or_create"}:
                allowed = {"name","relationship","birthday","age","home_city","current_location","projects","notes","aliases","groups","tags"}
                if set(fields) - allowed: raise PlanValidationError("unsupported_person_field")
            refs = [resolve(r, r.mention.casefold().strip() in create_mentions) for r in item.entity_refs]
            if item.domain == "event" and item.operation == "create": fields = _time_fields(fields, timezone, event=True)
            if item.domain == "reminder" and item.operation == "create": fields = _time_fields(fields, timezone, event=False)
            if item.domain == "event" and item.operation == "create" and (not str(fields.get("title") or "").strip() or fields.get("kind", "other") not in {"meeting", "call", "appointment", "lesson", "travel", "personal", "other"}): raise PlanValidationError("invalid_event_fields")
            if item.domain == "transaction" and item.operation == "create":
                try: amount = float(fields.get("amount"))
                except (TypeError, ValueError): raise PlanValidationError("invalid_transaction_amount")
                if amount <= 0 or not str(fields.get("currency", "RUB")).isalpha() or len(str(fields.get("currency", "RUB"))) not in {3}: raise PlanValidationError("invalid_transaction_amount")
            if item.domain in {"note", "task"} and item.operation == "create" and not str(fields.get("text") or "").strip(): raise PlanValidationError("missing_text")
            if item.domain == "reminder" and item.operation == "create" and not str(fields.get("title", fields.get("text", "")) or "").strip(): raise PlanValidationError("missing_reminder_text")
            actions.append(ValidatedAction(item.action_id, item.domain, item.operation, fields, refs, list(item.depends_on)))
        result = ValidatedPlan(int(trusted_owner), request_id, plan.intent, reads, actions); self._emit("semantic_plan_validated", read_count=len(reads), action_count=len(actions)); return result


class BotDomainServices:
    """Fixed adapter to existing exact-state functions; never accepts model function names."""
    def __init__(self, bot_module: Any): self.bot = bot_module
    def read(self, owner: int, item: ValidatedRead) -> dict[str, Any]:
        person = next((r.resolved_id for r in item.entity_refs if r.type == "person"), None)
        if item.domain == "person" and item.operation == "resolve": return {"ok": True, "person_id": person}
        if item.domain == "event" and item.operation == "list": return self.bot.event_list(owner, person_id=person, **item.filters)
        if item.domain == "event" and item.operation == "search": return self.bot.event_search(owner, person_id=person, **item.filters)
        if item.domain == "finance": return self.bot.finance_summary(owner, **item.filters)
        if item.domain == "transaction": return self.bot.finance_list_transactions(owner, **item.filters)
        if item.domain == "task": return self.bot.task_list(owner, **item.filters)
        if item.domain == "reminder": return self.bot.reminder_list(owner, **item.filters)
        if item.domain == "note": return self.bot.note_list(owner, **item.filters)
        raise PlanValidationError("unsupported_read")
    def write(self, owner: int, item: ValidatedAction, dependency_results: dict[str, dict[str, Any]], timezone_name: str) -> dict[str, Any]:
        fields = dict(item.fields); people = [r.resolved_id for r in item.entity_refs if r.type == "person" and r.resolved_id is not None]
        if item.domain == "person" and item.operation in {"upsert", "resolve_or_create"}:
            name = fields.pop("name", item.entity_refs[0].mention if item.entity_refs else "")
            return self.bot.person_upsert(owner, name, **fields)
        if item.domain == "event" and item.operation == "create":
            for dependency in item.depends_on:
                prior = dependency_results.get(dependency, {})
                if prior.get("domain") == "person" and prior.get("result", {}).get("id"):
                    people.append(prior["result"]["id"])
            return self.bot.event_create(owner, fields.pop("title", "Встреча"), fields.pop("starts_at"), kind=fields.pop("kind", "other"), all_day=fields.pop("all_day", False), timezone=timezone_name, participant_ids=list(dict.fromkeys(people)), **fields)
        if item.domain == "reminder" and item.operation == "create":
            result = self.bot.save_reminder(owner, fields.pop("title", fields.pop("text", "Напоминание")), fields.pop("remind_at"))
            for dependency in item.depends_on:
                dependency_result = dependency_results.get(dependency, {})
                event_id = dependency_result.get("result", {}).get("id") if dependency_result.get("domain") == "event" else None
                if event_id and result.get("ok"):
                    with self.bot.conn() as connection:
                        if connection.execute("SELECT id FROM events WHERE id=? AND chat_id=?", (event_id, owner)).fetchone():
                            connection.execute("UPDATE reminders SET event_id=? WHERE id=? AND chat_id=?", (event_id, result["id"], owner))
            return result
        if item.domain == "transaction" and item.operation == "create":
            method = self.bot.add_income if fields.get("kind") == "income" else self.bot.add_expense
            return method(owner, fields["amount"], fields.get("description", fields.get("category", "")), fields.get("currency", "RUB"), fields.get("category", "прочее"), spent_at=fields.get("spent_at", ""))
        if item.domain == "note" and item.operation == "create": return self.bot.save_note(owner, fields.get("text", ""), fields.get("title", ""))
        if item.domain == "task" and item.operation == "create": return self.bot.add_task(owner, fields.get("text", ""), fields.get("due_date", ""), fields.get("priority", "normal"))
        if item.domain == "event" and item.operation in {"delete", "cancel", "update"}:
            targets = [x for result in dependency_results.values() if result.get("domain") == "event" for x in result.get("result", {}).get("events", [])]
            if len(targets) != 1: raise PlanValidationError("ambiguous_target" if targets else "target_not_found")
            event_id = targets[0]["id"]
            return self.bot.event_delete(owner, event_id) if item.operation == "delete" else self.bot.event_update(owner, event_id, status="cancelled" if item.operation == "cancel" else fields.get("status", "scheduled"))
        raise PlanValidationError("unsupported_action")


class PlanExecutor:
    def __init__(self, services: BotDomainServices, connection_factory: Callable[[], Any], *, observer: Callable[..., Any] | None = None): self.services, self.connection_factory, self.observer = services, connection_factory, observer
    def _emit(self, event: str, **fields: Any) -> None:
        try:
            if self.observer: self.observer(event, **fields)
        except Exception: pass
    def execute(self, plan: ValidatedPlan, *, timezone_name: str) -> ExecutionResult:
        fingerprint = hashlib.sha256(repr(plan).encode()).hexdigest(); now = datetime.now(timezone.utc).isoformat()
        with self.connection_factory() as c:
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT plan_fingerprint,status,result_json FROM semantic_executions WHERE chat_id=? AND request_id=?", (plan.owner, plan.request_id)).fetchone()
            if prior and prior["plan_fingerprint"] != fingerprint:
                return ExecutionResult("REJECTED", plan.request_id, failure_category="idempotency_conflict")
            if prior and prior["status"] == "EXECUTED":
                result = ExecutionResult(**json.loads(prior["result_json"])); result.status = "REPLAYED"; self._emit("semantic_execution_replayed"); return result
            if prior:
                return ExecutionResult("REJECTED", plan.request_id, failure_category="request_not_replayable")
            claimed = c.execute("INSERT OR IGNORE INTO semantic_executions(chat_id,request_id,plan_fingerprint,status,result_json,created_at) VALUES(?,?,?,?,?,?)", (plan.owner, plan.request_id, fingerprint, "RUNNING", "", now))
            if claimed.rowcount != 1:
                return ExecutionResult("REJECTED", plan.request_id, failure_category="request_running")
        self._emit("semantic_execution_started"); result = ExecutionResult("EXECUTED", plan.request_id); deps: dict[str, dict[str, Any]] = {}
        try:
            for item in plan.reads:
                value = self.services.read(plan.owner, item); deps[item.read_id] = {"domain": item.domain, "operation": item.operation, "result": value}; result.reads.append(ExecutionStep(item.read_id, "read", "EXECUTED", value))
            for item in plan.actions:
                value = self.services.write(plan.owner, item, deps, timezone_name); deps[item.action_id] = {"domain": item.domain, "operation": item.operation, "result": value}
                if not value.get("ok"): raise PlanValidationError("domain_failure")
                result.actions.append(ExecutionStep(item.action_id, "action", "EXECUTED", value)); self._emit("semantic_execution_step", operation=item.operation)
                if value.get("id") is not None:
                    target = result.deleted_entities if item.operation == "delete" else result.updated_entities if item.operation in {"update", "cancel"} else result.created_entities
                    target.append({"domain": item.domain, "id": value["id"]})
            with self.connection_factory() as c: c.execute("UPDATE semantic_executions SET status=?,result_json=?,completed_at=? WHERE chat_id=? AND request_id=?", ("EXECUTED", json.dumps(result.to_dict()), datetime.now(timezone.utc).isoformat(), plan.owner, plan.request_id))
            self._emit("semantic_execution_completed"); return result
        except Exception as exc:
            exc = exc if isinstance(exc, PlanValidationError) else PlanValidationError("unexpected_execution_error")
            result.status = "CLARIFICATION_REQUIRED" if exc.category in {"ambiguous_target", "target_not_found"} else "FAILED"; result.failure_category = exc.category; result.clarification = exc.clarification
            with self.connection_factory() as c:
                c.execute("UPDATE semantic_executions SET status=?,result_json=?,completed_at=? WHERE chat_id=? AND request_id=?", ("FAILED", json.dumps(result.to_dict()), datetime.now(timezone.utc).isoformat(), plan.owner, plan.request_id))
            self._emit("semantic_execution_failed", failure_category=exc.category); return result
