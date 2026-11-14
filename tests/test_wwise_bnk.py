import struct
import unittest

from app.wwise_bnk import (
    WwiseBNKError,
    assess_wwise_bnk_overlay_set,
    compare_wwise_bnk,
    inspect_wwise_bnk,
)


def chunk(name, payload):
    return name.encode("ascii") + struct.pack("<I", len(payload)) + payload


def make_bank(*extra_chunks, version=134, bank_id=12345):
    header = chunk("BKHD", struct.pack("<II", version, bank_id))
    return header + b"".join(extra_chunks)



def make_identity_bank(
    bank_id, media_id, media_payload, object_id, object_payload=b"node", *, version=135
):
    didx = chunk("DIDX", struct.pack("<III", media_id, 0, len(media_payload)))
    data = chunk("DATA", media_payload)
    body = struct.pack("<I", object_id) + object_payload
    hirc = chunk(
        "HIRC",
        struct.pack("<I", 1)
        + b"\\x02"
        + struct.pack("<I", len(body))
        + body,
    )
    return make_bank(didx, data, hirc, version=version, bank_id=bank_id)


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


    def test_parses_legacy_u32_hirc_object_type_for_version_48(self):
        object_body = struct.pack("<I", 9002) + b"legacy"
        legacy_object = (
            struct.pack("<I", 2)
            + struct.pack("<I", len(object_body))
            + object_body
        )
        hirc = chunk("HIRC", struct.pack("<I", 1) + legacy_object)
        report = inspect_wwise_bnk(make_bank(hirc, version=48))
        self.assertEqual(report["version"], 48)
        self.assertEqual(report["hirc_objects"][0]["type"], 2)
        self.assertEqual(report["hirc_objects"][0]["id"], 9002)

    def test_uses_one_byte_hirc_type_from_version_49_onward(self):
        object_body = struct.pack("<I", 9003) + b"modern"
        modern_object = (
            bytes([2])
            + struct.pack("<I", len(object_body))
            + object_body
        )
        hirc = chunk("HIRC", struct.pack("<I", 1) + modern_object)
        report = inspect_wwise_bnk(make_bank(hirc, version=49))
        self.assertEqual(report["version"], 49)
        self.assertEqual(report["hirc_objects"][0]["type"], 2)
        self.assertEqual(report["hirc_objects"][0]["id"], 9003)

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
        self.assertEqual(report["status"], "NEEDS_RULE")
        self.assertEqual(report["media"]["new_overlay_ids"], [3])
        self.assertFalse(report["bank_payload_identical"])
        self.assertFalse(report["merge_authorized"])


    def test_identical_bank_bytes_are_reported_as_exact_duplicate(self):
        bank = make_bank()
        report = compare_wwise_bnk(bank, bank)
        self.assertEqual(report["status"], "IDENTICAL_BANK_PAYLOAD")
        self.assertTrue(report["bank_payload_identical"])
        self.assertTrue(report["bank_id_match"])

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

    def test_append_only_accepts_disjoint_bank_media_and_hirc_ids(self):
        base = [
            ("Sound/iOS/Base.bnk", make_identity_bank(100, 1000, b"base-media", 2000)),
        ]
        overlay = [
            ("Sound/iOS/Creator.bnk", make_identity_bank(101, 1001, b"new-media", 2001)),
        ]
        report = assess_wwise_bnk_overlay_set(base, overlay)
        self.assertTrue(report["verified"])
        self.assertTrue(report["no_id_remapping_required"])
        self.assertEqual(report["overlay_bank_count"], 1)
        self.assertEqual(
            report["overlay_banks"][0]["scope"],
            "ios|default",
        )

    def test_append_only_rejects_bank_id_collision_in_same_namespace(self):
        base = [("Sound/iOS/Base.bnk", make_identity_bank(100, 1000, b"a", 2000))]
        overlay = [("Sound/iOS/Overlay.bnk", make_identity_bank(100, 1001, b"b", 2001))]
        with self.assertRaisesRegex(WwiseBNKError, "Bank ID 100"):
            assess_wwise_bnk_overlay_set(base, overlay)

    def test_append_only_rejects_media_id_collision_with_different_payload(self):
        base = [("Sound/iOS/Base.bnk", make_identity_bank(100, 1000, b"base", 2000))]
        overlay = [("Sound/iOS/Overlay.bnk", make_identity_bank(101, 1000, b"changed", 2001))]
        with self.assertRaisesRegex(WwiseBNKError, "Media ID 1000"):
            assess_wwise_bnk_overlay_set(base, overlay)

    def test_append_only_rejects_hirc_id_collision_with_different_payload(self):
        base = [("Sound/iOS/Base.bnk", make_identity_bank(100, 1000, b"base", 2000))]
        overlay = [("Sound/iOS/Overlay.bnk", make_identity_bank(101, 1001, b"new", 2000, b"changed"))]
        with self.assertRaisesRegex(WwiseBNKError, "HIRC object ID 2000"):
            assess_wwise_bnk_overlay_set(base, overlay)

    def test_append_only_allows_identical_shared_media_and_hirc_payloads(self):
        base = [("Sound/iOS/Base.bnk", make_identity_bank(100, 1000, b"shared", 2000))]
        overlay = [("Sound/iOS/Overlay.bnk", make_identity_bank(101, 1000, b"shared", 2000))]
        report = assess_wwise_bnk_overlay_set(base, overlay)
        self.assertTrue(report["no_id_remapping_required"])

    def test_append_only_allows_same_bank_ids_in_distinct_locales(self):
        base = [
            (
                "Sound/iOS/English(US)/Hero_Show.bnk",
                make_identity_bank(100, 1000, b"english", 2000),
            )
        ]
        overlay = [
            (
                "Sound/iOS/Vietnamese/Hero_Show.bnk",
                make_identity_bank(100, 1000, b"vietnamese", 2000, b"translated"),
            )
        ]
        report = assess_wwise_bnk_overlay_set(base, overlay)
        self.assertEqual(report["overlay_banks"][0]["scope"], "ios|vietnamese")

    def test_append_only_rejects_same_bank_id_in_same_locale(self):
        base = [
            (
                "Sound/iOS/English(US)/Base.bnk",
                make_identity_bank(100, 1000, b"english", 2000),
            )
        ]
        overlay = [
            (
                "Sound/iOS/English(US)/Overlay.bnk",
                make_identity_bank(100, 1001, b"new", 2001),
            )
        ]
        with self.assertRaisesRegex(WwiseBNKError, "Bank ID 100"):
            assess_wwise_bnk_overlay_set(base, overlay)

    def test_append_only_keeps_ios_and_android_ids_in_separate_namespaces(self):
        base = [("Sound/iOS/Base.bnk", make_identity_bank(100, 1000, b"ios", 2000))]
        overlay = [("Sound/Android/Overlay.bnk", make_identity_bank(100, 1000, b"android", 2000, b"android"))]
        report = assess_wwise_bnk_overlay_set(base, overlay)
        self.assertEqual(report["overlay_banks"][0]["scope"], "android|default")

    def test_append_only_global_bank_collides_with_platform_bank(self):
        base = [("Sound/iOS/Base.bnk", make_identity_bank(100, 1000, b"ios", 2000))]
        overlay = [("Global/Overlay.bnk", make_identity_bank(100, 1001, b"global", 2001))]
        with self.assertRaisesRegex(WwiseBNKError, "Bank ID 100"):
            assess_wwise_bnk_overlay_set(base, overlay)

    def test_append_only_rejects_unknown_chunks(self):
        overlay = [("Sound/iOS/Overlay.bnk", make_bank(chunk("JUNK", b"opaque"), bank_id=400))]
        with self.assertRaisesRegex(WwiseBNKError, "chunk Wwise chưa được giải nghĩa"):
            assess_wwise_bnk_overlay_set([], overlay)

    def test_append_only_rejects_unresolved_external_wem_media(self):
        didx = chunk("DIDX", struct.pack("<III", 7001, 0, 0))
        data = chunk("DATA", b"")
        overlay = [("Sound/iOS/Overlay.bnk", make_bank(didx, data, bank_id=400))]
        with self.assertRaisesRegex(WwiseBNKError, "external media ID"):
            assess_wwise_bnk_overlay_set([], overlay)

    def test_append_only_rejects_different_wwise_versions(self):
        base = [("Sound/iOS/Base.bnk", make_identity_bank(100, 1000, b"base", 2000, version=135))]
        overlay = [("Sound/iOS/Overlay.bnk", make_identity_bank(101, 1001, b"new", 2001, version=134))]
        with self.assertRaisesRegex(WwiseBNKError, "Wwise version"):
            assess_wwise_bnk_overlay_set(base, overlay)


if __name__ == "__main__":
    unittest.main()
