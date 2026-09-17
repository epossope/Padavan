"""Provider-neutral, planning-only natural-language interface for Noema.

The planner converts an untrusted model response into the typed contracts in
``semantic_core``.  It deliberately has no database, owner, resolver, tool, or
execution dependency: exact identity and all state changes belong to later
runtime stages.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from semantic_core import ActionRequest, EntityReference, ReadRequest, SemanticPlan


class PlannerBackend(Protocol):
    """Replaceable structured-generation backend (provider and model agnostic)."""

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        input_payload: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> str | Mapping[str, Any]: ...


class SemanticPlanValidationError(ValueError):
    """A model response did not satisfy the safe SemanticPlan contract."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


ENTITY_TYPES = frozenset(
    {"person", "event", "task", "reminder", "transaction", "note", "knowledge", "project", "file"}
)
DISPOSITIONS = frozenset({"answer", "read", "commit", "clarify"})
DOMAIN_OPERATIONS: dict[str, frozenset[str]] = {
    "person": frozenset({"resolve", "resolve_or_create", "upsert", "interactions_list", "delete", "overwrite", "replace"}),
    "event": frozenset({"create", "get", "list", "search", "update", "delete", "cancel", "overwrite", "replace"}),
    "task": frozenset({"create", "get", "list", "search", "update", "delete", "overwrite", "replace"}),
    "reminder": frozenset({"create", "get", "list", "search", "update", "delete", "overwrite", "replace"}),
    "transaction": frozenset({"create", "get", "list", "search", "summary", "update", "delete", "overwrite", "replace"}),
    "finance": frozenset({"list", "search", "summary"}),
    "note": frozenset({"create", "get", "list", "search", "update", "delete", "overwrite", "replace"}),
    "knowledge": frozenset({"save", "get", "search", "delete", "overwrite", "replace"}),
    "project": frozenset({"resolve", "get", "list", "search"}),
    "file": frozenset({"create", "get", "list", "search", "update", "delete", "overwrite", "replace"}),
}
READ_OPERATIONS = frozenset({"resolve", "get", "list", "search", "summary", "interactions_list"})
DESTRUCTIVE_OPERATIONS = frozenset({"delete", "cancel", "overwrite", "replace"})
OWNER_KEYS = frozenset({"chat_id", "user_id", "owner_id"})
CONTEXT_ID_KEYS = OWNER_KEYS | frozenset({"id", "person_id", "resolved_id"})

MAX_RESPONSE_BYTES = 64 * 1024
MAX_ENTITIES = 16
MAX_READS = 16
MAX_ACTIONS = 16
MAX_ENTITY_REFS = 12
MAX_COLLECTION_ITEMS = 32
MAX_NESTING = 4
MAX_SHORT_STRING = 160
MAX_VALUE_STRING = 1000
MAX_CLARIFICATION = 600


