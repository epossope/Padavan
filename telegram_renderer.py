"""Safe presentation boundary for Telegram.

The LLM may use Markdown, but Telegram messages are always delivered as small,
safe HTML fragments.  User/model supplied text is escaped before formatting.
"""
from __future__ import annotations

import html
import re

SAFE_LIMIT = 3700


class TelegramRenderer:
    parse_mode = "HTML"

    @staticmethod
    def render(text: object) -> str:
        """Convert the small Markdown subset used by models to safe HTML."""
        raw = str(text or "").replace("\r\n", "\n")
        escaped = html.escape(raw, quote=False)
        escaped = re.sub(r"```(?:[\w+-]+)?\s*([\s\S]*?)```", r"<pre>\1</pre>", escaped)
        escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
        escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", escaped)
        escaped = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "<b>", escaped)
        # Close heading tags line-by-line. This intentionally supports headings
        # without exposing untrusted HTML.
        escaped = re.sub(r"(?m)^(<b>(?:(?!</b>)[^\n])*)(?=\n|$)", r"\1</b>", escaped)
        escaped = re.sub(r"(?m)^\s*[-*]\s+", "• ", escaped)
        return escaped.strip() or "Готово."

    @classmethod
    def chunks(cls, text: object, limit: int = SAFE_LIMIT) -> list[str]:
        """Split rendered HTML near paragraph/sentence boundaries.

        Tags and URLs stay intact: rendering occurs *after* splitting raw text.
        """
        raw = str(text or "").strip()
        if len(raw) <= limit:
            return [cls.render(raw)]
        out = []
        while raw:
            if len(raw) <= limit:
                out.append(cls.render(raw)); break
            window = raw[:limit]
            cuts = [window.rfind("\n\n"), window.rfind("\n"),
                    max(window.rfind(". "), window.rfind("! "), window.rfind("? "))]
            cut = max(cuts)
            if cut < limit // 2:
                cut = window.rfind(" ")
            if cut < 1:
                # A single unbroken URL/token: do not corrupt it. Telegram's
                # real limit is still far above this conservative split point.
                cut = limit
            part, raw = raw[:cut].rstrip(), raw[cut:].lstrip()
            if part:
                out.append(cls.render(part))
        return out or ["Готово."]
