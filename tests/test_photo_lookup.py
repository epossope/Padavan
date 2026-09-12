import tempfile
import unittest
from pathlib import Path

from knowledge_store import KnowledgeItem, KnowledgeStore


class PhotoLookupTest(unittest.TestCase):
    def test_entity_lookup_accepts_case_and_descriptive_phrase(self):
        store = KnowledgeStore(Path(tempfile.mkdtemp()) / "memory.sqlite3")
        item, _ = store.insert_item(KnowledgeItem(
            chat_id=1, title="Попугай Кеша", entities=[{"type": "pet", "name": "Кеша"}],
            searchable_text="попугай Кеша", content_hash="kesha", source_message_id=1,
        ))
        self.assertTrue(store.search_by_entity("Кешу", chat_id=1))
        self.assertTrue(store.search_by_entity("попугая Кеша", chat_id=1))
        self.assertEqual(store.search_by_entity("Ричи", chat_id=1), [])


if __name__ == "__main__":
    unittest.main()
