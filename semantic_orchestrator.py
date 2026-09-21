"""Default-off, read-only semantic shadow orchestration.

This module intentionally has no production reply or write hook.  Its only
executor is ``ShadowReadExecutor``; importing it cannot mutate user state.
"""
from __future__ import annotations
import asyncio, os, threading, time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from plan_runtime import ExecutionResult, ExecutionStep, PlanValidationError

@dataclass(slots=True)
class LegacyExecutionTrace:
    tool_names: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    finalized: bool = False
    _finished: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, tool_name: str, ok: bool) -> None:
        """Retain only the comparison-safe shape of a legacy tool call."""
        with self._lock:
            self.tool_names.append(str(tool_name))
            if ok:
                self.success_count += 1
            else:
                self.failure_count += 1

    def finalize(self) -> None:
        with self._lock:
            self.finalized = True
            self._finished.set()

    def wait_finalized(self, timeout: float | None = None) -> bool:
        return self._finished.wait(timeout)

@dataclass(slots=True)
class ShadowRunResult:
    status: str; disposition: str = ""; planner_status: str = ""; validation_status: str = ""; read_status: str = ""; grounding_status: str = ""; read_count: int = 0; action_count: int = 0; proposed_operations: list[str] = field(default_factory=list); failure_category: str = ""; latency_ms: float = 0.0; match_class: str = ""
    plan: Any = None; evidence: Any = None

class ShadowReadExecutor:
    """Read-only boundary: no journal, actions, or write-capable executor."""
    def __init__(self, services): self.services=services
    def execute(self, owner, validated_plan):
        result=ExecutionResult("EXECUTED",validated_plan.request_id)
        for item in validated_plan.reads:
            value=self.services.read(owner,item)
            if not value.get("ok"): raise PlanValidationError("shadow_read_failed")
            result.reads.append(ExecutionStep(item.read_id,"read","EXECUTED",value,item.domain,item.operation))
        return result

def compare_shadow(semantic_operations, legacy_trace):
    legacy=set(legacy_trace.tool_names if legacy_trace else [])
    if not semantic_operations: return "LEGACY_NO_TOOL" if not legacy else "NOT_COMPARABLE"
    table={
        "finance.summary":"finance_summary", "transaction.list":"finance_list_transactions",
        "task.list":"task_list", "reminder.list":"reminder_list", "note.list":"note_list",
        "event.list":"event_list", "event.search":"event_search",
        "person.upsert":"person_upsert", "event.create":"event_create",
        "reminder.create":"set_reminder", "transaction.create":"add_expense",
    }
    mapped={table.get(x,x) for x in semantic_operations}
    return "MATCH" if mapped==legacy else "PARTIAL_MATCH" if mapped & legacy else "DIFFERENT_OPERATIONS"

