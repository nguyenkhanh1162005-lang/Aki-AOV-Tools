import unittest

from app.compatibility_planner import CompatibilityPlanError, plan_resource_compatibility


def resource(path, digest, **extra):
    return {"path": path, "sha256": digest * 64, "format": "generic_zip", **extra}


class CompatibilityPlannerTests(unittest.TestCase):
    def test_unique_generic_path_is_safe_but_plan_never_authorizes_execution(self):
        result = plan_resource_compatibility([resource("docs/readme.txt", "a")])
        self.assertEqual(result["resources"][0]["status"], "SAFE_MERGE")
        self.assertTrue(result["advisory_only"])
        self.assertFalse(result["merge_authorized"])

    def test_runtime_like_generic_file_is_not_called_safe(self):
        result = plan_resource_compatibility([
            resource("skins/hero.png", "b", detected_format="PNG image")
        ])
        self.assertEqual(result["resources"][0]["status"], "NEEDS_RULE")
        self.assertIn("runtime", result["resources"][0]["reasons"][0])

    def test_identical_duplicate_is_classified(self):
        result = plan_resource_compatibility([
            resource("same.txt", "b", source="one.zip"),
            resource("same.txt", "b", source="two.zip"),
        ])
        self.assertEqual(result["resources"][0]["status"], "IDENTICAL_DUPLICATE")

    def test_different_duplicate_payload_requires_rule(self):
        result = plan_resource_compatibility([
            resource("same.txt", "c", source="one.zip"),
            resource("same.txt", "d", source="two.zip"),
        ])
        self.assertEqual(result["resources"][0]["status"], "NEEDS_RULE")

    def test_version_and_platform_conflicts_fail_closed(self):
        version = plan_resource_compatibility([
            resource("asset.bin", "e", game_version="1.0"),
            resource("asset.bin", "e", game_version="2.0"),
        ])
        self.assertEqual(version["resources"][0]["status"], "VERSION_CONFLICT")
        platform = plan_resource_compatibility([
            resource("asset.bin", "e", platform="ios"),
            resource("asset.bin", "e", platform="android"),
        ])
        self.assertEqual(platform["resources"][0]["status"], "VERSION_CONFLICT")

    def test_macos_metadata_is_ignored_not_treated_as_game_resource(self):
        result = plan_resource_compatibility([
            resource("__MACOSX/Resources/._BattleBank.bytes", "f",
                     format="metadata_noise", is_metadata_noise=True,
                     is_aov_resource=False)
        ])
        self.assertEqual(result["resources"][0]["status"], "IGNORED_METADATA")
        self.assertEqual(result["summary"]["IGNORED_METADATA"], 1)
        self.assertFalse(result["merge_authorized"])

    def test_aov_resource_is_never_marked_safe_from_extension_or_format_guess(self):
        result = plan_resource_compatibility([
            resource("Resources/BattleBank.bytes", "f", format="mses", is_aov_resource=True)
        ])
        self.assertEqual(result["resources"][0]["status"], "UNSUPPORTED")

    def test_missing_dependency_requires_rule(self):
        result = plan_resource_compatibility([
            resource("bundle.assetbundle", "1", format="unity_assetbundle",
                     is_aov_resource=True, dependencies=["missing.bundle"])
        ])
        self.assertEqual(result["resources"][0]["status"], "NEEDS_RULE")
        self.assertEqual(result["resources"][0]["missing_dependencies"], ["missing.bundle"])

    def test_rejects_traversal_and_bad_hash(self):
        with self.assertRaises(CompatibilityPlanError):
            plan_resource_compatibility([resource("../escape.txt", "a")])
        with self.assertRaises(CompatibilityPlanError):
            plan_resource_compatibility([{"path": "x", "sha256": "bad", "format": "generic_zip"}])

    def test_unity_dependency_inventory_is_preserved_in_report(self):
        result = plan_resource_compatibility([
            resource("hero.assetbundle", "a", format="unity_assetbundle",
                     is_aov_resource=True,
                     unity_dependencies=["shared/materials"],
                     unity_external_paths=["CAB-hero"],
                     dependency_inventory_complete=True)
        ])
        item = result["resources"][0]
        self.assertEqual(item["unity_dependencies"], ["shared/materials"])
        self.assertEqual(item["unity_external_paths"], ["CAB-hero"])
        self.assertTrue(item["dependency_inventory_complete"])
        self.assertEqual(item["status"], "UNSUPPORTED")

    def test_rejects_ambiguous_path_segments(self):
        for path in ("folder//asset.bytes", "folder/./asset.bytes", "folder/"):
            with self.subTest(path=path):
                with self.assertRaises(CompatibilityPlanError):
                    plan_resource_compatibility([resource(path, "a")])

    def test_rejects_invalid_optional_metadata_types(self):
        invalid_metadata = (
            {"game_version": 1641},
            {"platform": ["ios"]},
            {"actor_id": True},
            {"skin_id": -1},
            {"is_aov_resource": "false"},
            {"is_metadata_noise": 1},
        )
        for metadata in invalid_metadata:
            with self.subTest(metadata=metadata):
                with self.assertRaises(CompatibilityPlanError):
                    plan_resource_compatibility([resource("asset.bytes", "a", **metadata)])

    def test_detects_cross_archive_version_conflicts_on_different_paths(self):
        result = plan_resource_compatibility([
            resource("hero-a.bytes", "a", source="pack-1.64.1.12-ios.zip",
                     game_version="1.64.1.12", platform="ios", is_aov_resource=True),
            resource("hero-b.bytes", "b", source="pack-1.65.1.1-ios.zip",
                     game_version="1.65.1.1", platform="ios", is_aov_resource=True),
        ])
        compatibility = result["source_compatibility"]
        self.assertEqual(compatibility["status"], "VERSION_CONFLICT")
        self.assertEqual(
            compatibility["game_versions"], ["1.64.1.12", "1.65.1.1"]
        )
        self.assertEqual(len(compatibility["reasons"]), 1)

    def test_detects_cross_archive_platform_conflicts_on_different_paths(self):
        result = plan_resource_compatibility([
            resource("hero-ios.bytes", "a", source="pack-ios.zip",
                     platform="ios", is_aov_resource=True),
            resource("hero-android.bytes", "b", source="pack-android.zip",
                     platform="android", is_aov_resource=True),
        ])
        compatibility = result["source_compatibility"]
        self.assertEqual(compatibility["status"], "VERSION_CONFLICT")
        self.assertEqual(compatibility["platforms"], ["android", "ios"])

    def test_empty_plan_rejected(self):
        with self.assertRaises(CompatibilityPlanError):
            plan_resource_compatibility([])


if __name__ == "__main__":
    unittest.main()
