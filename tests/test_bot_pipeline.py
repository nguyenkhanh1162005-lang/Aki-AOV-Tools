import ast
import unittest
from pathlib import Path


class TelegramPipelineTests(unittest.TestCase):
    def test_aovmerge_uses_shared_production_pipeline(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text()
        tree = ast.parse(source)
        handler = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "aovmerge"
        )
        calls = [
            node.func.id for node in ast.walk(handler)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertIn("merge_mod_archives", calls)
        self.assertNotIn("merge_aov_archives", calls)


if __name__ == "__main__":
    unittest.main()
