"""Optional URL enrichment adapter, fully decoupled from ingestion.

The pipeline calls this only when a REAL url was found in the input. Any failure
(timeout / 403 / parse error) raises ``EnrichmentError`` which the pipeline
catches: the ingestion stays successful and the item keeps its data, only its
status changes (``enrichment_partial`` / ``enrichment_failed``).

The class hierarchy follows the adapter pattern: ``HttpUrlEnricher`` is one
concrete provider; others (Jina reader, custom fetcher, …) can be plugged in the
same way without touching the pipeline.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlparse

try:  # lxml is an optional dependency for nicer page-text extraction
    from lxml import html as _lxml_html
    HAS_LXML = True
except Exception:  # pragma: no cover - fallback path
    HAS_LXML = False


class EnrichmentError(Exception):
    """Raised when URL enrichment cannot be completed. Ingestion is unaffected."""


@dataclass
class UrlEnrichment:
    url: str
    title: str = ""
    description: str = ""
    canonical_url: str = ""
    page_text: str = ""
    domain: str = ""
    source: str = ""


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NoemaBot/1.0 (research/archive)"
    ),
    "Accept-Language": "ru,en;q=0.8",
}


class UrlEnricher(ABC):
    """Interface for URL enrichment providers."""

    name: str = "base"

    @abstractmethod
    def enrich(self, url: str, timeout: float = 10.0) -> UrlEnrichment:
        """Fetch and parse a URL into a UrlEnrichment or raise EnrichmentError."""


# ---------------------------------------------------------------------------
# HTML helpers (regex-first, lxml optional)
# ---------------------------------------------------------------------------

def _clean_text(raw: str, limit: int = 4000) -> str:
    if not raw:
        return ""
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()[:limit]


def _extract_title(raw_html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.I | re.S)
    return _clean_text(m.group(1), 300) if m else ""


def _metas(raw_html: str) -> dict:
    out = {}
    for m in re.finditer(r"<meta[^>]*>", raw_html, re.I):
        tag = m.group(0)
        nm = re.search(r'(?:name|property|itemprop)=["\']([^"\']+)["\']', tag, re.I)
        ct = re.search(r'content=["\']([^"\']*)["\']', tag, re.I)
        if nm and ct:
            out.setdefault(nm.group(1).lower(), _clean_text(ct.group(1), 1000))
    return out


def _canonical(raw_html: str, fallback: str) -> str:
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]*>', raw_html, re.I)
    href = re.search(r'href=["\']([^"\']+)["\']', m.group(0), re.I) if m else None
    return (href.group(1).strip() if href else "") or fallback


def _page_text(raw_html: str, limit: int = 4000) -> str:
    if HAS_LXML:
        try:
            doc = _lxml_html.fromstring(raw_html)
            for tag in ("script", "style", "noscript"):
                for node in doc.xpath(f"//{tag}"):
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
            return _clean_text(doc.text_content(), limit)
        except Exception:
            pass
    # regex fallback: strip scripts and tags
    raw = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.I | re.S)
    raw = re.sub(r"<style.*?</style>", " ", raw, flags=re.I | re.S)
    return _clean_text(raw, limit)


# ---------------------------------------------------------------------------
# HTTP provider
# ---------------------------------------------------------------------------

class HttpUrlEnricher(UrlEnricher):
    """Fetches the URL over HTTP(S) and extracts title/description/text."""

    name = "http"

    def __init__(self, headers: dict | None = None, fetch_bytes_limit: int = 2_000_000):
        self.headers = headers or DEFAULT_HEADERS
        self.fetch_bytes_limit = fetch_bytes_limit

    def enrich(self, url: str, timeout: float = 10.0) -> UrlEnrichment:
        import requests
        try:
            r = requests.get(
                url,
                timeout=timeout,
                headers=self.headers,
                allow_redirects=True,
            )
            r.raise_for_status()
        except Exception as e:
            raise EnrichmentError(f"fetch_failed:{e}") from e

        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype and not ctype.startswith("text/"):
            raise EnrichmentError(f"not_html:{ctype or 'unknown'}")

        try:
            raw_html = r.text or ""
        except Exception as e:
            raise EnrichmentError(f"read_failed:{e}") from e

        if r.content and len(r.content) > self.fetch_bytes_limit:
            raw_html = raw_html[: self.fetch_bytes_limit]

        final_url = r.url or url
        parsed = urlparse(url)
        metas = _metas(raw_html)
        return UrlEnrichment(
            url=url,
            title=_extract_title(raw_html) or metas.get("og:title", "") or metas.get("twitter:title", ""),
            description=metas.get("description", "") or metas.get("og:description", "") or metas.get("twitter:description", ""),
            canonical_url=_canonical(raw_html, final_url),
            page_text=_page_text(raw_html),
            domain=parsed.netloc or urlparse(final_url).netloc,
            source=self.name,
        )