"""Syntax + reference integrity checks for all source modules."""
import ast
import py_compile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class SyntaxAndRefsTest(unittest.TestCase):
    def test_py_compile_all(self):
        for name in ("bot.py", "ingestion.py", "knowledge_store.py", "url_enricher.py"):
            py_compile.compile(str(ROOT / name), doraise=True)

    def test_import_all_modules(self):
        import bot                      # noqa: F401
        import ingestion                # noqa: F401
        import knowledge_store          # noqa: F401
        import url_enricher             # noqa: F401
        self.assertTrue(bot.get_pipeline().store is not None)

    def test_ast_no_missing_tool_refs(self):
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        funcs = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.add(n.name)
        exec_targets = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "execute_tool":
                for sub in ast.walk(n):
                    if isinstance(sub, ast.Dict):
                        for k, v in zip(sub.keys, sub.values):
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Name) and k.value != "tool":
                                exec_targets.add(v.id)
        # keys may be aliases (set_reminder -> save_reminder); VALUES must exist
        missing_values = sorted(exec_targets - funcs)
        self.assertEqual(missing_values, [])


if __name__ == "__main__":
    unittest.main()