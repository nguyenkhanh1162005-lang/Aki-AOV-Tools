import unittest

from app.aov_zstd import WRAPPER_MAGIC, decode_aov_bytes, encode_aov_bytes


class AoVZstdWrapperTests(unittest.TestCase):
    def test_encode_decode_round_trip_with_embedded_dictionary(self):
        payload = (
            b"<Project><Action>"
            + (b"<Track trackName='sample' value='59500'/>" * 100)
            + b"</Action></Project>"
        )
        wrapped = encode_aov_bytes(payload)
        self.assertTrue(wrapped.startswith(WRAPPER_MAGIC))
        self.assertEqual(int.from_bytes(wrapped[4:8], "little"), len(payload))
        self.assertEqual(decode_aov_bytes(wrapped), payload)

    def test_rejects_invalid_wrapper(self):
        with self.assertRaises(ValueError):
            decode_aov_bytes(b"not-an-aov-resource")


if __name__ == "__main__":
    unittest.main()
