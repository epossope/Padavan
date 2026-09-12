"""Integration tests: the 10 Universal Ingestion scenarios (1-9 on fakes, 10 = E2E)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion import (ActionBuilder, Attachment, IngestionInput,  # noqa: E402
                       IngestionPipeline)
from tests import fakes  # noqa: E402
from tests import helpers as h  # noqa: E402

DOG_PAYLOAD = {
    "content_type": "photo", "title": "Ричи позирует", "summary": "Собака Ричи на фото",
    "visible_text": "", "urls": [],
    "entities": [{"type": "pet", "name": "Ричи"}],
    "objects": ["собака"], "tags": ["собака", "домашний любимец"],
    "category": "pet", "project_hint": None, "confidence": 0.92,
}

RECEIPT_PAYLOAD = {
    "content_type": "photo", "title": "Чек", "summary": "Чек из кафе",
    "visible_text": "Кофе 250 ₽\nИтого: 250 руб.", "urls": [],
    "entities": [], "objects": ["чек"], "tags": ["чек", "еда"],
    "category": "receipt", "project_hint": None, "confidence": 0.9,
}


class PipelineScenarioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="noema_pipe_"))

    def _pipeline(self, payload=None, vision_raise=False, enricher=None):
        self.store = h.make_store(self.tmp / "db.sqlite3")
        self.vision = fakes.FakeVision(payload or {}, raise_on_call=vision_raise)
        self.enricher = enricher
        self.runner = fakes.RecordingActionRunner()
        return IngestionPipeline(
            store=self.store,
            vision_extractor=self.vision,
            url_enricher=enricher,
            file_saver=fakes.SqlFileSaver(self.store),
            action_builder=ActionBuilder(action_runner=self.runner),
            storage_dir=self.tmp / "storage",
        )

    def _inp(self, chat_id=1, msg_id=100, text="", attachments=(), context="", reply="", hint=None):
        atts = []
        for i, a in enumerate(attachments):
            if isinstance(a, str):
                p = h.make_image(self.tmp, a)
                atts.append(Attachment(file_id=f"TLG_{i}", local_path=str(p),
                                       mime_type="image/jpeg", kind="image", original_name=a))
            else:
                atts.append(a)
        return IngestionInput(chat_id=chat_id, message_id=msg_id, user_text=text,
                              attachments=atts, conversation_context=context,
                              reply_to_text=reply, project_hint=hint)

    # TEST 1: фото собаки + «Это моя собака Ричи»
    def test_01_dog_saved_with_entity(self):
        pipe = self._pipeline(payload=DOG_PAYLOAD)
        res = pipe.ingest(self._inp(text="Это моя собака Ричи", attachments=["richi.jpg"]))
        self.assertTrue(res.ok)
        self.assertEqual(res.ingestion_status, "completed")
        it = res.item
        self.assertEqual(it["entities"][0]["type"], "pet")
        self.assertEqual(it["entities"][0]["name"], "Ричи")
        self.assertIn("Ричи", it["searchable_text"])
        files = self.store.item_files(it["id"])
        self.assertEqual(len(files), 1)
        self.assertTrue(Path(files[0]["local_path"]).exists())  # persisted on disk
        tools = [a["tool"] for a in res.actions]
        self.assertNotIn("person_upsert", tools)  # pet is knowledge, not a person
        self.assertEqual(res.actions, [])
        self.assertIn("📥 Сохранила", res.reply)

    # TEST 2: потом «покажи фото Ричи» -> находится original file
    def test_02_show_dog_photo_retrieval(self):
        pipe = self._pipeline(payload=DOG_PAYLOAD)
        inp = self._inp(text="Это моя собака Ричи", attachments=["richi.jpg"])
        original_bytes = Path(inp.attachments[0].local_path).read_bytes()
        res = pipe.ingest(inp)
        self.assertTrue(res.ok)
        self.assertEqual(res.ingestion_status, "completed")
        # -> storage search by entity
        hits = self.store.search_by_entity("Ричи", chat_id=1)
        self.assertTrue(hits)
        files = self.store.item_files(hits[0]["id"])
        self.assertEqual(len(files), 1)
        stored = Path(files[0]["local_path"])
        self.assertTrue(stored.exists())
        self.assertEqual(stored.read_bytes(), original_bytes)  # original file retrieval
        # generic search interface also finds it
        self.assertTrue(self.store.search("Ричи", filters={"chat_id": 1}))

    # TEST 3: скриншот сайта + URL + проект Noema -> url, проект, tags, файл
    def test_03_screenshot_url_project(self):
        payload = {
            "content_type": "screenshot", "title": "Noema UI kit",
            "summary": "Скриншот дизайн-ресурса для проекта Noema",
            "visible_text": "https://design.example.com/noema-ui Noema UI kit v3",
            "urls": ["https://design.example.com/noema-ui"],
            "entities": [], "objects": ["экран"], "tags": ["дизайн", "ui"],
            "category": "web_resource", "project_hint": None, "confidence": 0.9,
        }
        en = fakes.FakeEnricher()
        pipe = self._pipeline(payload=payload, enricher=en)
        res = pipe.ingest(self._inp(
            text="сохрани для проекта Noema как ресурс по дизайну", attachments=["shot.png"]))
        self.assertTrue(res.ok)
        self.assertEqual(res.project_id, "Noema")
        self.assertEqual(res.ingestion_status, "completed")
        self.assertEqual(res.enrichment_status, "completed")
        self.assertEqual(res.urls, ["https://design.example.com/noema-ui"])
        self.assertEqual(len(self.store.item_files(res.item["id"])), 1)
        it = res.item
        self.assertIn("дизайн", it["tags"])
        self.assertIn("web_resource", it["category"])
        self.assertEqual(en.calls, ["https://design.example.com/noema-ui"])

    # TEST 4: произвольный скриншот с текстом -> visible_text сохраняется
    def test_04_screenshot_visible_text(self):
        payload = {
            "content_type": "screenshot", "title": "Список покупок", "summary": "",
            "visible_text": "Купить молоко, хлеб и сыр. Встреча в 18:00.",
            "urls": [], "entities": [], "objects": [], "tags": [],
            "category": "", "project_hint": None, "confidence": 0.8,
        }
        pipe = self._pipeline(payload=payload)
        res = pipe.ingest(self._inp(text="сохрани", attachments=["list.png"]))
        self.assertTrue(res.ok)
        self.assertIn("молоко", res.item["visible_text"])
        self.assertIsNone(res.item["project_id"])
        self.assertEqual(res.actions, [])

    # TEST 5: чек + «добавь расход» -> knowledge + expense
    def test_05_receipt_add_expense(self):
        pipe = self._pipeline(payload=RECEIPT_PAYLOAD)
        res = pipe.ingest(self._inp(msg_id=500, text="добавь расход", attachments=["receipt.jpg"]))
        self.assertTrue(res.ok)
        exp = next((a for a in res.actions if a["tool"] == "add_expense"), None)
        self.assertIsNotNone(exp)
        self.assertEqual(exp["args"]["amount"], 250.0)
        self.assertEqual(exp["result"]["ok"], True)
        self.assertTrue(any(c["tool"] == "add_expense" for c in self.runner.calls))
        self.assertEqual(res.item["category"], "receipt")
        self.assertIn("Записала расход", res.reply)

    # TEST 6: тот же чек + «что тут написано?» -> expense НЕ создаётся
    def test_06_receipt_question_no_expense(self):
        pipe = self._pipeline(payload=RECEIPT_PAYLOAD)
        res = pipe.ingest(self._inp(msg_id=501, text="что тут написано?", attachments=["receipt.jpg"]))
        self.assertTrue(res.ok)
        self.assertEqual(res.actions, [])
        self.assertEqual(self.runner.calls, [])
        self.assertIn("Кофе 250 ₽", res.item["visible_text"])  # данные сохранены

    # TEST 7: скриншот без URL -> URL не придумывается
    def test_07_no_url_not_invented(self):
        payload = {
            "content_type": "screenshot", "title": "", "summary": "",
            "visible_text": "Просто текст на скриншоте.",
            "urls": [], "entities": [], "objects": [], "tags": [],
            "category": "", "project_hint": None, "confidence": 0.8,
        }
        en = fakes.FakeEnricher()
        pipe = self._pipeline(payload=payload, enricher=en)
        res = pipe.ingest(self._inp(text="посмотри", attachments=["s.png"]))
        self.assertTrue(res.ok)
        self.assertEqual(res.urls, [])
        self.assertEqual(en.calls, [])  # enricher даже не вызван

    # TEST 8: UrlEnricher failed -> ingestion всё равно SUCCESS, данные не теряются
    def test_08_enricher_failed_ingestion_ok(self):
        payload = {
            "content_type": "screenshot", "title": "Страница", "summary": "",
            "visible_text": "Скриншот https://example.com/page",
            "urls": ["https://example.com/page"], "entities": [], "objects": [],
            "tags": [], "category": "", "project_hint": None, "confidence": 0.8,
        }
        en = fakes.FakeEnricher(error="403 Forbidden")
        pipe = self._pipeline(payload=payload, enricher=en)
        res = pipe.ingest(self._inp(text="сохрани", attachments=["page.png"]))
        self.assertTrue(res.ok)
        # ingestion fully succeeded despite enrichment failure
        self.assertEqual(res.ingestion_status, "completed")
        self.assertEqual(res.enrichment_status, "failed")
        it = res.item
        self.assertEqual(it["status"], "completed")
        self.assertEqual(it["enrichment_status"], "failed")
        self.assertEqual(it["metadata"]["enrichments"][0]["status"], "failed")
        self.assertEqual(len(self.store.item_files(it["id"])), 1)
        self.assertIn("https://example.com/page", res.item["visible_text"])

    # TEST 9: duplicate Telegram update -> дубликат не создаётся
    def test_09_duplicate_not_created(self):
        payload = {"content_type": "photo", "title": "", "summary": "",
                   "visible_text": "текст", "urls": [], "entities": [], "objects": [],
                   "tags": [], "category": "", "project_hint": None, "confidence": 0.5}
        pipe = self._pipeline(payload=payload)
        inp = self._inp(msg_id=700, text="сохрани", attachments=["d.png"])
        r1 = pipe.ingest(inp)
        self.assertEqual(r1.status, "completed")
        r2 = pipe.ingest(inp)  # Telegram retry of the same update
        self.assertEqual(r2.status, "duplicate")
        self.assertTrue(r2.duplicate)
        self.assertEqual(self.store.count_items(chat_id=1), 1)
        r3 = pipe.ingest(self._inp(msg_id=701, text="сохрани", attachments=["d.png"]))
        self.assertEqual(r3.status, "completed")  # new message -> new item
        self.assertEqual(self.store.count_items(chat_id=1), 2)


if __name__ == "__main__":
    unittest.main()