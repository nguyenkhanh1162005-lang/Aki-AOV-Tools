import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import main


class _ChunkRequest:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def stream(self):
        yield self.payload


class UploadSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = dict(main.UPLOAD_SESSIONS)
        main.UPLOAD_SESSIONS.clear()

    def tearDown(self):
        for session in main.UPLOAD_SESSIONS.values():
            import shutil
            shutil.rmtree(session["workdir"], ignore_errors=True)
        main.UPLOAD_SESSIONS.clear()
        main.UPLOAD_SESSIONS.update(self.original)

    def _session(self, created):
        directory = tempfile.mkdtemp(prefix="aki-test-session-")
        return {"created": created, "workdir": directory, "files": [], "paths": []}

    def test_expired_sessions_are_removed_from_registry_and_disk(self):
        session = self._session(time.time() - main.UPLOAD_TTL_SECONDS - 1)
        path = Path(session["workdir"])
        main.UPLOAD_SESSIONS["expired"] = session

        removed = main._cleanup_expired_upload_sessions()

        self.assertEqual(removed, 1)
        self.assertNotIn("expired", main.UPLOAD_SESSIONS)
        self.assertFalse(path.exists())

    def test_active_session_limit_returns_429(self):
        session = self._session(time.time())
        main.UPLOAD_SESSIONS["active"] = session
        with patch.object(main, "MAX_ACTIVE_UPLOAD_SESSIONS", 1):
            with self.assertRaises(HTTPException) as raised:
                main._ensure_upload_capacity()
        self.assertEqual(raised.exception.status_code, 429)

    async def test_upload_status_reports_offsets_and_refreshes_session(self):
        directory = tempfile.mkdtemp(prefix="aki-test-status-")
        path = Path(directory) / "1_sample.zip"
        path.write_bytes(b"abc")
        main.UPLOAD_SESSIONS["resume-me"] = {
            "created": time.time() - 30,
            "workdir": directory,
            "files": [{"name": "sample.zip", "size": 8, "received": 3}],
            "paths": [str(path)],
        }

        result = await main.chunked_upload_status("resume-me")

        self.assertEqual(result["upload_id"], "resume-me")
        self.assertEqual(result["files"], [
            {
                "name": "sample.zip",
                "size": 8,
                "received": 3,
                "received_chunk_sha256": [],
            }
        ])
        self.assertEqual(result["chunk_size"], main.UPLOAD_CHUNK_BYTES)
        self.assertLess(time.time() - main.UPLOAD_SESSIONS["resume-me"]["created"], 2)

    async def test_chunk_upload_start_rejects_non_object_file_metadata(self):
        with self.assertRaises(HTTPException) as raised:
            await main.start_chunked_upload({"files": ["not-an-object"]})
        self.assertEqual(raised.exception.status_code, 400)

    async def test_chunk_upload_start_rejects_empty_zip(self):
        with self.assertRaises(HTTPException) as raised:
            await main.start_chunked_upload(
                {"files": [{"name": "empty.zip", "size": 0}]}
            )
        self.assertEqual(raised.exception.status_code, 400)

    async def test_chunk_upload_start_creates_session_for_valid_zip(self):
        result = await main.start_chunked_upload(
            {"files": [{"name": "sample.zip", "size": 123, "chunk_sha256": []}]}
        )
        session = main.UPLOAD_SESSIONS[result["upload_id"]]
        self.assertEqual(result["chunk_size"], main.UPLOAD_CHUNK_BYTES)
        self.assertEqual(session["files"][0]["size"], 123)
        self.assertEqual(session["files"][0]["received_chunk_sha256"], [])
        self.assertTrue(Path(session["workdir"]).exists())

    async def test_chunk_upload_verifies_inline_sha256_and_exposes_resume_hash(self):
        payload = b"one verified upload chunk"
        digest = hashlib.sha256(payload).hexdigest()
        directory = tempfile.mkdtemp(prefix="aki-test-inline-hash-")
        path = Path(directory) / "1_sample.zip"
        main.UPLOAD_SESSIONS["inline-hash"] = {
            "created": time.time(),
            "workdir": directory,
            "files": [{
                "name": "sample.zip",
                "size": len(payload),
                "received": 0,
                "chunk_sha256": [],
                "received_chunk_sha256": [],
            }],
            "paths": [str(path)],
        }

        with self.assertRaises(HTTPException) as raised:
            await main.upload_chunk(
                "inline-hash", 0, _ChunkRequest(payload), 0, "0" * 64
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(main.UPLOAD_SESSIONS["inline-hash"]["files"][0]["received"], 0)

        uploaded = await main.upload_chunk(
            "inline-hash", 0, _ChunkRequest(payload), 0, digest
        )
        self.assertEqual(uploaded["received"], len(payload))
        self.assertEqual(
            main.UPLOAD_SESSIONS["inline-hash"]["files"][0]["received_chunk_sha256"],
            [digest],
        )
        status = await main.chunked_upload_status("inline-hash")
        self.assertEqual(status["files"][0]["received_chunk_sha256"], [digest])

        retried = await main.upload_chunk(
            "inline-hash", 0, _ChunkRequest(payload), 0, digest
        )
        self.assertEqual(retried["received"], len(payload))


if __name__ == "__main__":
    unittest.main()
