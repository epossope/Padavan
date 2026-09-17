import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import bot
from streaming_runtime import ToolPackResolver


class ReminderOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.db")
        self.db_patch.start()
        self.fixed_now = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return self.fixed_now.astimezone(tz) if tz else self.fixed_now.replace(tzinfo=None)
        self.clock_patch = patch.object(bot, "datetime", FrozenDateTime)
        self.clock_patch.start()
        bot.init_db()
        bot.set_user_timezone(101, "Europe/Moscow")
        bot.set_user_timezone(202, "Asia/Vladivostok")
        self.a = bot.save_reminder(101, "Позвонить Саше завтра", (self.fixed_now.astimezone(bot.timezone_for(101)) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0).isoformat())
        self.b = bot.save_reminder(202, "Купить корм сегодня", (self.fixed_now.astimezone(bot.timezone_for(202)) + timedelta(hours=1)).isoformat())

    def tearDown(self):
        self.clock_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_cross_user_list_search_and_context_are_isolated(self):
        a_rows = bot.reminder_list(101, "all")
        b_rows = bot.reminder_list(202, "all")
        self.assertEqual(["Позвонить Саше завтра"], [row["text"] for row in a_rows["items"]])
        self.assertEqual(["Купить корм сегодня"], [row["text"] for row in b_rows["items"]])
        self.assertEqual(0, bot.reminder_list(101, "all", query="корм")["count"])
        self.assertEqual(0, bot.reminder_list(202, "all", query="Саше")["count"])
        bot.add_message(202, "assistant", "Напоминание: Купить корм сегодня")
        context = " ".join(item["content"] for item in bot.conversation_context(101))
        self.assertNotIn("Купить корм", context)

    def test_tool_owner_is_trusted_and_cannot_be_overridden_by_model_args(self):
        result = bot.execute_tool(101, "reminder_list", {"chat_id": 202, "scope": "all"})
        self.assertEqual(1, result["count"])
        self.assertEqual("Позвонить Саше завтра", result["items"][0]["text"])
        self.assertFalse(bot.update_reminder(101, self.b["id"], "чужое", datetime.now().isoformat())["updated"])
        self.assertEqual(0, bot.delete_reminder(101, self.b["id"])["deleted"])

    def test_exact_periods_timezone_and_empty_db_win_over_memory(self):
        self.assertEqual(1, bot.reminder_list(101, "tomorrow")["count"])
        self.assertEqual(1, bot.reminder_list(202, "today")["count"])
        bot.add_message(303, "assistant", "Напомни купить молоко")
        self.assertEqual(0, bot.reminder_list(303, "all")["count"])
        self.assertIn("не выдумывай", bot.system_prompt(303))

    def test_reminder_queries_require_exact_read_tool(self):
        router = ToolPackResolver()
        for text in ("какие у меня напоминания?", "что мне нужно сегодня?", "есть ли напоминание завтра?"):
            choice = router.required_tool_choice(text)
            self.assertEqual("reminder_list", choice["function"]["name"])
            names = {tool["function"]["name"] for tool in router.resolve(bot.TOOLS, text)}
            self.assertIn("reminder_list", names)
