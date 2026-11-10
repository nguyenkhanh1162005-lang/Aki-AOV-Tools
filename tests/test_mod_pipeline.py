import tempfile
import unittest
import zipfile
from pathlib import Path

from app.mod_pipeline import ModPipelineError, merge_mod_archives


class ModPipelineTests(unittest.TestCase):
    def _zip(self, folder: Path, name: str, entries: dict[str, bytes]) -> str:
        path = folder / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, payload in entries.items():
                archive.writestr(entry, payload)
        return str(path)

    def test_generic_archives_merge_new_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "one.zip", {"readme.txt": b"one"})
            second = self._zip(folder, "two.zip", {"extra.txt": b"two"})
            output = folder / "merged.zip"
            result = merge_mod_archives([first, second], str(output))
            self.assertEqual(result["adapter"], "generic_zip")
            self.assertTrue(result["structurally_validated"])
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {"readme.txt", "extra.txt"})
                self.assertIsNone(archive.testzip())

    def test_identical_duplicate_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "one.zip", {"same.txt": b"same"})
            second = self._zip(folder, "two.zip", {"same.txt": b"same"})
            output = folder / "merged.zip"
            result = merge_mod_archives([first, second], str(output))
            self.assertEqual(result["adapter"], "generic_zip")
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ["same.txt"])

    def test_conflicting_duplicate_preserves_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "one.zip", {"same.txt": b"one"})
            second = self._zip(folder, "two.zip", {"same.txt": b"two"})
            output = folder / "merged.zip"
            with self.assertRaises(ModPipelineError):
                merge_mod_archives([first, second], str(output))
            self.assertFalse(output.exists())

    def test_unknown_aov_resource_pack_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "unknown-aov.zip", {
                "Resources/1.64.1/Databin/Client/Actor/heroSkin.bytes": b"opaque"
            })
            output = folder / "merged.zip"
            with self.assertRaisesRegex(ModPipelineError, "chưa có adapter"):
                merge_mod_archives([source], str(output))
            self.assertFalse(output.exists())

    def test_traversal_is_rejected_before_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "bad.zip", {"../escape.txt": b"bad"})
            output = folder / "merged.zip"
            with self.assertRaisesRegex(ModPipelineError, "không an toàn"):
                merge_mod_archives([source], str(output))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
