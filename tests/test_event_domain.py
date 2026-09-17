import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import bot


class EventDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = Path(self.temp.name) / "noema.db"
        self.db_patch = patch.object(bot, "DB", self.database)
        self.db_patch.start()
        bot.init_db()
        self.owner_a = 101
        self.owner_b = 202
        self.ivan_a = bot.person_upsert(self.owner_a, "Иван")["id"]
        self.ivan_b = bot.person_upsert(self.owner_b, "Иван")["id"]

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def create_event(self, owner=None, **overrides):
        tomorrow = datetime.now(bot.timezone_for(self.owner_a)).date() + timedelta(days=1)
        values = {
            "title": "Встреча по Noema",
            "starts_at": f"{tomorrow.isoformat()}T15:00:00",
            "ends_at": f"{tomorrow.isoformat()}T16:00:00",
            "timezone": bot.timezone_name_for(self.owner_a),
            "kind": "meeting",
            "participant_ids": [self.ivan_a],
        }
        values.update(overrides)
        return bot.event_create(owner or self.owner_a, **values)

    def test_schema_and_required_indexes_are_migration_safe(self):
        with bot.conn() as connection:
            event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(events)")}
            participant_columns = {row["name"] for row in connection.execute("PRAGMA table_info(event_participants)")}
            indexes = {row["name"] for row in connection.execute("PRAGMA index_list(events)")}
            participant_indexes = {row["name"] for row in connection.execute("PRAGMA index_list(event_participants)")}
        self.assertEqual({
            "id", "chat_id", "kind", "title", "starts_at", "ends_at", "all_day", "timezone",
            "location", "notes", "status", "project_id", "source_turn_id", "created_at", "updated_at",
        }, event_columns)
        self.assertEqual({"event_id", "person_id", "role"}, participant_columns)
        self.assertTrue({"idx_events_owner_start", "idx_events_owner_status"} <= indexes)
        self.assertIn("idx_event_participants_person", participant_indexes)

    def test_crud_retains_timezone_and_participants(self):
        created = self.create_event(location="Офис", notes="Обсудить релиз")
        self.assertTrue(created["ok"])
        event_id = created["id"]
        event = bot.event_get(self.owner_a, event_id)["event"]
        self.assertEqual("meeting", event["kind"])
        self.assertEqual(bot.timezone_name_for(self.owner_a), event["timezone"])
        self.assertIn("+00:00", event["starts_at"])
        self.assertEqual(self.ivan_a, event["participants"][0]["person_id"])

        updated = bot.event_update(self.owner_a, event_id, title="Новая тема", kind="call")
        self.assertTrue(updated["ok"])
        self.assertEqual(("Новая тема", "call"), (updated["event"]["title"], updated["event"]["kind"]))
        self.assertEqual(1, bot.event_search(self.owner_a, query="новая тема")["count"])
        self.assertEqual(1, bot.event_list(self.owner_a)["count"])
        self.assertEqual(1, bot.event_delete(self.owner_a, event_id)["deleted"])
        self.assertEqual([], bot.event_list(self.owner_a)["events"])
        self.assertEqual("not_found", bot.event_get(self.owner_a, event_id)["error"])

    def test_owner_cannot_read_update_or_delete_another_event(self):
        event_id = self.create_event()["id"]
        self.assertEqual("not_found", bot.event_get(self.owner_b, event_id)["error"])
        self.assertEqual("not_found", bot.event_update(self.owner_b, event_id, title="Украдено")["error"])
        self.assertEqual("not_found", bot.event_delete(self.owner_b, event_id)["error"])
        self.assertEqual(1, bot.event_list(self.owner_a)["count"])

    def test_cross_owner_participant_link_is_rejected(self):
        event_id = self.create_event(participant_ids=[])["id"]
        rejected = bot.event_participant_add(self.owner_a, event_id, self.ivan_b)
        self.assertFalse(rejected["ok"])
        self.assertEqual("person_not_found", rejected["error"])
        accepted = bot.event_participant_add(self.owner_a, event_id, self.ivan_a, role="guest")
        self.assertTrue(accepted["ok"])
        participant = bot.event_get(self.owner_a, event_id)["event"]["participants"][0]
        self.assertEqual((self.ivan_a, "guest"), (participant["person_id"], participant["role"]))
        self.assertEqual(1, bot.event_participant_remove(self.owner_a, event_id, self.ivan_a)["removed"])

    def test_event_create_rejects_cross_owner_participant_atomically(self):
        result = self.create_event(participant_ids=[self.ivan_b])
        self.assertEqual("participant_not_found", result["error"])
        self.assertEqual([], bot.event_list(self.owner_a)["events"])

    def test_person_delete_removes_only_relation_not_event(self):
        event_id = self.create_event()["id"]
        self.assertEqual(1, bot.delete_person(self.owner_a, self.ivan_a)["deleted"])
        event = bot.event_get(self.owner_a, event_id)["event"]
        self.assertEqual("Встреча по Noema", event["title"])
        self.assertEqual([], event["participants"])

    def test_execute_tool_uses_trusted_owner_not_model_chat_id(self):
        tomorrow = datetime.now(bot.timezone_for(self.owner_a)).date() + timedelta(days=1)
        result = bot.execute_tool(self.owner_a, "event_create", {
            "chat_id": self.owner_b, "title": "Trusted owner",
            "starts_at": f"{tomorrow.isoformat()}T12:00:00", "participant_ids": [self.ivan_a],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(1, bot.event_list(self.owner_a)["count"])
        self.assertEqual(0, bot.event_list(self.owner_b)["count"])

    def test_day_plan_adds_events_without_changing_tasks_or_reminders(self):
        tomorrow = datetime.now(bot.timezone_for(self.owner_a)).date() + timedelta(days=1)
        bot.add_task(self.owner_a, "Задача", due_date=tomorrow.isoformat())
        bot.save_reminder(self.owner_a, "Напоминание", f"{tomorrow.isoformat()}T10:00:00")
        self.create_event(starts_at=f"{tomorrow.isoformat()}T15:00:00", ends_at="")
        plan = bot.get_plan_for_date(self.owner_a, tomorrow.isoformat())
        self.assertEqual(["Встреча по Noema"], [event["title"] for event in plan["events"]])
        self.assertEqual(["Задача"], [task["text"] for task in plan["tasks"]])
        self.assertEqual(["Напоминание"], [reminder["text"] for reminder in plan["reminders"]])

    def test_conversation_claim_does_not_synthesize_exact_event(self):
        bot.add_message(self.owner_a, "user", "Завтра встреча с Иваном")
        self.assertEqual([], bot.event_list(self.owner_a)["events"])

    def test_controlled_validation_errors(self):
        self.assertEqual("missing_title", bot.event_create(self.owner_a, "", "2026-01-01")["error"])
        self.assertEqual("invalid_kind", bot.event_create(self.owner_a, "X", "2026-01-01", kind="party")["error"])
        self.assertEqual("invalid_datetime_or_participant", bot.event_create(self.owner_a, "X", "not-a-date")["error"])


class ExistingDatabaseMigrationTests(unittest.TestCase):
    def test_existing_database_migrates_forward_without_reset(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            database = Path(temporary) / "legacy.db"
            with sqlite3.connect(database) as connection:
                connection.execute("""CREATE TABLE tasks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,text TEXT,due_date TEXT,
                    priority TEXT,status TEXT DEFAULT 'open',created_at TEXT)""")
                connection.execute("INSERT INTO tasks(chat_id,text,due_date,priority,status,created_at) VALUES(1,'legacy','','normal','open','now')")
            with patch.object(bot, "DB", database):
                bot.init_db()
                with bot.conn() as connection:
                    self.assertEqual("legacy", connection.execute("SELECT text FROM tasks WHERE chat_id=1").fetchone()[0])
                    self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'").fetchone())
                    self.assertIn("completed_at", {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")})
