import struct
import unittest

from app.wwise_bnk import WwiseBNKError, inspect_wwise_bnk


def chunk(name, payload):
    return name.encode("ascii") + struct.pack("<I", len(payload)) + payload


def make_bank(*extra_chunks):
    header = chunk("BKHD", struct.pack("<II", 134, 12345))
    return header + b"".join(extra_chunks)


class WwiseBNKInspectorTests(unittest.TestCase):
    def test_inspects_header_chunks_media_and_hirc_objects(self):
        didx = chunk("DIDX", struct.pack("<III", 7001, 0, 4))
        data = chunk("DATA", b"WEM!")
        object_body = struct.pack("<I", 9001) + b"opaque"
        hirc = chunk("HIRC", struct.pack("<I", 1) + b"\x02" + struct.pack("<I", len(object_body)) + object_body)
        report = inspect_wwise_bnk(make_bank(didx, data, hirc))
        self.assertEqual(report["version"], 134)
        self.assertEqual(report["bank_id"], 12345)
        self.assertEqual(report["media"], [{"id": 7001, "offset": 0, "size": 4}])
        self.assertEqual(report["hirc_objects"], [{"type": 2, "id": 9001, "size": 10}])

    def test_rejects_chunk_length_past_end(self):
        malformed = b"BKHD" + struct.pack("<I", 99) + b"short"
        with self.assertRaisesRegex(WwiseBNKError, "vượt quá cuối file"):
            inspect_wwise_bnk(malformed)

    def test_rejects_didx_without_data(self):
        didx = chunk("DIDX", struct.pack("<III", 1, 0, 1))
        with self.assertRaisesRegex(WwiseBNKError, "thiếu DATA"):
            inspect_wwise_bnk(make_bank(didx))

    def test_rejects_media_outside_data(self):
        didx = chunk("DIDX", struct.pack("<III", 1, 0, 5))
        with self.assertRaisesRegex(WwiseBNKError, "vượt quá kích thước DATA"):
            inspect_wwise_bnk(make_bank(didx, chunk("DATA", b"1234")))

    def test_rejects_overlapping_media_ranges(self):
        didx = chunk(
            "DIDX",
            struct.pack("<III", 1, 0, 3) + struct.pack("<III", 2, 2, 2),
        )
        with self.assertRaisesRegex(WwiseBNKError, "chồng lấn"):
            inspect_wwise_bnk(make_bank(didx, chunk("DATA", b"12345")))

    def test_rejects_duplicate_media_ids(self):
        didx = chunk(
            "DIDX",
            struct.pack("<III", 1, 0, 1) + struct.pack("<III", 1, 1, 1),
        )
        with self.assertRaisesRegex(WwiseBNKError, "media ID trùng"):
            inspect_wwise_bnk(make_bank(didx, chunk("DATA", b"12")))

    def test_rejects_duplicate_hirc_object_ids(self):
        obj = b"\x02" + struct.pack("<I", 4) + struct.pack("<I", 88)
        hirc = chunk("HIRC", struct.pack("<I", 2) + obj + obj)
        with self.assertRaisesRegex(WwiseBNKError, "object ID trùng"):
            inspect_wwise_bnk(make_bank(hirc))

    def test_rejects_hirc_trailing_bytes(self):
        hirc = chunk("HIRC", struct.pack("<I", 0) + b"extra")
        with self.assertRaisesRegex(WwiseBNKError, "dữ liệu thừa"):
            inspect_wwise_bnk(make_bank(hirc))


if __name__ == "__main__":
    unittest.main()
