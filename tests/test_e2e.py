"""TEST 10 (E2E): Telegram input -> ingestion -> storage -> search -> original file retrieval.

Uses the real bot.py file-saver (save_image_to_db) against a temp DB and a real
image file on disk. Vision and enrichment are faked for determinism (no network).
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from ingestion import (ActionBuilder, Attachment, IngestionInput,  # noqa: E402
                       IngestionPipeline)
from knowledge_store import KnowledgeStore  # noqa: E402
from tests import fakes  # noqa: E402

DOG_PAYLOAD = {
    "content_type": "photo", "title": "Ричи", "summary": "Собака Ричи на фото",
    "visible_text": "", "urls": [],
    "entities": [{"type": "pet", "name": "Ричи"}],
    "objects": ["собака"], "tags": ["собака"], "category": "pet",
    "project_hint": None, "confidence": 0.93,
}


class E2EIngestionFlowTest(unittest.TestCase):
    _seq = [0]

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="noema_e2e_"))
        bot.DB = cls.tmp / "e2e.sqlite3"
        bot.init_db()  # full prototype schema incl. files + knowledge tables
        cls.store = KnowledgeStore(bot.DB)

    def setUp(self):
        self.chat_id = 4200 + E2EIngestionFlowTest._seq[0]
        E2EIngestionFlowTest._seq[0] += 1
        self.img = self.tmp / f"original_{self.chat_id}.png"
        self.bytes = b"\x89PNG\r\n" + b"0123456789abcdef" * 16
        self.img.write_bytes(self.bytes)
        self.pipeline = IngestionPipeline(
            store=self.store,
            vision_extractor=fakes.FakeVision(DOG_PAYLOAD),
            url_enricher=None,
            file_saver=bot._bot_save_file,  # REAL bot file-saver into files table
            action_builder=ActionBuilder(action_runner=None),  # actions only planned
            storage_dir=self.tmp / f"storage_{self.chat_id}",
        )

    def _input(self, msg_id=900, text="Это моя собака Ричи"):
        return IngestionInput(
            chat_id=self.chat_id, message_id=msg_id, user_text=text,
            attachments=[Attachment(file_id="TLG_FILE_1", local_path=str(self.img),
                                    mime_type="image/png", kind="image", original_name="original.png")],
        )

    def test_e2e_save_search_retrieve(self):
        # 1. Telegram-like input -> ingestion
        res = self.pipeline.ingest(self._input())
        self.assertTrue(res.ok)
        self.assertEqual(res.status, "stored")
        item = res.item
        self.assertEqual(item["entities"][0]["name"], "Ричи")

        # 2. storage -> search interface (public bot.knowledge_search)
        hits = bot.knowledge_search("Ричи", filters={"chat_id": self.chat_id})
        self.assertTrue(hits)

        # 3. search by entity -> original file retrieval
        entity_hits = self.store.search_by_entity("Ричи", chat_id=self.chat_id)
        self.assertTrue(entity_hits)
        files = self.store.item_files(entity_hits[0]["id"])
        self.assertEqual(len(files), 1)
        stored_path = Path(files[0]["local_path"])
        self.assertTrue(stored_path.exists())
        self.assertEqual(stored_path.read_bytes(), self.bytes)  # exact original content

        # 4. duplicate Telegram retry -> no new item
        res2 = self.pipeline.ingest(self._input())
        self.assertTrue(res2.duplicate)
        self.assertEqual(self.store.count_items(chat_id=self.chat_id), 1)

    def test_e2e_question_no_expense(self):
        # receiving same-style input with a question must not create actions
        self.pipeline = IngestionPipeline(
            store=self.store,
            vision_extractor=fakes.FakeVision(DOG_PAYLOAD),
            file_saver=bot._bot_save_file,
            action_builder=ActionBuilder(action_runner=fakes.RecordingActionRunner()),
            storage_dir=self.tmp / f"storage2_{self.chat_id}",
        )
        res = self.pipeline.ingest(self._input(msg_id=901, text="что тут написано?"))
        self.assertTrue(res.ok)
        self.assertEqual(res.actions, [])


if __name__ == "__main__":
    unittest.main()