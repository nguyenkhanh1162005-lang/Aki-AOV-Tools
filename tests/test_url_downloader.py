import unittest
from unittest.mock import patch

import httpx

from app.url_downloader import DownloadError, _filename, _require_public_https


class UrlDownloaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_https_links(self):
        with self.assertRaises(DownloadError):
            await _require_public_https("http://example.com/file.zip")

    async def test_rejects_loopback_addresses(self):
        with patch(
            "app.url_downloader.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
        ):
            with self.assertRaises(DownloadError):
                await _require_public_https("https://localhost/file.zip")

    def test_uses_content_disposition_filename(self):
        response = httpx.Response(
            200,
            headers={"content-disposition": "attachment; filename=pack.zip"},
        )
        self.assertEqual(_filename(response, "https://files.example/download"), "pack.zip")

    def test_rejects_non_zip_filename(self):
        response = httpx.Response(200)
        with self.assertRaises(DownloadError):
            _filename(response, "https://files.example/readme.txt")


if __name__ == "__main__":
    unittest.main()
