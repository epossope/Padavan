import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import bot
from entity_resolver import (EntityResolver, ResolutionStatus,
                             normalize_person_identity)
from semantic_core import EntityReference


class EntityResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.db")
        self.db_patch.start()
        bot.init_db()
        self.owner_a = 301
        self.owner_b = 302
        self.resolver = EntityResolver(bot.conn, bot.person_upsert)

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_unicode_normalization_and_canonical_exact_match(self):
        created = bot.person_upsert(self.owner_a, "  Иван   Петров  ")
        resolved = self.resolver.resolve_person(self.owner_a, "иВАН   пЕТРОВ")
        self.assertEqual(ResolutionStatus.RESOLVED, resolved.status)
        self.assertEqual(created["id"], resolved.resolved_id)
        self.assertEqual("Иван Петров", resolved.canonical_name)
        self.assertEqual("иван петров", normalize_person_identity(" Иван   ПЕТРОВ "))

    def test_alias_schema_has_owner_and_person_indexes(self):
        with bot.conn() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(person_aliases)")}
            indexes = {row["name"] for row in connection.execute("PRAGMA index_list(person_aliases)")}
        self.assertEqual({"id", "chat_id", "person_id", "alias", "normalized_alias", "created_at"}, columns)
        self.assertTrue({"idx_person_aliases_owner_normalized", "idx_person_aliases_person"} <= indexes)

    def test_person_upsert_prevents_normalized_duplicate(self):
        first = bot.person_upsert(self.owner_a, "Иван Петров")
        second = bot.person_upsert(self.owner_a, "  иВАН   петров ", notes="Коллега")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual("Иван Петров", second["name"])
        self.assertEqual(1, len(bot.get_people(self.owner_a)["people"]))

    def test_explicit_alias_and_owner_isolation(self):
        person_a = bot.person_upsert(self.owner_a, "Иван Петров", aliases=["Ваня", "Ванька"])
        person_b = bot.person_upsert(self.owner_b, "Иван Петров", aliases=["Ваня"])
        resolved_a = self.resolver.resolve_person(self.owner_a, "ваня")
        resolved_b = self.resolver.resolve_person(self.owner_b, "ВАНЯ")
        self.assertEqual(person_a["id"], resolved_a.resolved_id)
        self.assertEqual(person_b["id"], resolved_b.resolved_id)
        self.assertEqual("explicit_alias", resolved_a.reason)

    def test_alias_cannot_cross_owner(self):
        foreign = bot.person_upsert(self.owner_b, "Иван")
        result = bot.person_alias_add(self.owner_a, foreign["id"], "Ваня")
        self.assertFalse(result["ok"])
        self.assertEqual("person_not_found", result["error"])

    def test_same_alias_for_two_people_is_ambiguous(self):
        first = bot.person_upsert(self.owner_a, "Александр Иванов", aliases=["Саша"])
        second = bot.person_upsert(self.owner_a, "Александр Петров", aliases=["Саша"])
        result = self.resolver.resolve_person(self.owner_a, "Саша")
        self.assertEqual(ResolutionStatus.AMBIGUOUS, result.status)
        self.assertIsNone(result.resolved_id)
        self.assertEqual({first["id"], second["id"]}, {candidate.id for candidate in result.candidates})

    def test_unambiguous_short_name_and_ambiguous_short_name(self):
        first = bot.person_upsert(self.owner_a, "Иван Петров")
        self.assertEqual(first["id"], self.resolver.resolve_person(self.owner_a, "Иван").resolved_id)
        bot.person_upsert(self.owner_a, "Иван Соколов")
        self.assertEqual(ResolutionStatus.AMBIGUOUS, self.resolver.resolve_person(self.owner_a, "Иван").status)

    def test_context_pronouns_require_one_owner_scoped_recent_person(self):
        artem = bot.person_upsert(self.owner_a, "Артём")
        for mention in ("он", "она", "с ним", "с ней", "ему", "ей", "его", "её"):
            with self.subTest(mention=mention):
                result = self.resolver.resolve_person(self.owner_a, mention, conversation_context={
                    "recent_entities": [{"type": "person", "id": artem["id"], "name": "Untrusted label"}],
                })
                self.assertEqual(ResolutionStatus.RESOLVED, result.status)
                self.assertEqual((artem["id"], "Артём"), (result.resolved_id, result.canonical_name))

    def test_context_with_multiple_people_is_ambiguous(self):
        artem = bot.person_upsert(self.owner_a, "Артём")
        anna = bot.person_upsert(self.owner_a, "Анна")
        result = self.resolver.resolve_person(self.owner_a, "он", conversation_context={
            "recent_entities": [
                {"type": "person", "id": artem["id"]},
                {"type": "person", "id": anna["id"]},
            ],
        })
        self.assertEqual(ResolutionStatus.AMBIGUOUS, result.status)
        self.assertEqual({artem["id"], anna["id"]}, {candidate.id for candidate in result.candidates})

    def test_foreign_context_person_does_not_resolve(self):
        foreign = bot.person_upsert(self.owner_b, "Артём")
        result = self.resolver.resolve_person(self.owner_a, "он", conversation_context={
            "recent_entities": [{"type": "person", "id": foreign["id"]}],
        })
        self.assertEqual(ResolutionStatus.NOT_FOUND, result.status)

    def test_allow_create_is_explicit_and_returns_created(self):
        missing = self.resolver.resolve_person(self.owner_a, "артём", allow_create=False)
        self.assertEqual(ResolutionStatus.NOT_FOUND, missing.status)
        created = self.resolver.resolve_person(self.owner_a, "артём", allow_create=True)
        self.assertEqual(ResolutionStatus.CREATED, created.status)
        again = self.resolver.resolve_person(self.owner_a, "АРТЁМ", allow_create=True)
        self.assertEqual(ResolutionStatus.RESOLVED, again.status)
        self.assertEqual(created.resolved_id, again.resolved_id)

    def test_allow_create_rejects_non_name_mentions(self):
        for mention in ("кто такой Артём?", "возможно поговорю с каким-то Сергеем", "может быть Иван", "он"):
            with self.subTest(mention=mention):
                result = self.resolver.resolve_person(self.owner_a, mention, allow_create=True)
                self.assertEqual(ResolutionStatus.NOT_FOUND, result.status)
        self.assertEqual([], bot.get_people(self.owner_a)["people"])

    def test_entity_reference_returns_resolved_copy_without_owner(self):
        person = bot.person_upsert(self.owner_a, "Иван Петров", aliases=["Ваня"])
        source = EntityReference(type="person", mention="Ваня")
        resolved, result = self.resolver.resolve_reference(self.owner_a, source)
        self.assertIsNone(source.resolved_id)
        self.assertEqual(person["id"], resolved.resolved_id)
        self.assertEqual("Иван Петров", resolved.attributes["canonical_name"])
        self.assertNotIn("chat_id", resolved.attributes)
        self.assertEqual(ResolutionStatus.RESOLVED, result.status)
        self.assertEqual("RESOLVED", result.to_dict()["status"])
        self.assertNotIn("chat_id", result.to_dict())

    def test_alias_event_link_and_rename_keep_stable_person_id(self):
        person = bot.person_upsert(self.owner_a, "Иван Петров", aliases=["Ваня"])
        resolved = self.resolver.resolve_person(self.owner_a, "Ваня")
        day = datetime.now(bot.timezone_for(self.owner_a)).date() + timedelta(days=1)
        event = bot.event_create(
            self.owner_a, "Встреча", f"{day.isoformat()}T15:00:00",
            kind="meeting", participant_ids=[resolved.resolved_id],
        )
        self.assertTrue(bot.update_person(self.owner_a, person["id"], name="Иван Соколов")["ok"])
        after = self.resolver.resolve_person(self.owner_a, "Ваня")
        linked = bot.event_get(self.owner_a, event["id"])["event"]["participants"][0]
        self.assertEqual(person["id"], after.resolved_id)
        self.assertEqual((person["id"], "Иван Соколов"), (linked["person_id"], linked["name"]))
        self.assertEqual(1, len(bot.get_people(self.owner_a)["people"]))

    def test_delete_cleans_alias_and_event_relation_but_keeps_event(self):
        person = bot.person_upsert(self.owner_a, "Иван Петров", aliases=["Ваня"])
        day = datetime.now(bot.timezone_for(self.owner_a)).date() + timedelta(days=1)
        event = bot.event_create(self.owner_a, "Встреча", f"{day.isoformat()}T15:00:00",
                                 participant_ids=[person["id"]])
        self.assertEqual(1, bot.delete_person(self.owner_a, person["id"])["deleted"])
        self.assertEqual(ResolutionStatus.NOT_FOUND, self.resolver.resolve_person(self.owner_a, "Ваня").status)
        self.assertEqual([], bot.event_get(self.owner_a, event["id"])["event"]["participants"])
        with bot.conn() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM person_aliases").fetchone()[0])
