import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from artifact_service import ArtifactService
from streaming_runtime import SpeechTextPolicy, ToolPackResolver


class ArtifactServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"

        def connect():
            connection = sqlite3.connect(self.db)
            connection.row_factory = sqlite3.Row
            return connection

        self.service = ArtifactService(connect, Path(self.tmp.name) / "artifacts", max_bytes=1024 * 1024, ttl_hours=24)

    def tearDown(self):
        self.tmp.cleanup()

    def test_source_artifact_and_owner_authorization(self):
        item = self.service.create(11, "parser.py", "print('ok')\n")
        self.assertEqual(item["filename"], "parser.py")
        self.assertEqual(self.service.metadata(item["artifact_id"], 11)["mime_type"], "text/x-python")
        with self.assertRaises(PermissionError):
            self.service.metadata(item["artifact_id"], 12)
        self.assertEqual(self.service.metadata(item["artifact_id"], 12, is_admin=True)["artifact_id"], item["artifact_id"])

    def test_paths_and_executables_are_rejected(self):
        for name in ("../evil.py", "C:\\evil.py", "/tmp/evil.py", "payload.exe"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.service.create(1, name, "x")

    def test_docx_xlsx_and_pdf_fallback_are_valid_packages(self):
        docx = self.service.create(1, "report.docx", "Заголовок\nТекст")
        xlsx = self.service.create(1, "table.xlsx", rows=[["Имя", "Сумма"], ["A", 10]])
        pdf = self.service.create(1, "report.pdf", "PDF fallback")
        self.assertEqual(pdf["filename"], "report.docx")
        self.assertEqual(pdf["fallback_from"], ".pdf")
        for item, expected in ((docx, "word/document.xml"), (xlsx, "xl/worksheets/sheet1.xml"), (pdf, "word/document.xml")):
            path = self.service.metadata(item["artifact_id"], 1, include_path=True)["local_path"]
            with zipfile.ZipFile(path) as package:
                self.assertIn(expected, package.namelist())
                self.assertIsNone(package.testzip())

    def test_zip_contains_only_current_safe_entries(self):
        item = self.service.create(1, "site.zip", files=[
            {"name": "index.html", "content": "<h1>Noema</h1>"},
            {"name": "assets/style.css", "content": "body{}"},
        ])
        path = self.service.metadata(item["artifact_id"], 1, include_path=True)["local_path"]
        with zipfile.ZipFile(path) as package:
            self.assertEqual(package.namelist(), ["index.html", "assets/style.css"])
        with self.assertRaises(ValueError):
            self.service.create(1, "bad.zip", files=[{"name": "../secret.txt", "content": "no"}])

    def test_message_history_links_metadata_not_binary(self):
        item = self.service.create(1, "result.json", json.dumps({"ok": True}))
        self.service.link_message(42, [item["artifact_id"]])
        linked = self.service.for_messages(1, [42])[42][0]
        self.assertNotIn("local_path", linked)
        self.assertNotIn("content", linked)


class SpeechTextPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = SpeechTextPolicy()

    def test_prose_and_long_story_are_not_arbitrarily_cut(self):
        prose = "Это длинная история. " * 500
        spoken = self.policy.build(prose)
        self.assertEqual(spoken.count("Это длинная история."), 500)

    def test_code_json_tables_urls_and_paths_are_not_read_verbatim(self):
        code = "Готово.\n```python\n" + "print('secret')\n" * 100 + "```"
        self.assertNotIn("secret", self.policy.build(code))
        dump = json.dumps({"items": list(range(100))})
        self.assertNotIn('"items"', self.policy.build(dump))
        table = "Итоги:\n| Имя | Сумма |\n|---|---|\n| A | 10 |"
        self.assertNotIn("|", self.policy.build(table))
        urls = "Ссылки:\nhttps://example.com/a\nhttps://example.org/b"
        self.assertNotIn("https://", self.policy.build(urls))
        path = "Файл C:\\Users\\name\\project\\very-long-file.txt готов."
        self.assertNotIn("C:\\Users", self.policy.build(path))

    def test_artifact_gets_natural_completion(self):
        speech = self.policy.build("Готово.", [{"filename": "parser.py"}])
        self.assertIn("прикреплён", speech)


class RoutingContractTests(unittest.TestCase):
    def test_artifact_tool_is_selected_only_for_file_nature_requests(self):
        resolver = ToolPackResolver()
        self.assertIn("artifact_create", resolver.select_names("сделай parser.py файлом"))
        self.assertIn("artifact_create", resolver.select_names("создай HTML сайт"))
        self.assertNotIn("artifact_create", resolver.select_names("покажи маленький пример Python функции"))

    def test_qwen_and_deepseek_share_reasoning_off_contract(self):
        import bot

        with patch.object(bot, "timezone_for", return_value=bot.ZoneInfo("Europe/Moscow")), \
             patch.object(bot, "behavior_rules_for", return_value=[]):
            persona = bot.system_prompt(42)
        for model in ("qwen/qwen3.5-flash-02-23", "deepseek/deepseek-v3.2"):
            messages = [{"role": "system", "content": persona}]
            payload = bot.build_chat_payload(model, messages)
            self.assertEqual(payload["reasoning"], {"enabled": False})
            self.assertEqual(payload["messages"][0]["content"], persona)
            self.assertIn("без мужского или женского гендера", payload["messages"][0]["content"])

    def test_length_finish_continues_without_losing_the_tail(self):
        import bot

        def response(content, finish_reason):
            result = Mock(ok=True, status_code=200)
            result.iter_lines.return_value = [
                ("data: " + json.dumps({"choices": [{"delta": {"content": content}, "finish_reason": finish_reason}]})).encode(),
                b"data: [DONE]",
            ]
            return result

        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="persona"), \
             patch.object(bot, "chat_model_candidates", return_value=["qwen/test"]), \
             patch.object(bot, "request_chat_stream", side_effect=[response("Первая часть ", "length"), response("и обязательный хвост.", "stop")]), \
             patch.object(bot, "record_usage"), patch.object(bot, "record_user_request"), \
             patch.object(bot, "record_runtime_metric"), patch.object(bot, "add_message", side_effect=[1, 2]):
            events = list(bot.stream_agent_response(42, "Дай длинный ответ"))
        visible = "".join(event["text"] for event in events if event["type"] == "delta")
        done = next(event["text"] for event in events if event["type"] == "done")
        self.assertEqual(visible, "Первая часть и обязательный хвост.")
        self.assertEqual(done, visible)

    def test_canonical_persona_is_neutral(self):
        source = Path(__file__).parents[1].joinpath("bot.py").read_text(encoding="utf-8")
        self.assertIn("цифровой персональный ассистент без мужского или женского гендера", source)
        self.assertIn("Noema — цифровой ассистент. Можно выбрать мужской или женский голос.", source)

    def test_user_settings_are_product_only_and_voice_is_integrated(self):
        import bot

        with patch.object(bot, "ADMIN_CHAT_IDS", set()), patch.object(bot, "QUICK_ACTIONS_BASE_URL", ""):
            keyboard = bot.settings_keyboard(77)
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row if button.callback_data]
        for label in ("Режим ответа", "Правила", "iPhone", "Часовой пояс", "Очистить диалог"):
            self.assertTrue(any(label in value for value in labels), label)
        for forbidden in ("Модель", "Vision", "Статус", "Usage", "Users", "API"):
            self.assertFalse(any(forbidden in value for value in labels), forbidden)
        self.assertNotIn("settings:voice", callbacks)
        with patch.object(bot, "get_mode", return_value="voice"), patch.object(bot, "get_voice_preferences", return_value={"gender": "male"}):
            mode = bot.mode_keyboard(77)
        mode_labels = [button.text for row in mode.inline_keyboard for button in row]
        self.assertTrue(any("Мужской" in value for value in mode_labels))
        self.assertTrue(any("Женский" in value for value in mode_labels))


class TransportContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_adapter_sends_one_document_from_metadata(self):
        import bot

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "test.sqlite3"

            def connect():
                connection = sqlite3.connect(db)
                connection.row_factory = sqlite3.Row
                return connection

            service = ArtifactService(connect, Path(folder) / "artifacts")
            item = service.create(42, "parser.py", "print('ok')")
            telegram = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=9)))
            with patch.object(bot, "artifact_store", return_value=service):
                delivered = await bot._deliver_telegram_artifacts(telegram, 42, [item])
            self.assertEqual(delivered, [9])
            telegram.send_document.assert_awaited_once()

    def test_miniapp_has_authenticated_download_and_attachment_card(self):
        root = Path(__file__).parents[1]
        api_source = (root / "miniapp_api.py").read_text(encoding="utf-8")
        app_source = (root / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn('elif action == "artifact"', api_source)
        self.assertIn('"X-Content-Type-Options": "nosniff"', api_source)
        self.assertIn('"Content-Security-Policy": "sandbox"', api_source)
        self.assertIn("data-artifact-download", app_source)
        self.assertIn("downloadArtifact", app_source)

    async def test_non_admin_direct_telegram_admin_callback_is_denied(self):
        import bot

        query = SimpleNamespace(
            answer=AsyncMock(), data="settings:status", edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=77, message_id=9),
        )
        update = SimpleNamespace(callback_query=query, effective_user=None)
        with patch.object(bot, "ADMIN_CHAT_IDS", set()), \
             patch.object(bot, "adopt_active_ui", new=AsyncMock()), \
             patch.object(bot, "register_bot_user"), \
             patch.object(bot, "settings_keyboard", return_value=None):
            await bot.callback(update, SimpleNamespace())
        query.edit_message_text.assert_awaited_once()
        self.assertIn("настроены Noema", query.edit_message_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
