import unittest

from app.access_control import is_allowed_user


class AccessControlTests(unittest.TestCase):
    def test_allows_configured_user(self):
        self.assertTrue(is_allowed_user(123456789, "123456789,987654321"))

    def test_rejects_unlisted_user(self):
        self.assertFalse(is_allowed_user(123456789, "987654321"))

    def test_fails_closed_when_unconfigured(self):
        self.assertFalse(is_allowed_user(123456789, ""))

    def test_ignores_invalid_entries(self):
        self.assertFalse(is_allowed_user(123456789, "abc, 456"))


if __name__ == "__main__":
    unittest.main()
