"""Hardening tests for Universal Ingestion Pipeline.

Covers:
  1. entity -> action routing (pet/company/... NEVER become `people`);
  2. action intent audit (expense/task/person/reminder);
  3. ingestion_status vs enrichment_status separation (+ legacy migration);
  4. URL safety (unsafe URL is stored but never enriched);
  5. idempotency on retries (enrichment/action failure => no duplicates).
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion import (ActionBuilder, Attachment, IngestionInput,  # noqa: E402
                       IngestionPipeline)
from knowledge_store import KnowledgeStore  # noqa: E402
from url_enricher import HttpUrlEnricher  # noqa: E402
from tests import fakes  # noqa: E402
from tests import helpers as h  # noqa: E402


def vision_payload(entity_type, entity_name, content_type="photo", visible_text="", category=""):
    return {
        "content_type": content_type,
        "title": entity_name,
        "summary": f"Картинка с {entity_name}",
        "visible_text": visible_text,
        "urls": [],
        "entities": [{"type": entity_type, "name": entity_name}],
        "objects": [], "tags": [], "category": category,
        "project_hint": None, "confidence": 0.9,
    }


class HardeningPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="noema_hard_"))

    def _pipeline(self, payload, enricher=None, runner=None):
        self.store = h.make_store(self.tmp / "db.sqlite3")
        self.runner = runner or fakes.RecordingActionRunner()
        return IngestionPipeline(
            store=self.store,
            vision_extractor=fakes.FakeVision(payload),
            url_enricher=enricher,
            file_saver=fakes.SqlFileSaver(self.store),
            action_builder=ActionBuilder(action_runner=self.runner),
            storage_dir=self.tmp / "storage",
        )

    def _inp(self, text="", msg_id=100):
        p = h.make_image(self.tmp, "img.png")
        return IngestionInput(
            chat_id=11, message_id=msg_id, user_text=text,
            attachments=[Attachment(file_id="TLG", local_path=str(p),
                                    mime_type="image/jpeg", kind="image", original_name="img.png")],
        )

    # --- 1. entity -> action routing --------------------------------------
    def test_pet_entity_never_person_upserted(self):
        """Pet + explicit-looking intent must NOT create a person."""
        pipe = self._pipeline(vision_payload("pet", "Ричи"))
        res = pipe.ingest(self._inp(text="Запомни, это Ричи, мой пёс"))
        self.assertTrue(res.ok)
        names = [a.get("args", {}).get("name") for a in res.actions if a["tool"] == "person_upsert"]
        self.assertEqual(names, [])
        self.assertEqual(res.actions, [])
        # knowledge + entity still saved and searchable
        it = res.item
        self.assertEqual(it["entities"][0]["type"], "pet")
        self.assertTrue(self.store.search_by_entity("Ричи", chat_id=11))

    def test_company_product_website_place_never_person_upserted(self):
        for etype in ("company", "product", "website", "place", "animal"):
            pipe = self._pipeline(vision_payload(etype, "Acme" if etype == "company" else "Вазон"))
            res = pipe.ingest(self._inp(text="Запомни, это важное"))
            self.assertTrue(res.ok)
            tools = [a["tool"] for a in res.actions]
            self.assertNotIn("person_upsert", tools, etype)
            self.assertEqual(self.runner.calls, [])

    def test_person_entity_with_explicit_intent_upserted(self):
        pipe = self._pipeline(vision_payload("person", "Алексей"))
        res = pipe.ingest(self._inp(text="Запомни, это Алексей, дизайнер проекта"))
        self.assertTrue(res.ok)
        upserts = [a for a in res.actions if a["tool"] == "person_upsert"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(upserts[0]["args"]["name"], "Алексей")
        self.assertEqual(upserts[0]["result"]["ok"], True)

    def test_person_entity_weak_mention_no_upsert(self):
        pipe = self._pipeline(vision_payload("person", "Алексей"))
        res = pipe.ingest(self._inp(text="На фотографии Алексей"))
        self.assertTrue(res.ok)
        self.assertEqual(res.actions, [])
        self.assertEqual(self.runner.calls, [])

    # --- 2. action intent ------------------------------------------------
    def test_expense_intent_and_no_intent(self):
        payload = {
            "content_type": "photo", "title": "Чек", "summary": "",
            "visible_text": "Кофе 250 ₽\nИтого: 250", "urls": [], "entities": [],
            "objects": [], "tags": [], "category": "receipt",
            "project_hint": None, "confidence": 0.9,
        }
        pipe = self._pipeline(payload)
        with_expense = pipe.ingest(self._inp(msg_id=201, text="Добавь это как расход"))
        tools = [a["tool"] for a in with_expense.actions]
        self.assertIn("add_expense", tools)
        exp = next(a for a in with_expense.actions if a["tool"] == "add_expense")
        self.assertEqual(exp["args"]["amount"], 250.0)

        no = pipe.ingest(self._inp(msg_id=202, text="Что написано на чеке?"))
        self.assertEqual(no.actions, [])
        self.assertIn("Кофе 250", no.item["visible_text"])

    def test_task_intent_and_no_intent(self):
        payload = vision_payload("other", "Планёр", visible_text="Купить хлеб")
        pipe = self._pipeline(payload)
        yes = pipe.ingest(self._inp(msg_id=203, text="Добавь это в задачи"))
        self.assertIn("add_task", [a["tool"] for a in yes.actions])
        no = pipe.ingest(self._inp(msg_id=204, text="Что здесь за задача написана?"))
        self.assertEqual(no.actions, [])

    def test_reminder_intent_and_no_intent(self):
        payload = vision_payload("other", "Заметка", visible_text="Встреча завтра в 10:00")
        pipe = self._pipeline(payload)
        yes = pipe.ingest(self._inp(msg_id=205, text="Напомни завтра"))
        self.assertIn("set_reminder", [a["tool"] for a in yes.actions])
        no = pipe.ingest(self._inp(msg_id=206, text="На скриншоте написано встреча завтра"))
        self.assertEqual(no.actions, [])
        self.assertEqual(no.item.get("visible_text", ""), "Встреча завтра в 10:00")

    # --- 3. ingestion_status vs enrichment_status ---------------------------
    def test_enrichment_failure_keeps_ingestion_completed(self):
        payload = {
            "content_type": "screenshot", "title": "Страница", "summary": "",
            "visible_text": "Скриншот https://example.com/page",
            "urls": ["https://example.com/page"], "entities": [], "objects": [],
            "tags": [], "category": "", "project_hint": None, "confidence": 0.8,
        }
        en = fakes.FakeEnricher(error="403 Forbidden")
        pipe = self._pipeline(payload, enricher=en)
        res = pipe.ingest(self._inp(text="сохрани"))
        self.assertTrue(res.ok)
        self.assertEqual(res.ingestion_status, "completed")
        self.assertEqual(res.enrichment_status, "failed")
        it = res.item
        self.assertEqual(it["status"], "completed")
        self.assertEqual(it["enrichment_status"], "failed")
        self.assertTrue(self.store.item_files(it["id"]))
        self.assertIn("https://example.com/page", it["visible_text"])

    def test_no_url_enrichment_not_required(self):
        pipe = self._pipeline(vision_payload("pet", "Ричи"))
        res = pipe.ingest(self._inp(text="Это моя собака Ричи"))
        self.assertEqual(res.enrichment_status, "not_required")
        self.assertEqual(res.item["enrichment_status"], "not_required")

    def test_url_without_enricher_pending(self):
        payload = vision_payload("screenshot", "Скрин", visible_text="https://example.com/x")
        payload["content_type"] = "screenshot"
        pipe = self._pipeline(payload, enricher=None)
        res = pipe.ingest(self._inp(text="сохрани"))
        self.assertEqual(res.enrichment_status, "pending")
        self.assertEqual(res.ingestion_status, "completed")

    def test_vision_failure_marks_partial(self):
        pipe = self._pipeline(vision_payload("pet", "Ричи"))
        pipe.vision_extractor = fakes.FakeVision({}, raise_on_call=True)
        res = pipe.ingest(self._inp(text="сохрани"))
        self.assertTrue(res.ok)
        self.assertEqual(res.ingestion_status, "partial")
        self.assertTrue(self.store.item_files(res.item["id"]))

    # --- 4. URL safety -----------------------------------------------------
    def test_unsafe_url_stored_but_never_enriched(self):
        payload = vision_payload("screenshot", "Скрин", visible_text="Сайт http://localhost:8888/admin")
        payload["content_type"] = "screenshot"
        enricher = HttpUrlEnricher()  # NO allowlist: localhost must be blocked
        pipe = self._pipeline(payload, enricher=enricher)
        res = pipe.ingest(self._inp(text="сохрани"))
        self.assertTrue(res.ok)
        self.assertEqual(res.ingestion_status, "completed")
        self.assertEqual(res.enrichment_status, "failed")
        # the URL is still stored as extracted data
        self.assertIn("http://localhost:8888/admin", res.urls)
        rec = res.enrichment[0]
        self.assertEqual(rec["status"], "blocked")
        self.assertIn("unsafe_url", rec.get("error", ""))
        self.assertTrue(self.store.item_files(res.item["id"]))

    # --- 5. idempotency ----------------------------------------------------
    def test_retry_after_enrichment_failure_no_duplicate(self):
        payload = vision_payload("screenshot", "Скрин", visible_text="https://example.com/page")
        payload["content_type"] = "screenshot"
        en = fakes.FakeEnricher(error="boom")
        pipe = self._pipeline(payload, enricher=en)
        inp = self._inp(msg_id=300, text="сохрани")
        r1 = pipe.ingest(inp)
        self.assertEqual(r1.ingestion_status, "completed")
        self.assertEqual(r1.enrichment_status, "failed")
        r2 = pipe.ingest(inp)
        self.assertTrue(r2.duplicate)
        self.assertEqual(r2.ingestion_status, "duplicate")
        self.assertEqual(self.store.count_items(chat_id=11), 1)

    def test_retry_after_action_failure_no_duplicate(self):
        def failing_runner(chat_id, tool_name, args):
            raise RuntimeError("action boom")
        pipe = self._pipeline(
            vision_payload("person", "Алексей"),
            runner=failing_runner,
        )
        inp = self._inp(msg_id=301, text="Запомни, это Алексей, дизайнер проекта")
        r1 = pipe.ingest(inp)
        self.assertEqual(r1.ingestion_status, "completed")
        self.assertEqual(r1.actions[0]["tool"], "person_upsert")
        self.assertIn("error", r1.actions[0])
        r2 = pipe.ingest(inp)
        self.assertTrue(r2.duplicate)
        self.assertEqual(self.store.count_items(chat_id=11), 1)

    def test_retry_after_action_success_no_duplicate(self):
        pipe = self._pipeline(vision_payload("person", "Алексей"))
        inp = self._inp(msg_id=302, text="Запомни, это Алексей, дизайнер проекта")
        r1 = pipe.ingest(inp)
        self.assertEqual(r1.actions[0]["result"]["ok"], True)
        r2 = pipe.ingest(inp)
        self.assertTrue(r2.duplicate)
        self.assertEqual(self.store.count_items(chat_id=11), 1)
        # person_upsert executed only once
        calls = [c for c in self.runner.calls if c["tool"] == "person_upsert"]
        self.assertEqual(len(calls), 1)


class LegacyStatusMigrationTest(unittest.TestCase):
    def test_migration_of_legacy_statuses(self):
        import sqlite3
        tmp = Path(tempfile.mkdtemp(prefix="noema_migr_"))
        db = tmp / "legacy.sqlite3"
        raw = sqlite3.connect(db)  # bypass KnowledgeStore so we can create the OLD schema
        raw.executescript("""
            CREATE TABLE knowledge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL, project_id TEXT,
                content_type TEXT NOT NULL DEFAULT 'text',
                title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
                visible_text TEXT NOT NULL DEFAULT '', searchable_text TEXT NOT NULL DEFAULT '',
                urls_json TEXT NOT NULL DEFAULT '[]', entities_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]', category TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}', content_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'stored',
                source_message_id INTEGER, source_file_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE knowledge_files (
                knowledge_id INTEGER REFERENCES knowledge_items(id) ON DELETE CASCADE,
                file_id INTEGER, role TEXT NOT NULL DEFAULT 'source',
                PRIMARY KEY (knowledge_id, file_id));
            """)
        for i, status in enumerate(("stored", "enriched", "enrichment_partial", "enrichment_failed")):
            raw.execute(
                "INSERT INTO knowledge_items(chat_id, content_hash, status, created_at, updated_at) "
                "VALUES (?,?,?,datetime('now'),datetime('now'))",
                (1 + i, f"hash_{i}", status))
        raw.commit()
        raw.close()

        store = KnowledgeStore(db)  # init_schema -> migration
        with store._connect() as c:
            rows = {r["status"]: r["enrichment_status"] for r in
                    c.execute("SELECT status, enrichment_status FROM knowledge_items").fetchall()}
        self.assertEqual(set(rows), {"completed"})


if __name__ == "__main__":
    unittest.main()