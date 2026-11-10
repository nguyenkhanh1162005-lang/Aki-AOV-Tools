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

    def test_sound_bank_requires_vtk_lixin_skin_row(self):
        name = "Databin/Client/Sound/LobbyBank.bytes"
        base = encode_aov_resource(table(3, [struct.pack("<I", 1) + b"base"]), name, "aes")
        overlay = encode_aov_resource(table(3, [struct.pack("<I", 1) + b"other"]), name, "aes")
        with self.assertRaises(AOVResourceMergeError):
            merge_aov_mses_resource(base, overlay, name)


if __name__ == "__main__":
    unittest.main()
