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


    def test_scans_nested_resources_zip_and_reads_encrypted_mses_header(self):
        import io
        from app.aov_crypto import encrypt_aov_resource

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            resource_path = "Databin/Client/Sound/BattleBank.bytes"
            mses = (
                b"MSES" + (7).to_bytes(4, "little")
                + (59).to_bytes(4, "little") + (1932).to_bytes(4, "little")
                + b"table-data"
            )
            encrypted = encrypt_aov_resource(resource_path, mses)
            nested_buffer = io.BytesIO()
            with zipfile.ZipFile(nested_buffer, "w", zipfile.ZIP_DEFLATED) as resources:
                resources.writestr("Resources/1.64.1/" + resource_path, encrypted)
            outer = self.make_zip(
                folder, "outer.zip",
                {"mod/Resources.zip": nested_buffer.getvalue()},
            )
            loose = self.make_zip(folder, "loose.zip", {resource_path: encrypted + b"x"})
            report = inspect_archives([outer, loose])

            self.assertIn("MSES v7 | 59 fields | 1932 records", report)
            self.assertIn("Databin/Client/Sound/BattleBank.bytes", report)
            self.assertIn("Trùng đường dẫn nhưng khác kích thước hoặc CRC32: 1", report)
            self.assertIn("Resources.zip", report)

    def test_scans_zip_based_pkg_inside_resources_archive(self):
        import io

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pkg_buffer = io.BytesIO()
            with zipfile.ZipFile(pkg_buffer, "w", zipfile.ZIP_DEFLATED) as pkg:
                pkg.writestr("commonresource/Back.xml", b"compressed-xml")
            resources_buffer = io.BytesIO()
            with zipfile.ZipFile(resources_buffer, "w", zipfile.ZIP_DEFLATED) as resources:
                resources.writestr(
                    "Resources/1.64.1/Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes",
                    pkg_buffer.getvalue(),
                )
            outer = self.make_zip(
                folder, "mod.zip", {"mod/Resources.zip": resources_buffer.getvalue()}
            )
            report = inspect_archives([outer])
            self.assertIn("CommonActions.pkg.bytes", report)
            self.assertIn("commonresource/Back.xml", report)
            self.assertIn("ZIP/lưu trữ lồng đã mở", report)


if __name__ == "__main__":
    unittest.main()
