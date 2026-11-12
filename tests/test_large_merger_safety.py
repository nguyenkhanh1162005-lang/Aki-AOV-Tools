import tempfile
import unittest
import zipfile
from pathlib import Path

from app.large_merger import MergeError, merge_zip_files


class LargeMergerSafetyTests(unittest.TestCase):
    def _zip(self, folder: Path, name: str, entries: dict[str, bytes]) -> str:
        path = folder / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, payload in entries.items():
                archive.writestr(entry, payload)
        return str(path)

    def test_rejects_windows_drive_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "drive.zip", {"C:/outside.txt": b"bad"})
            output = folder / "merged.zip"
            with self.assertRaisesRegex(MergeError, "đường dẫn không an toàn"):
                merge_zip_files([source], str(output))
            self.assertFalse(output.exists())

    def test_rejects_unc_and_parent_traversal_paths(self):
        for unsafe in ("../escape.txt", "//server/share/file.txt"):
            with self.subTest(path=unsafe), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                source = self._zip(folder, "unsafe.zip", {unsafe: b"bad"})
                output = folder / "merged.zip"
                with self.assertRaises(MergeError):
                    merge_zip_files([source], str(output))
                self.assertFalse(output.exists())

    def test_rejects_missing_input_with_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "merged.zip"
            with self.assertRaisesRegex(MergeError, "thông tin ZIP đầu vào"):
                merge_zip_files([str(Path(tmp) / "missing.zip")], str(output))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
