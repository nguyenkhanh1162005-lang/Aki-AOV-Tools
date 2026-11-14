import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.aov_bytes import encrypt_aes_resource
from app.aov_mses import MSESTable, build_mses
from app.aov_multi_merger import (
    AOVMultiMergeError,
    merge_multi_actor_aov_archives,
)

BASE_PREFIX = "Resources/1.64.1/"
COMMON_ACTIONS = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
PACKER = "assetbundle/resourcepackerinfosetall.assetbundle"
VERIFICATION = "assetbundle/resourceverificationinfosetall.assetbundle"
RES_SKIN = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"


def _mses_resource(path, rows):
    header = bytearray(0x8C)
    header[:4] = b"MSES"
    table = MSESTable(7, 69, tuple(), bytes(header), True)
    return encrypt_aes_resource(build_mses(table, rows), path)


def _write_inner(path, resources):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in resources.items():
            archive.writestr(BASE_PREFIX + name, payload)
    return buffer.getvalue()


def _write_wrapper(path, resources, skin_text="Skin nền"):
    inner = _write_inner(path, resources)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Mod/Resources.zip", inner)
        archive.writestr("Mod/DanhSachSkin.txt", skin_text + "\n")


def _write_direct(path, resources):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in resources.items():
            archive.writestr(BASE_PREFIX + name, payload)


def _base_resources():
    return {
        "version.txt": b"1.64.1.12",
        COMMON_ACTIONS: b"base-common-actions",
        PACKER: b"base-packer",
        VERIFICATION: b"base-verification",
        RES_SKIN: b"base-res-skin",
        "Ages/Prefab_Characters/Actor_593_Actions.pkg.bytes": b"actor-593",
    }



def _wwise_chunk(name, payload):
    return name.encode("ascii") + struct.pack("<I", len(payload)) + payload


def _wwise_bank(bank_id, media_id, media_payload, object_id, object_payload=b"node"):
    didx = _wwise_chunk(
        "DIDX", struct.pack("<III", media_id, 0, len(media_payload))
    )
    data = _wwise_chunk("DATA", media_payload)
    body = struct.pack("<I", object_id) + object_payload
    hirc = _wwise_chunk(
        "HIRC",
        struct.pack("<I", 1) + b"\\x02" + struct.pack("<I", len(body)) + body,
    )
    header = _wwise_chunk("BKHD", struct.pack("<II", 135, bank_id))
    return header + didx + data + hirc


