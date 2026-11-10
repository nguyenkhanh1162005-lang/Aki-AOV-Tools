import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request

from app import main


def make_request(payload: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": "/upload",
        "raw_path": b"/upload",
        "query_string": b"",
        "headers": [],
        "client": ("test", 1234),
        "server": ("test", 80),
    }
    return Request(scope, receive)


class ChunkedUploadEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.upload_id = "test-upload"
        self.path = Path(self.temp.name) / "1_test.zip"
        main.UPLOAD_SESSIONS[self.upload_id] = {
            "created": main.time.time(),
            "workdir": self.temp.name,
            "files": [{"name": "test.zip", "size": 8, "received": 0}],
            "paths": [str(self.path)],
        }

    async def asyncTearDown(self):
        main.UPLOAD_SESSIONS.pop(self.upload_id, None)
        self.temp.cleanup()

    async def test_chunk_is_written_and_offset_is_updated(self):
        result = await main.upload_chunk(
            self.upload_id, 0, make_request(b"zipdata!"), 0
        )
        self.assertEqual(result["received"], 8)
        self.assertEqual(self.path.read_bytes(), b"zipdata!")

    async def test_oversized_chunk_is_rejected_while_streaming(self):
        with patch("app.main.UPLOAD_CHUNK_BYTES", 3):
            with self.assertRaises(HTTPException) as raised:
                await main.upload_chunk(
                    self.upload_id, 0, make_request(b"four"), 0
                )
        self.assertEqual(raised.exception.status_code, 413)
        self.assertFalse(self.path.exists())
        self.assertEqual(
            main.UPLOAD_SESSIONS[self.upload_id]["files"][0]["received"], 0
        )


if __name__ == "__main__":
    unittest.main()
