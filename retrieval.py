"""Universal Retrieval for Noema.

Bridges the ingestion knowledge store and the LLM agent tool layer:

    user query -> understand retrieval intent -> search knowledge
    -> resolve entities/project/context -> retrieve related files
    -> compose answer -> optionally send original media

The core here is provider-agnostic (works against a ``KnowledgeSearch`` /
``KnowledgeStore``). Ranking is plain Python so it can be swapped for
PostgreSQL + pgvector later without touching callers.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

from knowledge_store import KnowledgeSearch, KnowledgeStore

# ---------------------------------------------------------------------------
# text normalization
# ---------------------------------------------------------------------------

_TRIM_RE = re.compile(r"[^a-zа-я0-9]+")


def normalize_token(s) -> str:
    """Lowercase, ё->е, strip non-word chars. Single-space joined tokens."""
    s = (s or "").lower().replace("ё", "е")
    return _TRIM_RE.sub(" ", s).strip()


def _word_stems_close(w1: str, w2: str) -> bool:
    """Tolerant name matching for Russian/English names across cases.

    Handles 'Тошка' vs 'Тошку', 'Honcho', 'Noema' vs 'Noemo'
    via equal-length prefix minus the final letter and a max length delta.
    """
    if not w1 or not w2:
        return False
    if w1 == w2:
        return True
    if abs(len(w1) - len(w2)) > 2:
        return False
    k = min(len(w1), len(w2))
    if k < 4:
        return False
    return w1[: k - 1] == w2[: k - 1]


def tokens_stems_close(a, b) -> bool:
    """True when any token of A is a case-tolerant match of any token of B."""
    wa, wb = normalize_token(a).split(), normalize_token(b).split()
    if not wa or not wb:
        return False
    for x in wa:
        for y in wb:
            if _word_stems_close(x, y):
                return True
    return False


# ---------------------------------------------------------------------------
# ranking (SQLite stage: plain python scoring, no vectors)
# ---------------------------------------------------------------------------

def rank_items(items, query: str, entity: Optional[str] = None,
               project: Optional[str] = None):
    """Rank knowledge item dicts.

    Priority (high->low): exact entity -> project match -> url/domain/title
    -> tags/category -> searchable_text/summary -> recency (stable tie-break).
    """
    q = normalize_token(query)
    qwords = set(q.split()) if q else set()
    entity_norm = normalize_token(entity) if entity else ""
    project_norm = normalize_token(project) if project else ""
    scored = []
    for it in items:
        s = 0
        names = [normalize_token(e.get("name") or "") for e in it.get("entities") or []]
        name_types = {normalize_token(e.get("type") or "") for e in it.get("entities") or []}

        # 1. exact entity
        if entity_norm and any(_word_stems_close(entity_norm, n) for n in names):
            s += 120
        if not entity_norm:
            for n in names:
                if n and (n in qwords or any(_word_stems_close(w, n) for w in qwords)):
                    s += 90
        # entity TYPE in query ("собака", "кот")
        if name_types and any(t and t in qwords for t in name_types):
            s += 40

        # 2. project match
        pid = normalize_token(it.get("project_id") or "")
        if project_norm and pid == project_norm:
            s += 60
        elif project_norm and pid and _word_stems_close(
            (pid.split()[0] if pid.split() else pid),
            (project_norm.split()[0] if project_norm.split() else project_norm),
        ):
            s += 40

        # 3. url / domain / title
        ql = q.lower()
        for u in it.get("urls") or []:
            ul = u.lower()
            if ql and ql in ul:
                s += 45
            if any(w and w in ul for w in qwords):
                s += 25
        tl = (it.get("title") or "").lower()
        if ql and tl and ql in tl:
            s += 35
        if any(w and w in tl for w in qwords):
            s += 20

        # 4. tags / category
        tags = [(t or "").lower() for t in it.get("tags") or []]
        if any(w and w in tags for w in qwords):
            s += 20
        cat = (it.get("category") or "").lower()
        if cat and (cat in qwords or (ql and ql in cat)):
            s += 15

        # 5. searchable_text / summary
        hay = normalize_token((it.get("searchable_text") or "") + " " + (it.get("summary") or ""))
        if ql and ql in hay:
            s += 12
        elif any(w and w in hay for w in qwords):
            s += 8

        scored.append((s, it.get("id") or 0, it))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [it for _, _, it in scored]

# ---------------------------------------------------------------------------
# compact output for the LLM (section 13 of the stage brief)
# ---------------------------------------------------------------------------

def compact_item(item: dict, files: Optional[list] = None) -> dict:
    """A small, LLM-friendly representation of a knowledge item."""
    files = files or []
    return {
        "id": item.get("id"),
        "title": item.get("title") or "",
        "summary": (item.get("summary") or "")[:600],
        "category": item.get("category") or "",
        "project": item.get("project_id"),
        "content_type": item.get("content_type") or "",
        "urls": item.get("urls") or [],
        "entities": item.get("entities") or [],
        "tags": item.get("tags") or [],
        "created_at": item.get("created_at") or "",
        "has_files": bool(files),
        "files": [
            {
                "file_id": (f or {}).get("id"),
                "original_name": (f or {}).get("original_name"),
                "mime_type": (f or {}).get("mime_type"),
                "telegram_file_id": (f or {}).get("telegram_file_id") or "",
            }
            for f in files[:5]
        ],
    }


# ---------------------------------------------------------------------------
# project normalization / fuzzy lookup (never creates new projects)
# ---------------------------------------------------------------------------

def normalize_project(raw) -> str:
    """Strict normalization used before any match/filter."""
    s = normalize_token(raw)
    if not s:
        return ""
    words = s.split()
    if words:
        words[0] = words[0].capitalize()
    return " ".join(words)


def resolve_project(store: "KnowledgeStore", chat_id: int, raw_project: Optional[str]) -> Optional[str]:
    """Resolve a user-stated project to an EXISTING project_id.

    Exact match first, then fuzzy (difflib + stem) over the user's existing
    project_ids. Returns None when nothing plausibly matches — never invents.
    """
    raw = (raw_project or "").strip()
    if not raw:
        return None
    with store._connect() as c:
        rows = c.execute(
            "SELECT DISTINCT project_id FROM knowledge_items "
            "WHERE chat_id=? AND project_id IS NOT NULL AND project_id<>''",
            (chat_id,),
        ).fetchall()
    existing = sorted({r["project_id"] for r in rows})
    if not existing:
        return None

    norm = normalize_token(raw)
    for pid in existing:
        if normalize_token(pid) == norm:
            return pid
    for pid in existing:
        if tokens_stems_close(raw, pid):
            return pid
    close = difflib.get_close_matches(norm, [normalize_token(p) for p in existing], n=1, cutoff=0.78)
    if close:
        target = close[0]
        for pid in existing:
            if normalize_token(pid) == target:
                return pid
    return None

# ---------------------------------------------------------------------------
# core retrieval
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

#: tokens that carry little retrieval value (stop-words / generic intent)
_SEARCH_STOPWORDS = {
    "покажи", "найди", "дай", "отправь", "показ", "фото", "фотку", "скрин",
    "картинку", "картинка", "изображение", "что", "есть", "было", "сохранен",
    "сохранено", "сохранял", "сохранила", "вчера", "сегодня", "позавчера",
    "последнее", "последние", "новое", "мой", "моя", "мою", "моей", "моего",
    "свою", "своего", "своей", "его", "ее", "её", "тот", "та", "это", "этот",
    "про", "у", "в", "на", "с", "со", "из", "по", "от", "при", "и", "но",
    "или", "же", "то", "не", "я", "мне", "меня", "где", "какой", "какая",
    "какие", "какое", "сайт", "база", "базу", "базы", "баз", "базам", "данные",
    "данных", "данным", "проект", "проекта", "файл", "файлы", "инфо",
    "материал", "запись", "знаешь", "помнишь", "собака", "собаку", "собаки",
    "кот", "кота", "пес", "пса", "питомец", "животное", "ресурс", "снимок",
    "экран", "сервис", "кидал", "кидала", "кинул", "хранил", "храню",
    "хранить", "для", "использовать",
}


def _meaningful_tokens(query: str) -> list:
    return [t for t in normalize_token(query).split() if t and t not in _SEARCH_STOPWORDS]


def _is_generic_query(query: str) -> bool:
    return not _meaningful_tokens(query)


def retrieve(store: "KnowledgeSearch", chat_id: int, query: str,
             project: Optional[str] = None, category: Optional[str] = None,
             entity: Optional[str] = None, limit: int = 10,
             date_from: Optional[str] = None, date_to: Optional[str] = None,
             include_recent_on_empty: bool = True):
    """Unified retrieval over the user's knowledge.

    Combines text search + entity search, ranks by intent and returns the
    requested slice. Chat-scoped by construction (isolation).
    """
    limit = max(1, min(int(limit or 10), 20))
    query = (query or "").strip()
    entity = (entity or "").strip() or None
    project = (project or "").strip() or None

    filters = {"chat_id": chat_id}
    if category:
        filters["category"] = category

    items: dict = {}
    meaningful = _meaningful_tokens(query)
    if query:
        if meaningful:
            for tok in meaningful[:8]:
                for it in store.search(tok, filters=filters, limit=30):
                    items[it["id"]] = it
                if len(items) >= 60:
                    break
        else:
            # fully generic query ("покажи что есть") -> most recent items
            latest = getattr(store, "latest", None)
            if latest:
                for it in latest(chat_id, limit=20):
                    items[it["id"]] = it
    if entity:
        for it in store.search_by_entity(entity, chat_id=chat_id, limit=20):
            items[it["id"]] = it
    elif query and not items:
        # entity/case-tolerant pass over the user's items
        for it in store.search_by_entity(query, chat_id=chat_id, limit=20):
            items[it["id"]] = it

    if meaningful:
        # stem pass against entity names (handles Тошка/Тошку, Honcho, ...)
        latest = getattr(store, "latest", None)
        if latest:
            for it in latest(chat_id, limit=100):
                names = [normalize_token(e.get("name") or "") for e in it.get("entities") or []]
                if any(_word_stems_close(t, n) for t in meaningful for n in names):
                    items[it["id"]] = it

    # date filtering
    after = _DATE_RE.search(date_from or "") and date_from[:10]
    before = _DATE_RE.search(date_to or "") and date_to[:10]
    if after or before:
        filtered = {}
        for it in items.values():
            d = (it.get("created_at") or "")[:10]
            if after and d < after:
                continue
            if before and d > before:
                continue
            filtered[it["id"]] = it
        items = filtered

    ranked = rank_items(list(items.values()), query, entity=entity, project=project)
    return ranked[:limit]