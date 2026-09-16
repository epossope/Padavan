import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot


class ReminderReplacementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.db")
        self.db_patch.start()
        bot.init_db()
        self.chat_id = 81
        self.reminder = bot.save_reminder(self.chat_id, "private reminder text", (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
        self.row = {"id": self.reminder["id"], "chat_id": self.chat_id, "text": "private reminder text", "followup_count": 0}

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_repeats_replace_current_message_and_ack_is_idempotent(self):
        telegram = SimpleNamespace(
            send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=10), SimpleNamespace(message_id=11), SimpleNamespace(message_id=12)]),
            delete_message=AsyncMock(),
        )
        context = SimpleNamespace(bot=telegram)
        with patch.object(bot, "due_reminder_rows", side_effect=[([self.row], []), ([], [self.row]), ([], [self.row])]):
            await bot.reminder_tick(context)
            await bot.reminder_tick(context)
            await bot.reminder_tick(context)
        self.assertEqual([10, 11], [call.kwargs["message_id"] for call in telegram.delete_message.await_args_list])
        with bot.conn() as c:
            current = c.execute("SELECT last_sent_message_id FROM reminders WHERE id=?", (self.reminder["id"],)).fetchone()[0]
        self.assertEqual(12, current)

        query = SimpleNamespace(answer=AsyncMock(), data=f"ackrem:{self.reminder['id']}", message=SimpleNamespace(chat_id=self.chat_id, message_id=12))
        update = SimpleNamespace(callback_query=query, effective_user=None)
        with patch.object(bot, "register_bot_user"):
            await bot.callback(update, context)
            await bot.callback(update, context)
        self.assertEqual(3, telegram.delete_message.await_count)
        self.assertEqual(12, telegram.delete_message.await_args_list[-1].kwargs["message_id"])
        with bot.conn() as c:
            row = c.execute("SELECT acknowledged,next_followup_at,last_sent_message_id FROM reminders WHERE id=?", (self.reminder["id"],)).fetchone()
        self.assertEqual((1, "", None), tuple(row))

    async def test_failed_replacement_keeps_previous_active_message(self):
        telegram = SimpleNamespace(
            send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=10), bot.TimedOut("slow")]),
            delete_message=AsyncMock(),
        )
        context = SimpleNamespace(bot=telegram)
        with patch.object(bot, "due_reminder_rows", side_effect=[([self.row], []), ([], [self.row])]), patch.object(bot, "TELEGRAM_SEND_RETRIES", 0):
            await bot.reminder_tick(context)
            await bot.reminder_tick(context)
        telegram.delete_message.assert_not_awaited()
        with bot.conn() as c:
            current = c.execute("SELECT last_sent_message_id FROM reminders WHERE id=?", (self.reminder["id"],)).fetchone()[0]
        self.assertEqual(10, current)
