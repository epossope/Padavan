import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
from scripts import semantic_production_trial as trial


class SemanticProductionTrialTests(unittest.TestCase):
    def test_temp_database_isolated_and_uses_synthetic_owner(self):
        original = bot.DB
        with trial.temp_database() as database:
            self.assertNotEqual(original, bot.DB)
            self.assertTrue(database.exists())
            self.assertIn("noema-semantic-production-", str(database.parent))
            trial.seed()
            self.assertEqual(1, trial.counts()["people"])
        self.assertEqual(original, bot.DB)

    def test_verify_expense_replay_and_new_turn_are_exact_state_based(self):
        base = {"expenses": 1, "people": 1, "events": 0, "reminders": 0, "semantic_executions": 1}
        original = {"expected": "expense", "status": "ACTION_RECEIPT", "before": base, "after": {**base, "expenses": 2, "semantic_executions": 2}, "execution_status": "EXECUTED"}
        replay = {"expected": "expense_replay", "status": "ACTION_RECEIPT", "before": original["after"], "after": original["after"], "execution_status": "EXECUTED", "replayed": True}
        new_turn = {"expected": "expense_second", "status": "ACTION_RECEIPT", "before": original["after"], "after": {**original["after"], "expenses": 3, "semantic_executions": 3}, "execution_status": "EXECUTED"}
        self.assertEqual("PASS", trial.verify(original)[0])
        self.assertEqual("PASS", trial.verify(replay)[0])
        self.assertEqual("PASS", trial.verify(new_turn)[0])

    def test_report_is_compact_and_has_no_credentials(self):
        report = {"cases": [{"number": 1, "phrase": "synthetic", "mode": "off", "status": "FALLBACK_TO_LEGACY", "operations": [], "check": "ok", "verdict": "PASS", "latency_ms": 1.0}], "summary": {"TOTAL": 1, "REAL_DB_WRITES": 0}}
        with tempfile.TemporaryDirectory() as directory:
            json_path, markdown_path = trial.write_report(report, Path(directory))
            content = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
        self.assertIn("DB_MODE: TEMP", content)
        self.assertNotIn("OPENROUTER_API_KEY", content)
        self.assertNotIn("sk-", content)
