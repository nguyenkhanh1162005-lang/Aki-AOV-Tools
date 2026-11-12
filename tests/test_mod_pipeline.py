import hashlib
import io
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from app.aov_bytes import encode_aov_resource
from app.mod_pipeline import ModPipelineError, _build_preservation_gates, _archive_profile, _preflight_inputs, _resource_merge_guidance, _resource_priority_hint, _select_adapter, analyze_aov_input_set, merge_mod_archives


class ModPipelineTests(unittest.TestCase):
    def _zip(self, folder: Path, name: str, entries: dict[str, bytes]) -> str:
        path = folder / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, payload in entries.items():
                archive.writestr(entry, payload)
        return str(path)

    def _nested_zip(self, entries: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for entry, payload in entries.items():
                archive.writestr(entry, payload)
        return buffer.getvalue()

    def test_rejects_more_than_ten_inputs_before_opening_files(self):
        with self.assertRaisesRegex(ModPipelineError, "tối đa 10"):
            _preflight_inputs([f"missing-{index}.zip" for index in range(11)])

    def test_rejects_total_input_over_500_mib(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = folder / "large-a.zip"
            second = folder / "large-b.zip"
            with first.open("wb") as stream:
                stream.truncate(250 * 1024 * 1024)
            with second.open("wb") as stream:
                stream.truncate(250 * 1024 * 1024 + 1)
            with self.assertRaisesRegex(ModPipelineError, "500 MB"):
                _preflight_inputs([str(first), str(second)])

    def test_aggregate_entry_limit_counts_directory_entries(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "first.zip", {
                "a/": b"", "b/": b"", "one.txt": b"1",
            })
            second = self._zip(folder, "second.zip", {
                "c/": b"", "d/": b"", "two.txt": b"2",
            })
            with patch("app.mod_pipeline.MAX_PROFILE_ENTRIES", 3):
                with self.assertRaisesRegex(ModPipelineError, "Tổng số mục"):
                    merge_mod_archives(
                        [first, second], str(folder / "merged.zip")
                    )

    def test_traversal_directory_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "bad-directory.zip", {
                "../escape/": b"",
            })
            with self.assertRaisesRegex(ModPipelineError, "không an toàn"):
                _archive_profile(source)

    def test_rejects_ambiguous_and_duplicate_archive_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for unsafe_name in ("folder/./asset.bytes", "folder//asset.bytes"):
                with self.subTest(path=unsafe_name):
                    source = self._zip(folder, "ambiguous.zip", {
                        unsafe_name: b"payload",
                    })
                    with self.assertRaisesRegex(ModPipelineError, "không an toàn"):
                        _archive_profile(source)
                    Path(source).unlink()

            duplicate = folder / "duplicate.zip"
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("same.bytes", b"first")
                archive.writestr("same.bytes", b"second")
            with self.assertRaisesRegex(ModPipelineError, "trùng lặp"):
                _archive_profile(str(duplicate))

    def test_generic_archives_merge_new_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "one.zip", {"readme.txt": b"one"})
            second = self._zip(folder, "two.zip", {"extra.txt": b"two"})
            output = folder / "merged.zip"
            result = merge_mod_archives([first, second], str(output))
            self.assertEqual(result["adapter"], "generic_zip")
            self.assertTrue(result["structurally_validated"])
            self.assertFalse(result["official_game_package_injected"])
            self.assertEqual(
                result["output_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )
            self.assertEqual(len(result["input_sha256"]), 2)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {"readme.txt", "extra.txt"})
                self.assertIsNone(archive.testzip())

    def test_mod_container_is_classified_as_user_mod_not_game_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "10-skin-mod.zip", {
                "Resources.zip": b"nested mod resources",
                "DanhSachSkin.txt": b"skin mod list",
            })
            profile = _archive_profile(source)
            self.assertEqual(profile["source_role"], "aov_skin_mod_container_candidate")
            self.assertTrue(profile["has_mod_skin_container"])

    def test_actor595_overlay_evidence_can_span_multiple_zip_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mod_pack = self._zip(folder, "10-skin-mod.zip", {
                "Resources.zip": b"nested mod resources",
                "DanhSachSkin.txt": b"skin mod list",
            })
            ifix_part = self._zip(folder, "vtk-ifix-part.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"VTK IFix payload",
            })
            asset_part = self._zip(folder, "vtk-assets-part.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"Actor 595 bundle",
            })
            profiles = [
                _archive_profile(path)
                for path in (mod_pack, ifix_part, asset_part)
            ]
            self.assertEqual(
                _select_adapter(profiles),
                "aov_mod_to_mod_vtk_actor595_1.64.1",
            )

    def test_actor595_overlay_evidence_can_span_all_three_vtk_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mod_pack = self._zip(folder, "10-skin-mod.zip", {
                "Resources.zip": b"nested mod resources",
                "DanhSachSkin.txt": b"skin mod list",
            })
            part1 = self._zip(folder, "Part1.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"VTK IFix part 1",
            })
            part2 = self._zip(folder, "Part2.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"Actor 595 main",
            })
            part3 = self._zip(folder, "Part3.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base_low.assetbundle": b"Actor 595 low",
            })
            profiles = [
                _archive_profile(path)
                for path in (mod_pack, part1, part2, part3)
            ]
            self.assertEqual(
                [item["source_part_number"] for item in profiles[1:]],
                [1, 2, 3],
            )
            self.assertEqual(
                _select_adapter(profiles),
                "aov_mod_to_mod_vtk_actor595_1.64.1",
            )

    def test_vtk_adapter_rejects_mixed_p9_p10_or_tm_overlays(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            wrapper = self._zip(folder, "10-skin-mod.zip", {
                "Resources.zip": b"nested resources",
                "DanhSachSkin.txt": b"skin list",
            })
            vtk = self._zip(folder, "vtk-part.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"VTK IFix",
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"Actor 595",
            })
            for name, marker in (
                ("P9-9-skin.zip", "9 skin"),
                ("P10-10-skin.zip", "10 skin"),
                ("TM-Tamyn-Robot.zip", "TM Robot"),
            ):
                with self.subTest(source=name):
                    other = self._zip(folder, name, {
                        "Resources/1.64.1/Databin/Client/Actor/heroSkin.bytes": marker.encode(),
                    })
                    profiles = [_archive_profile(path) for path in (wrapper, vtk, other)]
                    with self.assertRaisesRegex(ModPipelineError, "chưa có adapter"):
                        _select_adapter(profiles)

    def test_vtk_adapter_rejects_actor593_payload_mixed_with_actor595(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            wrapper = self._zip(folder, "10-skin-mod.zip", {
                "Resources.zip": b"nested resources",
                "DanhSachSkin.txt": b"skin list",
            })
            vtk = self._zip(folder, "vtk-part.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"VTK IFix",
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"Actor 595",
            })
            tm = self._zip(folder, "TM-Tamyn-Robot.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/593_machao_battle_base.assetbundle": b"Actor 593",
            })
            profiles = [_archive_profile(path) for path in (wrapper, vtk, tm)]
            with self.assertRaisesRegex(ModPipelineError, "chưa có adapter"):
                _select_adapter(profiles)

    def test_kpm100_direct_base_and_pinned_vtk_select_dedicated_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            base_entries = {
                "Resources/1.64.1/Thanos/HeroSkin_1.bytes": b"base-ifix",
                # The real KPM100 base also has a HeroSkin_2 and Actor595 references.
                # It must never be selected as the VTK overlay.
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"base-legacy-ifix",
                "Resources/1.64.1/assetbundle/resourcepackerinfosetall.assetbundle": b"packer-index",
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"base-actor-bundle",
            }
            base_entries.update({
                f"Resources/1.64.1/Mod_by_Youtube_KHAM-PHA-MOBILE/KPM_MOD_{index:016X}": b"payload"
                for index in range(70)
            })
            base = self._zip(folder, "kpm100.zip", base_entries)
            vtk = self._zip(folder, "vtk.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"vtk-ifix",
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"vtk-actor595",
            })
            profiles = [_archive_profile(base), _archive_profile(vtk)]
            self.assertEqual(profiles[0]["source_role"], "aov_kpm100_direct_base_candidate")
            self.assertEqual(profiles[0]["kpm_mod_payload_count"], 70)
            self.assertTrue(profiles[0]["has_vtk_ifix"])
            self.assertTrue(profiles[0]["has_595_assets"])
            self.assertEqual(profiles[1]["source_role"], "aov_actor595_mod_candidate")
            self.assertEqual(
                _select_adapter(profiles),
                "aov_kpm100_vtk_actor595_1.64.1",
            )


    def test_unpinned_kpm100_pair_fails_by_hash_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            base_entries = {
                "Resources/1.64.1/Thanos/HeroSkin_1.bytes": b"base-ifix",
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"legacy-base-ifix",
                "Resources/1.64.1/assetbundle/resourcepackerinfosetall.assetbundle": b"packer-index",
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"legacy-base-actor595",
            }
            base_entries.update({
                f"Resources/1.64.1/Mod_by_Youtube_KHAM-PHA-MOBILE/KPM_MOD_{index:016X}": b"payload"
                for index in range(70)
            })
            base = self._zip(folder, "kpm100.zip", base_entries)
            vtk = self._zip(folder, "vtk.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"vtk-ifix",
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"actor595",
            })
            output = folder / "candidate.zip"
            with self.assertRaisesRegex(ModPipelineError, "SHA-256 KPM100 gốc không khớp"):
                merge_mod_archives([base, vtk], str(output))
            self.assertFalse(output.exists())


    def test_10skin_wrapper_selects_typed_vtk_merger_and_publishes_valid_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mod_pack = self._zip(folder, "10-skin-mod.zip", {
                "Resources.zip": b"nested mod resources",
                "DanhSachSkin.txt": b"skin mod list",
            })
            ifix_part = self._zip(folder, "vtk-ifix-part.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"VTK IFix payload",
            })
            asset_part = self._zip(folder, "vtk-assets-part.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"Actor 595 bundle",
            })
            output = folder / "merged.zip"

            def fake_typed_merge(paths, output_path):
                self.assertEqual(paths, [mod_pack, ifix_part, asset_part])
                with zipfile.ZipFile(output_path, "w") as archive:
                    archive.writestr("validated-candidate.txt", b"structural fixture")
                return {
                    "base_version": "1.64.1.12",
                    "vtk_version": "1.64.1.11",
                    "assetbundles": 10,
                    "new_files": 24,
                    "validation": "fixture validation passed",
                    "game_tested": False,
                }

            with patch("app.mod_pipeline.merge_aov_archives", side_effect=fake_typed_merge) as typed_merge:
                result = merge_mod_archives(
                    [mod_pack, ifix_part, asset_part], str(output)
                )

            typed_merge.assert_called_once()
            self.assertEqual(
                result["adapter"], "aov_mod_to_mod_vtk_actor595_1.64.1"
            )
            self.assertTrue(result["structurally_validated"])
            self.assertFalse(result["game_compatibility_tested"])
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(
                    archive.namelist(), ["validated-candidate.txt"]
                )

    def test_source_family_hints_and_actor_action_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p9 = self._zip(folder, "IOS-Pack 9 Skin Cam xa 30%.zip", {
                "Resources.zip": b"nested resources",
                "DanhSachSkin.txt": b"nine skins",
            })
            p10 = self._zip(folder, "IOS-Pack 10 Skin Cam xa 30%.zip", {
                "Resources.zip": b"nested resources",
                "DanhSachSkin.txt": b"ten skins",
            })
            resources_zip = self._nested_zip({
                "Ages/Prefab_Characters/Prefab_Hero/Actor_593_Actions.pkg.bytes": b"actor actions",
                "assetbundle/battle/hero/593_machao_battle_base.assetbundle": b"actor bundle",
            })
            tamyn = self._zip(folder, "TM Tamyn Robot.zip", {
                "Resources.zip": resources_zip,
                "DanhSachSkin.txt": b"Tamyn Robot",
            })

            p9_profile = _archive_profile(p9)
            p10_profile = _archive_profile(p10)
            tm_profile = _archive_profile(tamyn)

            self.assertEqual(p9_profile["source_family_hint"], "P9")
            self.assertEqual(p9_profile["source_family_basis"], "filename_hint")
            self.assertEqual(p10_profile["source_family_hint"], "P10")
            self.assertEqual(tm_profile["source_family_hint"], "TM")
            self.assertEqual(tm_profile["source_family_basis"], "resource_signature")
            self.assertEqual(tm_profile["actor_action_ids"], ["593"])
            self.assertEqual(tm_profile["actor_asset_ids"], ["593"])
            self.assertEqual(tm_profile["nested_resource_entries"], 2)
            self.assertIsNone(tm_profile["nested_resource_scan_warning"])
            self.assertEqual(tm_profile["source_role"], "aov_skin_mod_container_candidate")

    def test_requested_six_archive_set_groups_three_vtk_parts_but_never_authorizes_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p9 = self._zip(folder, "IOS-Pack 9 Skin Cam xa 30%.zip", {
                "Resources.zip": b"p9 resources",
                "DanhSachSkin.txt": b"p9 skins",
            })
            p10 = self._zip(folder, "IOS-Pack 10 Skin Cam xa 30%.zip", {
                "Resources.zip": b"p10 resources",
                "DanhSachSkin.txt": b"p10 skins",
            })
            tm = self._zip(folder, "TM Tamyn Robot.zip", {
                "Resources/1.64.1/Ages/Prefab_Characters/Prefab_Hero/Actor_593_Actions.pkg.bytes": b"actor 593 actions",
                "Resources/1.64.1/assetbundle/battle/hero/593_machao_battle_base.assetbundle": b"actor 593 bundle",
            })
            vtk1 = self._zip(folder, "Part1.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"vtk ifix part",
            })
            vtk2 = self._zip(folder, "Part2.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"actor 595 main",
            })
            vtk3 = self._zip(folder, "Part3.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base_low.assetbundle": b"actor 595 low",
            })

            report = analyze_aov_input_set([p9, p10, tm, vtk1, vtk2, vtk3])
            target_set = report["target_set"]
            counts = {item["id"]: item["count"] for item in target_set["sources"]}

            self.assertTrue(target_set["input_set_complete"])
            self.assertEqual(target_set["detected_archive_count"], 6)
            self.assertEqual(counts, {
                "P9": 1,
                "P10": 1,
                "TM": 1,
                "VTK_PART_1": 1,
                "VTK_PART_2": 1,
                "VTK_PART_3": 1,
            })
            self.assertTrue(target_set["vtk_parts_grouped_by_actor595_signature"])
            self.assertFalse(target_set["merge_supported"])
            self.assertIn("593", report["actor_asset_ids"])
            self.assertIn("595", report["actor_asset_ids"])
            self.assertFalse(report["ready_to_merge"])

    def test_multi_mod_preflight_reports_multiple_wrappers_and_actor_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mod9 = self._zip(folder, "9-skin.zip", {
                "Resources.zip": b"9-skin nested resources",
                "DanhSachSkin.txt": b"9 skin list",
            })
            mod10 = self._zip(folder, "10-skin.zip", {
                "Resources.zip": b"10-skin nested resources",
                "DanhSachSkin.txt": b"10 skin list",
            })
            tamyn = self._zip(folder, "tamyn.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/593_tamyn_battle.assetbundle": b"Actor 593",
            })
            vtk_ifix = self._zip(folder, "vtk-part1.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"VTK IFix",
            })
            vtk_asset = self._zip(folder, "vtk-part2.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"Actor 595",
            })

            report = analyze_aov_input_set([mod9, mod10, tamyn, vtk_ifix, vtk_asset])

            self.assertFalse(report["ready_to_merge"])
            self.assertIsNone(report["selected_adapter"])
            self.assertEqual(report["role_counts"]["aov_skin_mod_container_candidate"], 2)
            self.assertEqual(report["actor_asset_ids"], ["593", "595"])
            self.assertTrue(any("nhiều MOD wrapper" in item for item in report["blockers"]))
            self.assertFalse(report["game_tested"])

    def test_multi_source_preflight_accepts_existing_single_wrapper_plus_split_vtk(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            wrapper = self._zip(folder, "10-skin.zip", {
                "Resources.zip": b"nested resources",
                "DanhSachSkin.txt": b"skin list",
            })
            vtk_ifix = self._zip(folder, "vtk-part1.zip", {
                "Resources/1.64.1/Thanos/HeroSkin_2.bytes": b"VTK IFix",
            })
            vtk_asset = self._zip(folder, "vtk-part2.zip", {
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle": b"Actor 595",
            })

            report = analyze_aov_input_set([wrapper, vtk_ifix, vtk_asset])

            self.assertTrue(report["ready_to_merge"])
            self.assertEqual(report["selected_adapter"], "aov_mod_to_mod_vtk_actor595_1.64.1")
            self.assertEqual(report["role_counts"]["aov_skin_mod_container_candidate"], 1)
            self.assertEqual(report["actor_asset_ids"], ["595"])
            self.assertEqual(report["blockers"], [])

    def test_resource_conflict_guidance_matches_existing_adapter_scope(self):
        common = _resource_merge_guidance("Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes")
        self.assertEqual(common["adapter_state"], "partial_parser")
        self.assertIn("CheckSkinIdTick", common["next_action"])

        ifix = _resource_merge_guidance("Thanos/HeroSkin_2.bytes")
        self.assertEqual(ifix["adapter_state"], "version_locked_parser")
        self.assertIn("Actor 595", ifix["next_action"])

        mses = _resource_merge_guidance("Databin/Client/Sound/BattleBank.bytes")
        self.assertEqual(mses["adapter_state"], "version_locked_schema")
        self.assertIn("allowlist", mses["next_action"])

        billboard = _resource_merge_guidance(
            "Databin/Client/Huanhua/ResKillBillboardCfg.bytes"
        )
        self.assertEqual(billboard["adapter_state"], "version_locked_schema")
        self.assertIn("0x253", billboard["next_action"])
        self.assertIn("ZIP nền", billboard["next_action"])

        nested = _resource_merge_guidance("KernelLua.pkg.bytes")
        self.assertEqual(nested["adapter_state"], "nested_package_union_only")
        self.assertIn("SHA-256", nested["next_action"])

        metadata = _resource_merge_guidance(
            "assetbundle/resourceverificationinfosetall.assetbundle"
        )
        self.assertEqual(metadata["adapter_state"], "version_locked_metadata")

        bundle = _resource_merge_guidance("assetbundle/battle/hero/593_machao_battle_base.assetbundle")
        self.assertEqual(bundle["adapter_state"], "inspect_only")

        sound = _resource_merge_guidance("Sound/iOS/ZMOD_MaChao_SFX.bnk")
        self.assertEqual(sound["adapter_state"], "inspect_only")
        self.assertIn("không nối byte", sound["safety_rule"].casefold())

        actor_actions = _resource_merge_guidance(
            "Ages/Prefab_Characters/Prefab_Hero/Actor_593_Actions.pkg.bytes"
        )
        self.assertEqual(actor_actions["adapter_state"], "package_candidate_only")

    def test_priority_hints_put_kill_notifications_before_effects_ui_and_audio(self):
        self.assertEqual(
            _resource_priority_hint("Databin/Client/Huanhua/ResKillBillboardCfg.bytes"),
            ("Thông báo hạ gục / billboard", 0),
        )
        self.assertEqual(
            _resource_priority_hint("Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"),
            ("Hiệu ứng / hoạt ảnh / biến về", 1),
        )
        self.assertEqual(
            _resource_priority_hint("Resources/1.64.1/assetbundle/uisystem/battle/battleotherui.assetbundle"),
            ("Giao diện / nút bấm / biểu tượng", 2),
        )
        self.assertEqual(
            _resource_priority_hint("Sound/iOS/Hero_LiXin_Skin_C_VO.bnk"),
            ("Âm thanh", 3),
        )

    def test_preservation_gates_count_complete_manifest_and_never_authorize_merge(self):
        resources = [
            {"path": "Databin/Client/Huanhua/ResKillBillboardCfg.bytes", "status": "NEEDS_RULE", "sources": ["P9", "P10"], "reasons": ["row conflict"]},
            {"path": "Databin/Client/Huanhua/ResBillboardCfg.bytes", "status": "IDENTICAL_DUPLICATE", "sources": ["P9", "P10"], "reasons": ["same SHA-256"]},
            {"path": "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes", "status": "UNSUPPORTED", "sources": ["TM", "VTK"], "reasons": ["condition rule missing"]},
            {"path": "Resources/1.64.1/assetbundle/uisystem/battle/battleotherui.assetbundle", "status": "VERSION_CONFLICT", "sources": ["P9", "P10"], "reasons": ["version mismatch"]},
            {"path": "Sound/iOS/Hero_MaChao_SFX.bnk", "status": "UNSUPPORTED", "sources": ["TM"], "reasons": ["Wwise remap missing"]},
        ]
        resources.extend({
            "path": "Databin/Client/Huanhua/ResBillboardCfg_{}.bytes".format(index),
            "status": "NEEDS_RULE",
            "sources": ["P9", "P10"],
            "reasons": ["additional manifest collision"],
        } for index in range(220))

        gates = _build_preservation_gates(resources)
        by_id = {item["id"]: item for item in gates}
        self.assertEqual(by_id["kill_notifications"]["path_count"], 222)
        self.assertEqual(by_id["kill_notifications"]["unresolved_count"], 221)
        self.assertEqual(by_id["kill_notifications"]["identical_duplicate_count"], 1)
        self.assertTrue(by_id["kill_notifications"]["details_truncated"])
        self.assertEqual(by_id["effects_recall"]["unresolved_count"], 1)
        self.assertEqual(by_id["ui_buttons"]["unresolved_count"], 1)
        self.assertEqual(by_id["audio"]["unresolved_count"], 1)
        self.assertEqual(by_id["audio"]["top_items"][0]["adapter_state"], "inspect_only")
        self.assertFalse(any(item["merge_authorized"] for item in gates))
        self.assertFalse(any(item["game_tested"] for item in gates))

    def test_multi_mod_preflight_reports_exact_nested_resource_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)

            def resources_zip(battle_payload: bytes) -> bytes:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as nested:
                    nested.writestr(
                        "Resources/1.64.1/Databin/Client/Sound/BattleBank.bytes",
                        battle_payload,
                    )
                    nested.writestr(
                        "Resources/1.64.1/Databin/Client/Global/HeadImage.bytes",
                        b"same shared image",
                    )
                return buffer.getvalue()

            mod9 = self._zip(folder, "P9-9-skin.zip", {
                "mod/Resources.zip": resources_zip(b"p9 battle payload"),
                "mod/DanhSachSkin.txt": b"skin 9",
            })
            mod10 = self._zip(folder, "P10-10-skin.zip", {
                "mod/Resources.zip": resources_zip(b"p10 battle payload"),
                "mod/DanhSachSkin.txt": b"skin 10",
            })

            report = analyze_aov_input_set([mod9, mod10])

            self.assertFalse(report["ready_to_merge"])
            self.assertEqual(len([
                item for item in report["blockers"]
                if "nhiều MOD wrapper" in item
            ]), 1)
            analysis = report["resource_analysis"]
            self.assertTrue(analysis["available"])
            self.assertFalse(analysis["merge_authorized"])
            gates = {item["id"]: item for item in analysis["preservation_gates"]}
            self.assertEqual(gates["audio"]["path_count"], 1)
            self.assertEqual(gates["audio"]["unresolved_count"], 1)
            self.assertFalse(gates["audio"]["merge_authorized"])
            self.assertEqual(gates["ui_buttons"]["path_count"], 1)
            self.assertGreaterEqual(analysis["summary"]["NEEDS_RULE"], 1)
            self.assertGreaterEqual(analysis["summary"]["IDENTICAL_DUPLICATE"], 1)
            by_path = {item["path"]: item for item in analysis["important_paths"]}
            battle_path = "Resources/1.64.1/Databin/Client/Sound/BattleBank.bytes"
            image_path = "Resources/1.64.1/Databin/Client/Global/HeadImage.bytes"
            self.assertEqual(by_path[battle_path]["status"], "NEEDS_RULE")
            self.assertEqual(by_path[battle_path]["sources"], ["P10-10-skin.zip", "P9-9-skin.zip"])
            self.assertEqual(by_path[image_path]["status"], "IDENTICAL_DUPLICATE")

    def test_unknown_aov_resource_pack_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "unknown-aov.zip", {
                "Resources/1.64.1/Databin/Client/Actor/heroSkin.bytes": b"opaque"
            })
            output = folder / "merged.zip"
            with self.assertRaisesRegex(ModPipelineError, "chưa có adapter"):
                merge_mod_archives([source], str(output))
            self.assertFalse(output.exists())

    def test_conflicting_duplicate_preserves_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "one.zip", {"same.txt": b"one"})
            second = self._zip(folder, "two.zip", {"same.txt": b"two"})
            output = folder / "merged.zip"
            with self.assertRaises(ModPipelineError):
                merge_mod_archives([first, second], str(output))
            self.assertFalse(output.exists())

    def test_identical_duplicate_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self._zip(folder, "one.zip", {"same.txt": b"same"})
            second = self._zip(folder, "two.zip", {"same.txt": b"same"})
            output = folder / "merged.zip"
            result = merge_mod_archives([first, second], str(output))
            self.assertEqual(result["adapter"], "generic_zip")
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ["same.txt"])

    def test_windows_drive_path_is_rejected_during_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "drive.zip", {"C:/outside.txt": b"bad"})
            with self.assertRaisesRegex(ModPipelineError, "không an toàn"):
                _archive_profile(source)

    def test_symlink_is_rejected_during_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / "symlink.zip"
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(info, b"target")
            with self.assertRaisesRegex(ModPipelineError, "không an toàn"):
                _archive_profile(str(path))

    def test_traversal_is_rejected_before_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = self._zip(folder, "bad.zip", {"../escape.txt": b"bad"})
            output = folder / "merged.zip"
            with self.assertRaisesRegex(ModPipelineError, "không an toàn"):
                merge_mod_archives([source], str(output))
            self.assertFalse(output.exists())



    def test_preflight_reports_common_actions_multi_overlay_dry_run(self):
        def common_package(skin_id: str, condition: str) -> bytes:
            root = ET.Element("Project")
            ET.SubElement(root, "TemplateObjectList")
            ET.SubElement(root, "RefParamList")
            action = ET.SubElement(root, "Action")
            track = ET.SubElement(action, "Track", {
                "trackName": "0_RecallTrack",
                "eventType": "TriggerParticleTick",
                "enabled": "true",
            })
            ET.SubElement(track, "SkinOrAvatarList", {
                "id": skin_id,
                "mode": condition,
            })
            xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            payload = encode_aov_resource(
                xml, "commonresource/Back.xml", "zstd"
            )
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("commonresource/Back.xml", payload)
            return buffer.getvalue()

        def resources_zip(package_bytes: bytes) -> bytes:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(
                    "Resources/1.64.1/Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes",
                    package_bytes,
                )
            return buffer.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p9 = self._zip(folder, "Actor593-source.zip", {
                "Resources.zip": resources_zip(common_package("59300", "recall-593")),
                "DanhSachSkin.txt": b"P9",
            })
            p10 = self._zip(folder, "Actor595-source.zip", {
                "Resources.zip": resources_zip(common_package("59500", "recall-595")),
                "DanhSachSkin.txt": b"P10",
            })

            report = analyze_aov_input_set([p9, p10])
            common = report["common_actions_overlay_assessment"]

            self.assertEqual(common["state"], "COMPATIBLE")
            self.assertEqual(common["skin_ids"], ["59300", "59500"])
            self.assertEqual(common["overlay_count"], 2)
            self.assertTrue(common["parser_verified"])
            self.assertFalse(common["merge_authorized"])
            self.assertFalse(common["full_merge_authorized"])
            self.assertTrue(common["output_discarded"])
            self.assertFalse(report["ready_to_merge"])

if __name__ == "__main__":
    unittest.main()
