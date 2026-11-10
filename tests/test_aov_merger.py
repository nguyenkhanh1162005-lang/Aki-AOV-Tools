import unittest

from app.aov_merger import _normalise_vtk_path


class AOVMergerPathTests(unittest.TestCase):
    def test_strips_versioned_resources_prefix(self):
        self.assertEqual(
            _normalise_vtk_path("Resources/1.64.1/Thanos/HeroSkin_2.bytes"),
            "Thanos/HeroSkin_2.bytes",
        )

    def test_strips_prefix_before_assetbundle_detection(self):
        self.assertEqual(
            _normalise_vtk_path(
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle"
            ),
            "assetbundle/battle/hero/595_lixin_battle_base.assetbundle",
        )

    def test_normalizes_assetbundle_without_version_prefix(self):
        self.assertEqual(
            _normalise_vtk_path("battle/hero/595_lixin_battle_base.assetbundle"),
            "assetbundle/battle/hero/595_lixin_battle_base.assetbundle",
        )


if __name__ == "__main__":
    unittest.main()
