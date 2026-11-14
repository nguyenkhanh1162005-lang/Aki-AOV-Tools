import unittest

from app.actor_detector import (
    detect_actor_ids,
    extract_skin_ids_from_text,
    extract_skin_name_hints,
)


class ActorDetectorTests(unittest.TestCase):
    def test_detects_actor_ids_from_multiple_aov_resource_families(self):
        evidence = detect_actor_ids([
            "Resources/1.64.1/Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes",
            "Resources/1.64.1/AssetRefs/Hero/731_AssetRef.bytes",
            "Resources/1.64.1/assetbundle/battle/hero/731_custom_battle_base.assetbundle",
            "Resources/1.64.1/Databin/Client/Actor/actor_842/config.bytes",
        ])
        self.assertEqual(evidence["actor_actions"], ["731"])
        self.assertEqual(evidence["assetrefs"], ["731"])
        self.assertEqual(evidence["assetbundles"], ["731"])
        self.assertEqual(evidence["actor_directories"], ["842"])

    def test_ignores_unqualified_three_digit_numbers(self):
        evidence = detect_actor_ids([
            "Resources/1.64.1/Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes",
            "images/731_icon.png",
            "assetbundle/ui/primary/allshared.assetbundle",
        ])
        self.assertEqual(evidence, {})

    def test_extracts_skin_ids_only_for_detected_actor_prefixes(self):
        skin_ids = extract_skin_ids_from_text(
            "Actor 731 skins: 73100, 73108, 84201, 1234567",
            {"731"},
        )
        self.assertEqual(skin_ids, ["73100", "73108"])

    def test_extracts_creator_skin_names_without_duplicates(self):
        names = extract_skin_name_hints(
            "Nakroth Bạch Diện chiến thương\n"
            "Tamyn Robot\n"
            "Tamyn Robot\n"
            "59300\n"
        )
        self.assertEqual(names, ["Nakroth Bạch Diện chiến thương", "Tamyn Robot"])


if __name__ == "__main__":
    unittest.main()
