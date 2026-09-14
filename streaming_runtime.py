"""Provider-neutral primitives shared by Telegram, Mini App and voice runtimes."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field


TOOL_PACKS = {
    "core_memory": {"knowledge_search", "knowledge_get"},
    "planning": {"add_task", "get_today_plan", "delete_task", "set_reminder", "delete_reminder"},
    "finance": {"add_expense", "add_income", "update_last_expense", "get_expenses", "delete_expense"},
    "people": {"person_upsert", "person_interaction", "get_people", "delete_person", "delete_interaction"},
    "web": {"internet_search", "get_weather"},
    "files": {"knowledge_files", "send_stored_image", "get_files"},
    "preferences": {"set_timezone", "set_briefing_preferences", "save_behavior_rule", "update_behavior_rule", "delete_behavior_rule"},
}


_REASONING_TAG = re.compile(r"^<\s*(/?)\s*(think|analysis|reasoning)\b[^>]*>$", re.IGNORECASE)
_REASONING_TAG_PREFIXES = (
    "<think", "</think", "<analysis", "</analysis", "<reasoning", "</reasoning",
)


class VisibleContentFilter:
    """Remove provider reasoning tags without leaking split streaming chunks."""

    def __init__(self):
        self.hidden_depth = 0
        self.tag_buffer = ""
        self.finished = False
        self.reasoning_tags_seen = 0

    def feed(self, value: str) -> str:
        if self.finished or not isinstance(value, str) or not value:
            return ""
        visible = []
        for char in value:
            if self.tag_buffer:
                self.tag_buffer += char
                if char == ">":
                    tag, self.tag_buffer = self.tag_buffer, ""
                    match = _REASONING_TAG.match(tag)
                    if match:
                        self.reasoning_tags_seen += 1
                        if match.group(1):
                            self.hidden_depth = max(0, self.hidden_depth - 1)
                        else:
                            self.hidden_depth += 1
                    elif self.hidden_depth == 0:
                        visible.append(tag)
                elif len(self.tag_buffer) > 160:
                    if self.hidden_depth == 0:
                        visible.append(self.tag_buffer)
                    self.tag_buffer = ""
            elif char == "<":
                self.tag_buffer = char
            elif self.hidden_depth == 0:
                visible.append(char)
        return "".join(visible)

    def finish(self) -> str:
        if self.finished:
            return ""
        self.finished = True
        tail, self.tag_buffer = self.tag_buffer, ""
        if self.hidden_depth or not tail:
            return ""
        compact = re.sub(r"\s+", "", tail).lower()
        if any(prefix.startswith(compact) or compact.startswith(prefix) for prefix in _REASONING_TAG_PREFIXES):
            return ""
        return tail


def sanitize_visible_content(value: str) -> str:
    content_filter = VisibleContentFilter()
    return content_filter.feed(str(value or "")) + content_filter.finish()


def sanitize_assistant_message(message: dict) -> dict:
    """Keep tool calls and final content, but discard provider-only reasoning."""
    clean = dict(message or {})
    for field in ("reasoning", "reasoning_details", "analysis", "thinking"):
        clean.pop(field, None)
    clean["content"] = sanitize_visible_content(clean.get("content") or "")
    return clean


def assistant_reasoning_contract_violated(message: dict) -> bool:
    """Detect only machine-readable reasoning; never guess from prose."""
    source = message or {}
    if any(source.get(field) for field in ("reasoning", "reasoning_details", "analysis", "thinking")):
        return True
    content_filter = VisibleContentFilter()
    content_filter.feed(str(source.get("content") or ""))
    content_filter.finish()
    return content_filter.reasoning_tags_seen > 0


class ToolPackResolver:
    """Cheap conservative routing: no extra LLM request and no lost common actions."""
    RULES = {
        "planning": ("задач", "напом", "план", "встреч", "календар"),
        "finance": ("руб", "расход", "доход", "бюджет", "купил", "потрат"),
        "people": ("контакт", "человек", "день рождения", "познаком", "созвон"),
        "web": ("интернет", "найди", "проверь", "погода", "новост", "сайт"),
        "files": ("файл", "фото", "скрин", "документ", "отправ"),
        "preferences": ("настрой", "правило", "часовой пояс", "брифинг"),
    }

    def select_names(self, text: str) -> set[str]:
        lowered = str(text or "").lower()
        packs = {"core_memory", "planning"}
        for pack, words in self.RULES.items():
            if any(word in lowered for word in words):
                packs.add(pack)
        return set().union(*(TOOL_PACKS[name] for name in packs))

    def resolve(self, tools: list[dict], text: str) -> list[dict]:
        names = self.select_names(text)
        selected = [tool for tool in tools if tool.get("function", {}).get("name") in names]
        return selected or tools


@dataclass
class StreamAccumulator:
    content: list[str] = field(default_factory=list)
    calls: dict[int, dict] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    visible_filter: VisibleContentFilter = field(default_factory=VisibleContentFilter, repr=False)
    reasoning_chunks_dropped: int = 0
    visible_chars: int = 0
    reasoning_contract_violated: bool = False

    def add(self, payload: dict) -> list[str]:
        if payload.get("usage"):
            self.usage = payload["usage"]
        choice = (payload.get("choices") or [{}])[0]
        self.finish_reason = choice.get("finish_reason") or self.finish_reason
        delta = choice.get("delta") or {}
        emitted = []
        # OpenRouter-compatible providers can put chain-of-thought in these
        # fields.  They are intentionally never normalised into content.
        if any(delta.get(name) for name in ("reasoning", "reasoning_details", "analysis", "thinking")):
            self.reasoning_chunks_dropped += 1
            self.reasoning_contract_violated = True
        content = delta.get("content")
        if isinstance(content, str) and content:
            tags_before = self.visible_filter.reasoning_tags_seen
            visible = self.visible_filter.feed(content)
            if self.visible_filter.reasoning_tags_seen > tags_before:
                self.reasoning_contract_violated = True
            if visible:
                self.content.append(visible)
                emitted.append(visible)
                self.visible_chars += len(visible)
            elif self.visible_filter.hidden_depth or self.visible_filter.tag_buffer:
                self.reasoning_chunks_dropped += 1
        for part in delta.get("tool_calls") or []:
            index = int(part.get("index", 0))
            call = self.calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if part.get("id"):
                call["id"] = part["id"]
            fn = part.get("function") or {}
            if fn.get("name"):
                call["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                call["function"]["arguments"] += fn["arguments"]
        return emitted

    def finish(self) -> str:
        tail = self.visible_filter.finish()
        if tail:
            self.content.append(tail)
            self.visible_chars += len(tail)
        return tail

    def message(self) -> dict:
        self.finish()
        result = {"role": "assistant", "content": "".join(self.content)}
        if self.calls:
            result["tool_calls"] = [self.calls[i] for i in sorted(self.calls)]
        return result


def iter_sse_json(lines):
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


class SentenceChunker:
    def __init__(self, max_chars=220, first_chars=28):
        self.buffer = ""
        self.max_chars = max_chars
        self.first_chars = first_chars
        self.emitted = False

    def _take(self, end):
        value = self.buffer[:end].strip()
        self.buffer = self.buffer[end:].lstrip()
        if value:
            self.emitted = True
        return value

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        output = []
        while True:
            match = re.search(r"(?<=[.!?…])\s+", self.buffer)
            if match:
                output.append(self._take(match.end()))
            elif not self.emitted and len(self.buffer) >= self.first_chars:
                # Start the first synthesis before a long sentence finishes,
                # but never cut an in-flight word. Markdown is cleaned by the
                # speech adapter before this chunk reaches a TTS provider.
                cap = min(len(self.buffer), 72)
                split = max(self.buffer.rfind(mark, 0, cap + 1) for mark in (" ", ", ", "; ", ": "))
                if split < max(1, self.first_chars - 8):
                    break
                output.append(self._take(split + 1))
            elif len(self.buffer) >= self.max_chars:
                split = self.buffer.rfind(" ", 0, self.max_chars)
                if split <= self.max_chars // 2:
                    break
                output.append(self._take(split + 1))
            else:
                break
        return [x for x in output if x]

    def flush(self) -> list[str]:
        tail = self.buffer.strip()
        self.buffer = ""
        return [tail] if tail else []


class AdaptiveDraftThrottle:
    """Rate-limit Telegram draft updates while keeping short answers responsive."""

    def __init__(self, min_interval=0.8, max_interval=1.2, min_chars=24, clock=None):
        self.min_interval = float(min_interval)
        self.max_interval = float(max_interval)
        self.min_chars = int(min_chars)
        self.clock = clock or time.monotonic
        self.last_at = 0.0
        self.last_size = 0

    def should_send(self, text: str, *, force=False) -> bool:
        now = self.clock()
        size = len(text or "")
        elapsed = now - self.last_at
        growth = size - self.last_size
        if not force and elapsed < self.min_interval:
            return False
        if not force and growth < self.min_chars and elapsed < self.max_interval:
            return False
        self.last_at = now
        self.last_size = size
        return True
