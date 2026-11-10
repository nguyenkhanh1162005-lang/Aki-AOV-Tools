import unittest

from app.adapter_registry import get_adapter_capabilities


class AdapterRegistryTests(unittest.TestCase):
    def test_registry_fails_closed_and_does_not_claim_universal_merging(self):
        result = get_adapter_capabilities()
        self.assertEqual(result["policy"], "fail_closed")
        self.assertFalse(result["merge_any_mod_claim"])
        self.assertFalse(result["game_tested"])

    def test_only_generic_zip_and_locked_vtk_are_merge_enabled(self):
        adapters = {item["id"]: item for item in get_adapter_capabilities()["adapters"]}
        self.assertEqual(adapters["generic_zip"]["status"], "merge_supported")
        self.assertEqual(
            adapters["aov_mod_to_mod_vtk_actor595_1.64.1"]["status"],
            "merge_supported_version_locked",
        )
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
