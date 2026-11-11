import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.resource_manifest import ResourceManifestError, build_resource_manifest


class ResourceManifestTests(unittest.TestCase):
    def _zip(self, folder, name, entries):
        path = folder / name
        with zipfile.ZipFile(path, "w") as archive:
            for item, data in entries.items():
                archive.writestr(item, data)
        return str(path)

    def test_manifest_hashes_entries_and_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "pack.zip", {"docs/readme.txt": b"hello"})
            before = Path(source).read_bytes()
            result = build_resource_manifest([source])
            self.assertEqual(result["resource_count"], 1)
            self.assertEqual(result["resources"][0]["sha256"],
                             "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
            self.assertFalse(result["payloads_modified"])
            self.assertEqual(Path(source).read_bytes(), before)

    def test_inspects_one_nested_resources_zip_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            nested_path = folder / "Resources.zip"
            with zipfile.ZipFile(nested_path, "w") as nested:
                nested.writestr(
                    "Resources/1.64.1/Databin/BattleBank.bytes", b"opaque"
                )
                nested.writestr("Resources/1.64.1/Sound/Hero/595.bnk", b"BKHD" + b"data")
            wrapper = folder / "skin-wrapper.zip"
            with zipfile.ZipFile(wrapper, "w") as outer:
                outer.write(nested_path, "mod/Resources.zip")
                outer.writestr("mod/danhsachskin.txt", b"skin list")
            result = build_resource_manifest([str(wrapper)])
            nested_items = [
                item for item in result["resources"]
                if item.get("container_path") == "mod/Resources.zip"
            ]
            self.assertEqual(len(nested_items), 2)
            by_path = {item["path"]: item for item in nested_items}
            self.assertEqual(
                by_path["Resources/1.64.1/Databin/BattleBank.bytes"]["format"],
                "mses",
            )
            self.assertTrue(
                by_path["Resources/1.64.1/Databin/BattleBank.bytes"]["is_aov_resource"]
            )
            self.assertEqual(
                by_path["Resources/1.64.1/Sound/Hero/595.bnk"]["format"],
                "wwise_bnk",
            )
            self.assertEqual(result["resource_count"], 4)

    def test_marks_macos_appledouble_files_as_metadata_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._zip(Path(tmp), "mac-pack.zip", {
                "__MACOSX/Resources/._BattleBank.bytes": b"AppleDouble",
                "Resources/1.64.1/._HeroSkin_2.bytes": b"AppleDouble",
                ".DS_Store": b"metadata",
            })
            result = build_resource_manifest([source])
            self.assertEqual(result["resource_count"], 3)
            self.assertTrue(all(item["is_metadata_noise"] for item in result["resources"]))
            self.assertTrue(all(item["format"] == "metadata_noise" for item in result["resources"]))
            self.assertTrue(all(not item["is_aov_resource"] for item in result["resources"]))

    def test_marks_aov_mses_as_unsupported_aov_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._zip(Path(tmp), "aov.zip", {
                "Resources/1.64.1/Databin/BattleBank.bytes": b"opaque"
            })
            result = build_resource_manifest([source])
            item = result["resources"][0]
            self.assertEqual(item["format"], "mses")
            self.assertTrue(item["is_aov_resource"])

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._zip(Path(tmp), "bad.zip", {"../escape": b"x"})
            with self.assertRaises(ResourceManifestError):
                build_resource_manifest([source])

    def test_rejects_ambiguous_dot_segments_and_repeated_separators(self):
        for unsafe_path in ("folder/./asset.bytes", "folder//asset.bytes"):
            with self.subTest(path=unsafe_path):
                with tempfile.TemporaryDirectory() as tmp:
                    source = self._zip(Path(tmp), "ambiguous.zip", {unsafe_path: b"x"})
                    with self.assertRaises(ResourceManifestError):
                        build_resource_manifest([source])

    def test_rejects_encrypted_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "encrypted.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("secret.bytes", b"x")
            data = bytearray(path.read_bytes())
            local = data.find(b"PK\x03\x04")
            central = data.find(b"PK\x01\x02")
            self.assertGreaterEqual(local, 0)
            self.assertGreaterEqual(central, 0)
            local_flags = struct.unpack_from("<H", data, local + 6)[0]
            central_flags = struct.unpack_from("<H", data, central + 8)[0]
            struct.pack_into("<H", data, local + 6, local_flags | 1)
            struct.pack_into("<H", data, central + 8, central_flags | 1)
            path.write_bytes(data)
            with self.assertRaisesRegex(ResourceManifestError, "được mã hóa"):
                build_resource_manifest([str(path)])

    @patch("app.resource_manifest.inspect_assetbundle")
    def test_extracts_unity_assetbundle_dependencies_read_only(self, inspect_bundle):
        inspect_bundle.return_value = {
            "assetbundle_metadata_complete": True,
            "reference_scan_complete": True,
            "assetbundle_metadata_count": 2,
            "assetbundle_metadata": [
                {"dependency_count": 2, "dependencies": ["shared/materials", "shared/textures"]},
                {"dependency_count": 1, "dependencies": ["shared/materials"]},
            ],
            "external_reference_count": 1,
            "external_references": [
                {"from": "CAB-1", "to": "CAB-2"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = self._zip(Path(tmp), "unity.zip", {
                "assets/hero.assetbundle": b"UnityFS\\x00" + b"payload"
            })
            result = build_resource_manifest([source])
            item = result["resources"][0]
            self.assertEqual(item["unity_dependencies"],
                             ["shared/materials", "shared/textures"])
            self.assertEqual(item["unity_external_paths"], ["CAB-2"])
            self.assertTrue(item["dependency_inventory_complete"])
            inspect_bundle.assert_called_once()

    @patch("app.resource_manifest.inspect_assetbundle")
    def test_truncated_unity_dependency_inventory_is_not_complete(self, inspect_bundle):
        inspect_bundle.return_value = {
            "assetbundle_metadata_complete": True,
            "reference_scan_complete": True,
            "assetbundle_metadata_count": 2,
            "assetbundle_metadata": [
                {"dependency_count": 201, "dependencies": ["only-first-200"]}
            ],
            "external_reference_count": 101,
            "external_references": [{"from": "CAB-1", "to": "CAB-2"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = self._zip(Path(tmp), "unity.zip", {
                "assets/hero.assetbundle": b"UnityFS\\x00" + b"payload"
            })
            item = build_resource_manifest([source])["resources"][0]
            self.assertFalse(item["dependency_inventory_complete"])
            self.assertEqual(item["dependency_inspection"], "partial")

    def test_rejects_empty_input(self):
        with self.assertRaises(ResourceManifestError):
            build_resource_manifest([])


if __name__ == "__main__":
    unittest.main()
