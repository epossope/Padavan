import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from diagnostics import DiagnosticsJournal


class DiagnosticsJournalTests(unittest.TestCase):
    def test_rotates_jsonl_and_drops_sensitive_or_unapproved_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = DiagnosticsJournal(Path(temporary), max_bytes=80, backups=2)
            journal.record("miniapp_trace", stage="STATE_OK", build="ui-a", platform="ios", duration_ms=3, init_data="secret", text="private")
            journal.record("event_loop_lag_warning", event_loop_lag_ms=999)
            journal.record("miniapp_trace", stage="APP_READY", build="ui-a", platform="ios", duration_ms=4)
            current = Path(temporary) / "noema_diagnostics.jsonl"
            content = "".join(path.read_text(encoding="utf-8") for path in Path(temporary).glob("noema_diagnostics.jsonl*"))
            self.assertTrue(current.exists())
            self.assertIn('"event":"miniapp_trace"', content)
            self.assertNotIn("secret", content)
            self.assertNotIn("private", content)
            for path in Path(temporary).glob("noema_diagnostics.jsonl*"):
                for line in path.read_text(encoding="utf-8").splitlines():
                    self.assertIn("event", json.loads(line))


class DiagnosticsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_can_download_current_file_and_non_admin_is_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = DiagnosticsJournal(Path(temporary))
            journal.record("miniapp_trace", stage="STATE_OK")
            admin_message = SimpleNamespace(reply_document=AsyncMock(), reply_text=AsyncMock())
            outsider_message = SimpleNamespace(reply_document=AsyncMock(), reply_text=AsyncMock())
            with patch.object(bot, "DIAGNOSTICS", journal), patch.object(bot, "ADMIN_CHAT_IDS", {42}):
                await bot.diagnostics_command(SimpleNamespace(effective_chat=SimpleNamespace(id=42), effective_message=admin_message), SimpleNamespace())
                await bot.diagnostics_command(SimpleNamespace(effective_chat=SimpleNamespace(id=99), effective_message=outsider_message), SimpleNamespace())
            admin_message.reply_document.assert_awaited_once()
            outsider_message.reply_document.assert_not_awaited()
            outsider_message.reply_text.assert_awaited_once()
