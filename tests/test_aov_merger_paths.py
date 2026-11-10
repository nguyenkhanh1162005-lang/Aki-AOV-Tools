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
        import io
        from types import SimpleNamespace

        class FakeArchive:
            def __init__(self, payload):
                self.payload = payload

            def open(self, info, mode):
                return io.BytesIO(self.payload)

        first_info = SimpleNamespace(filename="same.bytes", CRC=1234, file_size=4)
        second_info = SimpleNamespace(filename="same.bytes", CRC=1234, file_size=4)
        self.assertEqual(first_info.CRC, second_info.CRC)
        self.assertEqual(first_info.file_size, second_info.file_size)
        self.assertNotEqual(
            _zip_entry_sha256(FakeArchive(b"AAAA"), first_info),
            _zip_entry_sha256(FakeArchive(b"BBBB"), second_info),
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
