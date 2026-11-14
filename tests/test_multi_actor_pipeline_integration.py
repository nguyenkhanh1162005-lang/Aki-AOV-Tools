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


if __name__ == "__main__":
    unittest.main()
