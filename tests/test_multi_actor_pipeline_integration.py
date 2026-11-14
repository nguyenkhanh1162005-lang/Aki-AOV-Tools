import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.mod_pipeline import ModPipelineError, _select_adapter, merge_mod_archives


ADAPTER = "aov_multi_actor_auto_detect"


def _profile(name, actor_id, *, wrapper=False, version=True, looks_like_aov=True):
    return {
        "name": name,
        "looks_like_aov": looks_like_aov,
        "source_role": (
            "aov_skin_mod_container_candidate" if wrapper else "aov_actor_mod_candidate"
        ),
        "has_mod_skin_container": wrapper,
        "has_version_txt": version,
        "has_vtk_ifix": False,
        "has_595_assets": False,
        "has_actor593_content": False,
        "has_kpm100_direct_base": False,
        "source_family_hint": None,
        "actor_ids": [actor_id] if actor_id else [],
        "actor_action_ids": [actor_id] if actor_id else [],
        "actor_asset_ids": [],
        "uncompressed_bytes": 1024,
        "entries": 4,
    }


class MultiActorPipelineIntegrationTests(unittest.TestCase):
    def test_selects_auto_adapter_for_unlisted_actor_ids(self):
        profiles = [
            _profile("creator-one.zip", "731", wrapper=True),
            _profile("creator-two.zip", "842"),
        ]
        self.assertEqual(_select_adapter(profiles), ADAPTER)

    def test_rejects_candidate_without_explicit_version_marker(self):
        profiles = [
            _profile("creator-one.zip", "731", wrapper=True),
            _profile("creator-two.zip", "842", version=False),
        ]
        with self.assertRaisesRegex(ModPipelineError, "version"):
            _select_adapter(profiles)

    def test_rejects_ordinary_zip_in_mixed_input(self):
        profiles = [
            _profile("creator-one.zip", "731", wrapper=True),
            _profile("ordinary.zip", None, looks_like_aov=False),
        ]
        with self.assertRaises(ModPipelineError):
            _select_adapter(profiles)

    def test_merge_pipeline_dispatches_to_multi_actor_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "one.zip"
            second = Path(directory) / "two.zip"
            output = Path(directory) / "merged.zip"
            first.write_bytes(b"source-one")
            second.write_bytes(b"source-two")
            profiles = [
                _profile(first.name, "731", wrapper=True),
                _profile(second.name, "842"),
            ]

            def fake_engine(paths, destination, selected_profiles):
                self.assertEqual(paths, [str(first), str(second)])
                self.assertEqual(selected_profiles, profiles)
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr("Resources.zip", b"fixture")
                return {"adapter": ADAPTER, "verified": True}

            capabilities = {
                "adapters": [{"id": ADAPTER, "status": "merge_supported_conditional"}]
            }
            with (
                patch("app.mod_pipeline._preflight_inputs", return_value=20),
                patch("app.mod_pipeline._archive_profile", side_effect=profiles),
                patch("app.mod_pipeline.get_adapter_capabilities", return_value=capabilities),
                patch("app.mod_pipeline.merge_multi_actor_aov_archives", side_effect=fake_engine) as engine,
            ):
                report = merge_mod_archives([str(first), str(second)], str(output))

            engine.assert_called_once()
            self.assertEqual(report["adapter"], ADAPTER)
            self.assertTrue(report["structurally_validated"])
            self.assertFalse(report["game_compatibility_tested"])
            self.assertTrue(output.is_file())

    def test_selects_exact_p9_p10_vtk_split_composite(self):
        p9_ids = ["107", "130", "137", "163", "509", "510", "518", "540", "597"]
        p10_ids = ["111", "132", "150", "157", "167", "190", "524", "567", "598", "599"]
        profiles = [
            {
                **_profile("p9.zip", "107", wrapper=True),
                "source_family_hint": "P9",
                "resources_zip_count": 1,
                "skin_list_count": 1,
                "actor_action_ids": p9_ids,
            },
            {
                **_profile("p10.zip", "111", wrapper=True),
                "source_family_hint": "P10",
                "resources_zip_count": 1,
                "skin_list_count": 1,
                "actor_action_ids": p10_ids,
            },
            {
                **_profile("Part1.zip", "595"),
                "source_family_hint": "VTK",
                "source_part_number": 1,
                "has_vtk_ifix": True,
                "has_595_assets": False,
                "actor_action_ids": ["595"],
            },
            {
                **_profile("Part2.zip", "595"),
                "source_family_hint": "VTK",
                "source_part_number": 2,
                "has_vtk_ifix": False,
                "has_595_assets": True,
                "actor_asset_ids": ["595"],
            },
            {
                **_profile("Part3.zip", "595"),
                "source_family_hint": "VTK",
                "source_part_number": 3,
                "has_vtk_ifix": False,
                "has_595_assets": True,
                "actor_asset_ids": ["595"],
            },
        ]
        self.assertEqual(
            _select_adapter(profiles),
            "aov_p9_p10_vtk_actor595_1.64.1",
        )

    def test_does_not_include_tamyn_actor593_in_exact_p9_p10_vtk_route(self):
        p9_ids = ["107", "130", "137", "163", "509", "510", "518", "540", "597"]
        p10_ids = ["111", "132", "150", "157", "167", "190", "524", "567", "598", "599"]
        profiles = [
            {
                **_profile("p9.zip", "107", wrapper=True),
                "source_family_hint": "P9", "resources_zip_count": 1,
                "skin_list_count": 1, "actor_action_ids": p9_ids,
            },
            {
                **_profile("p10.zip", "111", wrapper=True),
                "source_family_hint": "P10", "resources_zip_count": 1,
                "skin_list_count": 1, "actor_action_ids": p10_ids,
            },
            *[
                {
                    **_profile(f"Part{part}.zip", "595"),
                    "source_family_hint": "VTK", "source_part_number": part,
                    "has_vtk_ifix": part == 1,
                    "has_595_assets": part in {2, 3},
                    "actor_action_ids": ["595"] if part == 1 else [],
                    "actor_asset_ids": ["595"] if part in {2, 3} else [],
                }
                for part in (1, 2, 3)
            ],
            {
                **_profile("Tamyn-Robot.zip", "593"),
                "source_family_hint": "TM",
                "has_actor593_content": True,
                "actor_action_ids": ["593"],
            },
        ]
        with self.assertRaises(ModPipelineError):
            _select_adapter(profiles)

    def test_composite_pipeline_runs_p9_p10_then_vtk_specialist(self):
        composite = "aov_p9_p10_vtk_actor595_1.64.1"
        p9_ids = ["107", "130", "137", "163", "509", "510", "518", "540", "597"]
        p10_ids = ["111", "132", "150", "157", "167", "190", "524", "567", "598", "599"]
        with tempfile.TemporaryDirectory() as directory:
            source_paths = [
                Path(directory) / "p9.zip",
                Path(directory) / "p10.zip",
                Path(directory) / "Part1.zip",
                Path(directory) / "Part2.zip",
                Path(directory) / "Part3.zip",
            ]
            for index, path in enumerate(source_paths):
                path.write_bytes(f"source-{index}".encode())
            output = Path(directory) / "merged.zip"
            profiles = [
                {
                    **_profile("p9.zip", "107", wrapper=True),
                    "source_family_hint": "P9", "resources_zip_count": 1,
                    "skin_list_count": 1, "actor_action_ids": p9_ids,
                },
                {
                    **_profile("p10.zip", "111", wrapper=True),
                    "source_family_hint": "P10", "resources_zip_count": 1,
                    "skin_list_count": 1, "actor_action_ids": p10_ids,
                },
                *[
                    {
                        **_profile(f"Part{part}.zip", "595"),
                        "source_family_hint": "VTK", "source_part_number": part,
                        "has_vtk_ifix": part == 1,
                        "has_595_assets": part in {2, 3},
                        "actor_action_ids": ["595"] if part == 1 else [],
                        "actor_asset_ids": ["595"] if part in {2, 3} else [],
                    }
                    for part in (1, 2, 3)
                ],
            ]

            def fake_p9p10(paths, destination):
                self.assertEqual(set(paths), {str(source_paths[0]), str(source_paths[1])})
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr("Resources.zip", b"p9-p10")
                    archive.writestr("DanhSachSkin.txt", b"19 skins")
                return {"verified": True}

            def fake_vtk(paths, destination):
                self.assertEqual(Path(paths[0]).name, "p9-p10-19-skin.zip")
                self.assertEqual(paths[1:], [str(x) for x in source_paths[2:]])
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr("Resources.zip", b"p9-p10-vtk")
                    archive.writestr("DanhSachSkin.txt", b"19 skins + VTK")
                return {
                    "adapter": "aov_mod_to_mod_vtk_actor595_1.64.1",
                    "validation": "passed",
                    "vtk_version": "1.64.1.11",
                    "assetbundles": 10,
                    "game_tested": False,
                }

            capabilities = {
                "adapters": [{"id": composite, "status": "merge_supported_conditional"}]
            }
            with (
                patch("app.mod_pipeline._preflight_inputs", return_value=100),
                patch("app.mod_pipeline._archive_profile", side_effect=profiles),
                patch("app.mod_pipeline.get_adapter_capabilities", return_value=capabilities),
                patch("app.mod_pipeline.merge_p9_p10_archives", side_effect=fake_p9p10) as p9p10,
                patch("app.mod_pipeline.merge_aov_archives", side_effect=fake_vtk) as vtk,
            ):
                report = merge_mod_archives([str(x) for x in source_paths], str(output))

            p9p10.assert_called_once()
            vtk.assert_called_once()
            self.assertEqual(report["adapter"], composite)
            self.assertEqual(report["composite_stages"]["p9_p10"]["skin_count"], 19)
            self.assertTrue(report["structurally_validated"])
            self.assertFalse(report["game_compatibility_tested"])
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
