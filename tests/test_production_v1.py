"""Production V1 regressions for one shared Telegram/Mini App conversation."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot


class CanonicalConversationE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_miniapp_then_telegram_share_one_history_and_context(self):
        original_db = bot.DB
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            try:
                bot.DB = Path(directory) / "production-v1.sqlite3"
                bot.init_db()
                chat_id = 701

                # This is the same core entry point used by Mini App chat_stream.
                with patch.object(bot, "direct_live_request", return_value="Ответ из Mini App"):
                    mini_events = list(bot.stream_agent_response(chat_id, "Сообщение из Mini App"))
                self.assertEqual(mini_events[-1]["type"], "done")

                # Telegram streaming calls the same entry point and therefore
                # must see the Mini App turn in its next context.
                message = SimpleNamespace(reply_text=AsyncMock())
                update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_message=message)
                context = SimpleNamespace(bot=SimpleNamespace(send_message_draft=AsyncMock()))
                with patch.object(bot, "direct_live_request", return_value="Ответ из Telegram"):
                    self.assertTrue(await bot.stream_answer_to_telegram(update, context, "Сообщение из Telegram"))

                history = bot.history(chat_id, 10)
                self.assertEqual([item["content"] for item in history], [
                    "Сообщение из Mini App", "Ответ из Mini App",
                    "Сообщение из Telegram", "Ответ из Telegram",
                ])
                context_contents = [item["content"] for item in bot.conversation_context(chat_id)]
                self.assertIn("Сообщение из Mini App", context_contents)
                self.assertIn("Ответ из Telegram", context_contents)
            finally:
                bot.DB = original_db


if __name__ == "__main__":
    unittest.main()
