"""Universal knowledge storage for Noema.

Stores extracted knowledge items (``knowledge_items``) with a stable link to the
original file records (``files`` table via ``knowledge_files``), idempotency
protection, plain text search and a clean interface for a later PostgreSQL +
pgvector (hybrid search) backend.

Interfaces:
    EmbeddingProvider - embed texts into vectors. A Null provider is used by
                        default; vectors are NOT emulated inside SQLite.
    KnowledgeSearch   - ``search(query, filters=None, limit=...) -> list[dict]``.
    KnowledgeStore    - SQLite implementation of KnowledgeSearch + persistence.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# schema (added on top of the existing prototype tables; `files` is untouched)
# ---------------------------------------------------------------------------

KNOWLEDGE_ITEMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id           INTEGER NOT NULL,
    project_id        TEXT,
    content_type      TEXT NOT NULL DEFAULT 'text',
    title             TEXT NOT NULL DEFAULT '',
    summary           TEXT NOT NULL DEFAULT '',
    visible_text      TEXT NOT NULL DEFAULT '',
    searchable_text   TEXT NOT NULL DEFAULT '',
    urls_json         TEXT NOT NULL DEFAULT '[]',
    entities_json     TEXT NOT NULL DEFAULT '[]',
    tags_json         TEXT NOT NULL DEFAULT '[]',
    category          TEXT NOT NULL DEFAULT '',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    content_hash      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'processing',
    enrichment_status TEXT NOT NULL DEFAULT 'not_required',
    source_message_id INTEGER,
    source_file_id    TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_idem
    ON knowledge_items(chat_id, source_message_id, content_hash);
CREATE INDEX IF NOT EXISTS ix_knowledge_chat     ON knowledge_items(chat_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_project  ON knowledge_items(project_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_category ON knowledge_items(category);

CREATE TABLE IF NOT EXISTS knowledge_files (
    knowledge_id INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    file_id      INTEGER NOT NULL REFERENCES files(id)       ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'source',
    PRIMARY KEY (knowledge_id, file_id)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_files_k ON knowledge_files(knowledge_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_files_f ON knowledge_files(file_id);

-- Generic graph layer. Entity and relation labels are plain text by design:
-- new real-world object types never require a schema migration.
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT 'other',
    normalized_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(chat_id, normalized_name, entity_type)
);
CREATE TABLE IF NOT EXISTS knowledge_entities (
    knowledge_item_id INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    confidence REAL NOT NULL DEFAULT 0.5,
    PRIMARY KEY (knowledge_item_id, entity_id)
);
CREATE TABLE IF NOT EXISTS entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    target_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    knowledge_item_id INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    confidence REAL NOT NULL DEFAULT 0.5,
    UNIQUE(source_entity_id, relation_type, target_entity_id, knowledge_item_id)
);
CREATE INDEX IF NOT EXISTS ix_entity_relations_source ON entity_relations(source_entity_id);
CREATE INDEX IF NOT EXISTS ix_entity_relations_target ON entity_relations(target_entity_id);
CREATE TABLE IF NOT EXISTS facets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
    name TEXT NOT NULL, normalized_name TEXT NOT NULL,
    UNIQUE(chat_id, normalized_name)
);
CREATE TABLE IF NOT EXISTS knowledge_facets (
    knowledge_item_id INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    facet_id INTEGER NOT NULL REFERENCES facets(id) ON DELETE CASCADE,
    PRIMARY KEY (knowledge_item_id, facet_id)
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# embedding contract — no vector emulation inside SQLite
# ---------------------------------------------------------------------------

class EmbeddingProvider(ABC):
    """Interface for an embedding backend.

    The SQLite prototype does not persist vectors. A future PostgreSQL adapter
    can implement this interface and be passed to ``KnowledgeStore`` to enable
    hybrid search without changing the pipeline code.
    """

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""

    @abstractmethod
    def dimension(self) -> int:
        """Vector dimensionality."""


class NullEmbeddingProvider(EmbeddingProvider):
    """No-op provider used until a real embedding backend is wired up."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []

    def dimension(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# search contract
# ---------------------------------------------------------------------------

class KnowledgeSearch(ABC):
    """Interface for knowledge retrieval (SQLite text-search today, hybrid later)."""

    @abstractmethod
    def search(self, query: str, filters: Optional[dict] = None, limit: int = 10) -> list[dict]:
        """Return ranked knowledge item dicts matching the query."""


# ---------------------------------------------------------------------------
# item model
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeItem:
    chat_id: int
    content_type: str = "text"
    title: str = ""
    summary: str = ""
    visible_text: str = ""
    searchable_text: str = ""
    urls: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    category: str = ""
    metadata: dict = field(default_factory=dict)
    content_hash: str = ""
    project_id: Optional[str] = None
    source_message_id: Optional[int] = None
    source_file_id: Optional[str] = None
    status: str = "processing"
    enrichment_status: str = "not_required"


class KnowledgeStore(KnowledgeSearch):
    """SQLite-backed knowledge store (plain text search) + clean interfaces.

    The same object stays behind ``KnowledgeSearch`` so a PostgreSQL + pgvector
    implementation can be swapped in transparently at the pipeline level.
    """

    def __init__(self, db_path, embedding_provider: Optional[EmbeddingProvider] = None):
        self.db_path = str(db_path)
        self.embeddings = embedding_provider or NullEmbeddingProvider()
        self.init_schema()

    # -- connection ---------------------------------------------------------
    def _connect(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def init_schema(self):
        with self._connect() as c:
            c.executescript(KNOWLEDGE_ITEMS_SCHEMA)
            self._ensure_column(c, "knowledge_items", "enrichment_status",
                                "TEXT NOT NULL DEFAULT 'not_required'")
            self._migrate_legacy_statuses(c)

    @staticmethod
    def _ensure_column(c, table, column, sql_type):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    @staticmethod
    def _migrate_legacy_statuses(c):
        """Migrate the pre-hardening single `status` column values.

        Old values carried enrichment info inside `status`:
          'enriched'            -> ingestion completed / enrichment completed
          'enrichment_partial'  -> ingestion completed / enrichment partial
          'enrichment_failed'   -> ingestion completed / enrichment failed
          'stored'              -> ingestion completed / enrichment not_required
        The `status` column now holds ONLY the ingestion status.
        """
        for old, ing, enr in (
            ("enriched", "completed", "completed"),
            ("enrichment_partial", "completed", "partial"),
            ("enrichment_failed", "completed", "failed"),
            ("stored", "completed", "not_required"),
        ):
            c.execute(
                "UPDATE knowledge_items SET status=?, enrichment_status=? "
                "WHERE status=? AND enrichment_status='not_required'",
                (ing, enr, old),
            )

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _parse_json(value, default):
        if not value:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    @staticmethod
    def content_hash(chat_id, message_id, file_ids, user_text):
        """Deterministic signature used for idempotency (Telegram retries)."""
        parts = ",".join(sorted(str(x) for x in (file_ids or []) if x))
        sig = f"{chat_id}|{message_id}|{parts}|{(user_text or '').strip()}"
        return hashlib.sha256(sig.encode("utf-8")).hexdigest()

    def _row_to_item(self, row) -> dict:
        d = dict(row)
        d["urls"] = self._parse_json(d.pop("urls_json", None), [])
        d["entities"] = self._parse_json(d.pop("entities_json", None), [])
        d["tags"] = self._parse_json(d.pop("tags_json", None), [])
        d["metadata"] = self._parse_json(d.pop("metadata_json", None), {})
        return d

    # -- persistence --------------------------------------------------------
    def insert_item(self, item: KnowledgeItem):
        """Insert item idempotently; returns (row_dict, is_duplicate)."""
        existing = self.find_duplicate(item.chat_id, item.source_message_id, item.content_hash)
        if existing:
            return existing, True
        now = utcnow()
        values = (
            item.chat_id,
            item.project_id,
            item.content_type,
            item.title,
            item.summary,
            item.visible_text,
            item.searchable_text,
            json.dumps(item.urls, ensure_ascii=False),
            json.dumps(item.entities, ensure_ascii=False),
            json.dumps(item.tags, ensure_ascii=False),
            item.category,
            json.dumps(item.metadata, ensure_ascii=False),
            item.content_hash,
            item.status,
            item.enrichment_status,
            item.source_message_id,
            item.source_file_id,
            now,
            now,
        )
        try:
            with self._connect() as c:
                cur = c.execute(
                    """INSERT INTO knowledge_items
                       (chat_id, project_id, content_type, title, summary, visible_text,
                        searchable_text, urls_json, entities_json, tags_json, category,
                        metadata_json, content_hash, status, enrichment_status,
                        source_message_id, source_file_id, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
                row = c.execute("SELECT * FROM knowledge_items WHERE id=?", (cur.lastrowid,)).fetchone()
            return self._row_to_item(row), False
        except sqlite3.IntegrityError:
            existing = self.find_duplicate(item.chat_id, item.source_message_id, item.content_hash)
            return existing, True

    def find_duplicate(self, chat_id, message_id, content_hash):
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM knowledge_items WHERE chat_id=? AND source_message_id=? AND content_hash=? LIMIT 1",
                (chat_id, message_id, content_hash),
            ).fetchone()
        return self._row_to_item(row) if row else None

    def get_item(self, item_id) -> Optional[dict]:
        with self._connect() as c:
            row = c.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def latest(self, chat_id=None, limit: int = 20) -> list[dict]:
        """Most recent knowledge items (score fallback when search is empty)."""
        limit = max(1, min(int(limit or 20), 50))
        q = "SELECT * FROM knowledge_items"
        args = []
        if chat_id is not None:
            q += " WHERE chat_id=?"
            args.append(chat_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._connect() as c:
            rows = c.execute(q, args).fetchall()
        return [self._row_to_item(r) for r in rows]

    def link_file(self, knowledge_id, file_id, role: str = "source"):
        with self._connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO knowledge_files(knowledge_id, file_id, role) VALUES (?,?,?)",
                (knowledge_id, file_id, role),
            )

    @staticmethod
    def _normalized_name(value):
        return " ".join(str(value or "").lower().replace("ё", "е").split())[:160]

    def upsert_entity(self, chat_id, name, entity_type="other") -> Optional[dict]:
        name = str(name or "").strip()[:120]
        kind = str(entity_type or "other").strip()[:80]
        normalized = self._normalized_name(name)
        if not normalized:
            return None
        now = utcnow()
        with self._connect() as c:
            c.execute("INSERT INTO entities(chat_id,name,entity_type,normalized_name,created_at,updated_at) VALUES(?,?,?,?,?,?) "
                      "ON CONFLICT(chat_id,normalized_name,entity_type) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at",
                      (chat_id, name, kind, normalized, now, now))
            row = c.execute("SELECT * FROM entities WHERE chat_id=? AND normalized_name=? AND entity_type=?", (chat_id, normalized, kind)).fetchone()
        return dict(row)

    def link_entity(self, knowledge_item_id, entity_id, confidence=0.5):
        with self._connect() as c:
            c.execute("INSERT OR REPLACE INTO knowledge_entities(knowledge_item_id,entity_id,confidence) VALUES(?,?,?)",
                      (knowledge_item_id, entity_id, float(confidence)))

    def add_relation(self, source_entity_id, relation_type, target_entity_id, knowledge_item_id, confidence=0.5):
        with self._connect() as c:
            c.execute("INSERT OR IGNORE INTO entity_relations(source_entity_id,relation_type,target_entity_id,knowledge_item_id,confidence) VALUES(?,?,?,?,?)",
                      (source_entity_id, str(relation_type or "related_to")[:80], target_entity_id, knowledge_item_id, float(confidence)))

    def sync_item_entities(self, item_id, chat_id, extracted_entities, confidence=0.5):
        """Mirror extracted entities into the normalized, source-grounded graph."""
        entities = []
        for extracted in extracted_entities or []:
            if not isinstance(extracted, dict):
                continue
            entity = self.upsert_entity(chat_id, extracted.get("name"), extracted.get("type", "other"))
            if entity:
                self.link_entity(item_id, entity["id"], confidence)
                entities.append(entity)
        return entities

    def relations_for(self, chat_id, name):
        normalized = self._normalized_name(name)
        with self._connect() as c:
            rows = c.execute("""SELECT r.relation_type, t.name AS target, s.name AS source,
                                      r.knowledge_item_id, r.confidence
                               FROM entity_relations r JOIN entities s ON s.id=r.source_entity_id
                               JOIN entities t ON t.id=r.target_entity_id
                               WHERE s.chat_id=? AND s.normalized_name=? OR t.chat_id=? AND t.normalized_name=?""",
                             (chat_id, normalized, chat_id, normalized)).fetchall()
        return [dict(r) for r in rows]

    def item_files(self, knowledge_id) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                """SELECT f.* FROM files f
                   JOIN knowledge_files kf ON kf.file_id=f.id
                   WHERE kf.knowledge_id=? ORDER BY kf.role, f.id""",
                (knowledge_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_items(self, chat_id=None) -> int:
        q = "SELECT COUNT(*) FROM knowledge_items"
        args = []
        if chat_id is not None:
            q += " WHERE chat_id=?"
            args.append(chat_id)
        with self._connect() as c:
            return c.execute(q, args).fetchone()[0]

    def set_enrichment(self, item_id, enrichments: list, enrichment_status: str) -> dict:
        """Attach UrlEnricher results to an item under ``enrichment_status``.

        The item's ingestion ``status`` is left untouched: enrichment success or
        failure must never downgrade a completed ingestion.
        """
        with self._connect() as c:
            row = c.execute("SELECT metadata_json FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
        meta = self._parse_json(row["metadata_json"] if row else None, {})
        meta["enrichments"] = enrichments
        with self._connect() as c:
            c.execute(
                "UPDATE knowledge_items SET metadata_json=?, enrichment_status=?, updated_at=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), enrichment_status, utcnow(), item_id),
            )
        return self.get_item(item_id)

    def update_status(self, item_id, status: str) -> dict:
        """Update the ingestion status only."""
        with self._connect() as c:
            c.execute("UPDATE knowledge_items SET status=?, updated_at=? WHERE id=?",
                      (status, utcnow(), item_id))
        return self.get_item(item_id)

    def search(self, query: str, filters: Optional[dict] = None, limit: int = 10) -> list[dict]:
        q = (query or "").strip()
        filters = filters or {}
        limit = max(1, min(int(limit or 10), 100))
        if not q:
            return []
        where, filter_args = [], []
        if filters.get("chat_id") is not None:
            where.append("chat_id=?")
            filter_args.append(filters["chat_id"])
        if filters.get("project_id"):
            where.append("project_id=?")
            filter_args.append(filters["project_id"])
        if filters.get("category"):
            where.append("category=?")
            filter_args.append(filters["category"])
        if filters.get("content_type"):
            where.append("content_type=?")
            filter_args.append(filters["content_type"])
        cols = ("searchable_text", "title", "summary", "visible_text", "category", "tags_json", "entities_json")
        score = " + ".join(
            f"(CASE WHEN LOWER(COALESCE({col},'')) LIKE LOWER(?) THEN 1 ELSE 0 END)" for col in cols
        )
        pattern_args = ["%" + q + "%"] * len(cols)
        inner = "SELECT k.*, ({}) AS score FROM knowledge_items k".format(score)
        if where:
            inner += " WHERE " + " AND ".join(where)
        # placeholder order in SQL text: SELECT score expr, then WHERE filters, then LIMIT
        sql = "SELECT * FROM (" + inner + ") WHERE score > 0 ORDER BY score DESC, id DESC LIMIT ?"
        args = pattern_args + filter_args + [limit]
        with self._connect() as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = self._row_to_item(r)
            d["score"] = r["score"] if "score" in r.keys() else 0
            out.append(d)
        return out

    def search_by_entity(self, name, chat_id=None, limit: int = 10) -> list[dict]:
        """Find items mentioning an entity/person/pet (for "покажи фото Ричи").

        Matching is done in Python because SQLite's LOWER() is ASCII-only and we
        must match Russian names case-insensitively.
        """
        name = (name or "").strip()
        if not name:
            return []
        limit = max(1, min(int(limit or 10), 100))
        q = "SELECT * FROM knowledge_items"
        args = []
        if chat_id is not None:
            q += " WHERE chat_id=?"
            args.append(chat_id)
        q += " ORDER BY id DESC LIMIT 200"
        with self._connect() as c:
            rows = c.execute(q, args).fetchall()
        needle = name.lower().replace("ё", "е")
        query_words = [w for w in re.findall(r"[a-zа-яё]{3,}", needle, re.I)
                       if w not in {"фото", "фотку", "картинку", "изображение", "попугая", "собаку", "кошку"}]

        def close_word(a, b):
            if a == b or a in b or b in a:
                return True
            # Russian name cases: "Кеша" / "Кешу", "Ричи" stays exact.
            return len(a) >= 4 and len(b) >= 4 and a[:-1] == b[:-1]

        out = []
        for r in rows:
            d = self._row_to_item(r)
            names = [str(e.get("name") or "").lower().replace("ё", "е")
                     for e in d.get("entities") or [] if isinstance(e, dict)]
            direct = any(needle in entity_name for entity_name in names)
            token_match = any(close_word(word, entity_word)
                              for word in query_words for entity_name in names
                              for entity_word in re.findall(r"[a-zа-яё]{3,}", entity_name, re.I))
            if direct or token_match:
                out.append(d)
                if len(out) >= limit:
                    break
        return out


def knowledge_search(db_path, query, filters: Optional[dict] = None, limit: int = 10) -> list[dict]:
    """Module-level convenience: search a store without keeping a reference."""
    return KnowledgeStore(db_path).search(query, filters=filters, limit=limit)
