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

    def test_inspects_assetbundle_dependencies_inside_zip(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = self._zip(
                folder,
                "assets.zip",
                {"Resources/Actor.assetbundle": b"UnityFS\x00fixture"},
            )
            report_data = {
                "serialized_file_count": 2,
                "object_count": 9,
                "container_entry_count": 3,
                "external_reference_count": 1,
                "object_reference_count": 2,
                "reference_scan_complete": True,
                "reference_scan_errors": [],
                "object_references": [],
                "reference_integrity": {
                    "local_resolved": 0,
                    "local_missing": [],
                    "external_resolved": 0,
                    "external_dependencies": [],
                    "invalid_external_indices": [],
                },
                "object_type_counts": {"Texture2D": 5, "Mesh": 4},
                "external_paths": ["archive:/CAB-other.resS"],
            }
            with patch("app.aov_inspector.inspect_assetbundle", return_value=report_data):
                report = inspect_archives([path])
            self.assertIn("KIỂM TRA CẤU TRÚC VÀ PHỤ THUỘC ASSETBUNDLE", report)
            self.assertIn("Serialized files: 2 | Objects: 9 | Container entries: 3", report)
            self.assertIn("archive:/CAB-other.resS", report)

    def test_compares_same_path_assetbundles_across_zip_inputs(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "first.zip", {"Actor/hero.assetbundle": b"UnityFS\x00one"})
            second = self._zip(folder, "second.zip", {"Actor/hero.assetbundle": b"UnityFS\x00two"})
            comparison = {
                "unity_versions_match": True,
                "platforms_match": True,
                "container_inventory_complete": True,
                "shared_container_paths": 2,
                "replacement_candidates": ["assets/hero.prefab"],
                "cross_bundle_references": {
                    "resolved_count": 0,
                    "missing_path_id_count": 0,
                    "ambiguous_cab_count": 0,
                    "unknown_cab_count": 0,
                    "index_incomplete": False,
                    "resolved": [],
                    "missing_path_ids": [],
                    "ambiguous_cab_names": [],
                    "unknown_cabs": [],
                },
                "new_container_paths": ["assets/new.prefab"],
                "container_type_conflicts": [],
                "structural_checks_pass": True,
            }
            with patch("app.aov_inspector.inspect_assetbundle", return_value={
                "serialized_file_count": 1,
                "object_count": 2,
                "container_entry_count": 2,
                "external_reference_count": 0,
                "object_reference_count": 0,
                "reference_scan_complete": True,
                "reference_scan_errors": [],
                "object_references": [],
                "reference_integrity": {
                    "local_resolved": 0,
                    "local_missing": [],
                    "external_resolved": 0,
                    "external_dependencies": [],
                    "invalid_external_indices": [],
                },
                "object_type_counts": {"GameObject": 2},
                "external_paths": [],
            }), patch("app.aov_inspector.compare_assetbundle_overlays", return_value=comparison):
                report = inspect_archives([first, second])
            self.assertIn("ĐỐI CHIẾU ASSETBUNDLE TRÙNG ĐƯỜNG DẪN", report)
            self.assertIn("Actor/hero.assetbundle [first.zip vs second.zip]", report)
            self.assertIn("Cho phép ghép tự động: False", report)

    def test_inspects_small_wwise_bnk_inside_zip(self):
        import struct

        def chunk(name, payload):
            return name.encode("ascii") + struct.pack("<I", len(payload)) + payload

        bank = chunk("BKHD", struct.pack("<II", 134, 4321))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = self._zip(folder, "audio.zip", {"Sound/Hero.bnk": bank})
            report = inspect_archives([path])
            self.assertIn("KIỂM TRA CẤU TRÚC WWISE BNK", report)
            self.assertIn("Version 134 | Bank ID 4321", report)
            self.assertIn("Media: 0 | HIRC objects: 0", report)

    def test_reports_malformed_wwise_bnk_without_aborting_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = self._zip(
                folder,
                "bad-audio.zip",
                {"Sound/Hero.bnk": b"BKHD" + bytes([99, 0, 0, 0])},
            )
            report = inspect_archives([path])
            self.assertIn("BNK không hợp lệ hoặc chưa hỗ trợ", report)

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
