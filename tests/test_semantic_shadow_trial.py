import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import semantic_shadow_trial as trial


def _semantic(*, zero_write=True, disposition="commit", operations=None, status="VALIDATED_COMMIT"):
    return {
        "status": status, "disposition": disposition, "intent": "test", "read_count": 0,
        "action_count": 1, "operations": operations or ["event.create"], "grounding_status": "",
        "failure_category": "", "latency_ms": 1.0, "plan": {}, "exact_evidence": True,
        "zero_write": zero_write,
    }


class SemanticShadowTrialTests(unittest.TestCase):
    def test_shared_trial_credentials_never_provision_managed_key(self):
        with patch.object(trial.bot, "OR_KEY", "shared-test-key"), \
             patch.object(trial.bot, "OR_MANAGEMENT_KEY", "management-test-key"), \
             patch.object(trial.bot, "provision_managed_api_key", side_effect=AssertionError("must not provision")):
            trial.ensure_live_model_is_configured()
            with trial.trial_credential_context():
                key, source = trial.bot.api_key_for_chat(trial.SYNTHETIC_OWNER)
        self.assertEqual(("shared-test-key", "shared"), (key, source))

    def test_fixture_setup_does_not_call_provider_credential_provisioning(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, \
             patch.object(trial.bot, "provision_managed_api_key", side_effect=AssertionError("must not provision")) as provision:
            trial.prepare_fixture(Path(directory) / "fixture.db")
        provision.assert_not_called()

    def test_management_key_without_shared_key_is_not_live_trial_configuration(self):
        with patch.object(trial.bot, "OR_KEY", ""), \
             patch.object(trial.bot, "OR_MANAGEMENT_KEY", "management-test-key"), \
             patch.object(trial.bot, "provision_managed_api_key", side_effect=AssertionError("must not provision")):
            with self.assertRaisesRegex(RuntimeError, "LIVE_MODEL_NOT_CONFIGURED"):
                trial.ensure_live_model_is_configured()

    def test_trial_temp_directory_retries_windows_lock_and_keeps_result(self):
        original_rmtree = trial.shutil.rmtree
        calls = []
        def flaky_rmtree(path):
            calls.append(Path(path))
            if len(calls) == 1:
                raise PermissionError(32, "locked")
            original_rmtree(path)
        with patch.object(trial.shutil, "rmtree", side_effect=flaky_rmtree), \
             patch.object(trial.time, "sleep"):
            with trial.trial_temp_directory() as directory:
                (directory / "legacy_case_10.db").write_text("synthetic", encoding="utf-8")
                completed_result = {"report": "complete"}
        self.assertEqual({"report": "complete"}, completed_result)
        self.assertEqual(2, len(calls))
        self.assertFalse(directory.exists())

    def test_trial_temp_directory_defers_unremovable_lock_without_private_warning(self):
        original_rmtree = trial.shutil.rmtree
        with patch.object(trial.shutil, "rmtree", side_effect=PermissionError(32, "locked")), \
             patch.object(trial.time, "sleep"), \
             self.assertLogs(trial.LOGGER, "WARNING") as logs:
            with trial.trial_temp_directory() as directory:
                (directory / "legacy_case_10.db").write_text("synthetic", encoding="utf-8")
        self.assertIn("TEMP_CLEANUP_DEFERRED", "\n".join(logs.output))
        self.assertNotIn("legacy_case_10.db", "\n".join(logs.output))
        original_rmtree(directory, ignore_errors=True)

    def test_fixture_copies_are_isolated_and_snapshot_detects_mutation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            base = root / "fixture.db"
            trial.seed_fixture(base)
            semantic_db, legacy_db = trial.copy_case_databases(base, root, 1)
            before = trial._table_snapshot(semantic_db)
            with sqlite3.connect(semantic_db) as connection:
                connection.execute("UPDATE people SET relationship='changed' WHERE chat_id=?", (trial.SYNTHETIC_OWNER,))
            after = trial._table_snapshot(semantic_db)
            self.assertFalse(trial.semantic_zero_write(before, after))
            with sqlite3.connect(legacy_db) as connection:
                relationship = connection.execute("SELECT relationship FROM people WHERE chat_id=?", (trial.SYNTHETIC_OWNER,)).fetchone()[0]
            self.assertEqual("синтетический коллега", relationship)

    def test_valid_structural_divergence_is_warn_not_fail(self):
        case = trial.CASES[0]
        verdict, notes = trial.verdict_for(case, _semantic(), "DIFFERENT_OPERATIONS")
        self.assertEqual("WARN", verdict)
        self.assertTrue(notes)

    def test_semantic_mutation_fails_and_acceptance_blocks_canary(self):
        case = trial.CASES[0]
        verdict, _ = trial.verdict_for(case, _semantic(zero_write=False), "MATCH")
        self.assertEqual("FAIL", verdict)
        report = {"cases": [{"verdict": verdict, "comparison": "MATCH", "semantic": _semantic(zero_write=False), "legacy": {"latency_ms": 1.0}}]}
        self.assertEqual("NO", trial.acceptance(report)["CANARY_READY"])

    def test_fake_backend_runs_harness_without_network_or_semantic_write(self):
        class FakeBackend:
            def __init__(self, owner):
                self.owner = owner
            async def generate_structured(self, **kwargs):
                return {"intent": "finance", "disposition": "read", "reads": [
                    {"domain": "finance", "operation": "summary", "read_id": "finance"},
                ], "actions": []}
            async def generate_grounded(self, **kwargs):
                return {"claims": [{"text": "1250 RUB", "claim_type": "personal_fact", "evidence_ids": ["e1"]}], "confidence": 1}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            base = root / "fixture.db"
            trial.seed_fixture(base)
            semantic_db, _legacy_db = trial.copy_case_databases(base, root, 5)
            result = trial.run_semantic_case(trial.CASES[4], semantic_db, backend_factory=FakeBackend)
        self.assertEqual("READ_COMPLETED", result["status"])
        self.assertEqual(["finance.summary"], result["operations"])
        self.assertTrue(result["zero_write"])

    def test_report_generation_is_compact_and_does_not_serialize_secrets(self):
        report = {
            "trial": "local_synthetic_shadow",
            "cases": [{
                "number": 1, "phrase": trial.CASES[0].phrase, "expected": trial.CASES[0].expected,
                "legacy": {"tool_names": ["event_create"], "success_count": 1, "failure_count": 0, "final_answer": "synthetic", "latency_ms": 1.0},
                "semantic": _semantic(), "comparison": "MATCH", "verdict": "PASS", "notes": [],
            }],
        }
        report["summary"] = trial.acceptance(report)
        with tempfile.TemporaryDirectory() as directory:
            json_path, markdown_path = trial.write_report(report, Path(directory))
            content = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
        self.assertIn("Semantic shadow local trial", content)
        self.assertNotIn("OPENROUTER_API_KEY", content)
        self.assertNotIn("sk-", content)