PLANNER_PROMPT = """You are Noema's semantic planner. Understand the user's intent; do not answer it or execute anything.
Return only JSON matching the supplied schema. Plan exact reads and semantic actions, but never invent or copy database IDs or owner identifiers.
User-specific facts require exact reads; current exact state outranks memory and conversation. Never invent missing personal facts.
Only explicit committed requests may produce actions. Uncertainty or missing required meaning must produce disposition=clarify with a useful question.
Keep relative/fuzzy time as semantic fields when it cannot be normalized without guessing. Use now and timezone supplied in input.
Pronouns remain mention text (for example "с ним"); a later trusted resolver handles identity.
Deletion/cancellation must first plan a target read and make the destructive action depend on that resolution. JSON only."""


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "disposition"],
    "properties": {
        "intent": {"type": "string"},
        "disposition": {"enum": sorted(DISPOSITIONS)},
        "entities": {"type": "array", "items": {"$ref": "#/$defs/entity"}},
        "reads": {"type": "array", "items": {"$ref": "#/$defs/read"}},
        "actions": {"type": "array", "items": {"$ref": "#/$defs/action"}},
        "clarification": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "$defs": {
        "entity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "mention"],
            "properties": {
                "type": {"enum": sorted(ENTITY_TYPES)},
                "mention": {"type": "string"},
                "attributes": {"type": "object"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "read": {
            "type": "object",
            "additionalProperties": False,
            "required": ["domain", "operation"],
            "properties": {
                "domain": {"enum": sorted(DOMAIN_OPERATIONS)},
                "operation": {"type": "string"},
                "filters": {"type": "object"},
                "entity_refs": {"type": "array", "items": {"$ref": "#/$defs/entity"}},
            },
        },
        "action": {
            "type": "object",
            "additionalProperties": False,
            "required": ["domain", "operation"],
            "properties": {
                "domain": {"enum": sorted(DOMAIN_OPERATIONS)},
                "operation": {"type": "string"},
                "fields": {"type": "object"},
                "entity_refs": {"type": "array", "items": {"$ref": "#/$defs/entity"}},
                "depends_on": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "action_id": {"type": "string"},
            },
        },
    },
}


def _invalid(category: str) -> SemanticPlanValidationError:
    return SemanticPlanValidationError(category)


def _object(value: Any, *, allowed: set[str], required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(f"{label}_type")
    keys = set(value)
    if not required <= keys or keys - allowed:
        raise _invalid(f"{label}_shape")
    if keys & OWNER_KEYS:
        raise _invalid("owner_field")
    return value


def _string(value: Any, *, label: str, maximum: int = MAX_SHORT_STRING, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{label}_type")
    cleaned = value.strip()
    if (not cleaned and not allow_empty) or len(cleaned) > maximum:
        raise _invalid(f"{label}_length")
    return cleaned


def _confidence(value: Any, *, default: float = 1.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid("confidence_type")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise _invalid("confidence_range")
    return result


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_NESTING:
        raise _invalid("nested_limit")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise _invalid("non_finite_number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_VALUE_STRING:
            raise _invalid("value_string_length")
        return value
    if isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise _invalid("collection_limit")
        return [_bounded_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise _invalid("collection_limit")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > MAX_SHORT_STRING:
                raise _invalid("field_name")
            if key.casefold() in OWNER_KEYS:
                raise _invalid("owner_field")
            result[key] = _bounded_value(item, depth=depth + 1)
        return result
    raise _invalid("unsupported_value")


def _entity(value: Any) -> EntityReference:
    data = _object(
        value,
        allowed={"type", "mention", "resolved_id", "attributes", "confidence"},
        required={"type", "mention"},
        label="entity",
    )
    entity_type = _string(data["type"], label="entity_type")
    if entity_type not in ENTITY_TYPES:
        raise _invalid("entity_type")
    # A model-provided resolved_id is deliberately discarded.  Only the
    # owner-scoped EntityResolver may establish exact identity later.
    return EntityReference(
        type=entity_type,  # type: ignore[arg-type]
        mention=_string(data["mention"], label="mention", maximum=300),
        resolved_id=None,
        attributes=_bounded_value(data.get("attributes", {})),
        confidence=_confidence(data.get("confidence")),
    )


def _entity_list(value: Any, *, maximum: int) -> list[EntityReference]:
    if not isinstance(value, list) or len(value) > maximum:
        raise _invalid("entity_list")
    return [_entity(item) for item in value]


def _validate_domain_operation(domain_value: Any, operation_value: Any, *, read: bool) -> tuple[str, str]:
    domain = _string(domain_value, label="domain").casefold()
    operation = _string(operation_value, label="operation").casefold()
    if domain not in DOMAIN_OPERATIONS or operation not in DOMAIN_OPERATIONS[domain]:
        raise _invalid("unknown_operation")
    if read and operation not in READ_OPERATIONS:
        raise _invalid("write_in_reads")
    return domain, operation


def _read(value: Any) -> ReadRequest:
    data = _object(
        value,
        allowed={"domain", "operation", "filters", "entity_refs"},
        required={"domain", "operation"},
        label="read",
    )
    domain, operation = _validate_domain_operation(data["domain"], data["operation"], read=True)
    filters = _bounded_value(data.get("filters", {}))
    if not isinstance(filters, dict):
        raise _invalid("filters_type")
    return ReadRequest(
        domain=domain,
        operation=operation,
        filters=filters,
        entity_refs=_entity_list(data.get("entity_refs", []), maximum=MAX_ENTITY_REFS),
    )


def _action(value: Any) -> ActionRequest:
    data = _object(
        value,
        allowed={"domain", "operation", "fields", "entity_refs", "depends_on", "confidence", "action_id"},
        required={"domain", "operation"},
        label="action",
    )
    domain, operation = _validate_domain_operation(data["domain"], data["operation"], read=False)
    fields = _bounded_value(data.get("fields", {}))
    if not isinstance(fields, dict):
        raise _invalid("fields_type")
    dependencies = data.get("depends_on", [])
    if not isinstance(dependencies, list) or len(dependencies) > MAX_ACTIONS:
        raise _invalid("dependencies_type")
    return ActionRequest(
        domain=domain,
        operation=operation,
        fields=fields,
        entity_refs=_entity_list(data.get("entity_refs", []), maximum=MAX_ENTITY_REFS),
        depends_on=[_string(item, label="dependency") for item in dependencies],
        confidence=_confidence(data.get("confidence")),
        action_id=_string(data.get("action_id", ""), label="action_id", allow_empty=True),
    )


def parse_semantic_plan(payload: str | Mapping[str, Any]) -> SemanticPlan:
    """Strictly parse untrusted structured model output into contracts."""
    if isinstance(payload, str):
        if not payload.strip():
            raise _invalid("empty")
        if len(payload.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise _invalid("oversized")
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, UnicodeError):
            raise _invalid("malformed_json") from None
    elif isinstance(payload, Mapping):
        raw = dict(payload)
        try:
            if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise _invalid("oversized")
        except (TypeError, ValueError, OverflowError):
            raise _invalid("malformed_structure") from None
    else:
        raise _invalid("response_type")

    data = _object(
        raw,
        allowed={"intent", "disposition", "entities", "reads", "actions", "clarification", "confidence"},
        required={"intent", "disposition"},
        label="plan",
    )
    intent = _string(data["intent"], label="intent")
    disposition = _string(data["disposition"], label="disposition").casefold()
    if disposition not in DISPOSITIONS:
        raise _invalid("disposition")
    for key, maximum in (("entities", MAX_ENTITIES), ("reads", MAX_READS), ("actions", MAX_ACTIONS)):
        value = data.get(key, [])
        if not isinstance(value, list) or len(value) > maximum:
            raise _invalid(f"{key}_limit")

    entities = _entity_list(data.get("entities", []), maximum=MAX_ENTITIES)
    reads = [_read(item) for item in data.get("reads", [])]
    actions = [_action(item) for item in data.get("actions", [])]
    clarification = _string(
        data.get("clarification", ""), label="clarification", maximum=MAX_CLARIFICATION, allow_empty=True
    )
    if actions and disposition != "commit":
        raise _invalid("actions_without_commit")
    if disposition == "clarify" and not clarification:
        raise _invalid("missing_clarification")
    if disposition == "read" and not reads:
        raise _invalid("read_without_request")
    if disposition == "answer" and (reads or actions):
        raise _invalid("answer_has_work")

    read_domains = {request.domain for request in reads}
    for action in actions:
        if action.operation in DESTRUCTIVE_OPERATIONS and (
            action.domain not in read_domains or not action.depends_on
        ):
            raise _invalid("unsafe_destructive_action")

    return SemanticPlan(
        intent=intent,
        disposition=disposition,  # type: ignore[arg-type]
        entities=entities,
        reads=reads,
        actions=actions,
        clarification=clarification,
        confidence=_confidence(data.get("confidence")),
    )


def _sanitize_context(value: Any, *, depth: int = 0) -> Any:
    """Bound model context and remove all owner/exact identity material."""
    if depth > MAX_NESTING:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:MAX_VALUE_STRING]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_COLLECTION_ITEMS]:
            if not isinstance(key, str) or key.casefold() in CONTEXT_ID_KEYS:
                continue
            result[key[:MAX_SHORT_STRING]] = _sanitize_context(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_context(item, depth=depth + 1) for item in list(value)[:MAX_COLLECTION_ITEMS]]
    return str(value)[:MAX_VALUE_STRING]


def _safe_failure_plan() -> SemanticPlan:
    return SemanticPlan(
        intent="planner_failure",
        disposition="clarify",
        clarification="Не удалось надёжно понять запрос. Пожалуйста, уточни его.",
        confidence=0.0,
    )


Observer = Callable[..., Any]
MetricRecorder = Callable[..., Any]


async def _ignore_observer_failure(awaitable: Awaitable[Any]) -> None:
    try:
        await awaitable
    except Exception:
        pass


class SemanticPlanner:
    """Natural-language planning boundary with no execution side effects."""

    def __init__(
        self,
        backend: PlannerBackend,
        *,
        timeout_seconds: float = 20.0,
        observer: Observer | None = None,
        metric_recorder: MetricRecorder | None = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.backend = backend
        self.timeout_seconds = float(timeout_seconds)
        self.observer = observer
        self.metric_recorder = metric_recorder

    def _observe(self, event: str, **fields: Any) -> None:
        if self.observer is None:
            return
        try:
            result = self.observer(event, **fields)
            if inspect.isawaitable(result):
                # Observation is best-effort and may never delay planning.
                asyncio.create_task(_ignore_observer_failure(result))
        except Exception:
            pass

    def _metric(self, name: str, value: float) -> None:
        if self.metric_recorder is None:
            return
        try:
            result = self.metric_recorder(name, value)
            if inspect.isawaitable(result):
                asyncio.create_task(_ignore_observer_failure(result))
        except Exception:
            pass

    async def plan(
        self,
        utterance: str,
        *,
        now: datetime | str,
        timezone: str,
        conversation_context: Any,
        memory_context: Any = None,
    ) -> SemanticPlan:
        started = time.perf_counter()
        self._observe("semantic_planner_started")
        try:
            clean_utterance = _string(utterance, label="utterance", maximum=MAX_VALUE_STRING)
            clean_timezone = _string(timezone, label="timezone", maximum=80)
            now_value = now.isoformat() if isinstance(now, datetime) else _string(now, label="now", maximum=80)
            response = await asyncio.wait_for(
                self.backend.generate_structured(
                    system_prompt=PLANNER_PROMPT,
                    input_payload={
                        "utterance": clean_utterance,
                        "now": now_value,
                        "timezone": clean_timezone,
                        "conversation_context": _sanitize_context(conversation_context),
                        "memory_context": _sanitize_context(memory_context),
                    },
                    output_schema=OUTPUT_SCHEMA,
                ),
                timeout=self.timeout_seconds,
            )
            result = parse_semantic_plan(response)
            self._observe(
                "semantic_planner_completed",
                intent=result.intent,
                disposition=result.disposition,
                entity_count=len(result.entities),
                read_count=len(result.reads),
                action_count=len(result.actions),
            )
            return result
        except SemanticPlanValidationError as exc:
            self._observe("semantic_planner_invalid_output", failure_category=exc.category)
            return _safe_failure_plan()
        except asyncio.TimeoutError:
            self._observe("semantic_planner_failed", failure_category="timeout")
            return _safe_failure_plan()
        except Exception:
            self._observe("semantic_planner_failed", failure_category="provider_error")
            return _safe_failure_plan()
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self._metric("planner_latency_ms", elapsed_ms)


__all__ = [
    "DOMAIN_OPERATIONS",
    "OUTPUT_SCHEMA",
    "PLANNER_PROMPT",
    "PlannerBackend",
    "SemanticPlanValidationError",
    "SemanticPlanner",
    "parse_semantic_plan",
]
