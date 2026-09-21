"""Deterministic, owner-scoped identity resolution for semantic entities."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any, Callable

from semantic_core import EntityReference


class ResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    CREATED = "CREATED"


@dataclass(frozen=True, slots=True)
class ResolutionCandidate:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    status: ResolutionStatus
    entity_type: str
    mention: str
    resolved_id: int | None = None
    canonical_name: str = ""
    candidates: list[ResolutionCandidate] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


_WHITESPACE = re.compile(r"\s+")
_CONTEXT_REFERENCES = {
    "он", "она", "с ним", "с ней", "ему", "ей", "его", "её", "ее",
}
_NON_NAME_WORDS = {
    "возможно", "может", "быть", "какой-то", "какая-то", "каким-то",
    "каким", "кто", "такой", "такая", "поговорю", "встречусь",
}


def normalize_person_identity(value: str) -> str:
    """Unicode-safe identity key without lossy ё/е merging."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE.sub(" ", normalized.strip()).casefold()


def plausible_person_name(value: str) -> bool:
    value = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", str(value or "")).strip())
    if not 2 <= len(value) <= 160 or normalize_person_identity(value) in _CONTEXT_REFERENCES:
        return False
    parts = value.split(" ")
    if not 1 <= len(parts) <= 3:
        return False
    return all(any(char.isalpha() for char in part) and
               all(char.isalpha() or char in "-'’" for char in part) for part in parts)


def plausible_creation_mention(value: str) -> bool:
    """Conservative create gate; intent commitment still belongs to the caller."""
    display = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", str(value or "")).strip())
    words = {normalize_person_identity(part) for part in display.split(" ")}
    return plausible_person_name(display) and not (words & _NON_NAME_WORDS)


