import struct
import unittest

from app.aov_bytes import encode_aov_resource, decode_aov_resource
from app.aov_merge import (
    AOVResourceMergeError,
    _P9_P10_ACTOR_OWNED_MSES,
    merge_aov_mses_resource,
)
from app.aov_mses import build_mses, MSESTable, parse_mses
import hashlib
from unittest.mock import patch


def table(field_count, rows):
    header = bytearray(0x8C)
    header[:4] = b"MSES"
    body = b"".join(struct.pack("<I", len(row)) + row for row in rows)
    struct.pack_into("<III", header, 4, 7, field_count, len(rows))
    header[0x60:0x80] = hashlib.md5(body).hexdigest().encode("ascii")
    return bytes(header) + body


class AOVResourceMergeTests(unittest.TestCase):
    def test_merges_aes_wrapped_table_and_verifies_round_trip(self):
        name = "Databin/Client/Sound/BattleBank.bytes"
        base_dec = table(3, [struct.pack("<I", 1) + b"base"])
        overlay_dec = table(2, [struct.pack("<I", 1) + b"base", struct.pack("<I", 2) + b"Hero_LiXin_Skin_C_SFX"])
        base = encode_aov_resource(base_dec, name, "aes")
        overlay = encode_aov_resource(overlay_dec, name, "aes")
        result, report = merge_aov_mses_resource(base, overlay, name)
        decoded, mode = decode_aov_resource(result, name)
        parsed = parse_mses(decoded)
        self.assertEqual(mode, "aes")
        self.assertEqual(len(parsed.rows), 2)
        self.assertEqual(report["verified"], True)
        self.assertEqual(report["appended_keys"], ["0x00000002"])

    def test_repairs_only_the_known_stale_res_skin_base_checksum(self):
        name = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        base_rows = [struct.pack("<I", key) + f"row-{key}".encode() for key in range(1, 54)]
        overlay_rows = [base_rows[0], struct.pack("<I", 59500) + b"VTK-skin-row"]
        base_raw = bytearray(table(69, base_rows))
        base_raw[0x60:0x80] = b"0" * 32
        overlay_raw = table(63, overlay_rows)
        base = encode_aov_resource(bytes(base_raw), name, "aes")
        overlay = encode_aov_resource(overlay_raw, name, "aes")

        with self.assertRaises(AOVResourceMergeError):
            merge_aov_mses_resource(base, overlay, name)

        result, report = merge_aov_mses_resource(
            base, overlay, name, allow_invalid_base_checksum=True
        )
        decoded, mode = decode_aov_resource(result, name)
        parsed = parse_mses(decoded)
        self.assertEqual(mode, "aes")
        self.assertEqual(parsed.field_count, 69)
        self.assertEqual(len(parsed.rows), 54)
        self.assertEqual(report["base_checksum_repaired"], True)
        self.assertEqual(parsed.rows[-1][:4], struct.pack("<I", 59500))

    def test_merges_only_source_verified_billboard_key_in_huanhua_tables(self):
        expected_paths = (
            "Databin/Client/Huanhua/ResBillboardCfg.bytes",
            "Databin/Client/Huanhua/ResBillboardSkinCfg.bytes",
            "Databin/Client/Huanhua/ResKillBillboardCfg.bytes",
        )
        shared = struct.pack("<I", 1) + b"shared-row"
        billboard = struct.pack("<I", 0x00000253) + b"VTK-595-billboard"
        for name in expected_paths:
            with self.subTest(name=name):
                base = encode_aov_resource(table(8, [shared]), name, "aes")
                overlay = encode_aov_resource(table(7, [shared, billboard]), name, "aes")
                result, report = merge_aov_mses_resource(base, overlay, name)
                decoded, _ = decode_aov_resource(result, name)
                parsed = parse_mses(decoded)
                self.assertEqual(len(parsed.rows), 2)
                self.assertEqual(parsed.rows[-1], billboard)
                self.assertEqual(report["appended_keys"], ["0x00000253"])
                self.assertTrue(report["verified"])

    def test_huanhua_tables_reject_unverified_new_row_keys(self):
        name = "Databin/Client/Huanhua/ResBillboardCfg.bytes"
        shared = struct.pack("<I", 1) + b"shared-row"
        allowed = struct.pack("<I", 0x00000253) + b"VTK-595-billboard"
        unrelated = struct.pack("<I", 0x00000254) + b"unrelated-row"
        base = encode_aov_resource(table(8, [shared]), name, "aes")
        overlay = encode_aov_resource(table(7, [shared, allowed, unrelated]), name, "aes")
        with self.assertRaisesRegex(AOVResourceMergeError, "ngoài allowlist billboard"):
            merge_aov_mses_resource(base, overlay, name)

    def test_skillcombine_merges_exact_five_source_verified_actor595_keys(self):
        name = "Databin/Client/Skill/skillcombine.bytes"
        shared = struct.pack("<I", 1) + b"shared-row"
        actor_rows = [
            struct.pack("<I", key) + f"VTK-row-{key:08x}".encode()
            for key in (0x000915A7, 0x000915A8, 0x000915A9, 0x000915AA, 0x000915AB)
        ]
        base = encode_aov_resource(table(9, [shared]), name, "aes")
        overlay = encode_aov_resource(table(8, [shared, *actor_rows]), name, "aes")
        result, report = merge_aov_mses_resource(base, overlay, name)
        decoded, _ = decode_aov_resource(result, name)
        parsed = parse_mses(decoded)
        self.assertEqual(len(parsed.rows), 6)
        self.assertEqual(parsed.rows[-5:], tuple(actor_rows))
        self.assertEqual(report["appended_keys"], [
            "0x000915a7", "0x000915a8", "0x000915a9", "0x000915aa", "0x000915ab"
        ])

    def test_skillcombine_rejects_unverified_or_missing_actor595_keys(self):
        name = "Databin/Client/Skill/skillcombine.bytes"
        shared = struct.pack("<I", 1) + b"shared-row"
        expected = [0x000915A7, 0x000915A8, 0x000915A9, 0x000915AA, 0x000915AB]
        for keys, error in (
            (expected[:-1], "thiếu key skillcombine"),
            (expected + [0x000915AC], "ngoài allowlist"),
        ):
            with self.subTest(keys=keys):
                rows = [shared] + [
                    struct.pack("<I", key) + f"row-{key:08x}".encode()
                    for key in keys
                ]
                base = encode_aov_resource(table(9, [shared]), name, "aes")
                overlay = encode_aov_resource(table(8, rows), name, "aes")
                with self.assertRaisesRegex(AOVResourceMergeError, error):
                    merge_aov_mses_resource(base, overlay, name)

    def test_merges_res_character_component_only_when_shared_rows_are_identical(self):
        name = "Databin/Client/Character/ResCharacterComponent.bytes"
        shared_a = struct.pack("<I", 1) + b"shared-A"
        shared_b = struct.pack("<I", 2) + b"shared-B"
        p9_only = struct.pack("<I", 9) + b"P9-row"
        p10_only = struct.pack("<I", 10) + b"P10-row"

        base = encode_aov_resource(
            table(188, [shared_a, p9_only, shared_b]), name, "aes"
        )
        overlay = encode_aov_resource(
            table(188, [shared_b, p10_only, shared_a]), name, "aes"
        )

        result, report = merge_aov_mses_resource(base, overlay, name)
        decoded, mode = decode_aov_resource(result, name)
        parsed = parse_mses(decoded)
        rows = {struct.unpack_from("<I", row)[0]: row for row in parsed.rows}

        self.assertEqual(mode, "aes")
        self.assertEqual(parsed.field_count, 188)
        self.assertEqual(set(rows), {1, 2, 9, 10})
        self.assertEqual(rows[1], shared_a)
        self.assertEqual(rows[2], shared_b)
        self.assertEqual(rows[9], p9_only)
        self.assertEqual(rows[10], p10_only)
        self.assertEqual(report["appended_keys"], ["0x0000000a"])
        self.assertEqual(report["retained_base_conflicts"], [])
        self.assertTrue(report["verified"])

    def test_res_character_component_rejects_different_shared_rows(self):
        name = "Databin/Client/Character/ResCharacterComponent.bytes"
        base = encode_aov_resource(
            table(188, [struct.pack("<I", 1) + b"base"]), name, "aes"
        )
        overlay = encode_aov_resource(
            table(188, [
                struct.pack("<I", 1) + b"changed",
                struct.pack("<I", 2) + b"new",
            ]), name, "aes"
        )
        with self.assertRaisesRegex(
            AOVResourceMergeError, "hàng chung khác nội dung"
        ):
            merge_aov_mses_resource(base, overlay, name)

    def test_res_character_component_rejects_schema_mismatch(self):
        name = "Databin/Client/Character/ResCharacterComponent.bytes"
        base = encode_aov_resource(
            table(188, [struct.pack("<I", 1) + b"base"]), name, "aes"
        )
        overlay = encode_aov_resource(
            table(187, [
                struct.pack("<I", 1) + b"base",
                struct.pack("<I", 2) + b"new",
            ]), name, "aes"
        )
        with self.assertRaisesRegex(
            AOVResourceMergeError, "khác chuẩn 188"
        ):
            merge_aov_mses_resource(base, overlay, name)

    def test_merges_exact_p9_p10_res_skin_label_tables_and_repairs_stale_checksums(self):
        name = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        common_rows = [
            struct.pack("<I", key) + f"shared-{key}".encode()
            for key in range(1, 9)
        ]
        p9_raw = bytearray(table(69, common_rows))
        p10_raw = bytearray(table(
            69,
            common_rows + [
                struct.pack("<I", key) + f"P10-only-{key}".encode()
                for key in range(9, 99)
            ],
        ))
        # These two real source tables carry the same stale checksum string.
        stale = b"6679e51360ee25c838a5ee74af96a364"
        p9_raw[0x60:0x80] = stale
        p10_raw[0x60:0x80] = stale
        base = encode_aov_resource(bytes(p9_raw), name, "aes")
        overlay = encode_aov_resource(bytes(p10_raw), name, "aes")

        result, report = merge_aov_mses_resource(
            base, overlay, name, allow_p9_p10_label_union=True
        )
        decoded, mode = decode_aov_resource(result, name)
        parsed = parse_mses(decoded)

        self.assertEqual(mode, "aes")
        self.assertEqual(parsed.field_count, 69)
        self.assertEqual(len(parsed.rows), 98)
        self.assertTrue(parsed.checksum_valid)
        self.assertTrue(report["base_checksum_repaired"])
        self.assertTrue(report["overlay_checksum_repaired"])
        self.assertEqual(
            len(report["appended_keys"]), 90
        )
        self.assertTrue(report["verified"])

    def test_p9_p10_label_union_rejects_different_shared_rows(self):
        name = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        common = [
            struct.pack("<I", key) + f"shared-{key}".encode()
            for key in range(1, 9)
        ]
        p9_raw = bytearray(table(69, common))
        p10_rows = [
            struct.pack("<I", 1) + b"changed-shared-row",
            *common[1:],
            *[
                struct.pack("<I", key) + f"P10-only-{key}".encode()
                for key in range(9, 99)
            ],
        ]
        p10_raw = bytearray(table(69, p10_rows))
        stale = b"6679e51360ee25c838a5ee74af96a364"
        p9_raw[0x60:0x80] = stale
        p10_raw[0x60:0x80] = stale
        base = encode_aov_resource(bytes(p9_raw), name, "aes")
        overlay = encode_aov_resource(bytes(p10_raw), name, "aes")

        with self.assertRaisesRegex(
            AOVResourceMergeError, "hàng chung khác nội dung"
        ):
            merge_aov_mses_resource(
                base, overlay, name, allow_p9_p10_label_union=True
            )

    def test_p9_p10_label_union_rejects_unknown_stale_checksum_value(self):
        name = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        rows_a = [
            struct.pack("<I", key) + f"shared-{key}".encode()
            for key in range(1, 9)
        ]
        rows_b = rows_a + [
            struct.pack("<I", key) + f"extra-{key}".encode()
            for key in range(9, 99)
        ]
        raw_a = bytearray(table(69, rows_a))
        raw_b = bytearray(table(69, rows_b))
        raw_a[0x60:0x80] = b"0" * 32
        raw_b[0x60:0x80] = b"0" * 32
        base = encode_aov_resource(bytes(raw_a), name, "aes")
        overlay = encode_aov_resource(bytes(raw_b), name, "aes")

        with self.assertRaisesRegex(
            AOVResourceMergeError, "đúng chuỗi checksum cũ"
        ):
            merge_aov_mses_resource(
                base, overlay, name, allow_p9_p10_label_union=True
            )

    def test_p9_p10_label_union_rejects_unrecognized_row_counts(self):
        name = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        rows_a = [
            struct.pack("<I", key) + f"A-{key}".encode()
            for key in range(1, 9)
        ]
        rows_b = [
            struct.pack("<I", key) + f"A-{key}".encode()
            for key in range(1, 10)
        ]
        raw_a = bytearray(table(69, rows_a))
        raw_b = bytearray(table(69, rows_b))
        stale = b"6679e51360ee25c838a5ee74af96a364"
        raw_a[0x60:0x80] = stale
        raw_b[0x60:0x80] = stale
        base = encode_aov_resource(bytes(raw_a), name, "aes")
        overlay = encode_aov_resource(bytes(raw_b), name, "aes")

        with self.assertRaisesRegex(
            AOVResourceMergeError, "cặp 8/98 hàng"
        ):
            merge_aov_mses_resource(
                base, overlay, name, allow_p9_p10_label_union=True
            )

    def test_rejects_unregistered_mses_table_path(self):
        base = encode_aov_resource(table(3, [struct.pack("<I", 1) + b"base"]), "Unknown.bytes", "aes")
        overlay = encode_aov_resource(table(3, [struct.pack("<I", 2) + b"overlay"]), "Unknown.bytes", "aes")
        with self.assertRaisesRegex(AOVResourceMergeError, "không có adapter MSES"):
            merge_aov_mses_resource(base, overlay, "Databin/Client/Unknown.bytes")

    def test_sound_bank_rejects_unrelated_overlay_only_rows(self):
        name = "Databin/Client/Sound/BattleBank.bytes"
        base_rows = [struct.pack("<I", 1) + b"base"]
        overlay_rows = [
            base_rows[0],
            struct.pack("<I", 2) + b"Hero_LiXin_Skin_C_SFX",
            struct.pack("<I", 3) + b"unrelated-new-sound-row",
        ]
        base = encode_aov_resource(table(3, base_rows), name, "aes")
        overlay = encode_aov_resource(table(2, overlay_rows), name, "aes")
        with self.assertRaisesRegex(AOVResourceMergeError, "ngoài allowlist theo bảng"):
            merge_aov_mses_resource(base, overlay, name)

    def test_skillcombine_rejects_unverified_new_row_keys(self):
        name = "Databin/Client/Skill/skillcombine.bytes"
        base_rows = [struct.pack("<I", 1) + b"base"]
        approved_keys = (0x000915A7, 0x000915A8, 0x000915A9, 0x000915AA, 0x000915AB)
        overlay_rows = [
            base_rows[0],
            *[
                struct.pack("<I", key) + f"approved-{key:08x}".encode()
                for key in approved_keys
            ],
            struct.pack("<I", 595001) + b"unrelated-skill-row",
        ]
        base = encode_aov_resource(table(4, base_rows), name, "aes")
        overlay = encode_aov_resource(table(3, overlay_rows), name, "aes")
        with self.assertRaisesRegex(AOVResourceMergeError, "ngoài allowlist"):
            merge_aov_mses_resource(base, overlay, name)

    def test_res_skin_rejects_unrelated_overlay_only_key(self):
        name = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        base_rows = [struct.pack("<I", key) + f"row-{key}".encode() for key in range(1, 54)]
        overlay_rows = [
            base_rows[0],
            struct.pack("<I", 59500) + b"VTK-skin-row",
            struct.pack("<I", 59501) + b"unrelated-row",
        ]
        base = encode_aov_resource(table(69, base_rows), name, "aes")
        overlay = encode_aov_resource(table(63, overlay_rows), name, "aes")
        with self.assertRaisesRegex(AOVResourceMergeError, "ngoài allowlist theo bảng"):
            merge_aov_mses_resource(
                base, overlay, name, allow_invalid_base_checksum=True
            )

    def test_res_skin_rejects_unverified_conflict_for_existing_59500(self):
        name = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        base_rows = [
            struct.pack("<I", key) + f"row-{key}".encode()
            for key in range(1, 53)
        ] + [struct.pack("<I", 59500) + b"base-skin-row"]
        overlay_rows = [
            struct.pack("<I", 1) + b"row-1",
            struct.pack("<I", 59500) + b"overlay-skin-row",
        ]
        base = encode_aov_resource(table(69, base_rows), name, "aes")
        overlay = encode_aov_resource(table(63, overlay_rows), name, "aes")
        with self.assertRaisesRegex(AOVResourceMergeError, "chưa có quy tắc ghi đè an toàn"):
            merge_aov_mses_resource(
                base, overlay, name, allow_invalid_base_checksum=True
            )

    def test_sound_bank_requires_vtk_lixin_skin_row(self):
        name = "Databin/Client/Sound/LobbyBank.bytes"
        base = encode_aov_resource(table(3, [struct.pack("<I", 1) + b"base"]), name, "aes")
        overlay = encode_aov_resource(table(3, [struct.pack("<I", 1) + b"other"]), name, "aes")
        with self.assertRaises(AOVResourceMergeError):
            merge_aov_mses_resource(base, overlay, name)


    def test_p9_p10_actor_owned_rows_merge_from_exact_source_inventory(self):
        name = "Databin/Client/Actor/heroSkin.bytes"
        p9_rows = [
            struct.pack("<I", 10700) + b"p9-actor-107",
            struct.pack("<I", 11100) + b"p9-actor-111",
            struct.pack("<I", 12300) + b"same-row",
        ]
        p10_rows = [
            struct.pack("<I", 10700) + b"p10-actor-107",
            struct.pack("<I", 11100) + b"p10-actor-111",
            struct.pack("<I", 12300) + b"same-row",
        ]
        p9_decoded = table(2, p9_rows)
        p10_decoded = table(2, p10_rows)
        spec = {
            "field_count": 2,
            "row_count": 3,
            "actor_divisor": 100,
            "conflict_count": 2,
            "p9_sha256": hashlib.sha256(p9_decoded).hexdigest(),
            "p10_sha256": hashlib.sha256(p10_decoded).hexdigest(),
            "p9_actor_ids": {107},
            "p10_actor_ids": {111},
        }
        with patch.dict(_P9_P10_ACTOR_OWNED_MSES, {name: spec}):
            p9 = encode_aov_resource(p9_decoded, name, "aes")
            p10 = encode_aov_resource(p10_decoded, name, "aes")
            merged, report = merge_aov_mses_resource(
                p9,
                p10,
                name,
                base_actor_action_ids={"107"},
                overlay_actor_action_ids={"111"},
            )
            decoded, _ = decode_aov_resource(merged, name)
            result = parse_mses(decoded)
            rows = {int.from_bytes(row[:4], "little"): row for row in result.rows}
            self.assertEqual(rows[10700], p9_rows[0])
            self.assertEqual(rows[11100], p10_rows[1])
            self.assertEqual(rows[12300], p9_rows[2])
            self.assertEqual(report["differing_shared_rows"], 2)
            self.assertEqual(report["selected_base_owned_rows"], 1)
            self.assertEqual(report["selected_overlay_owned_rows"], 1)
            self.assertTrue(report["output_checksum_valid"])

    def test_p9_p10_actor_owned_rows_reject_unknown_source_actor(self):
        name = "Databin/Client/Actor/heroSkin.bytes"
        p9_decoded = table(2, [struct.pack("<I", 10700) + b"p9"])
        p10_decoded = table(2, [struct.pack("<I", 10700) + b"p10"])
        spec = {
            "field_count": 2, "row_count": 1, "actor_divisor": 100,
            "conflict_count": 1,
            "p9_sha256": hashlib.sha256(p9_decoded).hexdigest(),
            "p10_sha256": hashlib.sha256(p10_decoded).hexdigest(),
            "p9_actor_ids": {107}, "p10_actor_ids": {111},
        }
        with patch.dict(_P9_P10_ACTOR_OWNED_MSES, {name: spec}):
            with self.assertRaisesRegex(AOVResourceMergeError, "inventory không khớp"):
                merge_aov_mses_resource(
                    encode_aov_resource(p9_decoded, name, "aes"),
                    encode_aov_resource(p10_decoded, name, "aes"),
                    name,
                    base_actor_action_ids={"107"},
                    overlay_actor_action_ids={"190"},
                )

    def test_p9_p10_actor_owned_table_requires_both_actor_inventories(self):
        name = "Databin/Client/Actor/heroSkin.bytes"
        with self.assertRaisesRegex(AOVResourceMergeError, "cần Actor_Action inventory"):
            merge_aov_mses_resource(b"invalid", b"invalid", name)



if __name__ == "__main__":
    unittest.main()
