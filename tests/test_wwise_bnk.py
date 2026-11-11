import struct
import unittest

from app.wwise_bnk import WwiseBNKError, compare_wwise_bnk, inspect_wwise_bnk


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
        self.assertEqual(report["media"][0]["id"], 7001)
        self.assertEqual(report["media"][0]["sha256"], __import__("hashlib").sha256(b"WEM!").hexdigest())
        self.assertEqual(report["hirc_objects"][0]["type"], 2)
        self.assertEqual(report["hirc_objects"][0]["id"], 9001)
        self.assertEqual(report["hirc_objects"][0]["size"], 10)
        self.assertEqual(report["hirc_objects"][0]["sha256"], __import__("hashlib").sha256(b"\x02" + struct.pack("<I", 10) + object_body).hexdigest())


    def test_compares_media_and_hirc_payloads_without_authorizing_merge(self):
        def bank(media_id, media_payload, object_id, object_payload=b"opaque"):
            didx = chunk("DIDX", struct.pack("<III", media_id, 0, len(media_payload)))
            data = chunk("DATA", media_payload)
            body = struct.pack("<I", object_id) + object_payload
            hirc = chunk("HIRC", struct.pack("<I", 1) + b"\x02" + struct.pack("<I", len(body)) + body)
            return make_bank(didx, data, hirc)

        report = compare_wwise_bnk(
            bank(10, b"same", 20),
            bank(10, b"diff", 20, b"changed"),
        )
        self.assertEqual(report["status"], "NEEDS_RULE")
        self.assertEqual(report["media"]["changed_payload_ids"], [10])
        self.assertEqual(report["hirc"]["changed_payload_ids"], [20])
        self.assertTrue(report["advisory_only"])
        self.assertFalse(report["merge_authorized"])

    def test_identical_and_new_ids_are_inventory_only(self):
        def bank(media_id, payload, object_id):
            didx = chunk("DIDX", struct.pack("<III", media_id, 0, len(payload)))
            data = chunk("DATA", payload)
            body = struct.pack("<I", object_id) + b"node"
            hirc = chunk("HIRC", struct.pack("<I", 1) + b"\x02" + struct.pack("<I", len(body)) + body)
            return make_bank(didx, data, hirc)

        report = compare_wwise_bnk(bank(1, b"a", 2), bank(3, b"b", 2))
        self.assertEqual(report["status"], "INSPECTED_ONLY")
        self.assertEqual(report["media"]["new_overlay_ids"], [3])
        self.assertFalse(report["merge_authorized"])


    def test_detects_bank_id_mismatch(self):
        base = make_bank()
        overlay = chunk("BKHD", struct.pack("<II", 134, 54321))
        report = compare_wwise_bnk(base, overlay)
        self.assertEqual(report["status"], "BANK_ID_CONFLICT")
        self.assertFalse(report["bank_id_match"])
        self.assertFalse(report["merge_authorized"])

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
