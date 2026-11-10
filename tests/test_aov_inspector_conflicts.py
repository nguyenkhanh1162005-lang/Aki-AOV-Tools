from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_inspector import inspect_archives


class AOVInspectorConflictTests(unittest.TestCase):
    def _zip(self, root: Path, name: str, entries: dict[str, bytes]) -> str:
        path = root / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for entry, data in entries.items():
                archive.writestr(entry, data)
        return str(path)

    def test_reports_same_path_with_different_content_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._zip(root, "mod-a.zip", {"Resources/Actor.bytes": b"first"})
            second = self._zip(root, "mod-b.zip", {"Resources/Actor.bytes": b"second"})
            report = inspect_archives([first, second])
            self.assertIn("Xung đột nội dung cần xử lý: 1", report)
            self.assertIn("XUNG ĐỘT: Resources/Actor.bytes", report)
            self.assertIn("mod-a.zip", report)
            self.assertIn("mod-b.zip", report)

    def test_reports_same_path_with_same_content_as_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"same-resource"
            first = self._zip(root, "mod-a.zip", {"Resources/Actor.bytes": payload})
            second = self._zip(root, "mod-b.zip", {"Resources/Actor.bytes": payload})
            report = inspect_archives([first, second])
            self.assertIn("Trùng nội dung theo kích thước + CRC32: 1", report)
            self.assertIn("Trùng giống nhau: Resources/Actor.bytes", report)
            self.assertIn("Xung đột nội dung cần xử lý: 0", report)


if __name__ == "__main__":
    unittest.main()
