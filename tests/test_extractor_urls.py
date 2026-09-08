"""Unit tests: URL parsing, extraction validation, project resolution, intents."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion import (ProjectResolver, analyze_intents, extract_money,  # noqa: E402
                       normalize_extraction, parse_urls)


class UrlParserTest(unittest.TestCase):
    def test_http_https_www(self):
        urls = parse_urls(
            "Смотри https://example.com/a и http://sub.example.ru/b?x=1&y=2 и www.other.net/path.")
        self.assertIn("https://example.com/a", urls)
        self.assertIn("http://sub.example.ru/b?x=1&y=2", urls)
        self.assertIn("http://www.other.net/path", urls)

    def test_domain_without_protocol(self):
        urls = parse_urls("домен example.org/page и ещё site.io")
        self.assertIn("http://example.org/page", urls)
        self.assertIn("http://site.io", urls)

    def test_no_url_is_not_invented(self):
        self.assertEqual(parse_urls("Просто текст без ссылок."), [])
        self.assertEqual(parse_urls("Цена 12,5 руб и версия 1.2.3 закончилась"), [])

    def test_trailing_punctuation_stripped(self):
        urls = parse_urls("ссылка: https://a.com/end, и https://b.com/x.")
        self.assertEqual(urls, ["https://a.com/end", "https://b.com/x"])

    def test_balanced_parens(self):
        urls = parse_urls("см (https://a.com/(x)) далее")
        self.assertIn("https://a.com/(x)", urls)

    def test_dedupe(self):
        urls = parse_urls("https://a.com/x и ещё раз https://a.com/x")
        self.assertEqual(urls, ["https://a.com/x"])


class NormalizeExtractionTest(unittest.TestCase):
    def test_defaults(self):
        ex = normalize_extraction({})
        self.assertEqual(ex.content_type, "text")
        self.assertEqual(ex.visible_text, "")
        self.assertEqual(ex.urls, [])
        self.assertEqual(ex.entities, [])
        self.assertEqual(ex.confidence, 0.5)

    def test_confidence_clamped(self):
        self.assertEqual(normalize_extraction({"confidence": 2.5}).confidence, 1.0)
        self.assertEqual(normalize_extraction({"confidence": -1}).confidence, 0.0)

    def test_urls_from_visible_text_only(self):
        ex = normalize_extraction({"content_type": "screenshot", "visible_text": "Сайт https://x.io/abc"})
        self.assertIn("https://x.io/abc", ex.urls)

    def test_entities_normalized(self):
        ex = normalize_extraction({
            "entities": [{"type": "pet", "name": " Ричи "}, {"type": "x", "name": ""}]
        })
        self.assertEqual(len(ex.entities), 1)
        self.assertEqual(ex.entities[0]["name"], "Ричи")


class ProjectResolverTest(unittest.TestCase):
    def test_explicit_in_text(self):
        self.assertEqual(ProjectResolver().resolve(1, "сохрани для проекта Noema как ресурс"), "Noema")

    def test_explicit_project_word(self):
        self.assertEqual(ProjectResolver().resolve(1, "проект Alpha: сохрани"), "Alpha")

    def test_project_hint(self):
        self.assertEqual(ProjectResolver().resolve(1, "сохрани", project_hint="Alpha"), "Alpha")

    def test_active_project(self):
        r = ProjectResolver(get_active_project=lambda cid: "ActiveProj")
        self.assertEqual(r.resolve(1, "сохрани картинку"), "ActiveProj")

    def test_context_mention(self):
        self.assertEqual(
            ProjectResolver().resolve(1, "сохрани", conversation_context="работаем над проектом Beta"),
            "Beta")

    def test_unresolved(self):
        self.assertIsNone(ProjectResolver().resolve(1, "сохрани картинку"))


class IntentTest(unittest.TestCase):
    def test_expense_intent(self):
        self.assertIn("expense", analyze_intents("добавь расход"))

    def test_no_intent_for_question(self):
        self.assertEqual(analyze_intents("что тут написано?"), [])

    def test_person_intent(self):
        self.assertIn("person", analyze_intents("Это моя собака Ричи"))

    def test_money_extraction(self):
        amt, _ = extract_money("Кофе 250 ₽ итого")
        self.assertEqual(amt, 250.0)

    def test_money_none(self):
        self.assertIsNone(extract_money("просто привет")[0])


if __name__ == "__main__":
    unittest.main()