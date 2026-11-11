import asyncio
import io
import unittest
import zipfile

from starlette.requests import Request

from app import main


def _request(payload: bytes) -> Request:
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


def _zip_bytes(path: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(path, payload)
    return buffer.getvalue()


class UploadMergeFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_upload_to_downloadable_merged_zip(self):
        first = _zip_bytes("docs/readme.txt", b"hello")
        second = _zip_bytes("docs/license.txt", b"license")
        started = await main.start_chunked_upload({
            "files": [
                {"name": "first.zip", "size": len(first)},
                {"name": "second.zip", "size": len(second)},
            ]
        })
        upload_id = started["upload_id"]

        try:
            for index, payload in enumerate((first, second)):
                result = await main.upload_chunk(
                    upload_id, index, _request(payload), 0
                )
                self.assertEqual(result["received"], len(payload))

            status = await main.chunked_upload_status(upload_id)
            self.assertTrue(all(
                item["received"] == item["size"] for item in status["files"]
            ))

            response = await main.merge_chunked_upload(upload_id)
            self.assertEqual(response.media_type, "application/zip")
            with zipfile.ZipFile(response.path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"docs/readme.txt", "docs/license.txt"},
                )
                self.assertEqual(archive.read("docs/readme.txt"), b"hello")
                self.assertEqual(archive.read("docs/license.txt"), b"license")
                self.assertIsNone(archive.testzip())

            self.assertNotIn(upload_id, main.UPLOAD_SESSIONS)
            if response.background is not None:
                await response.background()
        finally:
            session = main.UPLOAD_SESSIONS.pop(upload_id, None)
            if session:
                main.shutil.rmtree(session["workdir"], ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
