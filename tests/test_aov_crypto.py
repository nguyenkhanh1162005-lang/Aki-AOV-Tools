import unittest

from app.aov_crypto import (
    AoVResourceError,
    decode_aov_resource,
    decrypt_aov_resource,
    encrypt_aov_resource,
    parse_mses_header,
    peek_mses_header,
    resource_key,
)


class AoVResourceCryptoTests(unittest.TestCase):
    def setUp(self):
        self.name = "Databin/Client/Sound/BattleBank.bytes"
        self.plain = (
            b"MSES"
            + (7).to_bytes(4, "little")
            + (59).to_bytes(4, "little")
            + (1932).to_bytes(4, "little")
            + b"sample-table-payload"
        )

    def test_key_is_derived_from_resource_basename(self):
        self.assertEqual(
            resource_key(self.name),
            resource_key("BattleBank.bytes"),
        )
        self.assertNotEqual(
            resource_key(self.name),
            resource_key("LobbyBank.bytes"),
        )

    def test_aes_wrapper_round_trip_and_mses_header(self):
        encrypted = encrypt_aov_resource(self.name, self.plain)
        self.assertTrue(encrypted.startswith(b"\x22\x4a\x67\x00"))
        decoded, marker = decrypt_aov_resource(self.name, encrypted)
        self.assertEqual(marker, b"\x00")
        self.assertEqual(decoded, self.plain)
        self.assertEqual(decode_aov_resource(self.name, encrypted), self.plain)
        self.assertEqual(parse_mses_header(decoded).field_count, 59)
        self.assertEqual(parse_mses_header(decoded).row_count, 1932)

    def test_peek_header_decrypts_only_first_aes_block(self):
        encrypted = encrypt_aov_resource(self.name, self.plain)
        header = peek_mses_header(self.name, encrypted[:24])
        self.assertIsNotNone(header)
        self.assertEqual((header.version, header.field_count, header.row_count), (7, 59, 1932))

    def test_wrong_resource_name_is_rejected(self):
        encrypted = encrypt_aov_resource(self.name, self.plain)
        with self.assertRaises(AoVResourceError):
            decrypt_aov_resource("LobbyBank.bytes", encrypted)

    def test_unsupported_mses_version_is_rejected(self):
        with self.assertRaises(AoVResourceError):
            parse_mses_header(b"MSES" + (8).to_bytes(4, "little") + b"\x01" * 8)


if __name__ == "__main__":
    unittest.main()
