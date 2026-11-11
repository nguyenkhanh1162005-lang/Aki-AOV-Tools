import hashlib
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

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
            self.assertTrue(report["decoded"][0]["payload"]["checksum_valid"])
            with zipfile.ZipFile(output) as archive:
                decoded_path = report["decoded"][0]["output_path"]
                self.assertEqual(archive.read(decoded_path), original)
                stored_report = json.loads(archive.read("decode-report.json"))
            self.assertEqual(len(stored_report["decoded"]), 1)
            self.assertFalse(stored_report["game_tested"])

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