class AOVMultiMergerTests(unittest.TestCase):
    def test_appends_unique_resources_and_updates_verification_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator-pack.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, _base_resources())
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                "Ages/CreatorOnly/Actor_731_Custom.bytes": b"creator-resource",
                VERIFICATION: b"overlay-verification",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {
                    "name": base_path.name,
                    "actor_action_ids": ["593"],
                    "uncompressed_bytes": 1000,
                    "skin_name_hints": ["Skin nền"],
                },
                {
                    "name": overlay_path.name,
                    "actor_action_ids": ["731"],
                    "uncompressed_bytes": 500,
                    "skin_name_hints": ["Skin Ánh Sao"],
                },
            ]

            def merge_metadata(base, overlay, *, kind, prefer_overlay_paths=None):
                return base + b"+" + overlay, {
                    "kind": kind,
                    "preferred": sorted(prefer_overlay_paths or ()),
                    "verified": True,
                }

            with patch(
                "app.aov_multi_merger.merge_aov_metadata_assetbundle",
                side_effect=merge_metadata,
            ):
                result = merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )

            self.assertEqual(result["adapter"], "aov_multi_actor_auto_detect")
            self.assertEqual(result["added_resources"], 2)
            self.assertTrue(result["structurally_validated"])
            with zipfile.ZipFile(output_path) as outer:
                self.assertIsNone(outer.testzip())
                resources_info = next(
                    item for item in outer.infolist()
                    if item.filename.endswith("Resources.zip")
                )
                inner_bytes = outer.read(resources_info)
                skin_text = next(
                    outer.read(item).decode("utf-8")
                    for item in outer.infolist()
                    if item.filename.endswith("DanhSachSkin.txt")
                )
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                self.assertIsNone(inner.testzip())
                self.assertEqual(
                    inner.read(BASE_PREFIX + "Ages/CreatorOnly/Actor_731_Custom.bytes"),
                    b"creator-resource",
                )
                self.assertIn(b"base-packer", inner.read(BASE_PREFIX + PACKER))
                self.assertIn(b"overlay-verification", inner.read(BASE_PREFIX + VERIFICATION))
            self.assertIn("Skin nền", skin_text)
            self.assertIn("Skin Ánh Sao", skin_text)

    def test_rejects_unknown_conflicting_resource_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator.zip"
            output_path = Path(directory) / "merged.zip"
            resources = _base_resources()
            resources["Ages/Custom/Shared.bytes"] = b"base"
            _write_wrapper(base_path, resources)
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                "Ages/Custom/Shared.bytes": b"overlay",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]
            with self.assertRaisesRegex(AOVMultiMergeError, "chưa có parser"):
                merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )
            self.assertFalse(output_path.exists())

    def test_merges_dynamic_actor_owned_mses_during_archive_merge(self):
        sound_path = "Databin/Client/Sound/HeroSound.bytes"
        base_row = struct.pack("<I", 5930001) + b"actor-593-sound"
        overlay_row = struct.pack("<I", 5950001) + b"actor-595-sound"
        resources = _base_resources()
        resources[sound_path] = _mses_resource(sound_path, [base_row])
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "tm-robot.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, resources)
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                sound_path: _mses_resource(sound_path, [overlay_row]),
                VERIFICATION: b"overlay-verification",
                "Ages/Prefab_Characters/Actor_595_Actions.pkg.bytes": b"actor-595",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["595"]},
            ]

            def merge_metadata(base, overlay, *, kind, prefer_overlay_paths=None):
                return base + b"+" + overlay, {
                    "kind": kind,
                    "preferred": sorted(prefer_overlay_paths or ()),
                    "verified": True,
                }

            with patch(
                "app.aov_multi_merger.merge_aov_metadata_assetbundle",
                side_effect=merge_metadata,
            ):
                result = merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )
            self.assertIn(sound_path, result["resource_reports"])
            self.assertTrue(result["resource_reports"][sound_path]["verified"])
            with zipfile.ZipFile(output_path) as outer:
                inner_bytes = outer.read(
                    next(i for i in outer.infolist() if i.filename.endswith("Resources.zip"))
                )
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                merged_resource = inner.read(BASE_PREFIX + sound_path)
            from app.aov_bytes import decode_aov_resource
            from app.aov_mses import parse_mses
            decoded, _ = decode_aov_resource(merged_resource, sound_path)
            keys = {
                int.from_bytes(row[:4], "little")
                for row in parse_mses(decoded).rows
            }
            self.assertEqual(keys, {5930001, 5950001})


    def test_appends_wwise_bank_when_ids_are_disjoint(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator-audio.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, _base_resources())
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                "Sound/iOS/Creator_Hero_Show.bnk": _wwise_bank(
                    300, 3001, b"creator-media", 30001
                ),
                VERIFICATION: b"overlay-verification",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]

            def merge_metadata(base, overlay, *, kind, prefer_overlay_paths=None):
                return base + b"+" + overlay, {
                    "kind": kind,
                    "preferred": sorted(prefer_overlay_paths or ()),
                    "verified": True,
                }

            with patch(
                "app.aov_multi_merger.merge_aov_metadata_assetbundle",
                side_effect=merge_metadata,
            ):
                result = merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )

            self.assertTrue(
                result["resource_reports"]["Sound/iOS/Creator_Hero_Show.bnk"]["verified"]
            )
            self.assertEqual(result["resource_reports"][
                "Sound/iOS/Creator_Hero_Show.bnk"
            ]["adapter"], "wwise_bnk_append_only_disjoint_id_namespaces")
            with zipfile.ZipFile(output_path) as outer:
                inner_bytes = outer.read(
                    next(i for i in outer.infolist() if i.filename.endswith("Resources.zip"))
                )
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                self.assertEqual(
                    inner.read(BASE_PREFIX + "Sound/iOS/Creator_Hero_Show.bnk"),
                    _wwise_bank(300, 3001, b"creator-media", 30001),
                )

    def test_appends_same_bank_id_in_separate_language_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator-audio.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, _base_resources())
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                "Sound/iOS/English(US)/Creator_Show.bnk": _wwise_bank(
                    400, 4001, b"english-media", 40001
                ),
                "Sound/iOS/Vietnamese/Creator_Show.bnk": _wwise_bank(
                    400, 4001, b"vietnamese-media", 40001, b"translated"
                ),
                VERIFICATION: b"overlay-verification",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]

            def merge_metadata(base, overlay, *, kind, prefer_overlay_paths=None):
                return base + b"+" + overlay, {
                    "kind": kind,
                    "preferred": sorted(prefer_overlay_paths or ()),
                    "verified": True,
                }

            with patch(
                "app.aov_multi_merger.merge_aov_metadata_assetbundle",
                side_effect=merge_metadata,
            ):
                result = merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )

            self.assertEqual(result["resource_reports"][
                "Sound/iOS/English(US)/Creator_Show.bnk"
            ]["overlay_banks"][0]["scope"], "ios|englishus")
            with zipfile.ZipFile(output_path) as outer:
                inner_bytes = outer.read(
                    next(i for i in outer.infolist() if i.filename.endswith("Resources.zip"))
                )
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                self.assertIn(
                    BASE_PREFIX + "Sound/iOS/English(US)/Creator_Show.bnk",
                    inner.namelist(),
                )
                self.assertIn(
                    BASE_PREFIX + "Sound/iOS/Vietnamese/Creator_Show.bnk",
                    inner.namelist(),
                )

    def test_rejects_wwise_bank_id_collision_during_archive_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator-audio.zip"
            output_path = Path(directory) / "merged.zip"
            resources = _base_resources()
            resources["Sound/iOS/Base.bnk"] = _wwise_bank(
                500, 5001, b"base-media", 50001
            )
            _write_wrapper(base_path, resources)
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                "Sound/iOS/Creator_Show.bnk": _wwise_bank(
                    500, 5002, b"new-media", 50002
                ),
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]
            with self.assertRaisesRegex(AOVMultiMergeError, "Bank ID 500"):
                merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )
            self.assertFalse(output_path.exists())

    def test_rejects_unpaired_wem_during_archive_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator-audio.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, _base_resources())
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                "Sound/iOS/Creator_Media.wem": b"RIFF-wem-data",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]
            with self.assertRaisesRegex(AOVMultiMergeError, "WEM rời"):
                merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )
            self.assertFalse(output_path.exists())


    def test_rejects_unique_wwise_bank_without_reference_remap(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator-with-audio.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, _base_resources())
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                "Sound/iOS/Creator_Hero_Show.bnk": b"opaque-wwise-bank",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]
            with self.assertRaisesRegex(AOVMultiMergeError, "Wwise BNK/WEM"):
                merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )
            self.assertFalse(output_path.exists())

    def test_rejects_overlay_without_version_txt(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator-no-version.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, _base_resources())
            _write_direct(overlay_path, {
                "Ages/CreatorOnly/Actor_731_Custom.bytes": b"creator-resource",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]
            with self.assertRaisesRegex(AOVMultiMergeError, "thiếu version.txt"):
                merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
