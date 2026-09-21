import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_deploy_tree import RUNTIME_FILES, build


class DeployTreeTests(unittest.TestCase):
    def test_semantic_default_off_deploy_tree_imports_and_initializes_receipts(self):
        required = {
            "entity_resolver.py", "semantic_core.py", "semantic_planner.py",
            "plan_runtime.py", "grounded_response.py", "semantic_orchestrator.py",
            "semantic_runtime.py",
        }
        self.assertTrue(required.issubset(RUNTIME_FILES))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deploy = root / "deploy"
            persistent = root / "persistent"
            build(deploy)
            environment = dict(os.environ, DATA_DIR=str(persistent))
            environment.pop("SEMANTIC_RUNTIME_MODE", None)
            environment.pop("SEMANTIC_CANARY_USER_IDS", None)
            smoke = """
import os, sqlite3
import bot
from semantic_runtime import canary_owners, runtime_mode
assert runtime_mode() == 'off'
assert canary_owners() == frozenset()
bot.init_db()
database = os.path.join(os.environ['DATA_DIR'], 'noema_test.sqlite3')
with sqlite3.connect(database) as connection:
    assert connection.execute(\"SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_executions'\").fetchone()
    assert connection.execute(\"SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_semantic_executions_owner_request'\").fetchone()
"""
            subprocess.run(
                [sys.executable, "-c", smoke], cwd=deploy, env=environment,
                check=True, capture_output=True, text=True,
            )


if __name__ == "__main__":
    unittest.main()
