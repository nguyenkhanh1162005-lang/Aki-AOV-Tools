import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.assetbundle_inspector import (
    AssetBundleInspectError,
    build_path_id_remap_plan,
    build_external_file_id_remap_plan,
    build_pptr_remap_plan,
    build_assetbundle_metadata_remap_plan,
    build_assetbundle_metadata_merge_plan,
    build_streamed_resource_merge_plan,
    build_cross_cab_reference_merge_plan,
    build_assetbundle_merge_preflight,
    compare_assetbundle_overlays,
    inspect_assetbundle,
    verify_assetbundle_roundtrip,
)


class AssetBundleInspectorTests(unittest.TestCase):
    def test_path_id_plan_requires_explicit_proof_before_deduplication(self):
        args = (
            [10, 20],
            [10, 20, 30],
            {"10": "Texture2D", "20": "Mesh"},
            {"10": "Texture2D", "20": "Mesh"},
            {"10": "same", "20": "base-hash"},
            {"10": "same", "20": "overlay-hash"},
        )
        conservative = build_path_id_remap_plan(*args)
        self.assertFalse(conservative["ready"])
        self.assertEqual(conservative["deduplicate"], {})
        self.assertIn("PPtr/dependency equivalence", conservative["blocked_path_ids"][0]["reason"])

        proven = build_path_id_remap_plan(*args, allow_payload_deduplication=True)
        self.assertTrue(proven["ready"])
        self.assertEqual(proven["deduplicate"], {"10": 10})
        self.assertEqual(proven["remap"], {"20": 31})
        self.assertEqual(proven["preserved_overlay_only_count"], 1)

    def test_external_file_id_plan_reuses_exact_paths_and_appends_new_paths(self):
        plan = build_external_file_id_remap_plan(
            ["archive:/CAB-base", "archive:/CAB-shared"],
            ["archive:/CAB-shared", "archive:/CAB-overlay"],
        )
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["file_id_remap"], {"1": 2, "2": 3})
        self.assertEqual(
            plan["merged_externals"],
            ["archive:/CAB-base", "archive:/CAB-shared", "archive:/CAB-overlay"],
        )
        self.assertEqual(plan["appended_count"], 1)

    def test_external_file_id_plan_rejects_ambiguous_duplicate_paths(self):
        plan = build_external_file_id_remap_plan(
            ["archive:/CAB-dup", "archive:/CAB-dup"],
            ["archive:/CAB-dup"],
        )
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["file_id_remap"], {})
        self.assertIn("ambiguous", plan["reason"])

    def test_external_file_id_plan_rejects_empty_paths(self):
        plan = build_external_file_id_remap_plan(["archive:/CAB-base"], [""])
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["merged_externals"], [])






    def test_combined_preflight_requires_all_sidecar_inputs_together(self):
        base = {
            "cab-a": {
                "inventories_complete": True,
                "path_ids": [1],
                "types": {"1": "Texture2D"},
                "payload_hashes": {"1": "base"},
                "externals": [],
                "references": [],
            }
        }
        overlay = {
            "cab-a": {
                "inventories_complete": True,
                "path_ids": [2],
                "types": {"2": "Texture2D"},
                "payload_hashes": {"2": "overlay"},
                "externals": [],
                "references": [],
            }
        }
        plan = build_assetbundle_merge_preflight(
            base, overlay, base_resources={"base.resS": 10}
        )
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["stage"], "sidecar")

    def test_combined_preflight_rejects_empty_overlay_inventory(self):
        plan = build_assetbundle_merge_preflight({}, {})
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["stage"], "cross_cab")

    def test_cross_cab_plan_resolves_local_and_external_pptrs(self):
        base = {
            "cab-a": {
                "inventories_complete": True, "path_ids": [1], "types": {"1": "GameObject"},
                "payload_hashes": {"1": "base-a"}, "externals": ["cab-b"], "references": [],
            },
            "cab-b": {
                "inventories_complete": True, "path_ids": [2], "types": {"2": "Texture2D"},
                "payload_hashes": {"2": "base-b"}, "externals": [], "references": [],
            },
        }
        overlay = {
            "cab-a": {
                "inventories_complete": True, "path_ids": [1, 3],
                "types": {"1": "GameObject", "3": "GameObject"},
                "payload_hashes": {"1": "overlay-a", "3": "overlay-a3"},
                "externals": ["cab-b"],
                "references": [{"file_id": 0, "path_id": 1}, {"file_id": 1, "path_id": 2}],
            },
            "cab-b": {
                "inventories_complete": True, "path_ids": [2], "types": {"2": "Texture2D"},
                "payload_hashes": {"2": "overlay-b"}, "externals": [],
                "references": [{"file_id": 0, "path_id": 2}],
            },
        }
        plan = build_cross_cab_reference_merge_plan(base, overlay)
        self.assertTrue(plan["ready"])
        refs = plan["cabs"]["cab-a"]["pptr_plan"]["rewritten_references"]
        self.assertEqual(refs[0]["file_id"], 0)
        self.assertEqual(refs[1]["file_id"], 1)
        self.assertEqual(refs[1]["path_id"], 3)


    def test_cross_cab_plan_resolves_reference_to_base_only_cab(self):
        base = {
            "cab-a": {
                "inventories_complete": True, "path_ids": [1], "types": {"1": "GameObject"},
                "payload_hashes": {"1": "base-a"}, "externals": ["cab-c"], "references": [],
            },
            "cab-c": {
                "inventories_complete": True, "path_ids": [9], "types": {"9": "Texture2D"},
                "payload_hashes": {"9": "base-c"}, "externals": [], "references": [],
            },
        }
        overlay = {
            "cab-a": {
                "inventories_complete": True, "path_ids": [2], "types": {"2": "GameObject"},
                "payload_hashes": {"2": "overlay-a"}, "externals": ["cab-c"],
                "references": [{"file_id": 1, "path_id": 9}],
            },
        }
        plan = build_cross_cab_reference_merge_plan(base, overlay)
        self.assertTrue(plan["ready"])
        self.assertEqual(
            plan["cabs"]["cab-a"]["pptr_plan"]["rewritten_references"][0]["path_id"], 9
        )

    def test_cross_cab_plan_blocks_unknown_external_target(self):
        overlay = {
            "cab-a": {
                "inventories_complete": True, "path_ids": [1], "types": {"1": "GameObject"},
                "payload_hashes": {"1": "a"}, "externals": ["missing-cab"],
                "references": [{"file_id": 1, "path_id": 99}],
            },
        }
        plan = build_cross_cab_reference_merge_plan({}, overlay)
        self.assertFalse(plan["ready"])
        self.assertIn("PPtr plan blocked", plan["reason"])

    def test_streamed_resource_plan_offsets_overlay_ranges_for_shared_sidecar(self):
        plan = build_streamed_resource_merge_plan(
            {"shared.resS": 100},
            {"shared.resS": 50},
            [{"path": "shared.resS", "offset": 10, "size": 20}],
            [{"path": "shared.resS", "offset": 5, "size": 10}],
        )
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["merged_resource_lengths"]["shared.resS"], 150)
        self.assertEqual(plan["overlay_streams"][0]["offset"], 105)

    def test_streamed_resource_plan_rejects_out_of_bounds_and_traversal(self):
        invalid_range = build_streamed_resource_merge_plan(
            {"base.resS": 10}, {}, [{"path": "base.resS", "offset": 9, "size": 2}], []
        )
        self.assertFalse(invalid_range["ready"])
        invalid_path = build_streamed_resource_merge_plan(
            {"../escape.resS": 10}, {}, [], []
        )
        self.assertFalse(invalid_path["ready"])

    def test_assetbundle_metadata_merge_offsets_overlay_preload_ranges(self):
        base = {
            "inspection_complete": True, "assetbundle_name": "shared",
            "preload_table_count": 2,
            "preload_pointer_inventory": [{"file_id": 0, "path_id": 1}, {"file_id": 0, "path_id": 2}],
            "container_record_inventory": [{
                "path": "base/path", "preload_index": 0, "preload_size": 2,
                "asset": {"file_id": 0, "path_id": 1},
            }],
            "main_asset": None, "dependencies": ["dep-a"],
        }
        overlay = {
            "inspection_complete": True, "assetbundle_name": "shared",
            "preload_table_count": 2,
            "preload_pointer_inventory": [{"file_id": 0, "path_id": 3}, {"file_id": 0, "path_id": 4}],
            "container_record_inventory": [{
                "path": "overlay/path", "preload_index": 0, "preload_size": 2,
                "asset": {"file_id": 0, "path_id": 3},
            }],
            "main_asset": None, "dependencies": ["dep-a", "dep-b"],
        }
        rewritten = {
            "ready": True,
            "rewritten": {
                "preload": [{"file_id": 0, "path_id": 30}, {"file_id": 0, "path_id": 40}],
                "container": [{"file_id": 0, "path_id": 30}],
                "main_asset": None,
            },
        }
        plan = build_assetbundle_metadata_merge_plan(base, overlay, rewritten)
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["preload_offset"], 2)
        self.assertEqual(plan["container_records"][1]["preload_index"], 2)
        self.assertEqual(plan["container_records"][1]["preload_size"], 2)
        self.assertEqual(plan["dependencies"], ["dep-a", "dep-b"])

    def test_assetbundle_metadata_merge_rejects_container_collision(self):
        base = {
            "inspection_complete": True, "assetbundle_name": "shared",
            "preload_table_count": 0, "preload_pointer_inventory": [],
            "container_record_inventory": [{"path": "same", "preload_index": 0, "preload_size": 0,
                                             "asset": {"file_id": 0, "path_id": 1}}],
            "main_asset": None, "dependencies": [],
        }
        overlay = dict(base)
        rewritten = {"ready": True, "rewritten": {
            "preload": [],
            "container": [{"file_id": 0, "path_id": 1}],
            "main_asset": None,
        }}
        plan = build_assetbundle_metadata_merge_plan(base, overlay, rewritten)
        self.assertFalse(plan["ready"])
        self.assertIn("Container path collisions", plan["reason"])

    def test_assetbundle_metadata_plan_remaps_preload_container_and_main_asset(self):
        metadata = {
            "inspection_complete": True,
            "preload_table_count": 1,
            "preload_pointer_inventory": [{"file_id": 0, "path_id": 10}],
            "container_record_inventory": [{
                "path": "Assets/Test", "preload_index": 0, "preload_size": 1,
                "asset": {"file_id": 2, "path_id": 20},
            }],
            "main_asset": {
                "preload_index": 0, "preload_size": 1,
                "asset": {"file_id": 0, "path_id": 10},
            },
        }
        plan = build_assetbundle_metadata_remap_plan(
            metadata, {"2": 3}, {"10": 11}, {},
            local_target_path_ids={11},
            external_target_path_ids={"3": {20}},
        )
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["rewritten"]["preload"], [{"file_id": 0, "path_id": 11}])
        self.assertEqual(plan["rewritten"]["container"], [{"file_id": 3, "path_id": 20}])
        self.assertEqual(plan["rewritten"]["main_asset"], {"file_id": 0, "path_id": 11})
        self.assertTrue(plan["preload_ranges_preserved"])

    def test_assetbundle_metadata_plan_rejects_incomplete_inventory(self):
        plan = build_assetbundle_metadata_remap_plan(
            {"inspection_complete": False}, {}, {}, {},
            local_target_path_ids=set(), external_target_path_ids={},
        )
        self.assertFalse(plan["ready"])

    def test_pptr_remap_plan_preserves_null_pointer(self):
        plan = build_pptr_remap_plan(
            [{"file_id": 0, "path_id": 0}],
            {},
            {},
            {},
            local_target_path_ids=set(),
            external_target_path_ids={},
        )
        self.assertTrue(plan["ready"])
        self.assertEqual(
            [(item["file_id"], item["path_id"], item["target"])
             for item in plan["rewritten_references"]],
            [(0, 0, "null")],
        )

    def test_pptr_remap_plan_resolves_local_and_external_targets(self):
        plan = build_pptr_remap_plan(
            [{"file_id": 0, "path_id": 10}, {"file_id": 2, "path_id": 20}],
            {"1": 1, "2": 3},
            {"10": 11},
            {"2": {"20": 21}},
            local_target_path_ids={11},
            external_target_path_ids={"3": {21}},
        )
        self.assertTrue(plan["ready"])
        self.assertEqual(
            [(item["file_id"], item["path_id"]) for item in plan["rewritten_references"]],
            [(0, 11), (3, 21)],
        )

    def test_pptr_remap_plan_blocks_missing_external_mapping_or_target(self):
        plan = build_pptr_remap_plan(
            [{"file_id": 4, "path_id": 99}],
            {"1": 1},
            {},
            {},
            local_target_path_ids=set(),
            external_target_path_ids={"1": {99}},
        )
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["rewritten_references"], [])
        self.assertIn("file_id", plan["blocked_references"][0]["reason"])

    def test_pptr_remap_plan_blocks_missing_local_path_id(self):
        plan = build_pptr_remap_plan(
            [{"file_id": 0, "path_id": 123}],
            {},
            {},
            {},
            local_target_path_ids={1, 2},
            external_target_path_ids={},
        )
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["rewritten_references"], [])

    def test_path_id_plan_blocks_type_conflicts_and_unknown_payloads(self):
        plan = build_path_id_remap_plan(
            [10, 20],
            [10, 20],
            {"10": "Texture2D", "20": "Mesh"},
            {"10": "AudioClip", "20": "Mesh"},
            {"10": "a", "20": "b"},
            {"10": "c"},
        )

        self.assertFalse(plan["ready"])
        self.assertEqual(plan["remap"], {})
        self.assertEqual(plan["deduplicate"], {})
        self.assertEqual(len(plan["blocked_path_ids"]), 2)

    def test_path_id_plan_rejects_incomplete_and_invalid_inventories(self):
        incomplete = build_path_id_remap_plan(
            [1], [1], {}, {}, {}, {}, inventories_complete=False
        )
        invalid = build_path_id_remap_plan(
            [1, True], [1], {}, {}, {}, {}
        )

        self.assertFalse(incomplete["ready"])
        self.assertFalse(invalid["ready"])

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

    def test_verifies_unityfs_save_and_reopen_roundtrip(self):
        report = {
            "reference_scan_complete": True,
            "container_inventory_complete": True,
            "assetbundle_metadata_complete": True,
            "object_reference_count": 0,
            "external_reference_count": 0,
            "container_entry_count": 0,
            "assetbundle_metadata_count": 0,
            "serialized_files": [{
                "name": "CAB-main",
                "unity_version": "2022.3.0f1",
                "target_platform": "iOS",
                "object_count": 1,
                "external_count": 0,
                "object_path_ids": [7],
                "object_id_inventory_complete": True,
                "object_types_by_path_id": {"7": "Texture2D"},
                "object_payload_hashes": {"7": "abc123"},
                "object_payload_hashes_complete": True,
            }],
            "assetbundle_metadata": [],
            "object_references": [],
            "external_references": [],
            "container_entries": [],
            "reference_integrity": {
                "local_resolved": 0,
                "local_missing": [],
                "external_resolved": 0,
                "external_dependencies": [],
                "external_ambiguous_targets": [],
                "invalid_external_indices": [],
            },
            "serialized_file_count": 1,
            "object_count": 1,
        }
        bundle = SimpleNamespace(
            signature="UnityFS",
            save=lambda packer=None: b"UnityFS\x00roundtrip",
        )
        env = SimpleNamespace(files={"bundle": bundle})
        fake_unity = SimpleNamespace(load=lambda data: env)
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            with patch("app.assetbundle_inspector.inspect_assetbundle", return_value=report) as inspect:
                result = verify_assetbundle_roundtrip(b"UnityFS\x00original")

        self.assertTrue(result["verified"])
        self.assertEqual(result["original_size"], len(b"UnityFS\x00original"))
        self.assertEqual(result["roundtrip_size"], len(b"UnityFS\x00roundtrip"))
        self.assertEqual(inspect.call_count, 2)

    def test_roundtrip_refuses_incomplete_object_payload_inventory(self):
        report = {
            "reference_scan_complete": True,
            "container_inventory_complete": True,
            "assetbundle_metadata_complete": True,
            "object_reference_count": 0,
            "external_reference_count": 0,
            "container_entry_count": 0,
            "assetbundle_metadata_count": 0,
            "serialized_files": [{
                "object_id_inventory_complete": True,
                "object_payload_hashes_complete": False,
            }],
        }
        with patch("app.assetbundle_inspector.inspect_assetbundle", return_value=report):
            result = verify_assetbundle_roundtrip(b"UnityFS\x00original")

        self.assertFalse(result["verified"])
        self.assertIn("inventory chưa hoàn chỉnh", result["reason"])

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
        self.assertEqual(report["object_id_overlaps"][0]["shared_path_id_count"], 2)
        self.assertEqual(report["object_id_overlaps"][0]["type_conflict_count"], 0)
        self.assertTrue(report["structural_checks_pass"])
        self.assertFalse(report["merge_safe"])

    def test_inspects_assetbundle_preload_and_container_pointer_metadata(self):
        def ptr(file_id, path_id):
            return SimpleNamespace(file_id=file_id, path_id=path_id)

        local_target = SimpleNamespace(
            type=SimpleNamespace(name="Texture2D"),
            path_id=10,
            read_typetree=lambda: {},
        )
        parsed = SimpleNamespace(
            m_PreloadTable=[ptr(0, 10)],
            m_ContainerEntries=[
                ("assets/a", SimpleNamespace(
                    preload_index=0, preload_size=1, asset=ptr(0, 10)
                )),
                ("assets/a", SimpleNamespace(
                    preload_index=3, preload_size=1, asset=ptr(4, 999)
                )),
            ],
            m_Container={},
            m_MainAsset=SimpleNamespace(
                preload_index=0, preload_size=1, asset=ptr(0, 10)
            ),
            m_AssetBundleName="hero-bundle",
            m_Dependencies=["shared-dependency"],
            m_IsStreamedSceneAssetBundle=False,
        )
        bundle_obj = SimpleNamespace(
            type=SimpleNamespace(name="AssetBundle"),
            path_id=50,
            read=lambda return_typetree_on_error=False: parsed,
            read_typetree=lambda: {},
        )
        serialized = SimpleNamespace(
            name="CAB-main",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={10: local_target, 50: bundle_obj},
            externals=[],
        )
        env = SimpleNamespace(
            assets=[serialized],
            container={},
            files={"bundle": object()},
        )
        fake_unity = SimpleNamespace(load=lambda data: env)
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = inspect_assetbundle(b"UnityFS\x00fixture")

        self.assertEqual(report["assetbundle_metadata_count"], 1)
        self.assertTrue(report["assetbundle_metadata_complete"])
        metadata = report["assetbundle_metadata"][0]
        self.assertEqual(metadata["assetbundle_name"], "hero-bundle")
        self.assertEqual(metadata["preload_table_count"], 1)
        self.assertEqual(metadata["container_entry_count"], 2)
        self.assertEqual(metadata["duplicate_container_path_count"], 1)
        self.assertEqual(len(metadata["invalid_preload_ranges"]), 1)
        self.assertEqual(len(metadata["invalid_pointer_indices"]), 1)
        self.assertEqual(metadata["missing_local_pointers"], [])
        self.assertEqual(metadata["dependencies"], ["shared-dependency"])

    def test_reports_changed_object_payload_for_shared_path_id(self):
        def make_env(payload):
            obj = SimpleNamespace(
                type=SimpleNamespace(name="Texture2D"),
                path_id=42,
                byte_size=len(payload),
                get_raw_data=lambda: payload,
            )
            serialized = SimpleNamespace(
                name="CAB-shared",
                unity_version="2022.3.0f1",
                target_platform=SimpleNamespace(name="iOS"),
                objects={42: obj},
                externals=[],
            )
            return SimpleNamespace(
                assets=[serialized],
                container={},
                files={"bundle": object()},
            )

        envs = iter([make_env(b"base-texture"), make_env(b"mod-texture")])
        fake_unity = SimpleNamespace(load=lambda data: next(envs))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = compare_assetbundle_overlays(
                b"UnityFS\x00base",
                b"UnityFS\x00overlay",
            )

        overlap = report["object_id_overlaps"][0]
        self.assertEqual(overlap["shared_path_id_count"], 1)
        self.assertEqual(overlap["payload_identical_count"], 0)
        self.assertEqual(overlap["payload_changed_count"], 1)
        self.assertEqual(overlap["payload_unknown_count"], 0)
        self.assertEqual(overlap["payload_changed_path_ids"], [42])
        self.assertFalse(report["merge_safe"])

    def test_marks_duplicate_cab_basenames_as_ambiguous(self):
        source_obj = SimpleNamespace(
            type=SimpleNamespace(name="GameObject"),
            path_id=100,
            read_typetree=lambda: {"external": {"m_FileID": 1, "m_PathID": 300}},
        )
        source_file = SimpleNamespace(
            name="CAB-main",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={100: source_obj},
            externals=[SimpleNamespace(path="archive:/CAB-shared.assets", name="CAB-shared.assets")],
        )
        target_a = SimpleNamespace(
            name="folder-a/CAB-shared.assets",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={300: SimpleNamespace(type=SimpleNamespace(name="Texture2D"), path_id=300)},
            externals=[],
        )
        target_b = SimpleNamespace(
            name="folder-b/CAB-shared.assets",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={300: SimpleNamespace(type=SimpleNamespace(name="Mesh"), path_id=300)},
            externals=[],
        )
        env = SimpleNamespace(
            assets=[source_file, target_a, target_b],
            container={},
            files={"bundle": object()},
        )
        fake_unity = SimpleNamespace(load=lambda data: env)
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = inspect_assetbundle(b"UnityFS\x00fixture")
        integrity = report["reference_integrity"]
        self.assertEqual(integrity["external_resolved"], 0)
        self.assertEqual(len(integrity["external_ambiguous_targets"]), 1)
        self.assertEqual(integrity["external_ambiguous_targets"][0]["path_id"], 300)

    def test_structural_checks_fail_for_invalid_assetbundle_metadata(self):
        def make_env():
            target = SimpleNamespace(
                type=SimpleNamespace(name="Texture2D"),
                path_id=10,
                byte_size=0,
                get_raw_data=lambda: b"",
                read_typetree=lambda: {},
            )
            pointer = SimpleNamespace(file_id=0, path_id=999)
            asset_info = SimpleNamespace(
                preload_index=5,
                preload_size=2,
                asset=pointer,
            )
            parsed = SimpleNamespace(
                m_PreloadTable=[],
                m_ContainerEntries=[("assets/hero", asset_info)],
                m_Container={"assets/hero": asset_info},
                m_MainAsset=None,
                m_AssetBundleName="hero-bundle",
                m_Dependencies=[],
                m_IsStreamedSceneAssetBundle=False,
            )
            bundle_obj = SimpleNamespace(
                type=SimpleNamespace(name="AssetBundle"),
                path_id=50,
                byte_size=0,
                get_raw_data=lambda: b"",
                read=lambda return_typetree_on_error=False: parsed,
                read_typetree=lambda: {},
            )
            serialized = SimpleNamespace(
                name="CAB-main",
                unity_version="2022.3.0f1",
                target_platform=SimpleNamespace(name="iOS"),
                objects={10: target, 50: bundle_obj},
                externals=[],
            )
            return SimpleNamespace(
                assets=[serialized],
                container={"assets/hero": target},
                files={"bundle": object()},
            )

        envs = iter([make_env(), make_env()])
        fake_unity = SimpleNamespace(load=lambda data: next(envs))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = compare_assetbundle_overlays(
                b"UnityFS\x00base",
                b"UnityFS\x00overlay",
            )

        self.assertFalse(report["assetbundle_metadata_integrity_pass"])
        self.assertFalse(report["structural_checks_pass"])
        self.assertFalse(report["merge_safe"])

    def test_reports_unmatched_assetbundle_name_without_calling_it_ambiguous(self):
        def make_env(include_bundle):
            target = SimpleNamespace(
                type=SimpleNamespace(name="Texture2D"),
                path_id=10,
                byte_size=0,
                get_raw_data=lambda: b"",
                read_typetree=lambda: {},
            )
            objects = {10: target}
            if include_bundle:
                parsed = SimpleNamespace(
                    m_PreloadTable=[],
                    m_ContainerEntries=[],
                    m_Container={},
                    m_MainAsset=None,
                    m_AssetBundleName="only-in-base",
                    m_Dependencies=[],
                    m_IsStreamedSceneAssetBundle=False,
                )
                objects[50] = SimpleNamespace(
                    type=SimpleNamespace(name="AssetBundle"),
                    path_id=50,
                    byte_size=0,
                    get_raw_data=lambda: b"",
                    read=lambda return_typetree_on_error=False: parsed,
                    read_typetree=lambda: {},
                )
            serialized = SimpleNamespace(
                name="CAB-main",
                unity_version="2022.3.0f1",
                target_platform=SimpleNamespace(name="iOS"),
                objects=objects,
                externals=[],
            )
            return SimpleNamespace(
                assets=[serialized],
                container={},
                files={"bundle": object()},
            )

        envs = iter([make_env(True), make_env(False)])
        fake_unity = SimpleNamespace(load=lambda data: next(envs))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = compare_assetbundle_overlays(
                b"UnityFS\x00base",
                b"UnityFS\x00overlay",
            )

        self.assertEqual(report["ambiguous_assetbundle_metadata_names"], [])
        self.assertEqual(report["unmatched_base_assetbundle_names"], ["only-in-base"])
        self.assertEqual(report["unmatched_overlay_assetbundle_names"], [])

    def test_compares_assetbundle_preload_and_container_metadata(self):
        def make_env(target_path_id):
            target = SimpleNamespace(
                type=SimpleNamespace(name="Texture2D"),
                path_id=target_path_id,
                byte_size=0,
                get_raw_data=lambda: b"",
                read_typetree=lambda: {},
            )
            def ptr(path_id):
                return SimpleNamespace(file_id=0, path_id=path_id)
            asset_info = SimpleNamespace(
                preload_index=0,
                preload_size=1,
                asset=ptr(target_path_id),
            )
            parsed = SimpleNamespace(
                m_PreloadTable=[ptr(target_path_id)],
                m_ContainerEntries=[("assets/hero", asset_info)],
                m_Container={"assets/hero": asset_info},
                m_MainAsset=asset_info,
                m_AssetBundleName="hero-bundle",
                m_Dependencies=["shared-dependency"],
                m_IsStreamedSceneAssetBundle=False,
            )
            bundle_obj = SimpleNamespace(
                type=SimpleNamespace(name="AssetBundle"),
                path_id=50,
                read=lambda return_typetree_on_error=False: parsed,
                read_typetree=lambda: {},
                byte_size=0,
                get_raw_data=lambda: b"",
            )
            serialized = SimpleNamespace(
                name="CAB-main",
                unity_version="2022.3.0f1",
                target_platform=SimpleNamespace(name="iOS"),
                objects={target_path_id: target, 50: bundle_obj},
                externals=[],
            )
            return SimpleNamespace(
                assets=[serialized],
                container={"assets/hero": target},
                files={"bundle": object()},
            )

        envs = iter([make_env(10), make_env(11)])
        fake_unity = SimpleNamespace(load=lambda data: next(envs))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = compare_assetbundle_overlays(
                b"UnityFS\x00base",
                b"UnityFS\x00overlay",
            )

        self.assertEqual(len(report["assetbundle_metadata_comparisons"]), 1)
        metadata = report["assetbundle_metadata_comparisons"][0]
        self.assertFalse(metadata["preload_table_match"])
        self.assertFalse(metadata["container_metadata_match"])
        self.assertTrue(metadata["dependencies_match"])

    def test_resolves_overlay_external_ptrs_against_base_cab_path_ids(self):
        base_obj = SimpleNamespace(type=SimpleNamespace(name="Texture2D"), path_id=300)
        base_file = SimpleNamespace(
            name="CAB-base.assets",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={300: base_obj},
            externals=[],
        )
        overlay_obj = SimpleNamespace(
            type=SimpleNamespace(name="GameObject"),
            path_id=100,
            read_typetree=lambda: {
                "valid": {"m_FileID": 1, "m_PathID": 300},
                "missing": {"m_FileID": 1, "m_PathID": 999},
            },
        )
        overlay_file = SimpleNamespace(
            name="CAB-overlay",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={100: overlay_obj},
            externals=[SimpleNamespace(
                path="archive:/CAB-base.assets",
                name="CAB-base.assets",
            )],
        )
        envs = iter([
            SimpleNamespace(assets=[base_file], container={}, files={"base": object()}),
            SimpleNamespace(assets=[overlay_file], container={}, files={"overlay": object()}),
        ])
        fake_unity = SimpleNamespace(load=lambda data: next(envs))
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = compare_assetbundle_overlays(
                b"UnityFS\x00base",
                b"UnityFS\x00overlay",
            )

        refs = report["cross_bundle_references"]
        self.assertEqual(refs["resolved_count"], 1)
        self.assertEqual(refs["missing_path_id_count"], 1)
        self.assertFalse(report["cross_bundle_reference_integrity_pass"])
        self.assertFalse(report["structural_checks_pass"])
        self.assertEqual(refs["resolved"][0]["path_id"], 300)
        self.assertEqual(refs["missing_path_ids"][0]["path_id"], 999)
        self.assertFalse(refs["index_incomplete"])
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
        self.assertEqual(report["object_id_overlaps"][0]["type_conflict_count"], 1)
        self.assertEqual(report["object_id_overlaps"][0]["type_conflict_path_ids"], [1])
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

    def test_scans_null_object_reference_without_marking_it_local(self):
        source_obj = SimpleNamespace(
            type=SimpleNamespace(name="GameObject"),
            path_id=100,
            read_typetree=lambda: {
                "null": {"m_FileID": 0, "m_PathID": 0},
            },
        )
        serialized = SimpleNamespace(
            name="CAB-main",
            unity_version="2022.3.0f1",
            target_platform=SimpleNamespace(name="iOS"),
            objects={100: source_obj},
            externals=[],
        )
        env = SimpleNamespace(assets=[serialized], container={}, files={"bundle": object()})
        fake_unity = SimpleNamespace(load=lambda data: env)
        with patch("app.assetbundle_inspector._load_unitypy_aov", return_value=fake_unity):
            report = inspect_assetbundle(b"UnityFS\x00fixture")
        self.assertEqual(report["object_reference_count"], 1)
        self.assertEqual(report["object_references"][0]["target_file"], "null")
        self.assertEqual(report["reference_integrity"]["local_resolved"], 0)
        self.assertEqual(report["reference_integrity"]["local_missing"], [])

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


    def test_metadata_merge_rejects_malformed_base_and_overlay_inventory(self):
        def metadata(prefix):
            return {
                "inspection_complete": True,
                "assetbundle_name": "shared",
                "preload_table_count": 1,
                "preload_pointer_inventory": [{"file_id": 0, "path_id": 10, "source": "preload"}],
                "container_record_inventory": [{
                    "path": f"assets/{prefix}",
                    "preload_index": 0,
                    "preload_size": 1,
                    "asset": {"file_id": 0, "path_id": 10},
                }],
                "main_asset": None,
                "dependencies": [],
            }

        base = metadata("base")
        overlay = metadata("overlay")
        rewritten = {
            "ready": True,
            "rewritten": {
                "preload": [{"file_id": 0, "path_id": 20}],
                "container": [{"file_id": 0, "path_id": 20}],
                "main_asset": None,
            },
        }

        malformed_base = metadata("base")
        malformed_base["preload_pointer_inventory"][0]["file_id"] = True
        result = build_assetbundle_metadata_merge_plan(malformed_base, overlay, rewritten)
        self.assertFalse(result["ready"])
        self.assertIn("Base preload", result["reason"])

        malformed_overlay = metadata("overlay")
        malformed_overlay["container_record_inventory"][0]["path"] = ["not", "a", "path"]
        result = build_assetbundle_metadata_merge_plan(base, malformed_overlay, rewritten)
        self.assertFalse(result["ready"])
        self.assertIn("Overlay Container", result["reason"])

        result = build_assetbundle_metadata_merge_plan(base, overlay, rewritten)
        self.assertTrue(result["ready"])
        self.assertEqual(result["preload_table"], [
            {"file_id": 0, "path_id": 10},
            {"file_id": 0, "path_id": 20},
        ])


if __name__ == "__main__":
    unittest.main()
