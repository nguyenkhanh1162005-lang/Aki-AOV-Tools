import asyncio
import io
import unittest
import zipfile
import unittest

from fastapi.testclient import TestClient
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



class HTTPUploadMergeFlowTests(unittest.TestCase):
    def test_http_upload_merge_and_download_round_trip(self):
        first = _zip_bytes("docs/readme.txt", b"hello")
        second = _zip_bytes("docs/license.txt", b"license")
        with TestClient(main.app) as client:
            started = client.post("/api/upload/start", json={
                "files": [
                    {"name": "first.zip", "size": len(first)},
                    {"name": "second.zip", "size": len(second)},
                ]
            })
            self.assertEqual(started.status_code, 200)
            upload_id = started.json()["upload_id"]

            for index, payload in enumerate((first, second)):
                uploaded = client.put(
                    f"/api/upload/{upload_id}/{index}",
                    content=payload,
                    headers={"X-Chunk-Offset": "0"},
                )
                self.assertEqual(uploaded.status_code, 200)
                self.assertEqual(uploaded.json()["received"], len(payload))

            merged = client.post(f"/api/upload/{upload_id}/merge")
            self.assertEqual(merged.status_code, 200)
            self.assertIn("application/zip", merged.headers.get("content-type", ""))
            with zipfile.ZipFile(io.BytesIO(merged.content)) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"docs/readme.txt", "docs/license.txt"},
                )
                self.assertEqual(archive.read("docs/readme.txt"), b"hello")
                self.assertEqual(archive.read("docs/license.txt"), b"license")
                self.assertIsNone(archive.testzip())

            self.assertNotIn(upload_id, main.UPLOAD_SESSIONS)

    def test_http_merge_rejects_incomplete_upload_without_discarding_session(self):
        payload = _zip_bytes("docs/readme.txt", b"hello")
        with TestClient(main.app) as client:
            started = client.post("/api/upload/start", json={
                "files": [{"name": "first.zip", "size": len(payload)}]
            })
            self.assertEqual(started.status_code, 200)
            upload_id = started.json()["upload_id"]

            merged = client.post(f"/api/upload/{upload_id}/merge")
            self.assertEqual(merged.status_code, 400)
            self.assertIn(upload_id, main.UPLOAD_SESSIONS)

            session = main.UPLOAD_SESSIONS.pop(upload_id, None)
            if session:
                main.shutil.rmtree(session["workdir"], ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
