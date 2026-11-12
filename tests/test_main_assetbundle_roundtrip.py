import io
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.main import roundtrip_check_assetbundle
from app.assetbundle_inspector import verify_assetbundle_roundtrip


class AssetBundleRoundtripEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_returns_roundtrip_verification_result(self):
        payload = b"UnityFS\\x00sample"
        upload = UploadFile(
            filename="sample.assetbundle",
            file=io.BytesIO(payload),
        )
        expected = {
            "verified": True,
            "reason": "Inventory and Object payloads match.",
            "original_size": len(payload),
            "roundtrip_size": len(payload),
        }
        with patch(
            "app.main.run_in_threadpool",
            new=AsyncMock(return_value=expected),
        ) as run:
            result = await roundtrip_check_assetbundle(upload)

        self.assertEqual(result, expected)
        run.assert_awaited_once_with(verify_assetbundle_roundtrip, payload)
        self.assertTrue(upload.file.closed)

    async def test_endpoint_rejects_upload_over_roundtrip_limit(self):
        upload = UploadFile(
            filename="too-large.assetbundle",
            file=io.BytesIO(b"1234"),
        )
        with patch("app.main.MAX_ASSETBUNDLE_BYTES", 3):
            with self.assertRaises(HTTPException) as raised:
                await roundtrip_check_assetbundle(upload)

        self.assertEqual(raised.exception.status_code, 413)
        self.assertTrue(upload.file.closed)


if __name__ == "__main__":
    unittest.main()
