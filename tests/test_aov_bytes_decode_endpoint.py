import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from app import main
from app.aov_bytes import encrypt_aes_zstd_resource


class AOVBytesDecodeEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_upload_decode_endpoint_returns_downloadable_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            workdir = folder / "session"
            workdir.mkdir()
            source = workdir / "part.zip"
            original = b"decoded-from-web" * 30
            encoded = encrypt_aes_zstd_resource(original, "111_AssetRef")
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("Resources/111_AssetRef.bytes", encoded)
            upload_id = "decode-test"
            main.UPLOAD_SESSIONS[upload_id] = {
                "created": time.time(),
                "workdir": str(workdir),
                "files": [{
                    "name": "part.zip",
                    "size": source.stat().st_size,
                    "received": source.stat().st_size,
                }],
                "paths": [str(source)],
            }

            try:
                response = await main.decode_chunked_aov_bytes(upload_id)
                self.assertEqual(response.filename, "aki-aov-decoded-resources.zip")
                self.assertEqual(response.headers["X-AOV-Decoded-Resources"], "1")
                self.assertEqual(response.headers["X-AOV-Game-Tested"], "false")
                self.assertEqual(response.headers["X-AOV-MSES-Tables"], "0")
                self.assertEqual(response.headers["X-AOV-IFix-Patches"], "0")
                self.assertEqual(response.headers["X-AOV-Nested-Packages"], "0")
                with zipfile.ZipFile(response.path) as archive:
                    decoded_path = next(
                        name for name in archive.namelist()
                        if name.endswith("111_AssetRef.bytes")
                    )
                    self.assertEqual(archive.read(decoded_path), original)
                self.assertNotIn(upload_id, main.UPLOAD_SESSIONS)
                await response.background()
                self.assertFalse(workdir.exists())
            finally:
                main.UPLOAD_SESSIONS.pop(upload_id, None)


if __name__ == "__main__":
    unittest.main()
