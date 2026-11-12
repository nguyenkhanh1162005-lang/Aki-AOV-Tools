import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.mod_pipeline import ModPipelineError, _archive_profile, _preflight_inputs, _select_adapter, analyze_aov_input_set, merge_mod_archives


class ModPipelineTests(unittest.TestCase):
    def _zip(self, folder: Path, name: str, entries: dict[str, bytes]) -> str:
        path = folder / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, payload in entries.items():
                archive.writestr(entry, payload)
        return str(path)

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


if __name__ == "__main__":
    unittest.main()
