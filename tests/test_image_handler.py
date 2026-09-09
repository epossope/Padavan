"""Regression: image_handler must analyze BOTH photos and image documents.

Covers the PTB v22 gotcha: ``PhotoSize`` has NO ``file_name`` attribute, so the
old ``(msg.document or msg.photo[-1]).file_name`` crashed with
``AttributeError`` in the inner try/except and produced
"Не удалось проанализировать изображение" for every photo.

These tests drive the REAL async handler with a stub pipeline and assert the
happy path runs to "Сохранила" instead of the failure message.
"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from ingestion import IngestionResult  # noqa: E402


class _FakeBot:
    def __init__(self, file_bytes):
        self._file_bytes = file_bytes

    async def get_file(self, file_id):
        return _FakeTgFile(self._file_bytes)


class _FakeTgFile:
    def __init__(self, file_bytes):
        self._file_bytes = file_bytes

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(self._file_bytes)


class _FakeMSG:
    """Minimal Telegram Message stand-in exposing only what handler touches."""

    def __init__(self, photo_size, document, message_id=100, caption="", reply_to=None):
        self.photo = [photo_size] if photo_size else None
        self.document = document
        self.message_id = message_id
        self.caption = caption
        self.reply_to_message = reply_to
        self.status = None

    async def reply_text(self, text):
        self.status = _FakeStatus(text)
        return self.status

    def __getattr__(self, name):  # pragma: no cover - safety net
        return None


class _FakeStatus:
    def __init__(self, text):
        self.text = text
        self.edit_calls = []

    async def edit_text(self, text):
        self.edit_calls.append(text)


class _FakeUpdate:
    def __init__(self, message):
        class _Chat:
            id = 999
        _ = _Chat()
        self.effective_chat = type("Chat", (), {"id": 999})()
        self.effective_message = message


class _FakeContext:
    def __init__(self, bot):
        self.bot = bot


def _stub_pipeline(result):
    class Stub:
        def ingest(self, inp):
            return result
    return Stub()


def _run_handler(msg, file_bytes=b"\x89PNG\r\n" + b"0" * 64):
    orig_pipeline, orig_history = bot.get_ingestion_pipeline, bot.history
    bot.get_ingestion_pipeline = lambda chat_id: _stub_pipeline(
        IngestionResult(ok=True, ingestion_status="completed", enrichment_status="not_required",
                        item={"id": 1, "title": "тест"}, reply="📥 Сохранила: тест")
    )
    bot.history = lambda cid, n=18: []
    try:
        update = _FakeUpdate(msg)
        ctx = _FakeContext(_FakeBot(file_bytes))
        asyncio.run(bot.image_handler(update, ctx))
    finally:
        bot.get_ingestion_pipeline = orig_pipeline
        bot.history = orig_history


class ImageHandlerRegressionTest(unittest.TestCase):
    def test_photo_without_document_path(self):
        """msg.photo only -> PhotoSize has no file_name -> must NOT crash."""
        from telegram import PhotoSize
        ps = PhotoSize(file_id="PHOTO1", file_unique_id="u1", width=100, height=100, file_size=1000)
        msg = _FakeMSG(photo_size=ps, document=None)
        _run_handler(msg)
        last_text = msg.status.edit_calls[-1] if msg.status and msg.status.edit_calls else ""
        self.assertIn("📥 Сохранила", last_text)
        self.assertNotIn("Не удалось проанализировать", last_text)

    def test_image_document_path(self):
        """msg.document (image/*) -> Document.file_name exists -> must NOT crash."""
        from telegram import Document
        doc = Document(file_id="DOC1", file_unique_id="d1", file_name="design.png",
                       mime_type="image/png", file_size=1000)
        msg = _FakeMSG(photo_size=None, document=doc)
        _run_handler(msg)
        last_text = msg.status.edit_calls[-1] if msg.status and msg.status.edit_calls else ""
        self.assertIn("📥 Сохранила", last_text)
        self.assertNotIn("Не удалось проанализировать", last_text)

    def test_non_image_document_ignored(self):
        """document that is not an image must return early (no pipeline call)."""
        from telegram import Document
        doc = Document(file_id="DOC2", file_unique_id="d2", file_name="notes.txt",
                       mime_type="text/plain", file_size=10)
        msg = _FakeMSG(photo_size=None, document=doc)
        called = {}

        def _boom(*a, **k):
            called["x"] = True
            raise AssertionError("pipeline must not run for non-image document")

        orig_pipeline = bot.get_pipeline
        bot.get_pipeline = _boom
        try:
            asyncio.run(bot.image_handler(_FakeUpdate(msg), _FakeContext(_FakeBot(b""))))
        finally:
            bot.get_pipeline = orig_pipeline
        self.assertNotIn("x", called)


if __name__ == "__main__":
    unittest.main()
