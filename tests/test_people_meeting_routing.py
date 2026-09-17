import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import bot
from miniapp_api import register_miniapp
from streaming_runtime import ToolPackResolver


class PeopleMeetingRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.db")
        self.db_patch.start()
        bot.init_db()
        self.chat_id = 761

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_meeting_intents_expose_people_and_planning_tools(self):
        resolver = ToolPackResolver()
        for text in (
            "Встреча с Иваном завтра в 15:00",
            "Созвон с Аней завтра",
            "В пятницу встретиться с Олегом",
        ):
            with self.subTest(text=text):
                names = resolver.select_names(text)
                self.assertTrue({"person_upsert", "person_interaction"} <= names)
                self.assertTrue({"add_task", "set_reminder"} <= names)

    def test_relationship_and_person_saving_intents_expose_people_tools(self):
        resolver = ToolPackResolver()
        for text in (
            "Добавь Петю, это мой коллега",
            "Саша — мой брат",
            "Запомни Аню, это моя подруга",
            "Олег работает со мной над Noema",
        ):
            with self.subTest(text=text):
                names = resolver.select_names(text)
                self.assertTrue({"person_upsert", "person_interaction"} <= names)

    def test_mocked_exact_time_meeting_calls_preserve_person_interaction_and_reminder(self):
        """Model tool-call contract: named timed meetings must use all three stores."""
        tomorrow = datetime.now(bot.timezone_for(self.chat_id)).date() + timedelta(days=1)
        remind_at = f"{tomorrow.isoformat()}T15:00:00"
        calls = [
            ("person_upsert", {"name": "Иван"}),
            ("person_interaction", {"name": "Иван", "interaction": "Встреча по проекту Noema", "interaction_date": tomorrow.isoformat(), "interaction_type": "meeting"}),
            ("set_reminder", {"text": "Встреча с Иваном по проекту Noema", "remind_at": remind_at}),
        ]
        for name, args in calls:
            self.assertTrue(bot.execute_tool(self.chat_id, name, args)["ok"])

        people = bot.get_people(self.chat_id)["people"]
        self.assertEqual(["Иван"], [person["name"] for person in people])
        self.assertEqual("Встреча по проекту Noema", people[0]["recent_interactions"][0]["interaction"])
        plan = bot.get_plan_for_date(self.chat_id, tomorrow.isoformat())
        self.assertEqual(["Встреча с Иваном по проекту Noema"], [item["text"] for item in plan["reminders"]])
        # A second upsert is an update of the same owner-scoped record.
        bot.execute_tool(self.chat_id, "person_upsert", {"name": "иван", "relationship": "коллега"})
        self.assertEqual(1, len(bot.get_people(self.chat_id)["people"]))

    def test_mocked_date_only_meeting_calls_preserve_person_interaction_and_task(self):
        """Without a time, the daily-plan representation is a dated task."""
        friday = datetime.now(bot.timezone_for(self.chat_id)).date() + timedelta(days=4)
        calls = [
            ("person_upsert", {"name": "Олег"}),
            ("person_interaction", {"name": "Олег", "interaction": "Встреча", "interaction_date": friday.isoformat(), "interaction_type": "meeting"}),
            ("add_task", {"text": "Встретиться с Олегом", "due_date": friday.isoformat()}),
        ]
        for name, args in calls:
            self.assertTrue(bot.execute_tool(self.chat_id, name, args)["ok"])

        person = bot.get_people(self.chat_id)["people"][0]
        self.assertEqual("Олег", person["name"])
        self.assertEqual("Встреча", person["recent_interactions"][0]["interaction"])
        plan = bot.get_plan_for_date(self.chat_id, friday.isoformat())
        self.assertEqual(["Встретиться с Олегом"], [item["text"] for item in plan["tasks"]])

    def test_people_state_and_screen_contract_keep_new_person_renderable(self):
        bot.person_upsert(self.chat_id, "Аня", relationship="подруга")
        payload = asyncio.run(self._miniapp_state())
        self.assertEqual("Аня", payload["people"][0]["name"])
        state_people = bot.get_people(self.chat_id)["people"]
        self.assertEqual("Аня", state_people[0]["name"])
        root = Path(__file__).parents[1]
        screen_source = (root / "miniapp" / "screens.js").read_text(encoding="utf-8")
        self.assertIn("found.map((p,index)=>", screen_source)
        self.assertIn("esc(p.name)", screen_source)

    async def _miniapp_state(self):
        app = web.Application()
        app["telegram_app"] = type("TelegramApp", (), {"bot": None})()
        with patch.object(bot, "valid_webapp_user", return_value={"id": self.chat_id}):
            register_miniapp(app, bot)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                response = await client.post("/api/v1/miniapp", json={
                    "init_data": "trusted-test-init-data", "action": "state", "args": {},
                })
                self.assertEqual(200, response.status)
                return (await response.json())["data"]
            finally:
                await client.close()

    def test_system_prompt_states_internal_meeting_contract(self):
        prompt = bot.system_prompt(self.chat_id)
        self.assertIn("Внешней Calendar integration не существует", prompt)
        self.assertIn("профиль человека + interaction + task/reminder", prompt)
