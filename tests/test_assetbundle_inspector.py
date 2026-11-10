import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.assetbundle_inspector import (
    AssetBundleInspectError,
    inspect_assetbundle,
)


class AssetBundleInspectorTests(unittest.TestCase):
    def test_rejects_empty_or_unknown_bundle_signature(self):
        with self.assertRaisesRegex(AssetBundleInspectError, "rỗng"):
            inspect_assetbundle(b"")
        with self.assertRaisesRegex(AssetBundleInspectError, "chữ ký"):
            inspect_assetbundle(b"not-a-bundle")

    def test_reports_objects_containers_and_external_references(self):
        object_a = SimpleNamespace(type=SimpleNamespace(name="Texture2D"), path_id=101)
        object_b = SimpleNamespace(type=SimpleNamespace(name="Mesh"), path_id=102)
        serialized = SimpleNamespace(
            name="CAB-abc",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={101: object_a, 102: object_b},
            externals=[SimpleNamespace(path="archive:/CAB-dependency.resS")],
        )
        env = SimpleNamespace(
            assets=[serialized],
            container={"assets/icon.png": object_a},
            files={"bundle": object()},
        )
        fake_unity = SimpleNamespace(load=lambda data: env)
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = inspect_assetbundle(b"UnityFS\\x00fixture", "sample.assetbundle")

        self.assertEqual(report["serialized_file_count"], 1)
        self.assertEqual(report["object_count"], 2)
        self.assertEqual(report["object_type_counts"], {"Texture2D": 1, "Mesh": 1})
        self.assertEqual(report["container_entry_count"], 1)
        self.assertEqual(report["external_reference_count"], 1)
        self.assertEqual(
            report["external_paths"],
            ["archive:/CAB-dependency.resS"],
        )
        self.assertTrue(report["read_only"])

    def test_wraps_parser_errors_as_inspection_errors(self):
        fake_unity = SimpleNamespace(load=lambda data: (_ for _ in ()).throw(RuntimeError("bad bundle")))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            with self.assertRaisesRegex(AssetBundleInspectError, "bad bundle"):
                inspect_assetbundle(b"UnityFS\\x00fixture")


if __name__ == "__main__":
    unittest.main()
