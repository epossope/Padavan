import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import bot
from media_cache import image_thumbnail


class MediaThumbnailTests(unittest.TestCase):
    def test_thumbnail_is_bounded_and_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.png"
            Image.new("RGB", (1600, 900), "red").save(source)
            first, first_hit = image_thumbnail(source, Path(temporary) / "cache")
            second, second_hit = image_thumbnail(source, Path(temporary) / "cache")
            self.assertIsNotNone(first)
            self.assertFalse(first_hit)
            self.assertEqual(first, second)
            self.assertTrue(second_hit)
            with Image.open(first) as thumbnail:
                self.assertLessEqual(max(thumbnail.size), 480)


class PersonMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.sqlite3")
        self.db_patch.start()
        bot.init_db()
        self.person = bot.person_upsert(7, "Иван")
        image = Path(self.temp.name) / "photo.png"
        Image.new("RGB", (32, 32), "blue").save(image)
        self.file = bot.save_image_to_db(7, "photo.png", "image/png", str(image), "image", "")

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_profile_photo_is_a_relation_without_duplicate_file(self):
        linked = bot.set_person_avatar(7, self.person["id"], self.file["id"])
        self.assertTrue(linked["ok"])
        media = bot.person_media_list(7, self.person["id"])["items"]
        self.assertEqual({item["file_id"] for item in media}, {self.file["id"]})
        self.assertTrue(any(item["relation_type"] == "profile_photo" and item["is_current"] for item in media))
        self.assertEqual(bot.get_people(7)["people"][0]["avatar_file_id"], self.file["id"])

    def test_deleting_file_clears_current_avatar_and_relations(self):
        bot.set_person_avatar(7, self.person["id"], self.file["id"])
        self.assertTrue(bot.delete_file(7, self.file["id"])["ok"])
        self.assertEqual(bot.person_media_list(7, self.person["id"])["items"], [])
        self.assertIsNone(bot.get_people(7)["people"][0]["avatar_file_id"])
