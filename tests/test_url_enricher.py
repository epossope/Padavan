"""Unit/integration tests for HttpUrlEnricher against a local HTTP server."""
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from url_enricher import EnrichmentError, HttpUrlEnricher  # noqa: E402

HTML = """<!doctype html>
<html><head>
<title>Тестовая страница</title>
<meta name="description" content="Описание страницы">
<link rel="canonical" href="http://localhost/canonical">
</head><body>
<p>Текст страницы для извлечения.</p>
<script>var x = 1;</script>
</body></html>"""


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
        t = threading.Thread(target=cls.server.serve_forever, daemon=True)
        t.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server = None

    def setUp(self):
        self.enricher = HttpUrlEnricher()

    def test_enrich_ok(self):
        url = f"http://127.0.0.1:{self.port}/page"
        e = self.enricher.enrich(url)
        self.assertEqual(e.title, "Тестовая страница")
        self.assertEqual(e.description, "Описание страницы")
        self.assertEqual(e.canonical_url, "http://localhost/canonical")
        self.assertEqual(e.domain, f"127.0.0.1:{self.port}")
        self.assertIn("Текст страницы", e.page_text)
        self.assertNotIn("var x", e.page_text)  # scripts dropped

    def test_enrich_http_error(self):
        with self.assertRaises(EnrichmentError):
            self.enricher.enrich(f"http://127.0.0.1:{self.port}/403")

    def test_enrich_non_html(self):
        with self.assertRaises(EnrichmentError):
            self.enricher.enrich(f"http://127.0.0.1:{self.port}/binary")

    def test_enrich_unreachable(self):
        with self.assertRaises(EnrichmentError):
            self.enricher.enrich("http://127.0.0.1:1/", timeout=0.5)


if __name__ == "__main__":
    unittest.main()