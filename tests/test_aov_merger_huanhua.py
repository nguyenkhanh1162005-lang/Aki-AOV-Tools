import hashlib
import struct
import unittest

from app.aov_bytes import decode_aov_resource, encode_aov_resource
from app.aov_merger import (
    AOVMergeError,
    HUANHUA_TABLE_PATHS,
    _merge_huanhua_billboard_tables,
)
from app.aov_mses import parse_mses


def mses_table(field_count, rows):
    header = bytearray(0x8C)
    header[:4] = b"MSES"
    body = b"".join(struct.pack("<I", len(row)) + row for row in rows)
    struct.pack_into("<III", header, 4, 7, field_count, len(rows))
    header[0x60:0x80] = hashlib.md5(body).hexdigest().encode("ascii")
    return bytes(header) + body


def wrapped_table(path, field_count, rows):
    return encode_aov_resource(mses_table(field_count, rows), path, "aes")


class HuanhuaBillboardAdapterTests(unittest.TestCase):
    def test_merges_all_three_actor595_billboard_tables_and_verifies_them(self):
        shared = struct.pack("<I", 1) + b"shared-row"
        billboard = struct.pack("<I", 0x00000253) + b"Actor595-billboard"
        base = {
            path: wrapped_table(path, 8, [shared])
            for path in HUANHUA_TABLE_PATHS
        }
        overlay = {
            path: wrapped_table(path, 7, [shared, billboard])
            for path in HUANHUA_TABLE_PATHS
        }

        replacements, reports = _merge_huanhua_billboard_tables(base, overlay)

        self.assertEqual(set(replacements), set(HUANHUA_TABLE_PATHS))
        self.assertEqual(set(reports), set(HUANHUA_TABLE_PATHS))
        for path in HUANHUA_TABLE_PATHS:
            with self.subTest(path=path):
                decoded, mode = decode_aov_resource(replacements[path], path)
                parsed = parse_mses(decoded)
                self.assertEqual(mode, "aes")
                self.assertEqual(len(parsed.rows), 2)
                self.assertEqual(parsed.rows[-1], billboard)
                self.assertEqual(
                    reports[path]["appended_keys"], ["0x00000253"]
                )
                self.assertTrue(reports[path]["verified"])

    def test_rejects_conflicting_existing_actor595_billboard_row(self):
        path = HUANHUA_TABLE_PATHS[0]
        shared = struct.pack("<I", 1) + b"shared-row"
        base_595 = struct.pack("<I", 0x00000253) + b"base-billboard"
        overlay_595 = struct.pack("<I", 0x00000253) + b"overlay-billboard"

        with self.assertRaisesRegex(
            AOVMergeError,
            "chưa có quy tắc ghi đè an toàn",
        ):
            _merge_huanhua_billboard_tables(
                {path: wrapped_table(path, 8, [shared, base_595])},
                {path: wrapped_table(path, 7, [shared, overlay_595])},
            )

    def test_rejects_billboard_table_when_base_schema_is_missing(self):
        path = HUANHUA_TABLE_PATHS[0]
        billboard = struct.pack("<I", 0x00000253) + b"Actor595-billboard"
        with self.assertRaisesRegex(AOVMergeError, "ZIP nền thiếu bảng"):
            _merge_huanhua_billboard_tables(
                {},
                {path: wrapped_table(path, 7, [billboard])},
            )

    def test_rejects_unknown_huanhua_table_path(self):
        with self.assertRaisesRegex(AOVMergeError, "chưa có adapter"):
            _merge_huanhua_billboard_tables(
                {},
                {"Databin/Client/Huanhua/Unknown.bytes": b"opaque"},
            )

    def test_empty_overlay_keeps_base_resources_untouched(self):
        base = {HUANHUA_TABLE_PATHS[0]: b"base"}
        replacements, reports = _merge_huanhua_billboard_tables(base, {})
        self.assertEqual(replacements, {})
        self.assertEqual(reports, {})


if __name__ == "__main__":
    unittest.main()
