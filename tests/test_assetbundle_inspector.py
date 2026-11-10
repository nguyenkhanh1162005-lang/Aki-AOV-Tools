import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.assetbundle_inspector import (
    AssetBundleInspectError,
    compare_assetbundle_overlays,
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
            report = inspect_assetbundle(b"UnityFS\x00fixture", "sample.assetbundle")

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

    def test_compares_overlay_container_paths_without_claiming_merge_safety(self):
        def make_env(entries):
            objects = {
                index: SimpleNamespace(type=SimpleNamespace(name=kind), path_id=index)
                for index, (path, kind) in enumerate(entries.items(), start=1)
            }
            container = {
                path: objects[index]
                for index, path in enumerate(entries, start=1)
            }
            serialized = SimpleNamespace(
                name="CAB-main",
                unity_version="2022.3.0f1",
                target_platform=SimpleNamespace(name="iOS"),
                objects=objects,
                externals=[],
            )
            return SimpleNamespace(assets=[serialized], container=container, files={"bundle": object()})

        base_env = make_env({"assets/hero.prefab": "GameObject", "assets/old.png": "Texture2D"})
        overlay_env = make_env({"assets/hero.prefab": "GameObject", "assets/new.png": "Texture2D"})
        envs = iter([base_env, overlay_env])
        fake_unity = SimpleNamespace(load=lambda data: next(envs))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = compare_assetbundle_overlays(
                b"UnityFS\x00base",
                b"UnityFS\x00overlay",
            )

        self.assertTrue(report["unity_versions_match"])
        self.assertTrue(report["platforms_match"])
        self.assertEqual(report["shared_container_paths"], 1)
        self.assertEqual(report["replacement_candidates"], ["assets/hero.prefab"])
        self.assertEqual(report["new_container_paths"], ["assets/new.png"])
        self.assertTrue(report["structural_checks_pass"])
        self.assertFalse(report["merge_safe"])

    def test_detects_container_type_conflicts_and_version_drift(self):
        def make_env(version, kind):
            obj = SimpleNamespace(type=SimpleNamespace(name=kind), path_id=1)
            serialized = SimpleNamespace(
                name="CAB-main",
                unity_version=version,
                target_platform=SimpleNamespace(name="iOS"),
                objects={1: obj},
                externals=[],
            )
            return SimpleNamespace(
                assets=[serialized],
                container={"assets/hero.prefab": obj},
                files={"bundle": object()},
            )

        envs = iter([make_env("2022.3.0f1", "GameObject"), make_env("2021.3.0f1", "Texture2D")])
        fake_unity = SimpleNamespace(load=lambda data: next(envs))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = compare_assetbundle_overlays(b"UnityFS\x00base", b"UnityFS\x00overlay")

        self.assertFalse(report["unity_versions_match"])
        self.assertEqual(report["container_type_conflicts"], ["assets/hero.prefab"])
        self.assertFalse(report["structural_checks_pass"])
        self.assertFalse(report["merge_safe"])

    def test_scans_local_and_external_object_references(self):
        object_a = SimpleNamespace(
            type=SimpleNamespace(name="GameObject"),
            path_id=100,
            read_typetree=lambda: {
                "m_Component": [
                    {"component": {"m_FileID": 0, "m_PathID": 200}},
                    {"m_Script": {"m_FileID": 1, "m_PathID": 300}},
                ]
            },
        )
        serialized = SimpleNamespace(
            name="CAB-main",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={100: object_a},
            externals=[SimpleNamespace(path="archive:/CAB-dependency.resS", name="CAB-dependency.resS")],
        )
        env = SimpleNamespace(assets=[serialized], container={}, files={"bundle": object()})
        fake_unity = SimpleNamespace(load=lambda data: env)
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = inspect_assetbundle(b"UnityFS\x00fixture")
        self.assertEqual(report["object_reference_count"], 2)
        self.assertTrue(report["reference_scan_complete"])
        self.assertEqual(report["object_references"][0]["target_file"], "CAB-dependency.resS")
        self.assertEqual(report["object_references"][1]["target_file"], "local")

    def test_resolves_local_and_loaded_external_cab_targets(self):
        source_obj = SimpleNamespace(
            type=SimpleNamespace(name="GameObject"),
            path_id=100,
            read_typetree=lambda: {
                "local": {"m_FileID": 0, "m_PathID": 200},
                "external": {"m_FileID": 1, "m_PathID": 300},
                "missing": {"m_FileID": 0, "m_PathID": 999},
            },
        )
        local_target = SimpleNamespace(type=SimpleNamespace(name="Transform"), path_id=200)
        external_target = SimpleNamespace(type=SimpleNamespace(name="Texture2D"), path_id=300)
        source_file = SimpleNamespace(
            name="CAB-main",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={100: source_obj, 200: local_target},
            externals=[SimpleNamespace(path="archive:/CAB-dependency.assets", name="CAB-dependency.assets")],
        )
        external_file = SimpleNamespace(
            name="CAB-dependency.assets",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={300: external_target},
            externals=[],
        )
        env = SimpleNamespace(
            assets=[source_file, external_file],
            container={},
            files={"bundle": object()},
        )
        fake_unity = SimpleNamespace(load=lambda data: env)
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = inspect_assetbundle(b"UnityFS\x00fixture")
        integrity = report["reference_integrity"]
        self.assertEqual(integrity["local_resolved"], 1)
        self.assertEqual(integrity["external_resolved"], 1)
        self.assertEqual(len(integrity["local_missing"]), 1)
        self.assertEqual(integrity["local_missing"][0]["path_id"], 999)
        self.assertEqual(integrity["external_dependencies"], [])

    def test_real_unityfs_null_terminated_signature_is_required(self):
        self.assertTrue(b"UnityFS\x00fixture".startswith(b"UnityFS\x00"))
        with self.assertRaises(AssetBundleInspectError):
            inspect_assetbundle(b"UnityFS\\x00fixture")

    def test_wraps_parser_errors_as_inspection_errors(self):
        fake_unity = SimpleNamespace(load=lambda data: (_ for _ in ()).throw(RuntimeError("bad bundle")))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            with self.assertRaisesRegex(AssetBundleInspectError, "bad bundle"):
                inspect_assetbundle(b"UnityFS\x00fixture")


if __name__ == "__main__":
    unittest.main()
