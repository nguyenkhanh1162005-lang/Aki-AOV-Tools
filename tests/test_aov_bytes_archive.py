import hashlib
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.aov_bytes import encrypt_aes_zstd_resource
from app.ifix_merger import serialize_ifix_patch
from app.aov_bytes_archive import AOVBytesArchiveError, decode_archive_resources


def make_mses_payload():
    header = bytearray(0x8C)
    header[:4] = b"MSES"
    row = struct.pack("<I", 42) + b"test-row"
    body = struct.pack("<I", len(row)) + row
    struct.pack_into("<III", header, 4, 7, 3, 1)
    header[0x60:0x80] = hashlib.md5(body).hexdigest().encode("ascii")
    return bytes(header) + body


class AOVBytesArchiveTests(unittest.TestCase):
    def _archive(self, path: Path, entries: dict[str, bytes]) -> str:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries.items():
                archive.writestr(name, payload)
        return str(path)

    def test_decodes_supported_bytes_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            original = make_mses_payload()
            encoded = encrypt_aes_zstd_resource(original, "111_AssetRef")
            source = self._archive(folder / "mod.zip", {
                "Resources/111_AssetRef.bytes": encoded,
                "readme.txt": b"mod notes",
            })
            output = folder / "decoded.zip"

            report = decode_archive_resources([source], str(output))

            self.assertEqual(len(report["decoded"]), 1)
            self.assertEqual(report["decoded"][0]["mode"], "aes+zstd")
            self.assertEqual(report["decoded"][0]["decoded_size"], len(original))
            self.assertEqual(report["decoded"][0]["payload"]["payload_type"], "mses_v7")
            self.assertEqual(report["decoded"][0]["payload"]["field_count"], 3)
            self.assertEqual(report["decoded"][0]["payload"]["row_count"], 1)
            self.assertEqual(
                report["decoded"][0]["payload"]["row_prefix_inventory"],
                [{
                    "row_index": 0,
                    "row_prefix_u32_le": 42,
                    "row_prefix_hex": "0x0000002a",
                    "row_size": len(struct.pack("<I", 42) + b"test-row"),
                    "markers": [],
                }],
            )
            self.assertEqual(
                report["decoded"][0]["payload"]["duplicate_row_prefixes"], []
            )
            self.assertTrue(report["decoded"][0]["payload"]["checksum_valid"])
            with zipfile.ZipFile(output) as archive:
                decoded_path = report["decoded"][0]["output_path"]
                self.assertEqual(archive.read(decoded_path), original)
                stored_report = json.loads(archive.read("decode-report.json"))
            self.assertEqual(len(stored_report["decoded"]), 1)
            self.assertFalse(stored_report["game_tested"])

    def test_mses_inventory_reports_marker_and_duplicate_prefixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rows = [
                struct.pack("<I", 59500) + b"Hero_LiXin_Skin_C",
                struct.pack("<I", 59500) + b"second-row",
            ]
            header = bytearray(0x8C)
            header[:4] = b"MSES"
            body = b"".join(struct.pack("<I", len(row)) + row for row in rows)
            struct.pack_into("<III", header, 4, 7, 5, len(rows))
            header[0x60:0x80] = hashlib.md5(body).hexdigest().encode("ascii")
            original = bytes(header) + body
            encoded = encrypt_aes_zstd_resource(original, "InventoryTable")
            source = self._archive(folder / "inventory.zip", {
                "InventoryTable.bytes": encoded,
            })
            report = decode_archive_resources(
                [source], str(folder / "decoded.zip")
            )
            payload = report["decoded"][0]["payload"]
            self.assertEqual(
                payload["row_prefix_inventory"][0]["markers"],
                ["Hero_LiXin_Skin_C"],
            )
            self.assertEqual(
                payload["duplicate_row_prefixes"], ["0x0000e86c"]
            )

    def test_inspects_decoded_heroskin_ifix_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            patch = {
                "magic": 123456789,
                "bridge": "Project_d",
                "types": ["HeroType"],
                "methods": [{"inst": [(103, 0)], "eh": []}],
                "extern_methods": [],
                "strings": ["skin"],
                "fields": [{"new": False, "type": 0, "name": "skinField", "extra": b""}],
                "static_fields": [],
                "anon": [],
                "wrappers": "Wrappers",
                "assembly": "Project_d",
                "fixes": [{
                    "gen": False, "type": 0, "name": "SkinFix",
                    "g": [], "params": [], "patch": 0,
                }],
                "new_classes": [],
            }
            original = serialize_ifix_patch(patch)
            encoded = encrypt_aes_zstd_resource(original, "HeroSkin_2.bytes")
            source = self._archive(folder / "ifix.zip", {
                "Thanos/HeroSkin_2.bytes": encoded,
            })
            report = decode_archive_resources(
                [source], str(folder / "decoded.zip")
            )
            payload = report["decoded"][0]["payload"]
            self.assertEqual(payload["payload_type"], "ifix_patch")
            self.assertEqual(payload["fix_count"], 1)
            self.assertEqual(payload["method_count"], 1)
            self.assertEqual(payload["type_count"], 1)

    def test_decodes_bytes_inside_one_nested_resources_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            original = b"nested-resource" * 20
            encoded = encrypt_aes_zstd_resource(original, "NestedAsset")
            nested_path = folder / "Resources.zip"
            with zipfile.ZipFile(nested_path, "w") as nested:
                nested.writestr("NestedAsset.bytes", encoded)
            nested_data = nested_path.read_bytes()
            source = self._archive(folder / "wrapper.zip", {
                "Resources.zip": nested_data,
            })
            output = folder / "decoded.zip"

            report = decode_archive_resources([source], str(output))

            self.assertEqual(len(report["decoded"]), 1)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(
                    archive.read(report["decoded"][0]["output_path"]), original
                )

    def test_scans_startup_notice_text_inside_decoded_kernel_lua_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            package_buffer = __import__("io").BytesIO()
            with zipfile.ZipFile(package_buffer, "w") as package:
                package.writestr(
                    "Scripts/LoginNotice.lua",
                    "function ShowLoginNotice() print('Welcome notice') end",
                )
            encoded_package = encrypt_aes_zstd_resource(
                package_buffer.getvalue(), "KernelLua.pkg.bytes"
            )
            resources = self._archive(
                folder / "Resources.zip",
                {"KernelLua.pkg.bytes": encoded_package},
            )
            wrapper = self._archive(
                folder / "10-skin-wrapper.zip",
                {"Resources.zip": Path(resources).read_bytes()},
            )

            report = decode_archive_resources(
                [wrapper], str(folder / "scan-only.zip"), scan_only=True
            )

            self.assertGreater(report["notice_scan_files"], 0)
            self.assertTrue(report["notice_candidates"])
            self.assertTrue(any(
                item["keyword_group"] == "login_notice"
                and item["path"] == "Scripts/LoginNotice.lua"
                for item in report["notice_candidates"]
            ))
            self.assertIn(
                "bounded UnityFS AssetBundle object text",
                report["notice_scan_scope"],
            )
            self.assertFalse(report["game_tested"])
            self.assertTrue(report["scan_only"])
            self.assertFalse((folder / "scan-only.zip").exists())


    def test_scans_unity_assetbundle_typetree_strings_read_only(self):
        from types import SimpleNamespace

        class FakeObject:
            type = SimpleNamespace(name="MonoBehaviour")
            path_id = 17

            def read_typetree(self):
                return {
                    "m_Name": "LoginNoticeController",
                    "m_Message": "ShowLoginNotice",
                    "m_BrandLabel": "KIANA MOD AOV",
                }

        fake_asset = SimpleNamespace(
            name="resources.assets",
            objects={17: FakeObject()},
        )
        fake_unitypy = SimpleNamespace(
            load=lambda payload: SimpleNamespace(assets=[fake_asset])
        )

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            original_bundle = b"UnityFS\x00fake-unity-bundle"
            source = self._archive(folder / "bundle.zip", {
                "Resources/1.64.1/assetbundle/ui/startup.assetbundle": original_bundle,
            })

            with patch(
                "app.assetbundle_inspector._load_unitypy_aov",
                return_value=fake_unitypy,
            ):
                report = decode_archive_resources(
                    [source], str(folder / "scan-only.zip"), scan_only=True
                )

            self.assertEqual(report["notice_assetbundles_scanned"], 1)
            self.assertGreater(report["notice_scan_files"], 0)
            self.assertTrue(any(
                item["keyword_group"] == "login_notice"
                and "LoginNoticeController" in item["path"]
                for item in report["notice_candidates"]
            ))
            self.assertTrue(any(
                item["label"] == "brand"
                and "LoginNoticeController" in item["path"]
                for item in report["branding_candidates"]
            ))
            self.assertTrue(report["scan_only"])
            self.assertFalse((folder / "scan-only.zip").exists())
            with zipfile.ZipFile(source) as archive:
                self.assertEqual(
                    archive.read("Resources/1.64.1/assetbundle/ui/startup.assetbundle"),
                    original_bundle,
                )

    def test_expands_zip_inside_decoded_pkg_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            inner_payload = b"inner-aov-resource" * 25
            inner_encoded = encrypt_aes_zstd_resource(
                inner_payload, "InnerAsset.bytes"
            )
            package_path = folder / "CommonActions.zip"
            with zipfile.ZipFile(package_path, "w") as package:
                package.writestr("Actions/InnerAsset.bytes", inner_encoded)
            package_bytes = package_path.read_bytes()
            package_encoded = encrypt_aes_zstd_resource(
                package_bytes, "CommonActions.pkg.bytes"
            )
            resources_path = folder / "resources.zip"
            self._archive(resources_path, {
                "CommonActions.pkg.bytes": package_encoded,
            })
            source = self._archive(folder / "wrapper.zip", {
                "Resources.zip": resources_path.read_bytes(),
            })
            output = folder / "decoded.zip"

            report = decode_archive_resources([source], str(output))

            self.assertEqual(report["nested_packages_expanded"], 1)
            self.assertEqual(len(report["decoded"]), 2)
            nested_record = next(
                item for item in report["decoded"]
                if item["path"] == "Actions/InnerAsset.bytes"
            )
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(
                    archive.read(nested_record["output_path"]), inner_payload
                )

    def test_scans_exact_legacy_branding_labels_in_plain_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._archive(folder / "branding.zip", {
                "Resources/notice.txt": (
                    "KIANA MOD AOV\\n"
                    "Mod được tạo bởi youtuber KIANA MOD AOV\\n"
                    "Web Mod\\n"
                    "Youtube\\n"
                ).encode("utf-8"),
            })

            report = decode_archive_resources(
                [source], str(folder / "scan-only.zip"), scan_only=True
            )

            matches = report["branding_candidates"]
            self.assertEqual(
                {item["label"] for item in matches},
                {"creator_credit", "brand", "web_mod", "youtube"},
            )
            self.assertEqual(len(matches), 4)
            self.assertTrue(all(
                item["path"] == "Resources/notice.txt" for item in matches
            ))
            # The word "Youtube" must not be double-counted inside "youtuber".
            self.assertEqual(
                sum(item["label"] == "youtube" for item in matches), 1
            )
            self.assertFalse(report["branding_candidates_truncated"])
            self.assertFalse((folder / "scan-only.zip").exists())

    def test_scans_legacy_branding_inside_supported_encrypted_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plain = (
                "Mod được tạo bởi youtuber KIANA MOD AOV\\n"
                "Web Mod\\nYoutube\\n"
            ).encode("utf-8")
            encoded = encrypt_aes_zstd_resource(plain, "NoticeConfig")
            source = self._archive(folder / "wrapped-branding.zip", {
                "Resources/NoticeConfig.bytes": encoded,
            })

            report = decode_archive_resources(
                [source], str(folder / "scan-only.zip"), scan_only=True
            )

            matches = report["branding_candidates"]
            self.assertEqual(
                {item["label"] for item in matches},
                {"creator_credit", "web_mod", "youtube"},
            )
            self.assertTrue(all(
                item["path"] == "Resources/NoticeConfig.bytes"
                for item in matches
            ))
            self.assertEqual(len(report["decoded"]), 1)
            self.assertTrue(report["scan_only"])
            self.assertFalse((folder / "scan-only.zip").exists())


    def test_rejects_total_decoded_output_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            header = b"\x22\x4a\x00\x00" + (1).to_bytes(4, "little") + b"payload"
            source = self._archive(folder / "many-bytes.zip", {
                "first.bytes": header,
                "second.bytes": header,
            })
            output = folder / "decoded.zip"
            with patch("app.aov_bytes_archive.MAX_TOTAL_DECODED_BYTES", 10), patch(
                "app.aov_bytes_archive.decode_aov_resource",
                return_value=(b"123456", "test"),
            ):
                with self.assertRaisesRegex(
                    AOVBytesArchiveError, "Tổng dữ liệu sau giải mã vượt giới hạn"
                ):
                    decode_archive_resources([source], str(output))
            self.assertFalse(output.exists())

    def test_reports_unrecognized_bytes_without_claiming_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._archive(folder / "unknown.zip", {
                "unknown.bytes": b"not-an-aov-resource",
            })
            with self.assertRaisesRegex(
                AOVBytesArchiveError, "Không tìm thấy tài nguyên"
            ):
                decode_archive_resources([source], str(folder / "decoded.zip"))

    def test_rejects_ambiguous_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._archive(folder / "unsafe.zip", {
                "folder//asset.bytes": b"payload",
            })
            with self.assertRaisesRegex(AOVBytesArchiveError, "không an toàn"):
                decode_archive_resources([source], str(folder / "decoded.zip"))


if __name__ == "__main__":
    unittest.main()
