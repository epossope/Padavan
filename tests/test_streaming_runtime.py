import json
import unittest

from streaming_runtime import (AdaptiveDraftThrottle, SentenceChunker, StreamAccumulator,
                               ToolPackResolver, iter_sse_json,
                               sanitize_assistant_message, sanitize_visible_content)


class StreamingRuntimeTests(unittest.TestCase):
    def test_streamed_reasoning_is_never_emitted_or_persisted(self):
        acc = StreamAccumulator()
        chunks = [
            {"choices": [{"delta": {"reasoning": "internal only", "content": "<thi"}}]},
            {"choices": [{"delta": {"content": "nk>Пользователь спрашивает. Нужно ответить.</think>Го"}}]},
            {"choices": [{"delta": {"content": "тово."}}]},
        ]
        visible = []
        for chunk in chunks:
            visible.extend(acc.add(chunk))
        visible.append(acc.finish())
        self.assertEqual("".join(visible), "Готово.")
        self.assertEqual(acc.message()["content"], "Готово.")
        self.assertNotIn("reasoning", acc.message())

    def test_final_message_sanitizer_drops_reasoning_fields_and_tags(self):
        message = sanitize_assistant_message({
            "role": "assistant", "reasoning": "secret",
            "reasoning_details": [{"text": "secret"}],
            "content": "<think>Нужно ответить</think>Только ответ",
        })
        self.assertEqual(message, {"role": "assistant", "content": "Только ответ"})
        self.assertEqual(sanitize_visible_content("До <b>обычного</b> текста"), "До <b>обычного</b> текста")

    def test_streamed_tool_arguments_are_assembled_once(self):
        acc = StreamAccumulator()
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "add_", "arguments": '{"text":"'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "task", "arguments": "Позвонить"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"}'}}]}, "finish_reason": "tool_calls"}]},
        ]
        for chunk in chunks:
            acc.add(chunk)
        call = acc.message()["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "add_task")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"text": "Позвонить"})

    def test_sse_parser_ignores_noise(self):
        rows = [b": keepalive", b'data: {"choices":[]}', b"data: [DONE]"]
        self.assertEqual(list(iter_sse_json(rows)), [{"choices": []}])

    def test_sentence_chunking_starts_before_full_answer(self):
        chunker = SentenceChunker()
        self.assertEqual(chunker.feed("Первая фраза. Вто"), ["Первая фраза."])
        self.assertEqual(chunker.feed("рая фраза!"), [])
        self.assertEqual(chunker.flush(), ["Вторая фраза!"])

    def test_tool_policy_keeps_memory_and_relevant_pack(self):
        names = ToolPackResolver().select_names("Запиши расход 350 рублей на кофе")
        self.assertIn("knowledge_search", names)
        self.assertIn("add_expense", names)
        self.assertNotIn("internet_search", names)

    def test_draft_throttle_uses_growth_and_deadline(self):
        ticks = iter([0.4, 0.5, 0.8, 1.7])
        throttle = AdaptiveDraftThrottle(min_interval=0.35, max_interval=1.2, min_chars=10, clock=lambda: next(ticks))
        self.assertTrue(throttle.should_send("0123456789"))
        self.assertFalse(throttle.should_send("0123456789abc"))
        self.assertTrue(throttle.should_send("0123456789abcdefghij"))
        self.assertFalse(throttle.should_send("0123456789abcdefghijkl"))


if __name__ == "__main__":
    unittest.main()
