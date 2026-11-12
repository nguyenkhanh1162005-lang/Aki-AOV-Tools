import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_merger import (
    AOVMergeError,
    _assert_archive_inventory,
    _find_outer_entries,
    _normalise_vtk_path,
    _merge_nested_zip_package,
    _publish_staged_archive,
    _validate_archive_paths,
    _zip_entry_sha256,
)


class NestedPackageMergeTests(unittest.TestCase):
    @staticmethod
    def _zip(entries: dict[str, bytes]) -> bytes:
        import io
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries.items():
                archive.writestr(name, payload)
        return buffer.getvalue()

    def test_nested_zip_merge_keeps_unique_entries_and_identical_duplicates(self):
        from unittest.mock import patch

        base = self._zip({
            "Lua/base.lua": b"base",
            "Shared/config.txt": b"same",
        })
        overlay = self._zip({
            "Lua/vtk.lua": b"vtk",
            "Shared/config.txt": b"same",
        })
        with patch("app.aov_merger._decode", side_effect=lambda data, name: (data, "test")):
            with patch("app.aov_merger._encode", side_effect=lambda data, name, mode: data):
                merged, report = _merge_nested_zip_package(base, overlay, "KernelLua.pkg.bytes")

        self.assertTrue(report["verified"])
        self.assertEqual(report["added_entries"], 1)
        self.assertEqual(report["identical_duplicates"], 1)
        with zipfile.ZipFile(__import__("io").BytesIO(merged)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"Lua/base.lua", "Shared/config.txt", "Lua/vtk.lua"},
            )
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("Lua/vtk.lua"), b"vtk")

    def test_nested_zip_merge_fails_closed_on_different_inner_payload(self):
        import io
        from unittest.mock import patch

        base = self._zip({"Lua/shared.lua": b"base version"})
        overlay = self._zip({"Lua/shared.lua": b"overlay version"})
        with patch("app.aov_merger._decode", side_effect=lambda data, name: (data, "test")):
            with patch("app.aov_merger._encode", side_effect=lambda data, name, mode: data):
                with self.assertRaisesRegex(AOVMergeError, "Lua/shared.lua"):
                    _merge_nested_zip_package(base, overlay, "StableSystems_3.pkg.bytes")


class AOVArchivePathTests(unittest.TestCase):
    def test_finds_wrapper_files_case_insensitively_with_directory_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wrapper.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("mod/Payload/rEsOuRcEs.ZIP", b"resources")
                archive.writestr("mod/dAnHsAcHsKiN.TxT", b"skin list")
            with zipfile.ZipFile(path) as archive:
                resources = _find_outer_entries(archive, "Resources.zip")
                skins = _find_outer_entries(archive, "DanhSachSkin.txt")
            self.assertEqual(len(resources), 1)
            self.assertEqual(resources[0].filename, "mod/Payload/rEsOuRcEs.ZIP")
            self.assertEqual(len(skins), 1)
            self.assertEqual(skins[0].filename, "mod/dAnHsAcHsKiN.TxT")

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

    def test_rejects_dot_segments_and_repeated_separators_in_overlay_paths(self):
        for name in (
            "Resources/1.64.1/Thanos/./HeroSkin_2.bytes",
            "Resources/1.64.1//Thanos/HeroSkin_2.bytes",
        ):
            with self.subTest(name=name):
                with self.assertRaises(AOVMergeError):
                    _normalise_vtk_path(name)

    def test_rejects_ambiguous_and_duplicate_archive_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for unsafe_name in ("folder/./asset.bytes", "folder//asset.bytes"):
                path = folder / "ambiguous.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(unsafe_name, b"payload")
                with zipfile.ZipFile(path) as archive:
                    with self.assertRaisesRegex(AOVMergeError, "không an toàn"):
                        _validate_archive_paths(archive, "ambiguous.zip")

            path = folder / "duplicate.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("same.bytes", b"first")
                archive.writestr("same.bytes", b"second")
            with zipfile.ZipFile(path) as archive:
                with self.assertRaisesRegex(AOVMergeError, "trùng lặp"):
                    _validate_archive_paths(archive, "duplicate.zip")

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

    def test_exact_inventory_check_accepts_preserved_and_added_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("base.bytes", b"base")
                archive.writestr("added.bytes", b"added")
            with zipfile.ZipFile(path) as archive:
                _assert_archive_inventory(
                    archive, {"base.bytes", "added.bytes"}, "result.zip"
                )

    def test_exact_inventory_check_rejects_missing_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("base.bytes", b"base")
            with zipfile.ZipFile(path) as archive:
                with self.assertRaisesRegex(AOVMergeError, "không giữ nguyên"):
                    _assert_archive_inventory(
                        archive, {"base.bytes", "required.bytes"}, "result.zip"
                    )

    def test_exact_inventory_check_rejects_unexpected_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("base.bytes", b"base")
                archive.writestr("unexpected.bytes", b"extra")
            with zipfile.ZipFile(path) as archive:
                with self.assertRaisesRegex(AOVMergeError, "không giữ nguyên"):
                    _assert_archive_inventory(
                        archive, {"base.bytes"}, "result.zip"
                    )

    def test_atomic_publish_replaces_destination_only_after_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            staged = folder / "staged.zip"
            output = folder / "result.zip"
            output.write_bytes(b"previous-good-output")
            with zipfile.ZipFile(staged, "w") as archive:
                archive.writestr("base.bytes", b"base")
                archive.writestr("new.bytes", b"new")

            _publish_staged_archive(
                staged, output, {"base.bytes", "new.bytes"}
            )

            with zipfile.ZipFile(output) as archive:
                self.assertEqual(
                    set(archive.namelist()), {"base.bytes", "new.bytes"}
                )
            self.assertFalse(staged.exists())

    def test_atomic_publish_keeps_previous_output_when_validation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            staged = folder / "staged.zip"
            output = folder / "result.zip"
            previous = b"previous-good-output"
            output.write_bytes(previous)
            with zipfile.ZipFile(staged, "w") as archive:
                archive.writestr("only.bytes", b"partial")

            with self.assertRaisesRegex(AOVMergeError, "không giữ nguyên"):
                _publish_staged_archive(
                    staged, output, {"required.bytes"}
                )

            self.assertEqual(output.read_bytes(), previous)
            self.assertTrue(staged.exists())

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
