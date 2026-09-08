"""Universal Ingestion Pipeline for Noema.

One pipeline for ANY input (photos, screenshots, documents, receipts, notes):

    input -> normalize -> extract -> understand -> resolve context -> store
          -> embed -> enrichment (optional) -> actions (optional)

Design principles implemented here:
  * No dedicated scenarios for checks / sites / dogs / screenshots / notes.
  * INGESTION != ACTION: data is first understood and persisted, only then the
    user's explicit intent may trigger domain tools (add_expense, add_task, ...).
  * URL enrichment is optional and never affects the ingestion result.
  * No hard coupling to a specific vision model or to bot.py: vision, file
    saving, actions and enrichment are injected callables/interfaces.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from knowledge_store import EmbeddingProvider, KnowledgeItem, KnowledgeStore, NullEmbeddingProvider
from url_enricher import EnrichmentError, UrlEnricher


class ExtractionError(Exception):
    """Raised when structured vision extraction cannot produce valid JSON."""


# ---------------------------------------------------------------------------
# input / output models
# ---------------------------------------------------------------------------

@dataclass
class Attachment:
    file_id: str = ""
    local_path: Optional[str] = None
    mime_type: str = ""
    kind: str = "image"
    original_name: str = ""


@dataclass
class IngestionInput:
    chat_id: int
    message_id: int
    user_text: str = ""
    attachments: list = field(default_factory=list)
    timestamp: Optional[datetime] = field(default_factory=lambda: datetime.now(timezone.utc))
    conversation_context: str = ""
    reply_to_text: str = ""
    project_hint: Optional[str] = None


@dataclass
class ExtractionResult:
    content_type: str = "text"
    title: str = ""
    summary: str = ""
    visible_text: str = ""
    urls: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    category: str = ""
    project_hint: Optional[str] = None
    searchable_text: str = ""
    confidence: float = 0.5
    raw: dict = field(default_factory=dict)
    used_vision: bool = False


@dataclass
class IngestionResult:
    ok: bool
    status: str  # stored | duplicate | partial | enrichment_failed | enrichment_partial | failed
    item_id: Optional[int] = None
    duplicate: bool = False
    item: Optional[dict] = None
    project_id: Optional[str] = None
    urls: list = field(default_factory=list)
    enrichment: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    reply: str = ""


# ---------------------------------------------------------------------------
# deterministic URL parsing — never invent a URL that is not in the input text
# ---------------------------------------------------------------------------

TEXT_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'\[\]{}]+", re.I)

KNOWN_TLDS = {
    "com", "net", "org", "io", "dev", "ai", "tv", "me", "info", "xyz",
    "online", "site", "su", "pro", "app", "tech", "ru", "рф", "by", "kz", "ua",
}

DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:" + "|".join(KNOWN_TLDS) + r")(?:/[^\s<>\"'\]})]*)?",
    re.I,
)


def _normalize_url(tok: str) -> str:
    tok = tok.strip()
    while tok and tok[-1] in ".,;:!?»«\"'":
        tok = tok[:-1]
    while tok.endswith(")") and tok.count(")") > tok.count("("):
        tok = tok[:-1]
    if tok.endswith("("):
        tok = tok[:-1]
    if tok.lower().startswith("www."):
        tok = "http://" + tok
    return tok[:2048]


def _skip_token(all_text: str, tok: str, start: int) -> bool:
    """Heuristics: skip decimals / version-like tokens and fragments of longer urls."""
    if re.match(r"^\d{1,3}[.,]\d{1,3}$", tok):
        return True  # looks like a decimal number
    first = tok.split(".")[0]
    if re.match(r"^\d+$", first) and tok.lower().count(".") >= 1 and not tok.lower().startswith(("http", "www")):
        return True  # version-like token (1.2.3)
    if start > 0 and all_text[max(0, start - 8):start].lower().endswith(("http://", "https://", "://", "www.")):
        return True  # already covered by TEXT_URL_RE
    return False


def parse_urls(*texts: str) -> list:
    """Extract real URLs present in the supplied texts (order-preserving, dedup)."""
    all_text = " ".join(t or "" for t in texts if t)
    found = []
    for m in TEXT_URL_RE.finditer(all_text):
        tok = _normalize_url(m.group(0))
        if tok:
            found.append(tok)
    for m in DOMAIN_RE.finditer(all_text):
        tok = _normalize_url(m.group(0))
        if not tok or _skip_token(all_text, tok, m.start()):
            continue
        if tok.lower().startswith(("http://", "https://", "www.")):
            continue  # handled above
        found.append("http://" + tok)
    seen, out = set(), []
    for u in found:
        key = u.lower().rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out

# ---------------------------------------------------------------------------
# extraction validation / normalization
# ---------------------------------------------------------------------------

CONTENT_TYPES = {
    "photo", "image", "picture", "document", "text", "url", "web", "screenshot",
    "recording", "receipt", "invoice", "mixed",
}


def build_searchable(title, summary, visible_text, tags, entities, user_text="") -> str:
    parts = [title or "", summary or "", visible_text or "", user_text or ""]
    parts += [str(t) for t in (tags or [])]
    parts += [str(e.get("name")) for e in (entities or []) if isinstance(e, dict) and e.get("name")]
    return " ".join(p for p in parts if p and str(p).strip()).strip()[:8000]


def normalize_extraction(raw: Any, caption: str = "") -> ExtractionResult:
    raw = raw if isinstance(raw, dict) else {}
    ct = str(raw.get("content_type") or "").strip().lower()
    if ct not in CONTENT_TYPES:
        ct = "mixed" if (raw.get("visible_text") or raw.get("urls") or caption) else "text"

    entities = []
    for e in (raw.get("entities") or []):
        if isinstance(e, dict) and str(e.get("name") or "").strip():
            entities.append({
                "type": str(e.get("type") or "other").strip() or "other",
                "name": str(e["name"]).strip()[:120],
            })

    visible_text = str(raw.get("visible_text") or "").strip()
    title = str(raw.get("title") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    tags = [str(t).strip().lower() for t in (raw.get("tags") or []) if str(t).strip()]
    objects = [str(o).strip() for o in (raw.get("objects") or []) if str(o).strip()]

    # final urls = deterministic parse of the input material only
    source_text = " ".join(filter(None, [visible_text, caption, title, summary]))
    urls = parse_urls(source_text)

    conf = float(raw.get("confidence") or 0)
    confidence = max(0.0, min(1.0, conf)) if conf else 0.5

    category = str(raw.get("category") or "").strip()
    project_hint = str(raw.get("project_hint") or "").strip() or None

    return ExtractionResult(
        content_type=ct,
        title=title,
        summary=summary,
        visible_text=visible_text,
        urls=urls,
        entities=entities,
        objects=objects,
        tags=tags,
        category=category,
        project_hint=project_hint,
        searchable_text=build_searchable(title, summary, visible_text, tags, entities, caption),
        confidence=confidence,
        raw=raw,
        used_vision=True,
    )


def infer_category(ex: ExtractionResult, user_text: str) -> str:
    if ex.category:
        return ex.category
    hay = f"{ex.visible_text} {user_text}".lower()
    if any(w in hay for w in ("чек", "итого", "касса", "к оплате", "нал.")):
        return "receipt"
    if any(w in hay for w in ("собака", "кот", "питомец", "кошка")):
        return "pet"
    if ex.content_type in ("screenshot", "web") or any(e.get("type") == "url" for e in ex.entities):
        return "web_resource"
    return ex.content_type or "note"


_STOPWORDS = {
    "это", "моя", "мой", "мои", "моё", "для", "как", "на", "по", "про", "в", "и",
    "с", "со", "у", "не", "что", "зачем", "сохрани", "покажи", "найди", "этот",
    "эта", "из", "от", "при", "или", "но", "же", "то",
}


def derive_tags(ex: ExtractionResult, user_text: str) -> list:
    tags = list(ex.tags)
    words = re.findall(r"[А-Яа-яЁёA-Za-z0-9]{4,}", (user_text or "").lower())
    for w in words:
        if w not in _STOPWORDS and w not in tags:
            tags.append(w)
    return list(dict.fromkeys(tags))[:12]

# ---------------------------------------------------------------------------
# project / context resolution — projects are NEVER auto-created
# ---------------------------------------------------------------------------

PROJECT_PREFIX_PATTERN = re.compile(
    r"(?:проект(?:[еау]|ом|ов)?\s*[:\-]?\s*|для\s+проекта\s+|в\s+проекте\s+)"
)


def _find_project_tail(text):
    """Case-insensitive prefix find, strict-uppercase first capture letter.

    The whole regex cannot be IGNORECASE, otherwise the mandatory uppercase
    first letter of a project name (НоЕма vs омBeta) becomes meaningless.
    """
    text = text or ""
    low = text.lower()
    for m in PROJECT_PREFIX_PATTERN.finditer(low):
        tail = text[m.end():].lstrip(" :\t-")
        if tail and tail[0].isupper():
            return tail
    return None


class ProjectResolver:
    """Resolution order: explicit -> active project -> context -> None.

    Projects are NEVER created automatically: only strings found in the input /
    context (or the active project) can be returned.
    """

    def __init__(self, get_active_project=None):
        self.get_active_project = get_active_project  # callable(chat_id) -> str|None

    @staticmethod
    def _clean(name) -> Optional[str]:
        name = (name or "").strip().strip(".,!?;:»«\"' ")
        return (name[:80] or None)

    @staticmethod
    def _project_name(raw) -> Optional[str]:
        """Take the first word + any subsequent Capitalized words ("Noema Studio"),
        stop at lowercase words ("Noema как ресурс..." -> "Noema")."""
        raw = (raw or "").strip()
        if not raw:
            return None
        words = re.split(r"\s+", raw)
        keep = [words[0]]
        for w in words[1:]:
            if w and w[0].isupper():
                keep.append(w)
            else:
                break
        return ProjectResolver._clean(" ".join(keep))

    def resolve(self, chat_id, user_text="", conversation_context="", project_hint=None) -> Optional[str]:
        # 1. explicitly stated by the user
        tail = _find_project_tail(user_text)
        if tail:
            name = self._project_name(tail)
            if name:
                return name
        if project_hint and str(project_hint).strip():
            return self._clean(str(project_hint))
        # 2. active project from context
        if self.get_active_project is not None:
            try:
                active = self.get_active_project(chat_id)
                if active and str(active).strip():
                    return self._clean(str(active))
            except Exception:
                pass
        # 3. obvious project mention in the conversation
        tail = _find_project_tail(conversation_context)
        if tail:
            name = self._project_name(tail)
            if name:
                return name
        # 4. unresolved
        return None


# ---------------------------------------------------------------------------
# user intent (action separation: nothing here runs before storage)
# ---------------------------------------------------------------------------

INTENT_EXPENSE_RE = re.compile(
    r"(добавь|запиши|внеси|отметь|учти|занеси)\s+.*(расход|трат|покупк|чек|потрат)"
    r"|(потратил[аи]?|потратили)\s+\d",
    re.I,
)
INTENT_TASK_RE = re.compile(r"(добавь|создай|поставь|запиши)\s+.*(задач)", re.I)
INTENT_NOTE_RE = re.compile(r"сохрани\s+(в\s+замет|заметку|это)|запомни\s+(это|инфо|данные|факт|следующ)", re.I)
INTENT_REMINDER_RE = re.compile(r"напомни\s+мне|напомни\s+$|сделай\s+напоминание", re.I)
INTENT_PERSON_RE = re.compile(r"это\s+(мой|моя|мои|моё|наш|наша|наше)\s+", re.I)


def analyze_intents(user_text: str) -> list:
    t = user_text or ""
    intents = []
    if INTENT_EXPENSE_RE.search(t):
        intents.append("expense")
    if INTENT_TASK_RE.search(t):
        intents.append("task")
    if INTENT_NOTE_RE.search(t):
        intents.append("note")
    if INTENT_REMINDER_RE.search(t):
        intents.append("reminder")
    if INTENT_PERSON_RE.search(t):
        intents.append("person")
    return intents

def extract_money(*texts: str):
    """Return (amount, context_hint) from text if a real amount is present."""
    hay = " ".join(t or "" for t in texts).replace("\u00a0", " ")
    m = re.search(r"(\d[\d\s]*[.,]?\d{0,2}|[.,]\d{1,2})\s*(?:₽|руб(?:лей|ля)?|р\.?)", hay, re.I)
    if m:
        try:
            amount = float(m.group(1).replace(" ", "").replace(",", "."))
        except ValueError:
            amount = None
        if amount:
            return round(amount, 2), hay[:160]
    m = re.search(r"(?:итого|сумма|к оплате|всего|total)[:;]?\s*(\d[\d\s]*[.,]?\d{0,2})", hay, re.I)
    if m:
        try:
            amount = float(m.group(1).replace(" ", "").replace(",", "."))
        except ValueError:
            amount = None
        if amount:
            return round(amount, 2), hay[:160]
    amounts = re.findall(r"(?<![\d.,])(\d{1,7}[.,]\d{1,2})(?![\d.,])", hay)
    if amounts:
        vals = sorted((float(a.replace(",", ".")) for a in amounts), reverse=True)
        return vals[0], hay[:160]
    return None, hay[:160]


def pick_entity_name(entities) -> Optional[str]:
    for pref in ("person", "pet", "animal", "human"):
        for e in entities:
            if e.get("type") == pref:
                return e.get("name")
    return None


class ActionBuilder:
    """Turns explicit user intents into domain tool calls AFTER storage.

    ``action_runner(chat_id, tool_name, args) -> result dict`` is injected by the
    host application (bot.py uses execute_tool). Without a runner, actions are
    only planned and returned.
    """

    def __init__(self, action_runner=None):
        self.action_runner = action_runner

    def build(self, chat_id, intents, ex: ExtractionResult, project_id=None) -> list:
        actions = []
        for intent in intents:
            name, args = self._plan(ex, intent, project_id)
            if not name:
                continue
            if self.action_runner is None:
                actions.append({"tool": name, "args": args, "planned": True})
                continue
            try:
                res = self.action_runner(chat_id, name, args)
                actions.append({"tool": name, "args": args, "result": res if isinstance(res, dict) else {"ok": bool(res)}})
            except Exception as e:
                actions.append({"tool": name, "args": args, "error": str(e)})
        return actions

    def _plan(self, ex, intent, project_id):
        if intent == "expense":
            amount, hint = extract_money(ex.visible_text, ex.title, ex.summary)
            if amount:
                return "add_expense", {
                    "amount": amount, "currency": "RUB",
                    "category": ex.category or "прочее",
                    "description": (ex.title or ex.category or hint)[:120],
                    "merchant": "", "spent_at": "",
                }
        if intent == "task":
            text = ex.summary or ex.title or ex.visible_text[:200]
            if text:
                return "add_task", {"text": text[:300], "due_date": "", "priority": "normal"}
        if intent == "note":
            text = " | ".join(filter(None, [ex.title, ex.summary, ex.visible_text[:400]]))
            if text:
                return "save_note", {"text": text[:2000], "title": (ex.title or ex.content_type)[:60]}
        if intent == "reminder":
            text = ex.summary or ex.title or ex.visible_text[:200]
            if text:
                return "set_reminder", {
                    "text": text[:300],
                    "remind_at": datetime.now(timezone.utc).isoformat(),
                }
        if intent == "person":
            name = pick_entity_name(ex.entities)
            if name:
                notes = " | ".join(filter(None, [ex.title, ex.summary, ex.visible_text[:200]]))
                return "person_upsert", {"name": name, "notes": notes[:400]}
        return None, None

# ---------------------------------------------------------------------------
# VisionExtractor — structured JSON extraction over the existing vision layer
# ---------------------------------------------------------------------------

_EXTRACTION_SCHEMA_HINT = (
    "Жёсткий JSON без markdown и пояснений, со следующими полями:\n"
    '{"content_type": "photo|document|text|url|web|screenshot|receipt|mixed",\n'
    ' "title": "короткое название",\n'
    ' "summary": "краткое описание (1-3 предложения)",\n'
    ' "visible_text": "ВЕСЬ видимый текст дословно или пустая строка",\n'
    ' "urls": ["http://..."],\n'
    ' "entities": [{"type": "person|pet|company|place|product|date|money|other", "name": "..."}],\n'
    ' "objects": ["..."],\n'
    ' "tags": ["..."],\n'
    ' "category": "напр. receipt, pet, web_resource, document, photo",\n'
    ' "project_hint": "вероятный проект или null",\n'
    ' "searchable_text": "все ключевые слова для поиска одной строкой",\n'
    ' "confidence": 0.0-1.0}\n'
    "URL писать ТОЛЬКО если они реально есть на изображении или в подписи. "
    "Если текста нет — visible_text '', urls []. Ничего не выдумывать."
)


class VisionExtractor:
    """Structured JSON extractor on top of the existing vision/model layer.

    request_vision(model, messages) -> response-like object with ``.ok`` and
    ``.json()`` (bot.py's ``request_vision`` satisfies this). ``models`` is an
    ordered list of vision model ids; by default it lazily reads bot.py config.
    """

    def __init__(self, request_vision=None, models: Optional[list] = None):
        self.request_vision = request_vision or self._default_request_vision
        self.models = list(models) if models else None

    # -- defaults use bot.py only at call time (no circular import) ---------
    @staticmethod
    def _default_request_vision(model, messages):
        import bot
        return bot.request_vision(model, messages)

    def _default_models(self):
        import bot
        models = [bot.VISION_MODEL]
        models += [m for m in bot.VISION_FALLBACK_MODELS if m != bot.VISION_MODEL]
        return models

    # -- prompt -------------------------------------------------------------
    def _build_messages(self, image_path, mime, caption):
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        system = (
            "Ты — структурированный экстрактор Noema (Universal Ingestion). "
            "Анализируй изображение И подпись пользователя ВМЕСТЕ.\n" + _EXTRACTION_SCHEMA_HINT
        )
        content = [
            {"type": "text", "text": system},
            {"type": "image_url", "image_url": {"url": f"data:{mime or 'image/jpeg'};base64,{b64}"}},
        ]
        if caption and str(caption).strip():
            content.append({"type": "text", "text": f"Подпись пользователя: {caption}"})
        return [{"role": "user", "content": content}]

    def _extract_json(self, content):
        if isinstance(content, dict):
            return content
        s = str(content or "").strip()
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise ExtractionError("json_not_found")
        obj = json.loads(m.group(0))
        if not isinstance(obj, dict):
            raise ExtractionError("json_not_object")
        return obj

    # -- public API ---------------------------------------------------------
    def extract(self, image_path, mime="image/jpeg", caption="") -> dict:
        """Return validated raw extraction dict or raise ExtractionError."""
        last = None
        for model in (self.models or self._default_models()):
            for attempt in range(2):
                resp = self.request_vision(model, self._build_messages(image_path, mime, caption))
                if getattr(resp, "ok", False):
                    try:
                        msg = resp.json()["choices"][0]["message"]
                        return self._extract_json(msg.get("content"))
                    except Exception as e:
                        last = e
                        continue
                last = f"vision_http:{getattr(resp, 'status_code', '?')}"
        raise ExtractionError(f"vision_failed:{last}")

# ---------------------------------------------------------------------------
# IngestionPipeline — the universal orchestrator
# ---------------------------------------------------------------------------

class IngestionPipeline:
    """Universal pipeline: ingest ANY input once, store knowledge, then act.

    Injected dependencies (all decoupled, test-friendly):
        store             KnowledgeStore (implements KnowledgeSearch)
        vision_extractor  object with ``extract(image_path, mime, caption) -> dict``
        url_enricher      UrlEnricher or None (optional, never breaks ingestion)
        project_resolver  ProjectResolver
        file_saver        (chat_id, original_name, mime_type, local_path, kind, summary) -> file id
        action_builder    ActionBuilder (domain actions strictly after storage)
        storage_dir       where attachments are persisted (Path) or None
    """

    def __init__(self, store, vision_extractor=None, url_enricher=None,
                 project_resolver=None, file_saver=None, action_builder=None,
                 storage_dir=None):
        self.store = store
        self.vision_extractor = vision_extractor or VisionExtractor()
        self.url_enricher = url_enricher
        self.project_resolver = project_resolver or ProjectResolver()
        self.file_saver = file_saver
        self.action_builder = action_builder or ActionBuilder()
        self.storage_dir = Path(storage_dir) if storage_dir else None

    # -- public -------------------------------------------------------------
    def ingest(self, inp: IngestionInput) -> IngestionResult:
        try:
            return self._ingest(inp)
        except Exception as e:
            return IngestionResult(ok=False, status="failed",
                                   reply=f"Не удалось сохранить: {e}")

    # -- core ---------------------------------------------------------------
    def _ingest(self, inp: IngestionInput) -> IngestionResult:
        chat_id, mid = inp.chat_id, inp.message_id
        file_ids = [a.file_id for a in inp.attachments if a.file_id]
        hash_ = KnowledgeStore.content_hash(chat_id, mid, file_ids, inp.user_text)

        # normalize / idempotency pre-check (Telegram retry protection)
        dup = self.store.find_duplicate(chat_id, mid, hash_)
        if dup:
            return IngestionResult(
                ok=True, status="duplicate", duplicate=True, item=dup, item_id=dup.get("id"),
                project_id=dup.get("project_id"), urls=dup.get("urls") or [],
                reply=f"Уже сохраняла это (запись #{dup.get('id')}). Новая запись не создана.",
            )

        # extract
        ex = self._extract(inp)
        # understand
        ex = self._understand(inp, ex)
        # resolve context
        project_id = self.project_resolver.resolve(
            chat_id, inp.user_text, inp.conversation_context or "", inp.project_hint)

        # persist original files (kept on disk + registered in `files`)
        file_rows = self._persist_attachments(chat_id, inp, ex)

        # store
        item = KnowledgeItem(
            chat_id=chat_id, content_type=ex.content_type, title=ex.title, summary=ex.summary,
            visible_text=ex.visible_text, searchable_text=ex.searchable_text,
            urls=ex.urls, entities=ex.entities, tags=ex.tags, category=ex.category,
            metadata={
                "used_vision": ex.used_vision, "confidence": ex.confidence,
                "objects": ex.objects,
                "source": {"message_id": mid, "project_probe": project_id},
            },
            content_hash=hash_, project_id=project_id, source_message_id=mid,
            source_file_id=file_ids[0] if file_ids else None, status="stored",
        )
        created_item, is_dup = self.store.insert_item(item)
        if is_dup:
            return IngestionResult(
                ok=True, status="duplicate", duplicate=True, item=created_item,
                item_id=created_item.get("id"), project_id=project_id, urls=ex.urls,
                reply=f"Уже сохраняла это (запись #{created_item.get('id')}). Дубликат не создан.",
            )
        item_id = created_item["id"]
        for fid in file_rows:
            if fid:
                self.store.link_file(item_id, fid, role="source")

        # embed (clean interface; no-op for the SQLite/NULL provider)
        self._embed(item_id, created_item)

        # enrichment — optional, only for real urls, never breaks ingestion
        enrichment, enrich_status = [], "stored"
        if self.url_enricher is not None and ex.urls:
            enrichment, enrich_status = self._enrich(item_id, ex.urls)
            created_item = self.store.set_enrichment(item_id, enrichment, enrich_status)

        # actions — strictly AFTER storage, only on explicit user intent
        intents = analyze_intents(inp.user_text)
        actions = self.action_builder.build(chat_id, intents, ex, project_id) if intents else []

        reply = self._compose_reply(created_item, enrichment, actions, project_id)
        return IngestionResult(
            ok=True, status=enrich_status, item=created_item, item_id=item_id,
            project_id=project_id, urls=ex.urls, enrichment=enrichment,
            actions=actions, reply=reply,
        )

    def _extract(self, inp: IngestionInput) -> ExtractionResult:
        images = [a for a in inp.attachments if a.local_path and Path(a.local_path).exists()]
        if images:
            a = images[0]
            caption = inp.user_text or ""
            try:
                raw = self.vision_extractor.extract(str(a.local_path), a.mime_type or "image/jpeg", caption)
                ex = normalize_extraction(raw, caption)
                ex.used_vision = True
            except Exception:
                ex = normalize_extraction({}, caption)
                ex.used_vision = False
                ex.summary = "Не удалось проанализировать изображение; сохранено по подписи."
            # deterministic URL parse over everything that is part of the input
            ex.urls = parse_urls(ex.visible_text, caption, inp.reply_to_text, ex.title, ex.summary)
            return ex
        # text-only input
        ex = normalize_extraction({}, inp.user_text)
        ex.visible_text = inp.user_text
        ex.title = (inp.user_text or "")[:120]
        ex.summary = ""
        ex.urls = parse_urls(inp.user_text, inp.reply_to_text)
        ex.content_type = "text" if not ex.urls else "web"
        ex.used_vision = False
        return ex

    def _understand(self, inp: IngestionInput, ex: ExtractionResult) -> ExtractionResult:
        ex.category = infer_category(ex, inp.user_text)
        ex.tags = derive_tags(ex, inp.user_text)
        ex.searchable_text = build_searchable(
            ex.title, ex.summary, ex.visible_text, ex.tags, ex.entities, inp.user_text)
        return ex

    def _persist_attachments(self, chat_id, inp: IngestionInput, ex: ExtractionResult) -> list:
        """Copy incoming files into persistent storage and register them in `files`."""
        fids = []
        for a in inp.attachments:
            src = Path(a.local_path) if a.local_path else None
            dest = None
            if src and src.exists() and self.storage_dir is not None:
                dest = self._persist_copy(chat_id, inp.message_id, a, src)
            file_path = str(dest) if dest else (str(src) if src else None)
            original = a.original_name or (dest.name if dest else "attachment")
            if self.file_saver is None:
                continue
            fid = self.file_saver(chat_id, original, a.mime_type or "", file_path,
                                  a.kind or "image", (ex.summary or "")[:200])
            if fid:
                fids.append(fid)
        return fids

    def _persist_copy(self, chat_id, mid, a: Attachment, src: Path) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        name = a.original_name or src.name or "attachment"
        name = re.sub(r"[^A-Za-zА-Яа-яЁё0-9._\- ]", "_", name)
        folder = self.storage_dir / "inbox" / str(chat_id)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{ts}_{mid}_{name}"
        shutil.copy2(src, dest)
        return dest

    def _embed(self, item_id, created_item) -> None:
        """Clean embedding hook. Null provider -> no vectors are persisted in SQLite."""
        provider = getattr(self.store, "embeddings", None)
        if provider is None or isinstance(provider, NullEmbeddingProvider):
            return
        try:
            provider.embed([created_item.get("searchable_text") or ""])
        except Exception:
            pass  # embeddings are best-effort; they never fail ingestion

    def _enrich(self, item_id, urls) -> tuple:
        results, statuses = [], []
        for u in urls[:3]:
            try:
                e = self.url_enricher.enrich(u)
                results.append({
                    "url": u, "status": "ok",
                    "title": e.title, "description": e.description,
                    "canonical_url": e.canonical_url or u,
                    "domain": e.domain, "text_preview": (e.page_text or "")[:2000],
                    "source": e.source,
                })
                statuses.append(True)
            except EnrichmentError as err:
                results.append({"url": u, "status": "failed", "error": str(err)})
                statuses.append(False)
            except Exception as err:
                results.append({"url": u, "status": "failed", "error": f"unexpected:{err}"})
                statuses.append(False)
        if not statuses:
            return results, "stored"
        if all(statuses):
            return results, "enriched"
        if any(statuses):
            return results, "enrichment_partial"
        return results, "enrichment_failed"

    def _compose_reply(self, item, enrichment, actions, project_id) -> str:
        lines = []
        title = item.get("title") or item.get("summary") or "изображение"
        lines.append(f"📥 Сохранила: {title[:120]}")
        if item.get("category"):
            lines.append(f"Категория: {item.get('category')}")
        for u in (item.get("urls") or [])[:2]:
            lines.append(f"🔗 {u}")
        if project_id:
            lines.append(f"🗂 Проект: {project_id}")
        seen = []
        for e in item.get("entities") or []:
            n = e.get("name")
            if n and n not in seen:
                seen.append(n)
                lines.append(f"👤 Записала: {n}")
        if item.get("status") in ("enriched", "enrichment_partial"):
            ok_n = sum(1 for x in enrichment if x.get("status") == "ok")
            lines.append(f"🔎 Обогатила {ok_n} URL")
        elif item.get("status") == "enrichment_failed":
            lines.append("🔎 Обогащение URL не удалось (данные сохранены).")
        for action in actions:
            res = action.get("result")
            if isinstance(res, dict) and res.get("ok"):
                t = res.get("tool")
                if t == "add_expense":
                    lines.append(f'💸 Записала расход {res.get("amount")} {res.get("currency")} — {res.get("description")}')
                elif t == "save_note":
                    lines.append("📝 Заметка сохранена.")
                elif t == "add_task":
                    lines.append(f"✅ Задача: {res.get('text')}")
                elif t == "set_reminder":
                    lines.append(f"⏰ Напоминание на {res.get('local_time')}")
                elif t == "person_upsert":
                    lines.append(f"👤 Сохранила профиль: {res.get('name')}")
            elif action.get("error"):
                lines.append(f"⚠️ Не удалось вызвать {action.get('tool')}: {action.get('error')}")
        return "\n".join(lines) or "Готово."


def build_inquiry_input(result: IngestionResult) -> Optional[str]:
    """Material used later when the model answers a follow-up question."""
    if not result.ok or not result.item:
        return None
    it = result.item
    parts = [p for p in (it.get("title"), it.get("summary"), it.get("visible_text")) if p]
    body = "\n".join(parts)
    if result.urls:
        body += "\nURL: " + ", ".join(result.urls[:3])
    return ("[Сохранено в память]\n" + body) if body else None