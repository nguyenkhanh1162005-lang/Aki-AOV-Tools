import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.mod_pipeline import ModPipelineError, _archive_profile, _preflight_inputs, _select_adapter, merge_mod_archives


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
