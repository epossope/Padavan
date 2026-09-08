"""Regression: the original 23 tool smoke checks still pass after ingestion work."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


class ToolSmokeRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="noema_smoke_"))
        bot.DB = cls.tmp / "smoke.sqlite3"
        bot.init_db()
        cls.img = cls.tmp / "fake.png"
        cls.img.write_bytes(b"\x89PNG\r\n" + b"0" * 64)

    def t(self, tool_name, chat_id=42, **args):
        r = bot.execute_tool(chat_id, tool_name, args)
        self.assertTrue(isinstance(r, dict) and r.get("ok") is True, f"{tool_name} -> {r}")
        return r

    def test_01_note_crud(self):
        self.t("save_note", text="тестовая заметка", title="t1")
        rn = self.t("get_notes", limit=5)
        self.t("delete_note", note_id=rn["notes"][0]["id"])

    def test_02_expense_crud(self):
        self.t("add_expense", amount=123.5, description="кофе", category="еда",
               currency="RUB", merchant="кафе", spent_at="2026-09-08")
        r = self.t("get_expenses")
        self.t("update_last_expense", description="кофе с молоком")
        self.t("delete_expense", expense_id=r["items"][0]["id"])

    def test_03_task_crud(self):
        self.t("add_task", text="купить хлеб", due_date="2026-09-09", priority="high")
        r = self.t("get_today_plan")
        if r["tasks"]:
            self.t("delete_task", task_id=r["tasks"][0]["id"])

    def test_04_people_crud(self):
        self.t("person_upsert", name="Иван", relationship="друг", age=30, home_city="Москва")
        rp = self.t("get_people")
        self.t("person_interaction", name="Иван", interaction="созвон в 18:00", interaction_type="call")
        self.t("get_people", query="Иван")
        self.t("delete_person", person_id=rp["people"][0]["id"])

    def test_05_reminder_crud(self):
        r = self.t("set_reminder", text="полить цветы", remind_at="2026-09-08T23:59")
        self.t("get_today_plan")
        self.t("delete_reminder", reminder_id=r["id"])

    def test_06_files(self):
        self.t("save_image_to_db", original_name="img.png", mime_type="image/png",
               local_path=str(self.img), kind="user_image", summary="smoke")
        self.t("get_files", kind="user_image", limit=5)
        self.t("send_stored_image")
        r = bot.execute_tool(42, "get_file_from_telegram", {"file_id": "FAKE"})
        self.assertIsInstance(r, dict)
        self.assertFalse(r.get("ok"))
        r = bot.execute_tool(42, "unknown_tool", {})
        self.assertEqual(r.get("error"), "unknown_tool")

    def test_07_duplicate_chat_id_guard(self):
        r = bot.execute_tool(42, "save_note", {"chat_id": 42, "text": "с дубликатом chat_id"})
        self.assertTrue(r.get("ok"))

    def test_08_knowledge_tables_exist(self):
        with bot.conn() as c:
            tabs = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("knowledge_items", tabs)
        self.assertIn("knowledge_files", tabs)


if __name__ == "__main__":
    unittest.main()