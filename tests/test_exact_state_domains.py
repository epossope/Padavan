import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import bot
from streaming_runtime import ToolPackResolver


class ExactStateDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.db")
        self.db_patch.start(); bot.init_db()
        today = datetime.now(bot.timezone_for(11)).date()
        self.a_task = bot.add_task(11, "Купить молоко", due_date=today.isoformat())["id"]
        bot.add_task(22, "Чужая задача", due_date=today.isoformat())
        closed = bot.add_task(11, "Закрытая задача", due_date=today.isoformat())["id"]
        bot.set_task_status(11, closed, "done")
        bot.save_note(11, "Заметка про запуск", "Проект Alpha")
        bot.save_note(22, "Секрет", "Чужая заметка")
        bot.person_upsert(11, "Саша", relationship="коллега", projects="Alpha")
        bot.person_upsert(22, "Саша B", relationship="друг", projects="Beta")

    def tearDown(self):
        self.db_patch.stop(); self.temp.cleanup()

    def test_task_and_note_exact_reads_are_owner_scoped_and_searchable(self):
        tasks = bot.task_list(11, "today")
        self.assertEqual({"Купить молоко"}, {row["text"] for row in tasks["items"]})
        self.assertEqual(0, bot.task_list(11, "all", query="чужая")["count"])
        notes = bot.note_list(11, query="alpha")
        self.assertEqual(1, notes["count"])
        self.assertEqual("Проект Alpha", notes["items"][0]["title"])
        self.assertEqual(0, bot.note_list(11, query="секрет")["count"])
        self.assertFalse(bot.update_note(11, 2, "чужая")["updated"])
        self.assertEqual(0, bot.delete_task(11, 2)["deleted"])

    def test_people_and_context_cannot_cross_owner_boundary(self):
        people = bot.get_people(11, "alpha")["people"]
        self.assertEqual(["Саша"], [row["name"] for row in people])
        self.assertEqual([], bot.get_people(11, "beta")["people"])
        bot.add_message(22, "assistant", "Чужая задача и Чужая заметка")
        context = " ".join(row["content"] for row in bot.conversation_context(11))
        self.assertNotIn("Чужая задача", context)

    def test_exact_routing_and_model_owner_argument_protection(self):
        router = ToolPackResolver()
        cases = {"какие у меня задачи?": "task_list", "что просрочено?": "task_list", "покажи мои заметки": "note_list", "кого я знаю?": "get_people"}
        for text, expected in cases.items():
            self.assertEqual(expected, router.required_tool_choice(text)["function"]["name"])
        result = bot.execute_tool(11, "task_list", {"chat_id": 22, "scope": "all"})
        self.assertTrue(all(row["text"] != "Чужая задача" for row in result["items"]))
        self.assertEqual(0, bot.note_list(33, query="молоко")["count"])
        self.assertIn("не выдумывай", bot.system_prompt(33))
