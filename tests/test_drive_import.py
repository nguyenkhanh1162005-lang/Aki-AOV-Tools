import unittest

from app.drive_import import DriveImportError, _DriveFormParser, _extract_file_id


class DriveImportTests(unittest.TestCase):
    def test_extracts_standard_shared_file_link(self):
        self.assertEqual(
            _extract_file_id("https://drive.google.com/file/d/1JpKeA82ha9CmCeGL1pmAEfBhQWr19Mxe/view?usp=sharing"),
            "1JpKeA82ha9CmCeGL1pmAEfBhQWr19Mxe",
        )

    def test_extracts_open_link(self):
        self.assertEqual(
            _extract_file_id("https://drive.google.com/open?id=Abcdefghij_123"),
            "Abcdefghij_123",
        )

    def test_rejects_non_google_host(self):
        with self.assertRaises(DriveImportError):
            _extract_file_id("https://example.com/file/d/Abcdefghij")

    def test_rejects_non_https_link(self):
        with self.assertRaises(DriveImportError):
            _extract_file_id("http://drive.google.com/file/d/Abcdefghij/view")

    def test_rejects_malformed_file_id(self):
        with self.assertRaises(DriveImportError):
            _extract_file_id("https://drive.google.com/file/d/short/view")

    def test_reads_confirmation_form(self):
        parser = _DriveFormParser()
        parser.feed('<form action="/uc?export=download"><input type="hidden" name="confirm" value="token"></form>')
        self.assertEqual(parser.forms, [("/uc?export=download", {"confirm": "token"})])


if __name__ == "__main__":
    unittest.main()
