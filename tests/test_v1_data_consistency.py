import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
from streaming_runtime import (ToolPackResolver, artifact_request_extension,
                               artifact_request_instruction, enforce_artifact_request)
from telegram_renderer import TelegramRenderer


class V1DataConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(bot, "DB", Path(self.temp.name) / "noema.db")
        self.db_patch.start()
        bot.init_db()
        for amount, description, category in ((280, "sweets", "food"), (500, "products", "food"), (3000, "fuel", "transport")):
            bot.add_expense(101, amount, description, category=category)

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_finance_summary_and_rows_are_exact_and_owner_scoped(self):
        bot.add_expense(202, 9999, "other user", category="private")
        summary = bot.finance_summary(101, "current_month")
        rows = bot.finance_list_transactions(101, "current_month", kind="expense")
        self.assertEqual(3780, summary["expenses"])
        self.assertEqual(3, summary["count"])
        self.assertEqual(3, rows["count"])
        self.assertEqual({"sweets", "products", "fuel"}, {row["description"] for row in rows["items"]})
        self.assertFalse(any(row["description"] == "other user" for row in rows["items"]))

    def test_finance_filters_pagination_and_empty_state(self):
        fuel = bot.finance_list_transactions(101, "current_month", query="fuel", limit=1)
        self.assertEqual(1, fuel["count"])
        self.assertEqual("fuel", fuel["items"][0]["description"])
        empty = bot.finance_summary(303, "current_month")
        self.assertEqual(0, empty["count"])
        self.assertEqual(0, empty["expenses"])

    def test_explicit_file_and_exact_finance_requests_select_canonical_tools(self):
        router = ToolPackResolver()
        self.assertEqual("artifact_create", router.required_tool_choice("сделай Word документ")["function"]["name"])
        self.assertEqual("finance_summary", router.required_tool_choice("сколько потратил за месяц")["function"]["name"])
        self.assertEqual("finance_list_transactions", router.required_tool_choice("сделай таблицу моих трат")["function"]["name"])
        selected = {item["function"]["name"] for item in router.resolve(bot.TOOLS, "сделай таблицу моих трат")}
        self.assertTrue({"finance_list_transactions", "artifact_create"}.issubset(selected))

    def test_explicit_artifact_format_is_enforced_server_side(self):
        self.assertEqual(".xlsx", artifact_request_extension("сделай таблицу моих трат"))
        self.assertEqual(".html", artifact_request_extension("создай HTML файл"))
        self.assertEqual(".docx", artifact_request_extension("сделай Word документ"))
        self.assertEqual("", artifact_request_extension("покажи таблицу прямо здесь"))
        self.assertEqual(
            "monthly_expenses.xlsx",
            enforce_artifact_request({"filename": "monthly_expenses.docx"}, "сделай таблицу моих трат")["filename"],
        )
        self.assertIn("обязательно вызови artifact_create", artifact_request_instruction("создай HTML файл").lower())

    def test_telegram_renderer_preserves_content_without_raw_markdown(self):
        source = "# Заголовок\n\n**Важный текст** и *курсив*\n\n### Раздел\n- пункт 1\n- пункт 2\n```py\nprint('ok')\n```"
        rendered = TelegramRenderer.render(source)
        self.assertNotIn("**", rendered)
        self.assertNotIn("*курсив*", rendered)
        self.assertNotIn("###", rendered)
        self.assertNotIn("```", rendered)
        self.assertIn("Заголовок", rendered)
        self.assertIn("• пункт 1", rendered)
        self.assertIn("<pre>", rendered)

    def test_miniapp_renderer_is_safe_presentation_boundary(self):
        source = (Path(__file__).parents[1] / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.NoemaMiniAppRenderer", source)
        self.assertIn("renderAssistantContent(canonical)", source)
        self.assertIn("message.role==='assistant'?renderAssistantContent(message.content):esc(message.content)", source)
        self.assertIn("rel=\"noopener noreferrer\"", source)
