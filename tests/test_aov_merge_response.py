import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import main


class AOVMergeResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_aov_merge_returns_validation_metadata_headers(self):
        with tempfile.TemporaryDirectory() as workdir:
            upload_id = "aov-response-test"
            main.UPLOAD_SESSIONS[upload_id] = {
                "created": main.time.time(),
                "workdir": workdir,
                "files": [{"name": "base.zip", "size": 4, "received": 4}],
                "paths": [str(Path(workdir) / "base.zip")],
            }
            Path(main.UPLOAD_SESSIONS[upload_id]["paths"][0]).write_bytes(b"data")

            def fake_merge(_paths, output_path):
                Path(output_path).write_bytes(b"merged zip")
                return {
                    "validation": {"outer_zip_crc": "passed"},
                    "game_tested": False,
                    "base_version": "1.64.1.12_KPM",
                    "vtk_version": "1.64.1.11",
                    "packer": {"actor_assets_from_vtk": [f"bundle-{i}" for i in range(10)]},
                    "added_resources": [f"resource-{i}" for i in range(24)],
                }

            try:
                with patch(
                    "app.main.run_in_threadpool",
                    new=AsyncMock(side_effect=lambda fn, *args: fake_merge(*args)),
                ):
                    response = await main.merge_aov_chunked_upload(upload_id)

                self.assertEqual(response.headers["X-AOV-Validation"], "passed")
                self.assertEqual(response.headers["X-AOV-Game-Tested"], "false")
                self.assertEqual(response.headers["X-AOV-Base-Version"], "1.64.1.12")
                self.assertEqual(response.headers["X-AOV-VTK-Version"], "1.64.1.11")
                self.assertEqual(response.headers["X-AOV-AssetBundles"], "10")
                self.assertEqual(response.headers["X-AOV-New-Files"], "24")
                self.assertEqual(response.filename, "Aki-AOV-KPM100-VTK.zip")
                self.assertNotIn(upload_id, main.UPLOAD_SESSIONS)
            finally:
                main.UPLOAD_SESSIONS.pop(upload_id, None)


if __name__ == "__main__":
    unittest.main()
