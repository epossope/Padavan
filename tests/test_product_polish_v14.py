import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot


class ProductPolishV14Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = str(Path(self.temp.name) / "noema.db")
        self.db_patch = patch.object(bot, "DB", self.db)
        self.db_patch.start()
        bot.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_people_taxonomy_never_promotes_random_relationship_phrase(self):
        person = bot.person_upsert(71, "Антон", relationship="друг по игре Rust, встреча сегодня в 16")
        saved = bot.get_people(71)["people"][0]
        self.assertEqual(person["id"], saved["id"])
        self.assertIn("Друзья", saved["groups"])
        self.assertIn("Хобби", saved["groups"])
        self.assertNotIn("друг по игре Rust, встреча сегодня в 16", saved["groups"])
        self.assertIn("встреча сегодня", saved["relationship"])

    def test_avatar_add_replace_and_remove_uses_existing_files_table(self):
        person_id = bot.person_upsert(72, "Иван", relationship="друг")["id"]
        image = Path(self.temp.name) / "avatar.jpg"
        image.write_bytes(b"jpeg")
        first = bot.save_image_to_db(72, "one.jpg", "image/jpeg", str(image), "person_avatar")["id"]
        second = bot.save_image_to_db(72, "two.jpg", "image/jpeg", str(image), "person_avatar")["id"]
        self.assertTrue(bot.set_person_avatar(72, person_id, first)["ok"])
        self.assertTrue(bot.set_person_avatar(72, person_id, second)["ok"])
        self.assertEqual(second, bot.get_people(72)["people"][0]["avatar_file_id"])
        self.assertTrue(bot.set_person_avatar(72, person_id, None)["ok"])
        self.assertIsNone(bot.get_people(72)["people"][0]["avatar_file_id"])

    def test_photo_caption_links_unambiguous_person_and_asks_on_ambiguous_phrase(self):
        image = Path(self.temp.name) / "friend.jpg"
        image.write_bytes(b"jpeg")
        file_id = bot.save_image_to_db(73, "friend.jpg", "image/jpeg", str(image), "image")["id"]
        result = SimpleNamespace(ok=True, item={"id": 9})
        with patch.object(bot, "_files_for_item", return_value=[{"id": file_id, "mime_type": "image/jpeg"}]):
            linked = bot.link_person_avatar_from_caption(73, "Это мой друг Иван", result)
            unclear = bot.link_person_avatar_from_caption(73, "Это фото моего друга", result)
        self.assertTrue(linked["linked"])
        self.assertEqual(file_id, bot.get_people(73)["people"][0]["avatar_file_id"])
        self.assertTrue(unclear["needs_clarification"])

    def test_speech_sanitizer_removes_emoji_only_from_tts(self):
        visible = "Привет 😊 ✅. Цена 500 ₽."
        spoken = bot.clean_tts(visible)
        self.assertEqual("Привет . Цена 500 ₽.", spoken)
        self.assertEqual("Привет 😊 ✅. Цена 500 ₽.", visible)

    def test_voice_preferences_are_per_user_and_validated(self):
        self.assertTrue(bot.set_voice_preferences(74, "ru-RU-SvetlanaNeural", .8, .9, .7)["ok"])
        prefs = bot.get_voice_preferences(74)
        self.assertEqual(.8, prefs["speed"])
        self.assertEqual("ru-RU-SvetlanaNeural", prefs["voice"])
        self.assertFalse(bot.set_voice_preferences(74, "bad voice <x>", 1, 1, 1)["ok"])
        self.assertEqual(1.0, bot.get_voice_preferences(75)["speed"])

    def test_edge_voice_adapter_receives_locked_rate_pitch_and_volume(self):
        communicator = SimpleNamespace(save=AsyncMock())
        with patch.object(bot.edge_tts, "Communicate", return_value=communicator) as factory:
            path = asyncio.run(bot.make_voice("Готово 😊", preferences={
                "voice": "ru-RU-DmitryNeural", "speed": 1.2, "pitch": .8, "volume": .75,
            }))
        Path(path).unlink(missing_ok=True)
        _, kwargs = factory.call_args
        self.assertEqual("+20%", kwargs["rate"])
        self.assertEqual("-10Hz", kwargs["pitch"])
        self.assertEqual("-25%", kwargs["volume"])


class ProductPolishUiContractTests(unittest.TestCase):
    def test_archive_people_voice_and_integrated_chat_contracts(self):
        app = Path("miniapp/app.js").read_text(encoding="utf-8")
        screens = Path("miniapp/screens.js").read_text(encoding="utf-8")
        css = Path("miniapp/design-match.css").read_text(encoding="utf-8")
        self.assertIn("data-archive-open", screens)
        self.assertIn("data-person-avatar", screens)
        self.assertIn("personGroups=['Семья','Друзья','Работа'", screens)
        self.assertIn("Extended_Pictographic", app)
        self.assertIn("data-voice-preview", app)
        self.assertIn("--chat-sphere-size:88px", css)
        self.assertIn(".preview-sheet", css)


if __name__ == "__main__":
    unittest.main()
