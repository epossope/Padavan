"""Shared fakes for ingestion tests (no network / no real LLM needed)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from url_enricher import EnrichmentError, UrlEnrichment  # noqa: E402


class FakeVision:
    """Stands in for VisionExtractor.extract(image_path, mime, caption) -> dict."""

    def __init__(self, payload, raise_on_call=False):
        self.payload = dict(payload or {})
        self.raise_on_call = raise_on_call
        self.calls = 0

    def extract(self, image_path, mime="image/jpeg", caption=""):
        self.calls += 1
        if self.raise_on_call:
            raise RuntimeError("vision unavailable")
        return dict(self.payload)


class FakeEnricher:
    """Stands in for UrlEnricher.enrich(url, timeout) -> UrlEnrichment.

    With ``error`` set it raises EnrichmentError to simulate failures.
    """

    def __init__(self, result=None, error=None):
        self.result = result if isinstance(result, dict) else {}
        self.error = error
        self.calls = []

    def enrich(self, url, timeout=10.0):
        self.calls.append(url)
        if self.error is not None:
            raise EnrichmentError(str(self.error))
        return UrlEnrichment(
            url=url,
            title=self.result.get("title", "Fake title"),
            description=self.result.get("description", "Fake description"),
            canonical_url=url,
            page_text="Fake page text about the resource.",
            domain="example.com",
            source="fake",
        )


class RecordingActionRunner:
    """Records calls to execute_tool-like actions and returns ok results."""

    def __init__(self):
        self.calls = []

    def __call__(self, chat_id, tool_name, args):
        self.calls.append({"chat_id": chat_id, "tool": tool_name, "args": dict(args or {})})
        return {"ok": True, "tool": tool_name, "id": len(self.calls), **dict(args or {})}


class SqlFileSaver:
    """A real-ish file_saver that inserts rows into the same SQLite `files` table."""

    def __init__(self, store):
        self.store = store

    def __call__(self, chat_id, original_name, mime_type, local_path, kind, summary):
        with self.store._connect() as c:
            cur = c.execute(
                """INSERT INTO files
                   (chat_id, telegram_file_id, original_name, mime_type, local_path,
                    kind, summary, extracted_text, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (chat_id, "", original_name or "", mime_type or "", local_path or None,
                 kind or "image", summary or "", "", datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid