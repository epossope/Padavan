import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bot
from tools.history_reasoning_dry_run import audit as audit_history


FIXTURE = Path(__file__).parent / "fixtures" / "qwen_alibaba_reasoning_contract.json"


class P0ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.temp = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        self.enterContext(patch.object(bot, "DB", Path(self.temp) / "p0.sqlite3"))
        bot.init_db()

    def _response(self, frames):
        response = Mock(ok=True, status_code=200)
        response.iter_lines.return_value = [
            ("data: " + json.dumps(frame, ensure_ascii=False)).encode("utf-8") for frame in frames
        ] + [b"data: [DONE]"]
        return response

    def test_single_payload_builder_disables_reasoning_for_sync_and_stream(self):
        messages = [{"role": "user", "content": "test"}]
        sync = bot.build_chat_payload("qwen/test", messages)
        stream = bot.build_chat_payload("qwen/test", messages, stream=True)
        for payload in (sync, stream):
            self.assertEqual(payload["reasoning"], {"enabled": False})
            self.assertNotIn("exclude", payload["reasoning"])
        self.assertNotIn("stream", sync)
        self.assertTrue(stream["stream"])
        self.assertEqual(stream["stream_options"], {"include_usage": True})

        response = Mock()
        with patch.object(bot, "api_key_for_chat", return_value=("test-key", "shared")), \
             patch.object(bot, "provider_preferences_for", return_value=None), \
             patch.object(bot.requests, "post", return_value=response) as post:
            self.assertIs(bot.request_chat(1, "qwen/test", messages), response)
            self.assertIs(bot.request_chat_stream(1, "qwen/test", messages), response)
        sync_wire, stream_wire = [call.kwargs["json"] for call in post.call_args_list]
        self.assertEqual(sync_wire["reasoning"], {"enabled": False})
        self.assertEqual(stream_wire["reasoning"], {"enabled": False})

    def test_captured_qwen_schema_is_clean_at_telegram_miniapp_and_db_boundary(self):
        outbound = []

        def provider_post(_url, **kwargs):
            payload = kwargs["json"]
            outbound.append(payload)
            frames = (self.fixture["reasoning_disabled"]
                      if payload.get("reasoning") == {"enabled": False}
                      else self.fixture["legacy_without_reasoning_control"])
            return self._response(frames)

        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="system"), \
             patch.object(bot, "chat_model_candidates", return_value=["qwen/qwen3.5-flash-02-23"]), \
             patch.object(bot, "api_key_for_chat", return_value=("test-key", "shared")), \
             patch.object(bot, "provider_preferences_for", return_value={"only": ["alibaba"], "require_parameters": True}), \
             patch.object(bot.requests, "post", side_effect=provider_post), \
             patch.object(bot, "record_usage"):
            events = list(bot.stream_agent_response(77, "test"))

        deltas = "".join(event["text"] for event in events if event["type"] == "delta")
        done = next(event["text"] for event in events if event["type"] == "done")
        self.assertEqual(deltas, "Готовый ответ.")
        self.assertEqual(done, deltas)  # Mini App concatenated deltas == done.text
        self.assertNotIn("The user", repr(events))
        self.assertEqual(outbound[0]["reasoning"], {"enabled": False})
        with bot.conn() as database:
            stored = database.execute(
                "SELECT content FROM messages WHERE chat_id=77 AND role='assistant'"
            ).fetchone()["content"]
        self.assertEqual(stored, done)

    def test_machine_readable_reasoning_violation_fails_over_before_visible_output(self):
        violating = self._response([
            {"choices": [{"delta": {"reasoning": "private", "content": "unsafe"}}]},
        ])
        compliant = self._response(self.fixture["reasoning_disabled"])
        with patch.object(bot, "direct_live_request", return_value=None), \
             patch.object(bot, "conversation_context", return_value=[]), \
             patch.object(bot, "system_prompt", return_value="system"), \
             patch.object(bot, "chat_model_candidates", return_value=["qwen/primary", "safe/fallback"]), \
             patch.object(bot, "request_chat_stream", side_effect=[violating, compliant]), \
             patch.object(bot, "record_usage"):
            events = list(bot.stream_agent_response(88, "test"))
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "delta"), "Готовый ответ.")
        self.assertNotIn("unsafe", repr(events))

    def test_history_inventory_is_read_only_and_never_emits_content(self):
        private_content = "The user is asking for private details. This is candidate metadata only."
        with bot.conn() as database:
            database.execute(
                "INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)",
                (123, "assistant", private_content, "2026-09-14T00:00:00+00:00"),
            )
        database_path = Path(self.temp) / "p0.sqlite3"
        before = hashlib.sha256(database_path.read_bytes()).hexdigest()
        result = audit_history(database_path)
        after = hashlib.sha256(database_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(result["candidate_count"], 1)
        self.assertFalse(result["content_emitted"])
        self.assertFalse(result["database_mutated"])
        self.assertNotIn(private_content, repr(result))


if __name__ == "__main__":
    unittest.main()
