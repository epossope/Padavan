"""Guarded production semantic runtime, separate from read-only shadowing.

This module owns production eligibility, fallback boundaries, and deterministic
receipts.  It deliberately delegates every exact-state operation to the
existing validator, services, and executor contracts.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from plan_runtime import ExecutionResult, ExecutionStep, PlanValidationError


MODES = frozenset({"off", "read", "safe_write", "full"})
READ_OPERATIONS = frozenset({
    "person.resolve", "person.interactions_list", "event.list", "event.search",
    "finance.summary", "transaction.list", "task.list", "reminder.list",
    "note.list", "note.search",
})
SAFE_WRITE_OPERATIONS = frozenset({
    "person.upsert", "event.create", "reminder.create", "transaction.create",
    "note.create", "task.create",
})
FULL_WRITE_OPERATIONS = SAFE_WRITE_OPERATIONS | frozenset({"event.update", "event.delete", "event.cancel"})
SAFE_CLARIFICATIONS = {
    "ambiguous_person": "Уточни, о каком человеке речь.",
    "person_not_found": "Не удалось точно определить человека.",
    "missing_time": "Укажи точное время.",
    "fuzzy_time": "Укажи точное время.",
    "ambiguous_target": "Уточни, какую встречу нужно изменить.",
    "target_not_found": "Не удалось найти встречу для этого действия.",
}
RECEIPTS = {
    "event.create": "Готово. Встреча сохранена.",
    "reminder.create": "Готово. Напоминание создано.",
    "transaction.create": "Готово. Расход сохранён.",
    "person.upsert": "Готово. Информация сохранена.",
    "note.create": "Готово. Заметка сохранена.",
    "task.create": "Готово. Задача создана.",
    "event.delete": "Готово. Встреча удалена.",
    "event.cancel": "Готово. Встреча отменена.",
    "event.update": "Готово. Встреча обновлена.",
}


def runtime_mode(environ: dict[str, str] | None = None) -> str:
    value = str((os.environ if environ is None else environ).get("SEMANTIC_RUNTIME_MODE", "off")).strip().lower()
    return value if value in MODES else "off"


def canary_owners(environ: dict[str, str] | None = None) -> frozenset[int]:
    values = str((os.environ if environ is None else environ).get("SEMANTIC_CANARY_USER_IDS", "")).split(",")
    result = set()
    for value in values:
        try:
            if value.strip():
                result.add(int(value.strip()))
        except ValueError:
            continue
    return frozenset(result)


@dataclass(slots=True)
class SemanticRuntimeResult:
    status: str
    handled: bool = False
    reply: str = ""
    disposition: str = ""
    failure_category: str = ""
    execution_started: bool = False
    execution: ExecutionResult | None = None


class SemanticProductionRuntime:
    """Mode-gated semantic production path; never uses the shadow executor."""

    def __init__(self, planner, validator, services, executor, assembler, responder, *,
                 mode_getter: Callable[[], str] = runtime_mode,
                 owners_getter: Callable[[], frozenset[int]] = canary_owners,
                 observer=None, metric_recorder=None):
        self.planner, self.validator, self.services = planner, validator, services
        self.executor, self.assembler, self.responder = executor, assembler, responder
        self.mode_getter, self.owners_getter = mode_getter, owners_getter
        self.observer, self.metric = observer, metric_recorder

    def _emit(self, event: str, **fields: Any) -> None:
        try:
            if self.observer:
                self.observer(event, **fields)
        except Exception:
            pass

    def _result(self, status: str, **kwargs: Any) -> SemanticRuntimeResult:
        return SemanticRuntimeResult(status, **kwargs)

    def _eligible(self, owner: int) -> tuple[bool, str, str]:
        mode = self.mode_getter()
        if mode == "off":
            return False, mode, "disabled"
        if int(owner) not in self.owners_getter():
            return False, mode, "not_allowlisted"
        return True, mode, ""

    @staticmethod
    def _operations(plan) -> set[str]:
        return {f"{item.domain}.{item.operation}" for item in [*plan.reads, *plan.actions]}

    @staticmethod
    def _clarification(category: str, fallback: str = "") -> str:
        return fallback or SAFE_CLARIFICATIONS.get(category, "Нужно уточнение, прежде чем продолжить.")

    def _read_exact(self, owner: int, validated) -> ExecutionResult:
        result = ExecutionResult("EXECUTED", validated.request_id)
        for item in validated.reads:
            value = self.services.read(owner, item)
            if not value.get("ok"):
                raise PlanValidationError("shadow_read_failed")
            result.reads.append(ExecutionStep(item.read_id, "read", "EXECUTED", value, item.domain, item.operation))
        return result

    async def handle_turn(self, *, trusted_owner: int, request_id: str, utterance: str, now: Any,
                          timezone: str, conversation_context: dict | None, memory_context=None) -> SemanticRuntimeResult:
        allowed, mode, reason = self._eligible(trusted_owner)
        if not allowed:
            self._emit("semantic_runtime_skipped", runtime_mode=mode, failure_category=reason)
            return self._result("FALLBACK_TO_LEGACY", failure_category=reason)
        self._emit("semantic_runtime_started", runtime_mode=mode)
        existing = getattr(self.executor, "existing_execution", None)
        try:
            prior = await asyncio.to_thread(existing, trusted_owner, request_id) if existing else None
        except Exception:
            prior = ExecutionResult("REJECTED", request_id, failure_category="execution_store_error")
        if prior is not None:
            if prior.status == "REPLAYED":
                step = prior.actions[0] if prior.actions else None
                operation = f"{step.get('domain', '')}.{step.get('operation', '')}" if isinstance(step, dict) else f"{getattr(step, 'domain', '')}.{getattr(step, 'operation', '')}"
                return self._result("ACTION_RECEIPT", handled=True, reply=RECEIPTS.get(operation, "Готово. Изменения сохранены."), disposition="commit", execution_started=True, execution=prior)
            return self._result("FAILURE_AFTER_EXECUTION_STARTED", handled=True, reply=self._clarification(prior.failure_category), disposition="clarify", failure_category=prior.failure_category, execution_started=True, execution=prior)
        try:
            plan = await self.planner.plan(utterance, now=now, timezone=timezone,
                                           conversation_context=conversation_context or {}, memory_context=memory_context)
        except Exception:
            self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category="planner_error")
            return self._result("FALLBACK_TO_LEGACY", failure_category="planner_error")
        if plan.intent == "planner_failure":
            category = plan.planner_failure_category or "planner_error"
            self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category=category)
            return self._result("FALLBACK_TO_LEGACY", failure_category=category)
        if plan.disposition == "answer":
            self._emit("semantic_runtime_fallback", runtime_mode=mode, disposition="answer", failure_category="answer_disposition")
            return self._result("FALLBACK_TO_LEGACY", disposition="answer", failure_category="answer_disposition")
        if plan.disposition == "clarify":
            reply = plan.clarification or "Что именно нужно уточнить?"
            self._emit("semantic_runtime_handled", runtime_mode=mode, disposition="clarify")
            return self._result("CLARIFICATION", handled=True, reply=reply, disposition="clarify")
        operations = self._operations(plan)
        if plan.disposition == "read":
            if not operations or not operations <= READ_OPERATIONS:
                self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category="unsupported_read")
                return self._result("FALLBACK_TO_LEGACY", disposition="read", failure_category="unsupported_read")
        elif plan.disposition == "commit":
            permitted = SAFE_WRITE_OPERATIONS if mode == "safe_write" else FULL_WRITE_OPERATIONS if mode == "full" else frozenset()
            if mode == "read" or not operations <= (READ_OPERATIONS | permitted):
                self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category="operation_not_permitted")
                return self._result("FALLBACK_TO_LEGACY", disposition="commit", failure_category="operation_not_permitted")
            if len(plan.actions) > 1:
                self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category="multi_action_canary")
                return self._result("FALLBACK_TO_LEGACY", disposition="commit", failure_category="multi_action_canary")
        else:
            self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category="unsupported_disposition")
            return self._result("FALLBACK_TO_LEGACY", failure_category="unsupported_disposition")
        try:
            validated = await asyncio.to_thread(self.validator.validate, trusted_owner, plan,
                                                conversation_context=conversation_context or {}, now=now,
                                                timezone=timezone, request_id=request_id)
        except PlanValidationError as exc:
            if exc.category in SAFE_CLARIFICATIONS:
                reply = self._clarification(exc.category, exc.clarification)
                self._emit("semantic_runtime_handled", runtime_mode=mode, disposition="clarify", failure_category=exc.category)
                return self._result("CLARIFICATION", handled=True, reply=reply, disposition="clarify", failure_category=exc.category)
            self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category=exc.category)
            return self._result("FALLBACK_TO_LEGACY", failure_category=exc.category)
        except Exception:
            self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category="validation_error")
            return self._result("FALLBACK_TO_LEGACY", failure_category="validation_error")
        if plan.disposition == "read":
            try:
                exact = await asyncio.to_thread(self._read_exact, trusted_owner, validated)
                evidence = self.assembler.build(validated, exact, semantic_memory=memory_context)
                response = await self.responder.respond(utterance, evidence)
                if response.status != "OK":
                    self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category="grounding_error")
                    return self._result("FALLBACK_TO_LEGACY", failure_category="grounding_error")
                reply = response.render()
                if not reply:
                    raise PlanValidationError("grounding_failed")
            except PlanValidationError as exc:
                if exc.category in SAFE_CLARIFICATIONS:
                    reply = self._clarification(exc.category, exc.clarification)
                    self._emit("semantic_runtime_handled", runtime_mode=mode, disposition="clarify", failure_category=exc.category)
                    return self._result("CLARIFICATION", handled=True, reply=reply, disposition="clarify", failure_category=exc.category)
                self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category=exc.category)
                return self._result("FALLBACK_TO_LEGACY", failure_category=exc.category)
            except Exception:
                self._emit("semantic_runtime_fallback", runtime_mode=mode, failure_category="read_error")
                return self._result("FALLBACK_TO_LEGACY", failure_category="read_error")
            self._emit("semantic_runtime_read_completed", runtime_mode=mode, read_count=len(exact.reads))
            self._emit("semantic_runtime_handled", runtime_mode=mode, disposition="read")
            return self._result("READ_ANSWER", handled=True, reply=reply, disposition="read", execution=exact)
        # From this call onward legacy fallback is categorically prohibited:
        # PlanExecutor atomically claims the trusted request before any write.
        self._emit("semantic_runtime_execution_started", runtime_mode=mode, action_count=len(validated.actions))
        try:
            execution = await asyncio.to_thread(self.executor.execute, validated, timezone_name=timezone)
        except Exception:
            self._emit("semantic_runtime_execution_failed", runtime_mode=mode, failure_category="unexpected_execution_error")
            return self._result("FAILURE_AFTER_EXECUTION_STARTED", handled=True,
                                reply=self._clarification("unexpected_execution_error"), disposition="clarify",
                                failure_category="unexpected_execution_error", execution_started=True)
        if execution.status in {"EXECUTED", "REPLAYED"}:
            step = execution.actions[0] if execution.actions else None
            if isinstance(step, dict):
                operation = f"{step.get('domain', '')}.{step.get('operation', '')}"
            else:
                operation = f"{getattr(step, 'domain', '')}.{getattr(step, 'operation', '')}" if step else ""
            reply = RECEIPTS.get(operation, "Готово. Изменения сохранены.")
            event = "semantic_runtime_replayed" if execution.status == "REPLAYED" else "semantic_runtime_execution_completed"
            self._emit(event, runtime_mode=mode, operation=operation, action_count=len(execution.actions))
            self._emit("semantic_runtime_handled", runtime_mode=mode, disposition="commit")
            return self._result("ACTION_RECEIPT", handled=True, reply=reply, disposition="commit", execution_started=True, execution=execution)
        category = execution.failure_category or "execution_failed"
        reply = self._clarification(category, execution.clarification)
        self._emit("semantic_runtime_execution_failed", runtime_mode=mode, failure_category=category)
        return self._result("FAILURE_AFTER_EXECUTION_STARTED", handled=True, reply=reply, disposition="clarify", failure_category=category, execution_started=True, execution=execution)
