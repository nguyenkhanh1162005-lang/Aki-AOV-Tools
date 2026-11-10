import hashlib
import struct
import unittest

from app.aov_mses import MSESError, merge_aov_mses_resource, merge_mses, parse_mses
from app.aov_crypto import decode_aov_resource, encrypt_aov_resource
from app.aov_zstd import encode_aov_bytes


def make_mses(fields, rows):
    serialized = b"".join(struct.pack("<I", len(row)) + row for row in rows)
    header = bytearray(140)
    header[:4] = b"MSES"
    struct.pack_into("<III", header, 4, 7, fields, len(rows))
    struct.pack_into("<I", header, 132, 140)
    header[96:128] = hashlib.md5(serialized).hexdigest().encode("ascii")
    return bytes(header) + serialized


def row(key, payload):
    return struct.pack("<I", key) + payload


class MSESMergeTests(unittest.TestCase):
    def test_merges_only_target_hero_rows_and_recomputes_header(self):
        base_rows = [
            row(123001, b"unrelated-base"),
            row(5950001, b"old-sfx"),
            row(5950002, b"old-voice"),
        ]
        incoming_rows = [
            row(123002, b"unrelated-incoming"),
            row(5950001, b"new-sfx"),
            row(5950003, b"new-recall"),
        ]
        base = make_mses(69, base_rows)
        incoming = make_mses(63, incoming_rows)

        merged, report = merge_mses(
            base, incoming, target_prefixes=("595",)
        )
        parsed = parse_mses(merged)
        by_key = {struct.unpack_from("<I", r, 0)[0]: r for r in parsed.rows}

        self.assertEqual(parsed.field_count, 69)
        self.assertEqual(parsed.row_count, 4)
        self.assertEqual(by_key[123001], base_rows[0])
        self.assertNotIn(123002, by_key)
        self.assertEqual(by_key[5950001], incoming_rows[1])
        self.assertEqual(by_key[5950002], base_rows[2])
        self.assertEqual(by_key[5950003], incoming_rows[2])
        self.assertEqual(report.rows_added, 1)
        self.assertEqual(report.rows_replaced, 1)
        self.assertEqual(report.final_rows, 4)
        self.assertEqual(
            merged[96:128],
            hashlib.md5(merged[140:]).hexdigest().encode("ascii"),
        )

    def test_rejects_duplicate_keys_and_corrupt_lengths(self):
        duplicate = make_mses(2, [row(595001, b"a"), row(595001, b"b")])
        with self.assertRaises(MSESError):
            parse_mses(duplicate)

        corrupt = bytearray(make_mses(2, [row(595001, b"a")]))
        corrupt[140:144] = (9999).to_bytes(4, "little")
        with self.assertRaises(MSESError):
            parse_mses(bytes(corrupt))

    def test_requires_target_prefix(self):
        table = make_mses(1, [row(595001, b"one")])
        with self.assertRaises(MSESError):
            merge_mses(table, table, target_prefixes=())


    def test_resource_pipeline_merges_aes_base_with_zstd_incoming(self):
        resource_name = "Databin/Client/Sound/BattleBank.bytes"
        base_plain = make_mses(59, [
            row(5950001, b"old-sfx"),
            row(1230001, b"keep-base"),
        ])
        incoming_plain = make_mses(59, [
            row(5950001, b"new-sfx"),
            row(5950002, b"new-voice"),
        ])
        base_wrapped = encrypt_aov_resource(resource_name, base_plain)
        incoming_wrapped = encode_aov_bytes(incoming_plain)

        merged_wrapped, report = merge_aov_mses_resource(
            resource_name,
            base_wrapped,
            incoming_wrapped,
            target_prefixes=("595",),
        )
        self.assertTrue(merged_wrapped.startswith(bytes.fromhex("22 4a 67 00")))
        merged_plain = decode_aov_resource(resource_name, merged_wrapped)
        parsed = parse_mses(merged_plain)
        by_key = {struct.unpack_from("<I", r, 0)[0]: r for r in parsed.rows}
        self.assertEqual(by_key[5950001], row(5950001, b"new-sfx"))
        self.assertEqual(by_key[5950002], row(5950002, b"new-voice"))
        self.assertEqual(by_key[1230001], row(1230001, b"keep-base"))
        self.assertEqual(report.rows_added, 1)
        self.assertEqual(report.rows_replaced, 1)


if __name__ == "__main__":
    unittest.main()
