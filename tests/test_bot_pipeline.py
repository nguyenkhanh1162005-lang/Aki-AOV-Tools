import ast
import unittest
from pathlib import Path


class TelegramPipelineTests(unittest.TestCase):
    def test_start_describes_supported_pair_and_locked_wrapper(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text()
        self.assertIn("KPM100 iOS 1.64.1.12", source)
        self.assertIn("VTK iOS 1.64.1.11", source)
        self.assertIn("Wrapper MOD 10 skin hiện vẫn khóa", source)
        self.assertNotIn("Gửi gói MOD 10 skin và các ZIP phần", source)

    def test_bot_registers_read_only_resource_tools(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text()
        tree = ast.parse(source)
        main_function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        commands = {
            node.args[0].value
            for node in ast.walk(main_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CommandHandler"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }
        self.assertTrue({"compatibility", "findnotice", "decodebytes"}.issubset(commands))
        handlers = {
            node.name: [item.id for item in ast.walk(node) if isinstance(item, ast.Name)]
            for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
        }
        self.assertIn("plan_resource_compatibility", handlers["compatibility"])
        self.assertIn("decode_archive_resources", handlers["findnotice"])
        self.assertIn("decode_archive_resources", handlers["decodebytes"])


    def test_aovmerge_uses_shared_production_pipeline(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text()
        tree = ast.parse(source)
        handler = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "aovmerge"
        )
        referenced_names = [
            node.id for node in ast.walk(handler)
            if isinstance(node, ast.Name)
        ]
        self.assertIn("merge_mod_archives", referenced_names)
        self.assertNotIn("merge_aov_archives", referenced_names)


if __name__ == "__main__":
    unittest.main()
