import struct
import unittest

from app.aov_bytes import encode_aov_resource, decode_aov_resource
from app.aov_merge import AOVResourceMergeError, merge_aov_mses_resource
from app.aov_mses import build_mses, MSESTable, parse_mses
import hashlib


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
        overlay_rows = [
            base_rows[0],
            struct.pack("<I", 595001) + b"unverified-skill-row",
        ]
        base = encode_aov_resource(table(4, base_rows), name, "aes")
        overlay = encode_aov_resource(table(3, overlay_rows), name, "aes")
        with self.assertRaisesRegex(AOVResourceMergeError, "ngoài allowlist theo bảng"):
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


if __name__ == "__main__":
    unittest.main()
