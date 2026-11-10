import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_inspector import inspect_archives


class AOVInspectorDuplicateTests(unittest.TestCase):
    def _zip(self, folder: Path, name: str, entries: dict[str, bytes]) -> str:
        path = folder / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, payload in entries.items():
                archive.writestr(entry, payload)
        return str(path)

    def test_reports_identical_duplicate_by_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "first.zip", {"Resources/a.bytes": b"same"})
            second = self._zip(folder, "second.zip", {"Resources/a.bytes": b"same"})
            report = inspect_archives([first, second])
            self.assertIn("NỘI DUNG GIỐNG HỆT (SHA-256)", report)
            self.assertIn("1 đường dẫn giống hệt", report)

    def test_reports_conflicting_duplicate_by_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "first.zip", {"Resources/a.bytes": b"skin-one"})
            second = self._zip(folder, "second.zip", {"Resources/a.bytes": b"skin-two"})
            report = inspect_archives([first, second])
            self.assertIn("XUNG ĐỘT NỘI DUNG (SHA-256 KHÁC)", report)
            self.assertIn("1 đường dẫn có nội dung khác", report)

    def test_detects_duplicate_entries_inside_same_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / "duplicate-inside.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Resources/a.bytes", b"first")
                archive.writestr("Resources/a.bytes", b"second")
            report = inspect_archives([str(path)])
            self.assertIn("XUNG ĐỘT NỘI DUNG (SHA-256 KHÁC)", report)


if __name__ == "__main__":
    unittest.main()
