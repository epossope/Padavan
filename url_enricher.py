"""Optional URL enrichment adapter, fully decoupled from ingestion.

The pipeline calls this only when a REAL url was found in the input. Any failure
(timeout / 403 / parse error / blocked host) raises ``EnrichmentError`` which the
pipeline catches: the ingestion stays successful and the item keeps its data.

Hardening:
  * SSRF protection: private / loopback / link-local / reserved addresses,
    ``localhost``, single-label "internal" hostnames and non-http(s) schemes are
    rejected BEFORE any request is made. Every redirect hop is re-validated.
  * Limits: per-request timeout, max redirects, max response bytes, Content-Type
    validation, no binary downloads.
  * ``allowed_hosts``: explicit test/debug allow-list (checked before the unsafe
    scan). It does NOT bypass the scheme check.

The class hierarchy follows the adapter pattern: ``HttpUrlEnricher`` is one
concrete provider; others can be plugged in without touching the pipeline.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

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


# ---------------------------------------------------------------------------
# URL safety (SSRF hardening) — used by every provider, not just the HTTP one
# ---------------------------------------------------------------------------

_INTERNAL_SUFFIXES = (
    ".local", ".internal", ".lan", ".corp", ".home", ".intranet", ".localhost",
)

#: content types we are willing to treat as page text
_TEXT_CONTENT_TYPES = ("text/", "application/xhtml+xml", "application/xml", "application/json")


def _ip_unsafe(ip) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def _host_static_unsafe(hostname: str) -> bool:
    """Static (no DNS) check: literal IPs, localhost, internal suffixes/names."""
    hostname = (hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return True
    # IP literal (includes 127.0.0.0/8, ::1, 10/8, 172.16/12, 192.168/16,
    # 169.254/16 link-local, reserved/multicast/unassigned)
    if _ip_unsafe(hostname):
        return True
    if hostname == "localhost" or hostname.endswith(_INTERNAL_SUFFIXES):
        return True
    # single-label hostname without a dot => internal name (intranet, metadata, ...)
    if "." not in hostname:
        return True
    return False


def host_unsafe(hostname: str) -> bool:
    """Full check incl. DNS resolution (defense-in-depth, used before connect)."""
    if _host_static_unsafe(hostname):
        return True
    # resolve and re-check every answer (covers DNS rebinding at check time)
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return True  # cannot resolve => conservative: treat as unsafe
    for info in infos:
        try:
            if _ip_unsafe(info[4][0]):
                return True
        except Exception:
            continue
    return False


def is_safe_url(url: str, allowed_hosts=None) -> bool:
    """Scheme + STATIC host check (no DNS).

    Used by the pipeline as a coarse pre-gate so obviously-internal URLs are
    never handed to ANY enricher. Public-looking domains are allowed here; the
    enricher itself performs the full DNS-revalidating check (assert_safe_url)
    right before connecting.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    if allowed_hosts:
        allow = {h.lower().rstrip(".") for h in allowed_hosts}
        if host in allow:
            return True
    return not _host_static_unsafe(host)


def assert_safe_url(url: str, allowed_hosts=None) -> None:
    """Full safety check (incl. DNS); raises for forbidden targets."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise EnrichmentError("unsafe_url:bad_scheme")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise EnrichmentError("unsafe_url:no_host")
    if allowed_hosts:
        allow = {h.lower().rstrip(".") for h in allowed_hosts}
        if host in allow:
            return
    if host_unsafe(host):
        raise EnrichmentError("unsafe_url:blocked_host")


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
    raw = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.I | re.S)
    raw = re.sub(r"<style.*?</style>", " ", raw_html, flags=re.I | re.S)
    return _clean_text(raw, limit)


class UrlEnricher(ABC):
    """Interface for URL enrichment providers."""

    name: str = "base"

    @abstractmethod
    def enrich(self, url: str, timeout: float = 10.0) -> UrlEnrichment:
        """Fetch and parse a URL into a UrlEnrichment or raise EnrichmentError."""


class HttpUrlEnricher(UrlEnricher):
    """Fetches the URL over HTTP(S) with SSRF + size/redirect/type limits.

    Parameters:
      headers            HTTP headers to send.
      fetch_bytes_limit  hard cap on the response body (bytes).
      max_redirects      max 3xx hops to follow (redirects are re-validated).
      timeout            default per-request timeout (seconds).
      allowed_hosts      OPTIONAL debug/test allow-list (e.g. {"127.0.0.1"}).
    """

    name = "http"

    def __init__(self, headers=None, fetch_bytes_limit: int = 2_000_000,
                 max_redirects: int = 5, timeout: float = 10.0,
                 allowed_hosts=None):
        self.headers = headers or DEFAULT_HEADERS
        self.fetch_bytes_limit = int(fetch_bytes_limit)
        self.max_redirects = int(max_redirects)
        self.timeout = float(timeout)
        self.allowed_hosts = frozenset(allowed_hosts) if allowed_hosts else None

    # -- fetching (manual redirects so every hop is re-validated) ------------
    def _fetch(self, url: str, timeout: float):
        import requests
        session = requests.Session()
        current = url
        response = None
        for _ in range(self.max_redirects + 1):
            assert_safe_url(current, self.allowed_hosts)
            response = session.get(
                current, timeout=timeout, headers=self.headers,
                allow_redirects=False, stream=True,
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise EnrichmentError("redirect_no_location")
                current = urljoin(current, location)
                continue
            break
        else:
            raise EnrichmentError("too_many_redirects")

        if response.status_code >= 400:
            status_code = response.status_code
            response.close()
            raise EnrichmentError(f"http_{status_code}")

        ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype and not any(ctype.startswith(p) for p in _TEXT_CONTENT_TYPES):
            response.close()
            raise EnrichmentError(f"not_html:{ctype or 'unknown'}")

        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.fetch_bytes_limit:
                    response.close()
                    raise EnrichmentError("too_large")
            except ValueError:
                pass
        encoding = response.encoding or "utf-8"
        raw = b""
        for chunk in response.iter_content(65536):
            raw += chunk
            if len(raw) > self.fetch_bytes_limit:
                response.close()
                raise EnrichmentError("too_large")
        response.close()
        return current, raw, encoding

    # -- public --------------------------------------------------------------
    def enrich(self, url: str, timeout=None) -> UrlEnrichment:
        timeout = self.timeout if timeout is None else timeout
        final_url, raw, encoding = self._fetch(url, timeout)
        try:
            raw_html = raw.decode(encoding, errors="replace")
        except Exception as e:
            raise EnrichmentError(f"decode_failed:{e}") from e

        metas = _metas(raw_html)
        parsed = urlparse(url)
        return UrlEnrichment(
            url=url,
            title=_extract_title(raw_html) or metas.get("og:title", "") or metas.get("twitter:title", ""),
            description=metas.get("description", "") or metas.get("og:description", "") or metas.get("twitter:description", ""),
            canonical_url=_canonical(raw_html, final_url),
            page_text=_page_text(raw_html),
            domain=parsed.netloc or urlparse(final_url).netloc,
            source=self.name,
        )