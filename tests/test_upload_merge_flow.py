import asyncio
import io
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


def _zip_entries_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, payload in entries.items():
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

    def test_http_upload_resume_uses_server_offset_and_accepts_identical_retry(self):
        payload = _zip_bytes("docs/resume.txt", b"resumable upload")
        split = len(payload) // 2
        first_chunk, second_chunk = payload[:split], payload[split:]
        with TestClient(main.app) as client:
            started = client.post("/api/upload/start", json={
                "files": [{"name": "resume.zip", "size": len(payload)}]
            })
            self.assertEqual(started.status_code, 200)
            upload_id = started.json()["upload_id"]

            first = client.put(
                f"/api/upload/{upload_id}/0",
                content=first_chunk,
                headers={"X-Chunk-Offset": "0"},
            )
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["received"], split)

            status = client.get(f"/api/upload/{upload_id}/status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["files"][0]["received"], split)

            retry = client.put(
                f"/api/upload/{upload_id}/0",
                content=first_chunk,
                headers={"X-Chunk-Offset": "0"},
            )
            self.assertEqual(retry.status_code, 200)
            self.assertEqual(retry.json()["received"], split)

            resumed = client.put(
                f"/api/upload/{upload_id}/0",
                content=second_chunk,
                headers={"X-Chunk-Offset": str(split)},
            )
            self.assertEqual(resumed.status_code, 200)
            self.assertEqual(resumed.json()["received"], len(payload))

            merged = client.post(f"/api/upload/{upload_id}/merge")
            self.assertEqual(merged.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(merged.content)) as archive:
                self.assertEqual(archive.read("docs/resume.txt"), b"resumable upload")
                self.assertIsNone(archive.testzip())
            self.assertNotIn(upload_id, main.UPLOAD_SESSIONS)

    def test_aov_preflight_classifies_multi_wrapper_inputs_and_keeps_session(self):
        payloads = [
            _zip_entries_bytes({"Resources.zip": b"mod9", "DanhSachSkin.txt": b"skin9"}),
            _zip_entries_bytes({"Resources.zip": b"mod10", "DanhSachSkin.txt": b"skin10"}),
            _zip_bytes(
                "Resources/1.64.1/assetbundle/battle/hero/593_tamyn.assetbundle",
                b"Actor 593",
            ),
            _zip_bytes("Resources/1.64.1/Thanos/HeroSkin_2.bytes", b"VTK IFix"),
            _zip_bytes(
                "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle",
                b"Actor 595",
            ),
        ]
        names = ["9-skin.zip", "10-skin.zip", "tamyn.zip", "vtk-part1.zip", "vtk-part2.zip"]
        with TestClient(main.app) as client:
            started = client.post("/api/upload/start", json={
                "files": [
                    {"name": name, "size": len(payload)}
                    for name, payload in zip(names, payloads)
                ]
            })
            self.assertEqual(started.status_code, 200)
            upload_id = started.json()["upload_id"]

            for index, payload in enumerate(payloads):
                uploaded = client.put(
                    f"/api/upload/{upload_id}/{index}",
                    content=payload,
                    headers={"X-Chunk-Offset": "0"},
                )
                self.assertEqual(uploaded.status_code, 200)

            response = client.post(f"/api/upload/{upload_id}/aov-preflight")
            self.assertEqual(response.status_code, 200)
            report = response.json()
            self.assertFalse(report["ready_to_merge"])
            self.assertEqual(report["role_counts"]["aov_skin_mod_container_candidate"], 2)
            self.assertEqual(report["actor_asset_ids"], ["593", "595"])
            self.assertIn(upload_id, main.UPLOAD_SESSIONS)

            session = main.UPLOAD_SESSIONS.pop(upload_id, None)
            if session:
                main.shutil.rmtree(session["workdir"], ignore_errors=True)

    def test_notice_scan_endpoint_separates_startup_candidates_and_opaque_resources(self):
        package = _zip_entries_bytes({
            "Lua_Signed/AOV/Lobby/GameEntryControl_lua.bytes":
                bytes.fromhex("4fe3a00b") + b"opaque-startup-script",
            "Lua_Signed/AOV/Lobby/LoginNotice.lua":
                b"function ShowLoginNotice() end",
        })
        resources = _zip_entries_bytes({
            "Resources/1.64.1/StableSystems_3.pkg.bytes": package,
        })
        wrapper = _zip_entries_bytes({"Resources.zip": resources})

        with TestClient(main.app) as client:
            started = client.post("/api/upload/start", json={
                "files": [{"name": "startup-wrapper.zip", "size": len(wrapper)}]
            })
            self.assertEqual(started.status_code, 200)
            upload_id = started.json()["upload_id"]
            try:
                uploaded = client.put(
                    f"/api/upload/{upload_id}/0",
                    content=wrapper,
                    headers={"X-Chunk-Offset": "0"},
                )
                self.assertEqual(uploaded.status_code, 200)

                response = client.post(
                    f"/api/upload/{upload_id}/notice-scan"
                )
                self.assertEqual(response.status_code, 200)
                report = response.json()

                self.assertEqual(report["startup_notice_candidate_count"], 1)
                self.assertEqual(
                    report["startup_notice_candidates"][0]["resource_role"],
                    "startup_flow_candidate",
                )
                self.assertEqual(
                    report["startup_flow_unsupported_count"], 1
                )
                self.assertTrue(
                    report["startup_flow_unsupported_resources"][0]["path"].endswith(
                        "GameEntryControl_lua.bytes"
                    )
                )
                self.assertIn(upload_id, main.UPLOAD_SESSIONS)
            finally:
                session = main.UPLOAD_SESSIONS.pop(upload_id, None)
                if session:
                    main.shutil.rmtree(session["workdir"], ignore_errors=True)

    def test_branding_scan_endpoint_returns_candidates_and_keeps_session(self):
        payload = _zip_bytes(
            "Resources/notice.txt",
            (
                "KIANA MOD AOV\n"
                "Mod được tạo bởi youtuber KIANA MOD AOV\n"
                "Web Mod\n"
                "Youtube\n"
            ).encode("utf-8"),
        )
        with TestClient(main.app) as client:
            started = client.post("/api/upload/start", json={
                "files": [{"name": "branding.zip", "size": len(payload)}]
            })
            self.assertEqual(started.status_code, 200)
            upload_id = started.json()["upload_id"]
            try:
                uploaded = client.put(
                    f"/api/upload/{upload_id}/0",
                    content=payload,
                    headers={"X-Chunk-Offset": "0"},
                )
                self.assertEqual(uploaded.status_code, 200)

                response = client.post(
                    f"/api/upload/{upload_id}/branding-scan"
                )
                self.assertEqual(response.status_code, 200)
                report = response.json()
                self.assertEqual(
                    {item["label"] for item in report["candidates"]},
                    {"creator_credit", "brand", "web_mod", "youtube"},
                )
                self.assertIn(upload_id, main.UPLOAD_SESSIONS)
            finally:
                session = main.UPLOAD_SESSIONS.pop(upload_id, None)
                if session:
                    main.shutil.rmtree(session["workdir"], ignore_errors=True)

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



class EnabledWrapperMergeRouteTests(unittest.TestCase):
    def test_aov_merge_endpoint_returns_10skin_named_zip_for_wrapper_adapter(self):
        wrapper_buffer = io.BytesIO()
        with zipfile.ZipFile(wrapper_buffer, "w") as archive:
            archive.writestr("mod/Resources.zip", b"nested mod resources")
            archive.writestr("mod/DanhSachSkin.txt", b"10-skin mod list")
        wrapper = wrapper_buffer.getvalue()
        ifix_part = _zip_bytes(
            "Resources/1.64.1/Thanos/HeroSkin_2.bytes",
            b"VTK IFix payload",
        )
        assets_part = _zip_bytes(
            "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle",
            b"Actor 595 bundle",
        )
        payloads = [wrapper, ifix_part, assets_part]
        names = ["10-skin.zip", "vtk-ifix.zip", "vtk-assets.zip"]

        def fake_merge(_merge_function, _paths, output_path):
            with zipfile.ZipFile(output_path, "w") as archive:
                archive.writestr("validated-candidate.txt", b"structural fixture")
            return {
                "adapter": "aov_mod_to_mod_vtk_actor595_1.64.1",
                "validation": "fixture validation passed",
                "game_tested": False,
                "base_version": "1.64.1.12",
                "vtk_version": "1.64.1.11",
                "assetbundles": 10,
                "new_files": 24,
                "output_bytes": Path(output_path).stat().st_size,
            }

        with TestClient(main.app) as client:
            started = client.post("/api/upload/start", json={
                "files": [
                    {"name": name, "size": len(payload)}
                    for name, payload in zip(names, payloads)
                ]
            })
            self.assertEqual(started.status_code, 200)
            upload_id = started.json()["upload_id"]

            try:
                for index, payload in enumerate(payloads):
                    uploaded = client.put(
                        f"/api/upload/{upload_id}/{index}",
                        content=payload,
                        headers={"X-Chunk-Offset": "0"},
                    )
                    self.assertEqual(uploaded.status_code, 200)

                with patch(
                    "app.main.run_in_threadpool",
                    new=AsyncMock(side_effect=fake_merge),
                ):
                    merged = client.post(
                        f"/api/upload/{upload_id}/aov-merge"
                    )

                self.assertEqual(merged.status_code, 200)
                self.assertIn("application/zip", merged.headers.get("content-type", ""))
                self.assertEqual(
                    merged.headers["X-AOV-Adapter"],
                    "aov_mod_to_mod_vtk_actor595_1.64.1",
                )
                self.assertEqual(
                    merged.headers["X-AOV-Validation"], "passed"
                )
                self.assertEqual(
                    merged.headers["X-AOV-Game-Tested"], "false"
                )
                self.assertIn(
                    "Aki-AOV-10Skin-VanTamKiem.zip",
                    merged.headers.get("content-disposition", ""),
                )
                with zipfile.ZipFile(io.BytesIO(merged.content)) as archive:
                    self.assertEqual(
                        archive.namelist(), ["validated-candidate.txt"]
                    )
                    self.assertIsNone(archive.testzip())
                self.assertNotIn(upload_id, main.UPLOAD_SESSIONS)
            finally:
                session = main.UPLOAD_SESSIONS.pop(upload_id, None)
                if session:
                    main.shutil.rmtree(session["workdir"], ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
