"""Typed semantic contracts for Noema's model-neutral assistant runtime.

The model may decide what the user means, but these objects are the only
language understood by validation/execution code.  They intentionally contain
no trusted owner identifier; the runtime supplies that separately.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


EntityType = Literal[
    "person", "event", "task", "reminder", "transaction",
    "note", "knowledge", "project", "file",
]


@dataclass(slots=True)
class EntityReference:
    type: EntityType
    mention: str
    resolved_id: int | str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(slots=True)
class ReadRequest:
    domain: str
    operation: str
    filters: dict[str, Any] = field(default_factory=dict)
    entity_refs: list[EntityReference] = field(default_factory=list)


@dataclass(slots=True)
class ActionRequest:
    domain: str
    operation: str
    fields: dict[str, Any] = field(default_factory=dict)
    entity_refs: list[EntityReference] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    confidence: float = 1.0
    action_id: str = ""


@dataclass(slots=True)
class SemanticPlan:
    intent: str
    disposition: Literal["answer", "read", "commit", "clarify"] = "answer"
    entities: list[EntityReference] = field(default_factory=list)
    reads: list[ReadRequest] = field(default_factory=list)
    actions: list[ActionRequest] = field(default_factory=list)
    clarification: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SOURCE_PRECEDENCE = {
    "exact_current": 500,
    "exact_historical": 400,
    "semantic_memory": 300,
    "conversation": 200,
    "model": 100,
}


@dataclass(slots=True)
class EvidenceItem:
    source: Literal[
        "exact_current", "exact_historical", "semantic_memory",
        "conversation", "model",
    ]
    entity_type: str
    entity_id: int | str | None
    fields: dict[str, Any]
    timestamp: str = ""
    confidence: float = 1.0

    @property
    def precedence(self) -> int:
        return SOURCE_PRECEDENCE[self.source]


@dataclass(slots=True)
class EvidencePacket:
    items: list[EvidenceItem] = field(default_factory=list)
    exact_empty: bool = False

    def add(self, item: EvidenceItem) -> None:
        self.items.append(item)
        self.items.sort(key=lambda value: value.precedence, reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [asdict(item) for item in self.items], "exact_empty": self.exact_empty}
