import json
import unittest

from streaming_runtime import SentenceChunker, StreamAccumulator, ToolPackResolver, iter_sse_json


class StreamingRuntimeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
