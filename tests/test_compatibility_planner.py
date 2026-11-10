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

    def test_empty_plan_rejected(self):
        with self.assertRaises(CompatibilityPlanError):
            plan_resource_compatibility([])


if __name__ == "__main__":
    unittest.main()
