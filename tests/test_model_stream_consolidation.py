import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
from streaming_runtime import StreamAccumulator


class ModelStreamConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.db")
        self.db_patch.start()
        bot.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_global_primary_ignores_legacy_user_models_and_updates_live(self):
        with bot.conn() as database:
            database.executemany(
                "INSERT INTO user_settings(chat_id,primary_model,fallback_model) VALUES(?,?,?)",
                [(101, "legacy/a", "legacy/fallback"), (102, "legacy/b", "")],
            )
        bot.set_admin_runtime_config(1, "fast_model", "admin/global-a")
        self.assertEqual([bot.resolved_chat_models(cid)["primary"] for cid in (100, 101, 102)],
                         ["admin/global-a"] * 3)
        bot.set_admin_runtime_config(1, "fast_model", "admin/global-b")
        self.assertEqual([bot.resolved_chat_models(cid)["primary"] for cid in (100, 101, 102)],
                         ["admin/global-b"] * 3)
        self.assertEqual(bot.effective_user_ai_config(101)["personal"]["model_override"], "")

    def test_provider_reasoning_and_split_tags_never_enter_visible_accumulator(self):
        accumulator = StreamAccumulator()
        chunks = [
            {"choices": [{"delta": {"reasoning": "private", "content": "<thi"}}]},
            {"choices": [{"delta": {"thinking": "private", "content": "nk>hidden</thi"}}]},
            {"choices": [{"delta": {"content": "nk>Готово"}}]},
        ]
        visible = "".join(part for payload in chunks for part in accumulator.add(payload)) + accumulator.finish()
        self.assertEqual(visible, "Готово")
        self.assertEqual(accumulator.message()["content"], "Готово")
        self.assertGreaterEqual(accumulator.reasoning_chunks_dropped, 2)

    def test_semantic_gender_resolves_to_current_admin_voice_without_storing_voice_id(self):
        self.assertTrue(bot.set_voice_preferences(55, "female", 1.1, 1.0, 0.8)["ok"])
        bot.set_admin_runtime_config(1, "tts_female_voice", "ru-RU-AdminFemaleNeural")
        prefs = bot.get_voice_preferences(55)
        self.assertEqual(prefs["gender"], "female")
        self.assertEqual(prefs["voice"], "ru-RU-AdminFemaleNeural")
        raw = bot.app_setting("voice_preferences:55")
        self.assertIn('"gender": "female"', raw)
        self.assertNotIn("AdminFemaleNeural", raw)

    def test_canonical_persistence_uses_same_visible_stream_text(self):
        visible = "Ответ без reasoning."
        with patch.object(bot, "direct_live_request", return_value=visible):
            events = list(bot.stream_agent_response(77, "тест"))
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "delta"), visible)
        self.assertEqual(events[-1]["text"], visible)
        with bot.conn() as database:
            row = database.execute("SELECT content FROM messages WHERE chat_id=77 AND role='assistant'").fetchone()
        self.assertEqual(row["content"], visible)


if __name__ == "__main__":
    unittest.main()