class SemanticShadowOrchestrator:
    _global_in_flight = 0
    _global_limit = None
    def __init__(self, planner, validator, services, assembler, responder, *, enabled=None, max_concurrency=2, timeout_seconds=20, dedupe_max=2048, observer=None, metric_recorder=None):
        self.planner,self.validator,self.reads,self.assembler,self.responder=planner,validator,ShadowReadExecutor(services),assembler,responder
        self.enabled=(os.getenv("SEMANTIC_SHADOW_ENABLED","0")=="1") if enabled is None else bool(enabled)
        self.timeout=float(timeout_seconds); self.max_concurrency=max(1,int(max_concurrency)); self.semaphore=asyncio.Semaphore(self.max_concurrency); self.observer,self.metric=observer,metric_recorder; self._scheduled=OrderedDict(); self.dedupe_max=max(1,int(dedupe_max)); self._tasks=set()
        if SemanticShadowOrchestrator._global_limit is None: SemanticShadowOrchestrator._global_limit=int(os.getenv("SEMANTIC_SHADOW_MAX_CONCURRENCY", str(self.max_concurrency)))
    def _emit(self,event,**fields):
        try:
            if self.observer:self.observer(event,**fields)
        except Exception:pass
    def schedule(self, *, trusted_owner, request_id, utterance, now, timezone, conversation_context, memory_context=None, legacy_trace=None):
        if not self.enabled: return None
        key=(trusted_owner,request_id)
        if key in self._scheduled: return None
        if len(self._tasks) >= self.max_concurrency or SemanticShadowOrchestrator._global_in_flight >= SemanticShadowOrchestrator._global_limit: self._emit("shadow_capacity_skipped"); return None
        self._scheduled[key]=None
        while len(self._scheduled)>self.dedupe_max:self._scheduled.popitem(last=False)
        SemanticShadowOrchestrator._global_in_flight += 1
        task=asyncio.create_task(self.run(trusted_owner=trusted_owner,request_id=request_id,utterance=utterance,now=now,timezone=timezone,conversation_context=conversation_context,memory_context=memory_context,legacy_trace=legacy_trace)); self._tasks.add(task)
        def done(task):
            self._tasks.discard(task); SemanticShadowOrchestrator._global_in_flight=max(0, SemanticShadowOrchestrator._global_in_flight-1)
        task.add_done_callback(done); return task
    async def run(self, *, trusted_owner, request_id, utterance, now, timezone, conversation_context, memory_context=None, legacy_trace=None):
        started=time.perf_counter()
        try:
            async with self.semaphore:
                result=await asyncio.wait_for(self._run(trusted_owner,request_id,utterance,now,timezone,conversation_context,memory_context,legacy_trace),self.timeout)
        except asyncio.TimeoutError: result=ShadowRunResult(status="TIMEOUT",failure_category="timeout")
        except Exception: result=ShadowRunResult(status="PLANNER_FAILED",failure_category="unexpected")
        # Shadow completion may precede legacy tool execution.  Waiting here is
        # isolated to the background task, never to the visible response.
        result.match_class = await self._final_comparison(result, legacy_trace)
        result.latency_ms=(time.perf_counter()-started)*1000
        self._emit("semantic_shadow_completed",shadow_status=result.status,shadow_disposition=result.disposition,shadow_match_class=result.match_class,shadow_read_count=result.read_count,shadow_action_count=result.action_count,shadow_failure_category=result.failure_category,shadow_total_ms=result.latency_ms)
        if self.metric:
            try:self.metric("shadow_total_ms",result.latency_ms)
            except Exception:pass
        return result

    async def _final_comparison(self, result, legacy_trace):
        if legacy_trace is None:
            return "NOT_COMPARABLE"
        # Bounded only in a detached shadow task; incomplete traces are not
        # misclassified as different operations.
        if not await asyncio.to_thread(legacy_trace.wait_finalized, self.timeout):
            return "NOT_COMPARABLE"
        if result.status == "CLARIFICATION":
            return "SEMANTIC_CLARIFIED"
        if result.status in {"TIMEOUT", "PLANNER_FAILED", "VALIDATION_FAILED", "READ_FAILED", "GROUNDING_FAILED"}:
            return "SEMANTIC_FAILED"
        return compare_shadow(result.proposed_operations, legacy_trace)
    async def _run(self, owner, request_id, utterance, now, timezone, context, memory, legacy):
        plan=await self.planner.plan(utterance,now=now,timezone=timezone,conversation_context=context,memory_context=memory)
        if plan.intent=="planner_failure": return ShadowRunResult(status="PLANNER_FAILED",disposition=plan.disposition,planner_status="FAILED",failure_category=plan.planner_failure_category or "planner",plan=plan)
        # This list is diagnostic-only.  Reads must be represented too: the
        # legacy path often executes exactly one canonical read tool.
        operations=[f"{r.domain}.{r.operation}" for r in plan.reads]
        operations += [f"{a.domain}.{a.operation}" for a in plan.actions]
        if plan.disposition=="answer": return ShadowRunResult(status="ANSWER_ONLY",disposition=plan.disposition,planner_status="OK",action_count=len(plan.actions),proposed_operations=operations,plan=plan)
        if plan.disposition=="clarify": return ShadowRunResult(status="CLARIFICATION",disposition=plan.disposition,planner_status="OK",proposed_operations=operations,match_class="SEMANTIC_CLARIFIED",plan=plan)
        try: validated=await asyncio.to_thread(self.validator.validate,owner,plan,conversation_context=context,now=now,timezone=timezone,request_id=request_id)
        except Exception as exc:
            category = getattr(exc, "category", "validation_error")
            if category == "missing_time":
                return ShadowRunResult(status="CLARIFICATION", disposition="clarify", planner_status="OK", validation_status="FAILED", action_count=len(plan.actions), proposed_operations=operations, failure_category=category, plan=plan)
            return ShadowRunResult(status="VALIDATION_FAILED",disposition=plan.disposition,planner_status="OK",validation_status="FAILED",action_count=len(plan.actions),proposed_operations=operations,failure_category=category,plan=plan)
        if plan.disposition=="commit":
            # A proposed commit may depend on trusted lookup results.  Read
            # those prerequisites, but never pass actions to any executor.
            try:
                reads = await asyncio.to_thread(self.reads.execute, owner, validated)
            except Exception as exc:
                return ShadowRunResult(status="READ_FAILED", disposition=plan.disposition, planner_status="OK", validation_status="OK", read_status="FAILED", action_count=len(validated.actions), proposed_operations=operations, failure_category=getattr(exc, "category", "read_error"), plan=plan)
            return ShadowRunResult(status="VALIDATED_COMMIT", disposition=plan.disposition, planner_status="OK", validation_status="OK", read_status="OK", read_count=len(reads.reads), action_count=len(validated.actions), proposed_operations=operations, plan=plan)
        try: reads=await asyncio.to_thread(self.reads.execute,owner,validated)
        except Exception as exc: return ShadowRunResult(status="READ_FAILED",disposition=plan.disposition,planner_status="OK",validation_status="OK",read_status="FAILED",action_count=len(validated.actions),proposed_operations=operations,failure_category=getattr(exc,"category","read_error"),plan=plan)
        try:
            evidence=self.assembler.build(validated,reads,semantic_memory=memory)
            response=await self.responder.respond(utterance,evidence)
            status="READ_COMPLETED" if response.status=="OK" else "GROUNDING_FAILED"
            return ShadowRunResult(status=status,disposition=plan.disposition,planner_status="OK",validation_status="OK",read_status="OK", grounding_status="OK" if status=="READ_COMPLETED" else "FAILED",read_count=len(reads.reads),action_count=len(validated.actions),proposed_operations=operations,failure_category=getattr(response,"failure_category","") if status=="GROUNDING_FAILED" else "",plan=plan,evidence=evidence)
        except Exception: return ShadowRunResult(status="GROUNDING_FAILED",disposition=plan.disposition,planner_status="OK",validation_status="OK",read_status="OK",grounding_status="FAILED",read_count=len(reads.reads),action_count=len(validated.actions),proposed_operations=operations,plan=plan)
