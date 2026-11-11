import asyncio
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from fastapi import HTTPException

from app import main


class CompatibilityUploadAPITests(unittest.TestCase):
    def setUp(self):
        self._original_sessions = dict(main.UPLOAD_SESSIONS)

    def tearDown(self):
        for upload_id, session in list(main.UPLOAD_SESSIONS.items()):
            if upload_id not in self._original_sessions:
                main.shutil.rmtree(session["workdir"], ignore_errors=True)
                main.UPLOAD_SESSIONS.pop(upload_id, None)

    def _register_upload(self, path, *, received=None):
        upload_id = "compatibility-api-test-" + path.stem
        size = path.stat().st_size
        main.UPLOAD_SESSIONS[upload_id] = {
            "created": time.time(),
            "workdir": str(path.parent),
            "files": [{
                "name": path.name,
                "size": size,
                "received": size if received is None else received,
            }],
            "paths": [str(path)],
        }
        return upload_id

    def test_completed_upload_returns_read_only_manifest_and_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            archive_path = folder / "docs.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("docs/readme.txt", b"hello")
            original_bytes = archive_path.read_bytes()
            upload_id = self._register_upload(archive_path)

            result = asyncio.run(main.inspect_chunked_compatibility(upload_id))

            self.assertTrue(result["manifest"]["read_only"])
            self.assertFalse(result["manifest"]["payloads_modified"])
            self.assertEqual(result["manifest"]["resource_count"], 1)
            self.assertEqual(
                result["compatibility_plan"]["resources"][0]["status"],
                "SAFE_MERGE",
            )
            self.assertTrue(result["compatibility_plan"]["advisory_only"])
            self.assertFalse(result["compatibility_plan"]["merge_authorized"])
            self.assertIn("không giải mã", result["warning"])
            self.assertEqual(archive_path.read_bytes(), original_bytes)
            self.assertIn(upload_id, main.UPLOAD_SESSIONS)

    def test_api_reports_archive_wide_version_conflict_from_zip_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            paths = []
            for name, resource_name in (
                ("skin-1.64.1.12-ios.zip", "skin-a.bytes"),
                ("skin-1.65.1.1-ios.zip", "skin-b.bytes"),
            ):
                archive_path = folder / name
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(resource_name, b"opaque")
                paths.append(archive_path)

            upload_id = "compatibility-api-multi-source"
            main.UPLOAD_SESSIONS[upload_id] = {
                "created": time.time(),
                "workdir": str(folder),
                "files": [
                    {"name": path.name, "size": path.stat().st_size,
                     "received": path.stat().st_size}
                    for path in paths
                ],
                "paths": [str(path) for path in paths],
            }

            result = asyncio.run(main.inspect_chunked_compatibility(upload_id))

            compatibility = result["compatibility_plan"]["source_compatibility"]
            self.assertEqual(compatibility["status"], "VERSION_CONFLICT")
            self.assertEqual(
                compatibility["game_versions"], ["1.64.1.12", "1.65.1.1"]
            )
            self.assertIn(upload_id, main.UPLOAD_SESSIONS)

    def test_incomplete_upload_is_rejected_before_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "partial.zip"
            archive_path.write_bytes(b"partial")
            upload_id = self._register_upload(archive_path, received=1)

            with self.assertRaises(HTTPException) as raised:
                asyncio.run(main.inspect_chunked_compatibility(upload_id))

            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("chưa tải lên đầy đủ", raised.exception.detail)

    def test_invalid_zip_is_reported_as_client_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "invalid.zip"
            archive_path.write_bytes(b"not a zip")
            upload_id = self._register_upload(archive_path)

            with self.assertRaises(HTTPException) as raised:
                asyncio.run(main.inspect_chunked_compatibility(upload_id))

            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("Không mở được ZIP", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
