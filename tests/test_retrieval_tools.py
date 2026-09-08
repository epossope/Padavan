"""Universal Retrieval tests: agent tools, entity/project awareness,
follow-up context, no-result honesty and cross-user isolation."""
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from knowledge_store import KnowledgeItem  # noqa: E402
from tests import helpers as h  # noqa: E402


class RetrievalTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="noema_retr_"))
        self.store = h.make_store(self.tmp / "db.sqlite3")
        self._old_pipeline = bot._pipeline
        bot._pipeline = SimpleNamespace(store=self.store)
        bot._media_outbox.clear()
        bot._LAST_RETRIEVAL.clear()

    def tearDown(self):
        bot._pipeline = self._old_pipeline
        bot._media_outbox.clear()
        bot._LAST_RETRIEVAL.clear()

    # ----- seeding helpers --------------------------------------------------
    def seed(self, chat_id, title, entity=None, project=None, urls=None,
             summary="", tags=None, content_type="photo",
             file_spec=None, category=""):
        seq = getattr(self, "_seq", None)
        if seq is None:
            self._seq = [0]
        self._seq[0] += 1
        entities = [{"type": entity[0], "name": entity[1]}] if entity else []
        item = KnowledgeItem(
            chat_id=chat_id, content_type=content_type, title=title, summary=summary,
            visible_text=summary, searchable_text=f"{title} {summary} {' '.join(tags or [])}",
            urls=urls or [], entities=entities, tags=tags or [], category=category,
            content_hash=f"hash_{self._seq[0]}_{chat_id}", project_id=project,
            source_message_id=1000 + self._seq[0], status="completed",
            enrichment_status="not_required",
        )
        created, dup = self.store.insert_item(item)
        if file_spec:
            path = str(file_spec if isinstance(file_spec, Path) else self._make_img(file_spec))
            with self.store._connect() as c:
                cur = c.execute(
                    """INSERT INTO files (chat_id, telegram_file_id, original_name, mime_type, local_path,
                        kind, summary, extracted_text, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (chat_id, "TLG_FILE_" + str(created["id"]), Path(path).name or "file",
                     "image/jpeg", path, "image", summary or "", "",
                     created.get("created_at") or ""))
                fid = cur.lastrowid
            self.store.link_file(created["id"], fid, role="source")
        return created

    def _make_img(self, name):
        p = self.tmp / name
        if not p.exists():
            p.write_bytes(b"\x89PNG\r\n" + b"0" * 16)
        return p

    def exec(self, chat_id, tool, **args):
        return bot.execute_tool(chat_id, tool, args)

    # ----- common seeds -----------------------------------------------------
    def seed_toshka(self, chat_id=1):
        return self.seed(chat_id, title="Тошка", entity=("pet", "Тошка"),
                         summary="Собака Тошка, светлая, весёлая.",
                         tags=["собака"], content_type="photo",
                         file_spec="toshka.png", category="pet")

    def seed_honcho(self, chat_id=1, project="Noema"):
        return self.seed(chat_id, title="Honcho", project=project,
                         urls=["https://app.honcho.dev"],
                         summary="База данных по проекту Noema — сервис https://app.honcho.dev.",
                         tags=["база", "данные"], content_type="screenshot",
                         file_spec="honcho.png", category="web_resource")


class KnowledgeSearchToolTest(RetrievalTestBase):
    # TEST 1 / 5: Тошка — поиск + отправка original image
    def test_01_toshka_found_with_image(self):
        it = self.seed_toshka()
        res = self.exec(1, "knowledge_search", query="Покажи Тошку")
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["title"], "Тошка")
        self.assertTrue(res["results"][0]["has_files"])

        sent = self.exec(1, "send_stored_image", knowledge_id=it["id"])
        self.assertTrue(sent["ok"])
        self.assertEqual(sent["queued"], 1)
        self.assertEqual(len(bot._media_outbox[1]), 1)
        entry = bot._media_outbox[1][0]
        self.assertTrue(entry["local_path"])

    def test_02_toshka_info_no_media(self):
        self.seed_toshka()
        res = self.exec(1, "knowledge_search", query="Что ты знаешь про Тошку?")
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 1)
        self.assertIn("Тошка", res["results"][0]["title"] + res["results"][0]["summary"])
        # no media call was made
        self.assertEqual(bot._media_outbox.get(1), None)

    # TEST 3: Honcho project-aware
    def test_03_honcho_project_noema(self):
        self.seed_honcho()
        res = self.exec(1, "knowledge_search",
                        query="где я храню базу данных по проекту Noema", project="Noema")
        self.assertEqual(res["count"], 1)
        r = res["results"][0]
        self.assertEqual(r["title"], "Honcho")
        self.assertEqual(r["project"], "Noema")
        self.assertIn("https://app.honcho.dev", r["urls"])

    # TEST 4: "какой сайт я сохранял для Noema?" -> Honcho
    def test_04_site_for_noema(self):
        self.seed_honcho()
        res = self.exec(1, "knowledge_search", query="Какой сайт я сохранял для Noema?")
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["title"], "Honcho")

    # TEST 5: "покажи тот Honcho" -> send screenshot
    def test_05_show_honcho(self):
        it = self.seed_honcho()
        sent = self.exec(1, "send_stored_image", knowledge_id=it["id"])
        self.assertTrue(sent["ok"])
        self.assertEqual(bot._media_outbox[1][0]["mime_type"], "image/jpeg")

    # TEST 6: natural language forms all hit the same knowledge
    def test_06_natural_language(self):
        self.seed_honcho()
        self.seed_toshka()
        for q in (
            "где у меня база",
            "что я кидал по базам для Noema",
            "найди сервис Honcho",
            "покажи скрин с Honcho",
            "дай фотку моей собаки",
        ):
            res = self.exec(1, "knowledge_search", query=q)
            self.assertGreater(res["count"], 0, f"query failed: {q}")

    # TEST 7: no-result honesty
    def test_07_no_result(self):
        self.seed_toshka()
        res = self.exec(1, "knowledge_search", query="покажи фото несуществующего кота Барсика")
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 0)

    # TEST 8: user isolation
    def test_08_isolation(self):
        self.seed_toshka(chat_id=1)
        self.seed_honcho(chat_id=1)
        res = self.exec(99, "knowledge_search", query="Тошка")  # other user
        self.assertEqual(res["count"], 0)
        other = self.exec(99, "knowledge_search", query="Honcho")
        self.assertEqual(other["count"], 0)

    # TEST 9: follow-up context
    def test_09_follow_up_context(self):
        self.seed_toshka()
        self.exec(1, "knowledge_search", query="Покажи мою собаку")
        ctx = bot._LAST_RETRIEVAL.get(1)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["item"]["title"], "Тошка")
        # follow-up resolves the same item via knowledge_get
        g = self.exec(1, "knowledge_get", knowledge_id=ctx["item"]["id"])
        self.assertTrue(g["ok"])
        self.assertEqual(g["item"]["title"], "Тошка")


class KnowledgeFilesToolTest(RetrievalTestBase):
    def test_files_user_scoped(self):
        it = self.seed_honcho()
        files = self.exec(1, "knowledge_files", knowledge_id=it["id"])
        self.assertTrue(files["ok"])
        self.assertEqual(files["count"], 1)
        self.assertEqual(files["files"][0]["original_name"], "honcho.png")
        # other user must not see it
        other = self.exec(99, "knowledge_files", knowledge_id=it["id"])
        self.assertFalse(other["ok"])
        self.assertEqual(other["error"], "not_found")

    def test_missing_id(self):
        res = self.exec(1, "knowledge_files", knowledge_id=123456)
        self.assertFalse(res["ok"])


class ProjectFuzzyTest(RetrievalTestBase):
    def test_noema_vs_noemo(self):
        self.seed_honcho(project="Noema")
        res = self.exec(1, "knowledge_search",
                        query="база данных", project="Noemo")  # typo -> Noema
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["project"], "Noema")
        self.assertEqual(res["results"][0]["project"], "Noema")

    def test_unknown_project_not_invented(self):
        self.seed_honcho(project="Noema")
        res = self.exec(1, "knowledge_search", query="база данных", project="СовсемДругойПроект")
        self.assertEqual(res["count"], 0)


class MediaOutboxTest(RetrievalTestBase):
    def test_send_stored_image_via_query(self):
        self.seed_toshka()
        sent = self.exec(1, "send_stored_image", query="дай фотку моей собаки")
        self.assertTrue(sent["ok"])
        self.assertEqual(sent["queued"], 1)
        entry = bot._media_outbox.get(1, [])[0]
        self.assertEqual(entry["telegram_file_id"], "TLG_FILE_1")
        self.assertTrue(entry["local_path"])

    def test_send_unknown_query(self):
        self.seed_toshka()
        sent = self.exec(1, "send_stored_image", query="кот Барсик")
        self.assertFalse(sent["ok"])
        self.assertEqual(sent["error"], "no_item_found")


class DateRetrievalTest(RetrievalTestBase):
    def test_date_filter(self):
        self.seed_honcho()
        # Honcho created today; a far-past window must not match it
        res = self.exec(1, "knowledge_search", query="Honcho",
                        date_from="2000-01-01", date_to="2000-01-02")
        self.assertEqual(res["count"], 0)


if __name__ == "__main__":
    unittest.main()