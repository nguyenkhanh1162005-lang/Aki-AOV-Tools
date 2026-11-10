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

    def test_sound_bank_requires_vtk_lixin_skin_row(self):
        name = "Databin/Client/Sound/LobbyBank.bytes"
        base = encode_aov_resource(table(3, [struct.pack("<I", 1) + b"base"]), name, "aes")
        overlay = encode_aov_resource(table(3, [struct.pack("<I", 1) + b"other"]), name, "aes")
        with self.assertRaises(AOVResourceMergeError):
            merge_aov_mses_resource(base, overlay, name)


if __name__ == "__main__":
    unittest.main()
