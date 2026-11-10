import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_merger import AOVMergeError, _normalise_vtk_path, _validate_archive_paths, _zip_entry_sha256


class AOVArchivePathTests(unittest.TestCase):
    def test_normalizes_supported_vtk_prefix(self):
        self.assertEqual(
            _normalise_vtk_path("Resources/1.64.1/Thanos/HeroSkin_2.bytes"),
            "Thanos/HeroSkin_2.bytes",
        )

    def test_rejects_parent_traversal(self):
        for name in ("../outside.bytes", "Resources/../../outside.bytes", r"..\outside.bytes"):
            with self.subTest(name=name):
                with self.assertRaises(AOVMergeError):
                    _normalise_vtk_path(name)

    def test_rejects_absolute_and_drive_paths(self):
        for name in ("/absolute/file.bytes", "C:/outside/file.bytes"):
            with self.subTest(name=name):
                with self.assertRaises(AOVMergeError):
                    _normalise_vtk_path(name)

    def test_rejects_windows_drive_path(self):
        with self.assertRaises(AOVMergeError):
            _normalise_vtk_path("C:/outside/file.bytes")

    def test_duplicate_payloads_are_compared_by_sha256_not_only_crc_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_path = Path(tmp) / "first.zip"
            second_path = Path(tmp) / "second.zip"
            with zipfile.ZipFile(first_path, "w") as archive:
                archive.writestr("same.bytes", b"AAAA")
            with zipfile.ZipFile(second_path, "w") as archive:
                archive.writestr("same.bytes", b"BBBB")
            with zipfile.ZipFile(first_path) as first, zipfile.ZipFile(second_path) as second:
                first_info = first.getinfo("same.bytes")
                second_info = second.getinfo("same.bytes")
                second_info.CRC = first_info.CRC
                self.assertEqual(first_info.file_size, second_info.file_size)
                self.assertEqual(first_info.CRC, second_info.CRC)
                self.assertNotEqual(
                    _zip_entry_sha256(first, first_info),
                    _zip_entry_sha256(second, second_info),
                )

    def test_rejects_unsafe_archive_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape.bytes", b"bad")
            with zipfile.ZipFile(path) as archive:
                with self.assertRaisesRegex(AOVMergeError, "không an toàn"):
                    _validate_archive_paths(archive, "unsafe.zip")

    def test_rejects_symlink_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "symlink.zip"
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(info, b"target")
            with zipfile.ZipFile(path) as archive:
                with self.assertRaisesRegex(AOVMergeError, "không an toàn"):
                    _validate_archive_paths(archive, "symlink.zip")


if __name__ == "__main__":
    unittest.main()
