import sqlite3
import tempfile
import unittest
from pathlib import Path

from model_router import ModelRouter
from telegram_renderer import TelegramRenderer
from knowledge_store import KnowledgeItem, KnowledgeStore
from retrieval import retrieve


class DeployFeaturesTest(unittest.TestCase):
    def test_renderer_escapes_markdown_and_splits(self):
        self.assertEqual(TelegramRenderer.render("**Проект:** <Noema>"), "<b>Проект:</b> &lt;Noema&gt;")
        chunks = TelegramRenderer.chunks("Первое предложение.\n\n" + "x" * 3800)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(x) < 3900 for x in chunks))

    def test_model_router_reads_current_database_value(self):
        db = Path(tempfile.mkdtemp()) / "settings.sqlite3"
        def connect():
            c = sqlite3.connect(db); c.row_factory = sqlite3.Row
            c.execute("CREATE TABLE IF NOT EXISTS user_settings(chat_id INTEGER PRIMARY KEY, primary_model TEXT NOT NULL DEFAULT '', fallback_model TEXT NOT NULL DEFAULT '', vision_model TEXT NOT NULL DEFAULT '')")
            return c
        router = ModelRouter(connect, "model-a", ["model-f"], "vision-a")
        self.assertEqual(router.resolve(1)["primary"], "model-a")
        router.set_primary(1, "model-b")
        self.assertEqual(router.resolve(1)["primary"], "model-b")

    def test_generic_relation_has_one_source_item(self):
        db = Path(tempfile.mkdtemp()) / "memory.sqlite3"
        store = KnowledgeStore(db)
        item, _ = store.insert_item(KnowledgeItem(chat_id=1, title="Инфраструктура", content_hash="source-1", source_message_id=1))
        a = store.upsert_entity(1, "Узел", "website")
        b = store.upsert_entity(1, "Hosting R", "hosting")
        store.add_relation(a["id"], "hosted_on", b["id"], item["id"], .9)
        relations = store.relations_for(1, "Узел")
        self.assertEqual(relations[0]["target"], "Hosting R")
        self.assertEqual(relations[0]["knowledge_item_id"], item["id"])

    def test_pet_category_query_finds_saved_guinea_pigs(self):
        db = Path(tempfile.mkdtemp()) / "pets.sqlite3"
        store = KnowledgeStore(db)
        item, _ = store.insert_item(KnowledgeItem(
            chat_id=7, title="Люся и Пинита", summary="Две морские свинки",
            searchable_text="Люся Пинита едят огурец", content_hash="pets-1", source_message_id=1,
        ))
        found = retrieve(store, 7, "У меня есть домашние животные?")
        self.assertEqual([row["id"] for row in found], [item["id"]])


if __name__ == "__main__":
    unittest.main()
