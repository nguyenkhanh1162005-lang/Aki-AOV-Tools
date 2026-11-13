import tempfile
import unittest
from pathlib import Path

from app.aov_merger import (
    AOVMergeError,
    P9_ACTOR_IDS,
    P10_ACTOR_IDS,
    merge_p9_p10_archives,
    p9_p10_actor_id_from_packer_path,
)


class P9P10SourceLockedAdapterTests(unittest.TestCase):
    def test_actor_id_parser_matches_real_packer_skin_bundle_names(self):
        cases = {
            "assetbundle/battle/skin/1072_zhaoyun_battle.assetbundle": "107",
            "assetbundle/battle/skin/1678_sunwukong_aw1_fx0_battle_low.assetbundle": "167",
            "assetbundle/battle/skin/5091_skin_variant_battle.assetbundle": "509",
            "AssetBundle\\Battle\\Skin\\1503_skin_fx_battle_low.assetbundle": "150",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(p9_p10_actor_id_from_packer_path(path), expected)

    def test_actor_id_parser_rejects_paths_outside_the_exact_skin_bundle_family(self):
        for path in (
            "assetbundle/battle/hero/107_zhaoyun_battle.assetbundle",
            "assetbundle/battle/skin/5930_tamyn_battle.assetbundle",
            "assetbundle/battle/skin/1072_zhaoyun_show.assetbundle",
            "assetbundle/show/hero/1072_zhaoyun_show_base.assetbundle",
        ):
            with self.subTest(path=path):
                self.assertIsNone(p9_p10_actor_id_from_packer_path(path))

    def test_actor_inventories_are_disjoint_and_cover_nineteen_skins(self):
        self.assertEqual(len(P9_ACTOR_IDS), 9)
        self.assertEqual(len(P10_ACTOR_IDS), 10)
        self.assertFalse(P9_ACTOR_IDS & P10_ACTOR_IDS)
        self.assertEqual(len(P9_ACTOR_IDS | P10_ACTOR_IDS), 19)

    def test_exporter_rejects_any_archive_outside_the_pinned_source_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = folder / "P9.zip"
            second = folder / "P10.zip"
            output = folder / "must-not-exist.zip"
            first.write_bytes(b"not the pinned P9 source")
            second.write_bytes(b"not the pinned P10 source")

            with self.assertRaisesRegex(AOVMergeError, "SHA-256"):
                merge_p9_p10_archives([str(first), str(second)], str(output))

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
