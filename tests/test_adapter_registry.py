import unittest

from app.adapter_registry import get_adapter_capabilities


class AdapterRegistryTests(unittest.TestCase):
    def test_registry_fails_closed_and_does_not_claim_universal_merging(self):
        result = get_adapter_capabilities()
        self.assertEqual(result["policy"], "fail_closed")
        self.assertFalse(result["merge_any_mod_claim"])
        self.assertFalse(result["game_tested"])

    def test_only_two_version_locked_aov_merge_routes_are_enabled(self):
        result = get_adapter_capabilities()
        adapters = {item["id"]: item for item in result["adapters"]}
        self.assertEqual(adapters["generic_zip"]["status"], "merge_supported")
        direct = adapters["aov_kpm100_vtk_actor595_1.64.1"]
        self.assertEqual(direct["status"], "merge_supported_version_locked")
        self.assertIn("70 KPM_MOD payload", direct["merge_policy"])
        wrapper = adapters["aov_mod_to_mod_vtk_actor595_1.64.1"]
        self.assertEqual(wrapper["status"], "merge_supported_version_locked")
        self.assertIn("Resources.zip", wrapper["scope"])
        self.assertIn("10 AssetBundle", wrapper["scope"])
        self.assertEqual(result["summary"]["version_locked"], 2)
        self.assertEqual(result["summary"]["blocked"], 0)
        self.assertEqual(adapters["unity_assetbundle"]["status"], "inspect_only")
        self.assertEqual(adapters["wwise_bnk"]["status"], "inspect_only")
        self.assertEqual(adapters["aov_wrapper_inspection"]["status"], "inspect_only")

    def test_registry_returns_detached_data(self):
        first = get_adapter_capabilities()
        first["adapters"][0]["status"] = "unsafe"
        second = get_adapter_capabilities()
        self.assertEqual(second["adapters"][0]["status"], "merge_supported")


if __name__ == "__main__":
    unittest.main()
