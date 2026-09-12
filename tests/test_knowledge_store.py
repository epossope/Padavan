"""Unit tests: KnowledgeStore schema, persistence, idempotency, search, enrichment."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_store import KnowledgeItem, NullEmbeddingProvider  # noqa: E402
from tests import helpers as h  # noqa: E402


def make_item(**kw):
    defaults = dict(
        chat_id=1, content_type="screenshot", title="T", summary="S",
        visible_text="какой-то текст про Ричи", searchable_text="текст Ричи",
        urls=["https://x.io"], entities=[{"type": "pet", "name": "Ричи"}],
        tags=["дизайн"], category="web_resource", content_hash="h1",
        source_message_id=10, source_file_id="F1",
    )
    defaults.update(kw)
    return KnowledgeItem(**defaults)


class KnowledgeStoreTest(unittest.TestCase):
    def setUp(self):
        self.db = h.make_tmp_db()
        self.store = h.make_store(self.db)

    def test_insert_get(self):
        item, dup = self.store.insert_item(make_item())
        self.assertFalse(dup)
        got = self.store.get_item(item["id"])
        self.assertEqual(got["title"], "T")
        self.assertEqual(got["urls"], ["https://x.io"])
        self.assertEqual(got["entities"][0]["name"], "Ричи")
        self.assertEqual(self.store.count_items(), 1)

    def test_idempotent_duplicate(self):
        self.store.insert_item(make_item())
        _, dup = self.store.insert_item(make_item())
        self.assertTrue(dup)
        self.assertEqual(self.store.count_items(), 1)

    def test_link_and_retrieve_files(self):
        item, _ = self.store.insert_item(make_item())
        with self.store._connect() as c:
            cur = c.execute(
                "INSERT INTO files(chat_id, original_name, local_path, mime_type, kind, created_at) "
                "VALUES (1,'a.png','/tmp/a.png','image/png','image','now')")
            fid = cur.lastrowid
        self.store.link_file(item["id"], fid)
        files = self.store.item_files(item["id"])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["local_path"], "/tmp/a.png")

    def test_search_text_with_filters(self):
        self.store.insert_item(make_item())
        self.assertEqual(len(self.store.search("Ричи", filters={"chat_id": 1})), 1)
        self.assertEqual(self.store.search("Ричи", filters={"chat_id": 999}), [])
        self.assertEqual(self.store.search("no_such_token"), [])

    def test_search_by_entity(self):
        self.store.insert_item(make_item())
        self.assertEqual(len(self.store.search_by_entity("ричи", chat_id=1)), 1)

    def test_enrichment_update(self):
        item, _ = self.store.insert_item(make_item())
        updated = self.store.set_enrichment(
            item["id"], [{"url": "https://x.io", "status": "ok"}], "completed")
        # enrichment status is stored separately; ingestion status is untouched
        self.assertEqual(updated["enrichment_status"], "completed")
        self.assertEqual(updated["status"], "processing")
        self.assertEqual(updated["metadata"]["enrichments"][0]["url"], "https://x.io")

    def test_content_hash_stable(self):
        h1 = self.store.content_hash(1, 10, ["A", "B"], "text")
        h2 = self.store.content_hash(1, 10, ["B", "A"], "text")
        h3 = self.store.content_hash(1, 10, ["A", "B"], "text!")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)

    def test_null_embedding_provider(self):
        self.assertIsInstance(self.store.embeddings, NullEmbeddingProvider)


if __name__ == "__main__":
    unittest.main()