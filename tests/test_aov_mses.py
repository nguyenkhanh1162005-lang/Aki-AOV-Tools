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
        output, report = merge_mses_rows(
            base, overlay, allow_schema_mismatch=True,
            allow_new_rows_across_schema_mismatch=True,
            allowed_new_keys={3},
        )
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

    def test_rejects_new_opaque_row_across_schema_mismatch(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(3, [
            struct.pack("<I", 1) + b"base",
            struct.pack("<I", 2) + b"new-row-with-unknown-schema",
        ])
        with self.assertRaisesRegex(MSESError, "bản ghi mới chưa được chứng minh tương thích"):
            merge_mses_rows(base, overlay, allow_schema_mismatch=True)

    def test_rejects_new_key_outside_explicit_allowlist(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(3, [
            struct.pack("<I", 1) + b"base",
            struct.pack("<I", 2) + b"approved-looking",
            struct.pack("<I", 3) + b"unrelated",
        ])
        with self.assertRaisesRegex(MSESError, "ngoài allowlist theo bảng"):
            merge_mses_rows(
                base, overlay,
                allow_schema_mismatch=True,
                allow_new_rows_across_schema_mismatch=True,
                allowed_new_keys={2},
            )

    def test_rejects_non_uint32_override_keys(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(4, [struct.pack("<I", 1) + b"overlay"])
        for key in (-1, 0x1_0000_0000, True):
            with self.subTest(key=key):
                with self.assertRaisesRegex(MSESError, "uint32"):
                    merge_mses_rows(base, overlay, override_keys={key})

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

    def test_rejects_override_key_missing_from_overlay(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(4, [struct.pack("<I", 1) + b"overlay"])
        with self.assertRaisesRegex(MSESError, "không tồn tại trong overlay"):
            merge_mses_rows(base, overlay, override_keys={2})

    def test_rejects_unapproved_conflict_when_schemas_differ(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(3, [struct.pack("<I", 1) + b"overlay"])
        with self.assertRaisesRegex(MSESError, "bản ghi chung không được cho phép ghi đè"):
            merge_mses_rows(base, overlay, allow_schema_mismatch=True)

    def test_allows_explicit_override_for_selected_row_across_schema_drift(self):
        base = make_table(4, [struct.pack("<I", 1) + b"base"])
        overlay = make_table(3, [struct.pack("<I", 1) + b"overlay"])
        output, report = merge_mses_rows(
            base,
            overlay,
            override_keys={1},
            allow_schema_mismatch=True,
        )
        self.assertEqual(parse_mses(output).rows[0][4:], b"overlay")
        self.assertEqual(report["replaced_keys"], ["0x00000001"])

    def test_rejects_bad_checksum(self):
        original = bytearray(make_table(4, [struct.pack("<I", 1) + b"one"]))
        original[-1] ^= 1
        with self.assertRaises(MSESError):
            parse_mses(bytes(original))

    def test_rejects_oversized_row_length_before_reading_payload(self):
        header = bytearray(0x8C)
        header[:4] = b"MSES"
        struct.pack_into("<III", header, 4, 7, 1, 1)
        oversized = 64 * 1024 * 1024 + 1
        payload = bytes(header) + struct.pack("<I", oversized)
        with self.assertRaisesRegex(MSESError, "Độ dài bản ghi MSES"):
            parse_mses(payload, verify_checksum=False)

    def test_rejects_trailing_bytes(self):
        original = make_table(4, [struct.pack("<I", 1) + b"one"]) + b"x"
        with self.assertRaises(MSESError):
            parse_mses(original)



    def test_schema_drift_can_override_owned_rows_and_retain_unowned_conflicts(self):
        base = make_table(4, [
            struct.pack("<I", 1) + b"base-owned",
            struct.pack("<I", 2) + b"base-unowned",
        ])
        overlay = make_table(3, [
            struct.pack("<I", 1) + b"overlay-owned",
            struct.pack("<I", 2) + b"overlay-unowned",
            struct.pack("<I", 3) + b"new-approved",
        ])

        output, report = merge_mses_rows(
            base,
            overlay,
            override_keys={1},
            allow_retained_conflict_keys={2},
            allow_schema_mismatch=True,
            allow_new_rows_across_schema_mismatch=True,
            allowed_new_keys={3},
        )
        result = parse_mses(output)
        rows = {
            struct.unpack_from("<I", row)[0]: row[4:]
            for row in result.rows
        }
        self.assertEqual(rows[1], b"overlay-owned")
        self.assertEqual(rows[2], b"base-unowned")
        self.assertEqual(rows[3], b"new-approved")
        self.assertEqual(report["retained_base_conflicts"], ["0x00000002"])


if __name__ == "__main__":
    unittest.main()