class EntityResolver:
    """Resolve person mentions against exact owner-scoped SQLite state."""

    def __init__(self, connection_factory: Callable[[], Any],
                 create_person: Callable[..., dict] | None = None):
        self.connection_factory = connection_factory
        self.create_person = create_person

    @staticmethod
    def _candidate(row: Any) -> ResolutionCandidate:
        return ResolutionCandidate(id=int(row["id"]), name=str(row["name"]))

    @staticmethod
    def _result(mention: str, status: ResolutionStatus, candidates: list[ResolutionCandidate],
                *, confidence: float, reason: str) -> ResolutionResult:
        candidate = candidates[0] if len(candidates) == 1 and status in {
            ResolutionStatus.RESOLVED, ResolutionStatus.CREATED,
        } else None
        return ResolutionResult(
            status=status, entity_type="person", mention=str(mention or ""),
            resolved_id=candidate.id if candidate else None,
            canonical_name=candidate.name if candidate else "",
            candidates=candidates, confidence=confidence, reason=reason,
        )

    @staticmethod
    def _dedupe(rows: list[Any]) -> list[ResolutionCandidate]:
        unique: dict[int, ResolutionCandidate] = {}
        for row in rows:
            candidate = EntityResolver._candidate(row)
            unique[candidate.id] = candidate
        return sorted(unique.values(), key=lambda item: (normalize_person_identity(item.name), item.id))

    def _context_candidates(self, trusted_owner: int, context: dict | None) -> list[ResolutionCandidate]:
        recent = context.get("recent_entities", []) if isinstance(context, dict) else []
        ids = []
        for item in recent if isinstance(recent, list) else []:
            if isinstance(item, dict) and item.get("type") == "person":
                try:
                    ids.append(int(item.get("id")))
                except (TypeError, ValueError):
                    continue
        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        with self.connection_factory() as connection:
            rows = connection.execute(
                f"SELECT id,name FROM people WHERE chat_id=? AND id IN ({','.join('?' for _ in ids)})",
                (trusted_owner, *ids),
            ).fetchall()
        return self._dedupe(rows)

    def resolve_person(self, trusted_owner: int, mention: str, *, conversation_context: dict | None = None,
                       allow_create: bool = False) -> ResolutionResult:
        normalized = normalize_person_identity(mention)
        if not normalized:
            return self._result(mention, ResolutionStatus.NOT_FOUND, [], confidence=0.0, reason="empty_mention")

        with self.connection_factory() as connection:
            people = connection.execute(
                "SELECT id,name FROM people WHERE chat_id=? ORDER BY id", (trusted_owner,)
            ).fetchall()
            exact = self._dedupe([row for row in people if normalize_person_identity(row["name"]) == normalized])
            if exact:
                status = ResolutionStatus.RESOLVED if len(exact) == 1 else ResolutionStatus.AMBIGUOUS
                return self._result(mention, status, exact, confidence=1.0 if len(exact) == 1 else 0.0,
                                    reason="canonical_exact" if len(exact) == 1 else "canonical_ambiguous")

            alias_rows = connection.execute(
                """SELECT p.id,p.name FROM person_aliases a JOIN people p ON p.id=a.person_id
                   WHERE a.chat_id=? AND p.chat_id=? AND a.normalized_alias=? ORDER BY p.id""",
                (trusted_owner, trusted_owner, normalized),
            ).fetchall()
            aliases = self._dedupe(alias_rows)
            if aliases:
                status = ResolutionStatus.RESOLVED if len(aliases) == 1 else ResolutionStatus.AMBIGUOUS
                return self._result(mention, status, aliases, confidence=0.98 if len(aliases) == 1 else 0.0,
                                    reason="explicit_alias" if len(aliases) == 1 else "alias_ambiguous")

            short = self._dedupe([
                row for row in people
                if normalize_person_identity(row["name"]).split(" ", 1)[0] == normalized
            ])
            if short:
                status = ResolutionStatus.RESOLVED if len(short) == 1 else ResolutionStatus.AMBIGUOUS
                return self._result(mention, status, short, confidence=0.85 if len(short) == 1 else 0.0,
                                    reason="short_name" if len(short) == 1 else "short_name_ambiguous")

        if normalized in _CONTEXT_REFERENCES:
            contextual = self._context_candidates(trusted_owner, conversation_context)
            if contextual:
                status = ResolutionStatus.RESOLVED if len(contextual) == 1 else ResolutionStatus.AMBIGUOUS
                return self._result(mention, status, contextual,
                                    confidence=0.9 if len(contextual) == 1 else 0.0,
                                    reason="recent_context" if len(contextual) == 1 else "recent_context_ambiguous")

        if allow_create and plausible_creation_mention(mention) and self.create_person is not None:
            created = self.create_person(trusted_owner, str(mention).strip())
            if created.get("ok") and created.get("id") is not None:
                with self.connection_factory() as connection:
                    row = connection.execute(
                        "SELECT id,name FROM people WHERE id=? AND chat_id=?",
                        (int(created["id"]), trusted_owner),
                    ).fetchone()
                if row:
                    candidate = self._candidate(row)
                    return self._result(mention, ResolutionStatus.CREATED, [candidate], confidence=1.0,
                                        reason="explicit_create")
        return self._result(mention, ResolutionStatus.NOT_FOUND, [], confidence=0.0,
                            reason="not_found" if not allow_create else "not_created")

    def resolve_reference(self, trusted_owner: int, reference: EntityReference, *,
                          conversation_context: dict | None = None,
                          allow_create: bool = False) -> tuple[EntityReference, ResolutionResult]:
        if reference.type != "person":
            result = ResolutionResult(
                status=ResolutionStatus.NOT_FOUND, entity_type=str(reference.type),
                mention=reference.mention, confidence=0.0, reason="unsupported_entity_type",
            )
            return replace(reference), result
        result = self.resolve_person(
            trusted_owner, reference.mention, conversation_context=conversation_context,
            allow_create=allow_create,
        )
        attributes = dict(reference.attributes)
        if result.canonical_name:
            attributes["canonical_name"] = result.canonical_name
        resolved = replace(
            reference, resolved_id=result.resolved_id, attributes=attributes,
            confidence=min(reference.confidence, result.confidence),
        )
        return resolved, result
