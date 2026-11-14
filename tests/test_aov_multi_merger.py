import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.aov_bytes import decode_aov_resource, encrypt_aes_resource
from app.aov_mses import MSESTable, build_mses
from app.assetref_inspector import inspect_asset_reference_set
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


def _assetref_payload(label=b"assetref-fixture"):
    return (
        b"\x31\x0a\x01\x00"
        + b"\x08\x00\x00\x00ROOTC\x00\x00\x00"
        + b"\x02\x00\x00\x00\x0d\x00\x00\x00\x06\x00\x00\x00"
        + b"JTCom.\x00\x00\x00\x08\x00\x00\x00"
        + b"TypeAssetRefAnalyser.AssetReferenceSet"
        + b"\x04\x00\x00\x00baseSubset\x00"
        + b"VPrefab_Characters/Prefab_Hero/595_LiXin/skill/A1\x00"
        + label
    )


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
        struct.pack("<I", 1) + bytes([2]) + struct.pack("<I", len(body)) + body,
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

    def test_keeps_one_copy_of_identical_resource_shared_by_two_overlays(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            first_path = Path(directory) / "creator-one.zip"
            second_path = Path(directory) / "creator-two.zip"
            output_path = Path(directory) / "merged.zip"
            shared_path = "Ages/CreatorOnly/Shared.bytes"
            _write_wrapper(base_path, _base_resources())
            _write_direct(first_path, {
                "version.txt": b"1.64.1.12",
                shared_path: b"identical-creator-payload",
                VERIFICATION: b"verification-one",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            _write_direct(second_path, {
                "version.txt": b"1.64.1.12",
                shared_path: b"identical-creator-payload",
                VERIFICATION: b"verification-two",
                "Ages/Prefab_Characters/Actor_842_Actions.pkg.bytes": b"actor-842",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": first_path.name, "actor_action_ids": ["731"]},
                {"name": second_path.name, "actor_action_ids": ["842"]},
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
                    [str(base_path), str(first_path), str(second_path)],
                    str(output_path),
                    profiles,
                )

            self.assertEqual(result["added_resources"], 3)
            with zipfile.ZipFile(output_path) as outer:
                inner_bytes = outer.read(
                    next(i for i in outer.infolist() if i.filename.endswith("Resources.zip"))
                )
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                target = BASE_PREFIX + shared_path
                self.assertEqual(inner.read(target), b"identical-creator-payload")
                self.assertEqual(
                    sum(i.filename == target for i in inner.infolist()),
                    1,
                )


    def test_merges_unknown_mses_table_only_when_actor_prefixes_are_unique(self):
        custom_path = "Databin/Client/Skill/CreatorCustomCfg.bytes"
        base_rows = [
            struct.pack("<I", 5930001) + b"base-593-row",
            struct.pack("<I", 5930002) + b"base-593-row-two",
        ]
        overlay_rows = [
            struct.pack("<I", 5930001) + b"overlay-cannot-overwrite-base",
            struct.pack("<I", 7310001) + b"overlay-731-row",
            struct.pack("<I", 7310002) + b"overlay-731-row-two",
        ]
        resources = _base_resources()
        resources[custom_path] = _mses_resource(custom_path, base_rows)
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, resources)
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                custom_path: _mses_resource(custom_path, overlay_rows),
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

            self.assertEqual(
                result["resource_reports"][custom_path]["adapter"],
                "heuristic_actor_prefix_mses_multi_source_chain",
            )
            self.assertTrue(result["resource_reports"][custom_path]["verified"])
            with zipfile.ZipFile(output_path) as outer:
                inner_bytes = outer.read(
                    next(i for i in outer.infolist() if i.filename.endswith("Resources.zip"))
                )
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                merged_bytes = inner.read(BASE_PREFIX + custom_path)
            decoded, _mode = decode_aov_resource(merged_bytes, custom_path)
            from app.aov_mses import parse_mses
            table = parse_mses(decoded)
            by_key = {int.from_bytes(row[:4], "little"): row for row in table.rows}
            self.assertEqual(set(by_key), {5930001, 5930002, 7310001, 7310002})
            self.assertEqual(by_key[5930001], base_rows[0])
            self.assertEqual(by_key[7310001], overlay_rows[1])


    def test_rejects_unknown_mses_conflict_with_ambiguous_actor_prefix(self):
        custom_path = "Databin/Client/Skill/CreatorAmbiguousCfg.bytes"
        base_rows = [struct.pack("<I", 5930001) + b"base-row"]
        overlay_rows = [
            struct.pack("<I", 5930001) + b"changed-common-row",
            struct.pack("<I", 7310001) + b"overlay-owned-row",
        ]
        resources = _base_resources()
        resources[custom_path] = _mses_resource(custom_path, base_rows)
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, resources)
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                custom_path: _mses_resource(custom_path, overlay_rows),
                VERIFICATION: b"overlay-verification",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["59", "593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]
            with self.assertRaisesRegex(AOVMultiMergeError, "Actor owner duy nhất"):
                merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )
            self.assertFalse(output_path.exists())


    def test_rejects_unknown_mses_rows_without_actor_prefix_owner(self):
        custom_path = "Databin/Client/Skill/CreatorOpaqueCfg.bytes"
        resources = _base_resources()
        resources[custom_path] = _mses_resource(
            custom_path, [struct.pack("<I", 5930001) + b"base-row"]
        )
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "creator.zip"
            output_path = Path(directory) / "merged.zip"
            _write_wrapper(base_path, resources)
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                custom_path: _mses_resource(
                    custom_path,
                    [
                        struct.pack("<I", 5930001) + b"base-row",
                        struct.pack("<I", 123456) + b"unowned-row",
                    ],
                ),
                VERIFICATION: b"overlay-verification",
                "Ages/Prefab_Characters/Actor_731_Actions.pkg.bytes": b"actor-731",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["731"]},
            ]
            with self.assertRaisesRegex(AOVMultiMergeError, "không quy được duy nhất"):
                merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)],
                    str(output_path),
                    profiles,
                )
            self.assertFalse(output_path.exists())


    def test_inspects_wrapped_asset_reference_set(self):
        path = "AssetRefs/Hero/595_AssetRef.bytes"
        payload = _assetref_payload()
        wrapped = encrypt_aes_resource(payload, path)

        report = inspect_asset_reference_set(path, wrapped)

        self.assertTrue(report["recognized"])
        self.assertEqual(report["wrapper_mode"], "aes")
        self.assertEqual(report["actor_id"], "595")
        self.assertTrue(report["has_base_subset"])
        self.assertFalse(report["merge_supported"])
        self.assertGreater(report["reference_path_candidates"], 0)

    def test_preserves_unique_asset_reference_set_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "robot.zip"
            output_path = Path(directory) / "merged.zip"
            assetref_path = "AssetRefs/Hero/595_AssetRef.bytes"
            assetref = encrypt_aes_resource(_assetref_payload(), assetref_path)
            _write_wrapper(base_path, _base_resources())
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                assetref_path: assetref,
                "Ages/Prefab_Characters/Actor_595_Actions.pkg.bytes": b"actor-595",
                VERIFICATION: b"overlay-verification",
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
                    [str(base_path), str(overlay_path)], str(output_path), profiles
                )

            report = result["resource_reports"][assetref_path]
            self.assertEqual(report["adapter"], "asset_reference_set_unique_passthrough")
            self.assertTrue(report["recognized"])
            self.assertTrue(report["preserved_unchanged"])
            with zipfile.ZipFile(output_path) as outer:
                inner_info = next(
                    item for item in outer.infolist()
                    if item.filename.endswith("Resources.zip")
                )
                with zipfile.ZipFile(io.BytesIO(outer.read(inner_info))) as inner:
                    self.assertEqual(
                        inner.read(BASE_PREFIX + assetref_path), assetref
                    )

    def test_rejects_conflicting_asset_reference_set_without_mses_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.zip"
            overlay_path = Path(directory) / "robot.zip"
            output_path = Path(directory) / "merged.zip"
            assetref_path = "AssetRefs/Hero/593_AssetRef.bytes"
            base_assetref = encrypt_aes_resource(
                _assetref_payload(b"base"), assetref_path
            )
            overlay_assetref = encrypt_aes_resource(
                _assetref_payload(b"overlay"), assetref_path
            )
            base_resources = _base_resources()
            base_resources[assetref_path] = base_assetref
            _write_wrapper(base_path, base_resources)
            _write_direct(overlay_path, {
                "version.txt": b"1.64.1.12",
                assetref_path: overlay_assetref,
                "Ages/Prefab_Characters/Actor_595_Actions.pkg.bytes": b"actor-595",
            })
            profiles = [
                {"name": base_path.name, "actor_action_ids": ["593"]},
                {"name": overlay_path.name, "actor_action_ids": ["595"]},
            ]

            with self.assertRaisesRegex(AOVMultiMergeError, "AssetReferenceSet"):
                merge_multi_actor_aov_archives(
                    [str(base_path), str(overlay_path)], str(output_path), profiles
                )
            self.assertFalse(output_path.exists())

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


    def test_rejects_malformed_wwise_bank_before_append_only_merge(self):
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
            with self.assertRaisesRegex(AOVMultiMergeError, "Wwise BNK"):
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
