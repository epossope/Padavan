import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot


class InteractionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.db")
        self.db_patch.start()
        bot.init_db()
        self.owner_a = 401
        self.owner_b = 402

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_schema_has_nullable_person_id_and_lookup_index(self):
        with bot.conn() as connection:
            columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(interactions)")}
            indexes = {row["name"] for row in connection.execute("PRAGMA index_list(interactions)")}
        self.assertIn("person_id", columns)
        self.assertEqual(0, columns["person_id"]["notnull"])
        self.assertIn("idx_interactions_owner_person_date", indexes)

    def test_interaction_writes_person_id_and_canonical_snapshot(self):
        person = bot.person_upsert(self.owner_a, "Иван Петров")
        result = bot.person_interaction(
            self.owner_a, person_id=person["id"], interaction="Обсуждали Noema",
        )
        self.assertTrue(result["ok"])
        self.assertEqual((person["id"], "Иван Петров"), (result["person_id"], result["name"]))
        with bot.conn() as connection:
            row = connection.execute("SELECT person_id,person_name FROM interactions WHERE id=?", (result["id"],)).fetchone()
        self.assertEqual((person["id"], "Иван Петров"), (row["person_id"], row["person_name"]))

    def test_rename_keeps_person_id_interaction_visible(self):
        person = bot.person_upsert(self.owner_a, "Иван Петров")
        bot.person_interaction(self.owner_a, person_id=person["id"], interaction="Обсуждали Noema")
        self.assertTrue(bot.update_person(self.owner_a, person["id"], name="Иван Соколов")["ok"])
        profile = bot.get_people(self.owner_a)["people"][0]
        self.assertEqual("Иван Соколов", profile["name"])
        self.assertEqual("Обсуждали Noema", profile["recent_interactions"][0]["interaction"])
        self.assertEqual(person["id"], profile["recent_interactions"][0]["person_id"])

    def test_foreign_person_id_is_rejected(self):
        foreign = bot.person_upsert(self.owner_a, "Иван")
        result = bot.person_interaction(self.owner_b, person_id=foreign["id"], interaction="Чужая запись")
        self.assertFalse(result["ok"])
        self.assertEqual("person_not_found", result["error"])
        with bot.conn() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM interactions").fetchone()[0])

    def test_legacy_name_only_row_migrates_when_unambiguous(self):
        person = bot.person_upsert(self.owner_a, "Иван Петров")
        with bot.conn() as connection:
            connection.execute("""INSERT INTO interactions(
                chat_id,person_name,interaction,interaction_date,interaction_type,created_at
            ) VALUES(?,?,?,?,?,?)""", (self.owner_a, "иван петров", "Legacy", "2026-09-10", "other", "now"))
            bot._migrate_legacy_interaction_person_ids(connection)
            row = connection.execute("SELECT person_id FROM interactions WHERE interaction='Legacy'").fetchone()
        self.assertEqual(person["id"], row["person_id"])

    def test_ambiguous_legacy_row_is_not_guessed_or_attached(self):
        first = bot.person_upsert(self.owner_a, "Иван")
        with bot.conn() as connection:
            connection.execute("INSERT INTO people(chat_id,name,updated_at) VALUES(?,?,?)",
                               (self.owner_a, "иван", "now"))
            connection.execute("""INSERT INTO interactions(
                chat_id,person_name,interaction,interaction_date,interaction_type,created_at
            ) VALUES(?,?,?,?,?,?)""", (self.owner_a, "ИВАН", "Ambiguous legacy", "2026-09-10", "other", "now"))
            bot._migrate_legacy_interaction_person_ids(connection)
            row = connection.execute("SELECT person_id FROM interactions WHERE interaction='Ambiguous legacy'").fetchone()
        self.assertIsNone(row["person_id"])
        profiles = {person["id"]: person for person in bot.get_people(self.owner_a)["people"]}
        self.assertEqual([], profiles[first["id"]]["recent_interactions"])

    def test_name_only_person_interaction_remains_backward_compatible(self):
        result = bot.person_interaction(self.owner_a, name="Анна", interaction="Познакомились")
        self.assertTrue(result["ok"])
        self.assertIsInstance(result["person_id"], int)
        people = bot.get_people(self.owner_a)["people"]
        self.assertEqual(("Анна", "Познакомились"),
                         (people[0]["name"], people[0]["recent_interactions"][0]["interaction"]))

    def test_delete_preserves_history_as_unlinked_snapshot(self):
        person = bot.person_upsert(self.owner_a, "Иван Петров")
        interaction = bot.person_interaction(self.owner_a, person_id=person["id"], interaction="История")
        self.assertEqual(1, bot.delete_person(self.owner_a, person["id"])["deleted"])
        with bot.conn() as connection:
            row = connection.execute("SELECT person_id,identity_detached,person_name,interaction FROM interactions WHERE id=?",
                                     (interaction["id"],)).fetchone()
        self.assertEqual((None, 1, "Иван Петров", "История"),
                         (row["person_id"], row["identity_detached"], row["person_name"], row["interaction"]))
        replacement = bot.person_upsert(self.owner_a, "Иван Петров")
        self.assertEqual([], bot.get_people(self.owner_a)["people"][0]["recent_interactions"])
        with bot.conn() as connection:
            bot._migrate_legacy_interaction_person_ids(connection)
            row = connection.execute("SELECT person_id FROM interactions WHERE id=?", (interaction["id"],)).fetchone()
        self.assertIsNone(row["person_id"])
        self.assertIsInstance(replacement["id"], int)
