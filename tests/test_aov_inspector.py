import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_inspector import InspectError, inspect_archives


class ArchiveInspectorTests(unittest.TestCase):
    def make_zip(self, folder: Path, filename: str, entries: dict[str, bytes]) -> str:
        path = folder / filename
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return str(path)

    def test_reports_aov_assets_and_startup_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_zip(
                Path(temp),
                "mod.zip",
                {
                    "Resources/BattleBank.bytes": b"bank-data",
                    "UI/startup_notification.prefab": b"candidate",
                    "Actors/hero/skill.bytes": b"skill-data",
                },
            )
            report = inspect_archives([path])
            self.assertIn("BattleBank.bytes", report)
            self.assertIn("startup_notification.prefab", report)
            self.assertIn("DẤU HIỆU CẦN KIỂM TRA THÊM", report)

    def test_classifies_same_path_with_different_content_as_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            first = self.make_zip(folder, "first.zip", {"same.bytes": b"one"})
            second = self.make_zip(folder, "second.zip", {"same.bytes": b"two"})
            report = inspect_archives([first, second])
            self.assertIn("Trùng đường dẫn nhưng khác kích thước hoặc CRC32: 1", report)

    def test_rejects_parent_directory_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_zip(Path(temp), "unsafe.zip", {"../escape.bytes": b"x"})
            with self.assertRaises(InspectError):
                inspect_archives([path])


if __name__ == "__main__":
    unittest.main()
