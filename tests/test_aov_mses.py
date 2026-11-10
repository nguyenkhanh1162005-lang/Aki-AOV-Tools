import hashlib
import struct
import unittest

from app.aov_mses import MSESError, MSESTable, build_mses, merge_mses_rows, parse_mses


def make_table(field_count, rows):
    header = bytearray(0x8C)
    header[:4] = b"MSES"
    body = b"".join(struct.pack("<I", len(row)) + row for row in rows)
    struct.pack_into("<III", header, 4, 7, field_count, len(rows))
    header[0x60:0x80] = hashlib.md5(body).hexdigest().encode("ascii")
    return bytes(header) + body


class MSESTests(unittest.TestCase):
    def test_parse_and_rebuild_round_trip(self):
        original = make_table(4, [struct.pack("<I", 1) + b"one", struct.pack("<I", 2) + b"two"])
        parsed = parse_mses(original)
        self.assertEqual(parsed.field_count, 4)
        self.assertEqual(len(parsed.rows), 2)
        self.assertEqual(build_mses(parsed, parsed.rows), original)

    def test_merge_appends_new_and_preserves_base_conflict(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base", struct.pack("<I", 2) + b"same"])
        overlay = make_table(3, [struct.pack("<I", 1) + b"base", struct.pack("<I", 2) + b"same", struct.pack("<I", 3) + b"new"])
        output, report = merge_mses_rows(base, overlay, allow_schema_mismatch=True)
        parsed = parse_mses(output)
        self.assertEqual(parsed.field_count, 4)
        self.assertEqual([row[4:] for row in parsed.rows], [b"base", b"same", b"new"])
        self.assertEqual(report["appended_keys"], ["0x00000003"])
        self.assertEqual(report["retained_base_conflicts"], [])

    def test_keeps_base_row_when_same_schema_conflicts(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(4, [struct.pack("<I", 1) + b"overlay"])
        output, report = merge_mses_rows(base, overlay)
        self.assertEqual(parse_mses(output).rows[0][4:], b"base")
        self.assertEqual(report["retained_base_conflicts"], ["0x00000001"])

    def test_rejects_schema_mismatch_with_changed_shared_row(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(3, [struct.pack("<I", 1) + b"overlay"])
        with self.assertRaises(MSESError):
            merge_mses_rows(base, overlay, allow_schema_mismatch=True)

    def test_merge_explicitly_overrides_selected_key(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(4, [struct.pack("<I", 1) + b"overlay"])
        output, report = merge_mses_rows(base, overlay, override_keys={1})
        self.assertEqual(parse_mses(output).rows[0][4:], b"overlay")
        self.assertEqual(report["replaced_keys"], ["0x00000001"])

    def test_rejects_bad_checksum(self):
        original = bytearray(make_table(4, [struct.pack("<I", 1) + b"one"]))
        original[-1] ^= 1
        with self.assertRaises(MSESError):
            parse_mses(bytes(original))

    def test_rejects_trailing_bytes(self):
        original = make_table(4, [struct.pack("<I", 1) + b"one"]) + b"x"
        with self.assertRaises(MSESError):
            parse_mses(original)


if __name__ == "__main__":
    unittest.main()
