"""Unit/integration tests for HttpUrlEnricher against a local HTTP server.

Covers the hardening requirements: SSRF blocking, redirect re-validation,
response size / content-type limits, and scheme checks.
"""
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from url_enricher import EnrichmentError, HttpUrlEnricher, host_unsafe, is_safe_url  # noqa: E402

HTML = """<!doctype html>
<html><head>
<title>Тестовая страница</title>
<meta name="description" content="Описание страницы">
<link rel="canonical" href="http://localhost/canonical">
</head><body>
<p>Текст страницы для извлечения.</p>
<script>var x = 1;</script>
</body></html>"""

BIG_BODY = b"a" * (300 * 1024)  # 300KB > default-safe fetch cap used in tests


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/403":
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/binary":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"PNG")
            return
        if self.path == "/redir-to-blocked":
            self.send_response(302)
            self.send_header("Location", "http://10.255.255.1/steal")
            self.end_headers()
            return
        if self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
            return
        if self.path == "/too-big":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(BIG_BODY)))
            self.end_headers()
            self.wfile.write(BIG_BODY)
            return
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class HttpUrlEnricherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server = None

    def setUp(self):
        # 127.0.0.1 is needed only because the test server runs there.
        self.enricher = HttpUrlEnricher(allowed_hosts={"127.0.0.1"})
        self.base = f"http://127.0.0.1:{self.port}"

    def test_enrich_ok(self):
        e = self.enricher.enrich(f"{self.base}/page")
        self.assertEqual(e.title, "Тестовая страница")
        self.assertEqual(e.description, "Описание страницы")
        self.assertEqual(e.canonical_url, "http://localhost/canonical")
        self.assertEqual(e.domain, f"127.0.0.1:{self.port}")
        self.assertIn("Текст страницы", e.page_text)
        self.assertNotIn("var x", e.page_text)  # scripts dropped

    def test_enrich_http_error(self):
        with self.assertRaises(EnrichmentError):
            self.enricher.enrich(f"{self.base}/403")

    def test_enrich_non_html(self):
        with self.assertRaises(EnrichmentError):
            self.enricher.enrich(f"{self.base}/binary")

    def test_enrich_redirect_revalidated(self):
        # initial hop is allowed (127.0.0.1), redirect target is private -> block
        with self.assertRaises(EnrichmentError) as ctx:
            self.enricher.enrich(f"{self.base}/redir-to-blocked")
        self.assertIn("unsafe_url", str(ctx.exception))

    def test_too_many_redirects(self):
        en = HttpUrlEnricher(allowed_hosts={"127.0.0.1"}, max_redirects=2)
        with self.assertRaises(EnrichmentError) as ctx:
            en.enrich(f"{self.base}/loop")
        self.assertIn("too_many_redirects", str(ctx.exception))

    def test_too_large(self):
        en = HttpUrlEnricher(allowed_hosts={"127.0.0.1"}, fetch_bytes_limit=100_000)
        with self.assertRaises(EnrichmentError) as ctx:
            en.enrich(f"{self.base}/too-big")
        self.assertIn("too_large", str(ctx.exception))


class UrlSafetyTest(unittest.TestCase):
    def test_safe_public_urls(self):
        self.assertTrue(is_safe_url("https://example.com/path"))
        self.assertTrue(is_safe_url("http://www.iana.org/"))

    def test_unsafe_schemes(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "data:text/html,x", "javascript:void(0)"):
            self.assertFalse(is_safe_url(url), url)

    def test_unsafe_loopback_and_private(self):
        for host in ("localhost", "127.0.0.1", "127.8.8.8", "10.0.0.5", "172.16.5.5",
                     "192.168.1.1", "169.254.1.1", "0.0.0.0", "::1", "[::1]", "fc00::1", "fe80::1"):
            url = f"http://{host}/x"
            self.assertFalse(is_safe_url(url), url)
            self.assertTrue(host_unsafe(host), host)

    def test_internal_hostnames(self):
        for host in ("intranet", "metadata", "internal-host", "app.internal", "db.local", "nas.home"):
            self.assertTrue(host_unsafe(host), host)
            self.assertFalse(is_safe_url(f"http://{host}/x"), host)

    def test_allowed_hosts_bypass(self):
        self.assertTrue(is_safe_url("http://127.0.0.1:8080/x", allowed_hosts={"127.0.0.1"}))
        # scheme check still applies even when allowed
        self.assertFalse(is_safe_url("file://127.0.0.1/x", allowed_hosts={"127.0.0.1"}))


if __name__ == "__main__":
    unittest.main()