from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Body, Header, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .large_merger import MAX_FILES, MAX_INPUT_BYTES
from .mod_pipeline import merge_mod_archives, ModPipelineError
from .aov_inspector import inspect_archives, InspectError
from .drive_import import download_drive_zip, DriveImportError
from .aov_merger import merge_aov_archives, AOVMergeError
from .assetbundle_inspector import verify_assetbundle_roundtrip, AssetBundleInspectError, MAX_ASSETBUNDLE_BYTES

app = FastAPI(title="Aki AOV Tools", version="0.2.0")
WEB = Path(__file__).resolve().parent.parent / "web"
CHUNK_SIZE = 1024 * 1024
UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024
UPLOAD_SESSIONS: dict[str, dict] = {}
UPLOAD_TTL_SECONDS = 3600
MAX_ACTIVE_UPLOAD_SESSIONS = 8


def _cleanup_expired_upload_sessions(now: float | None = None) -> int:
    """Remove abandoned upload directories so public web uploads cannot pile up forever."""
    current = time.time() if now is None else now
    expired = [
        upload_id
        for upload_id, session in UPLOAD_SESSIONS.items()
        if current - session["created"] > UPLOAD_TTL_SECONDS
    ]
    for upload_id in expired:
        session = UPLOAD_SESSIONS.pop(upload_id, None)
        if session:
            shutil.rmtree(session["workdir"], ignore_errors=True)
    return len(expired)


def _ensure_upload_capacity() -> None:
    _cleanup_expired_upload_sessions()
    if len(UPLOAD_SESSIONS) >= MAX_ACTIVE_UPLOAD_SESSIONS:
        raise HTTPException(
            429,
            "Máy chủ đang có quá nhiều phiên tải file. Hãy thử lại sau ít phút.",
        )


def _get_upload_session(upload_id: str) -> dict:
    session = UPLOAD_SESSIONS.get(upload_id)
    if not session or time.time() - session["created"] > UPLOAD_TTL_SECONDS:
        if session:
            shutil.rmtree(session["workdir"], ignore_errors=True)
            UPLOAD_SESSIONS.pop(upload_id, None)
        raise HTTPException(404, "Phiên tải lên không tồn tại hoặc đã hết hạn. Hãy chọn file lại.")
    return session


@app.post("/api/upload/start")
async def start_chunked_upload(payload: dict = Body(...)):
    _ensure_upload_capacity()
    files = payload.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"Chọn từ 1 đến {MAX_FILES} file ZIP.")
    total = 0
    clean_files = []
    for item in files:
        if not isinstance(item, dict):
            raise HTTPException(400, "Thông tin file ZIP không hợp lệ.")
        name = Path(str(item.get("name", ""))).name
        size = item.get("size")
        if (
            not name.lower().endswith(".zip")
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
        ):
            raise HTTPException(400, "Thông tin file ZIP không hợp lệ.")
        total += size
        clean_files.append({"name": name, "size": size, "received": 0})
    if total > MAX_INPUT_BYTES:
        raise HTTPException(413, "Tổng dung lượng ZIP vượt quá 500 MB.")
    upload_id = uuid.uuid4().hex
    workdir = Path(tempfile.mkdtemp(prefix="aki-aov-chunks-"))
    UPLOAD_SESSIONS[upload_id] = {
        "created": time.time(), "workdir": str(workdir), "files": clean_files,
        "paths": [str(workdir / f"{i}_{item['name']}") for i, item in enumerate(clean_files, 1)]
    }
    return {"upload_id": upload_id, "chunk_size": UPLOAD_CHUNK_BYTES}


@app.get("/api/upload/{upload_id}/status")
async def chunked_upload_status(upload_id: str):
    """Return received offsets so a browser can resume an interrupted upload."""
    session = _get_upload_session(upload_id)
    session["created"] = time.time()
    return {
        "upload_id": upload_id,
        "chunk_size": UPLOAD_CHUNK_BYTES,
        "files": [
            {"name": item["name"], "size": item["size"], "received": item["received"]}
            for item in session["files"]
        ],
        "expires_in_seconds": UPLOAD_TTL_SECONDS,
    }


@app.put("/api/upload/{upload_id}/{file_index}")
async def upload_chunk(upload_id: str, file_index: int, request: Request, chunk_offset: int = Header(..., alias="X-Chunk-Offset")):
    session = _get_upload_session(upload_id)
    if chunk_offset < 0:
        raise HTTPException(400, "Vị trí khối tải lên không hợp lệ.")
    if file_index < 0 or file_index >= len(session["files"]):
        raise HTTPException(404, "Không tìm thấy file trong phiên tải lên.")
    item = session["files"][file_index]
    chunk_buffer = bytearray()
    async for part in request.stream():
        chunk_buffer.extend(part)
        if len(chunk_buffer) > UPLOAD_CHUNK_BYTES:
            raise HTTPException(413, "Mỗi khối tải lên không được vượt quá 4 MB.")
    chunk = bytes(chunk_buffer)
    if not chunk:
        raise HTTPException(400, "Kích thước khối tải lên không hợp lệ.")
    offset = item["received"]
    if chunk_offset < offset:
        # Idempotent retry: accept an already-written identical chunk.
        if chunk_offset + len(chunk) > offset:
            raise HTTPException(409, "Khối tải lên bị chồng lấn, hãy chọn lại file.")
        with open(session["paths"][file_index], "rb") as existing:
            existing.seek(chunk_offset)
            if existing.read(len(chunk)) != chunk:
                raise HTTPException(409, "Khối gửi lại khác dữ liệu đã nhận.")
        return {"received": offset, "size": item["size"]}
    if chunk_offset != offset:
        raise HTTPException(409, "Sai vị trí khối tải lên, hãy thử lại.")
    if offset + len(chunk) > item["size"]:
        raise HTTPException(400, "Khối tải lên vượt quá kích thước file.")
    with open(session["paths"][file_index], "ab") as target:
        target.write(chunk)
    item["received"] += len(chunk)
    session["created"] = time.time()
    return {"received": item["received"], "size": item["size"]}


@app.post("/api/upload/{upload_id}/inspect")
async def inspect_chunked_upload(upload_id: str, keep_session: bool = Query(False)):
    session = _get_upload_session(upload_id)
    if any(item["received"] != item["size"] for item in session["files"]):
        raise HTTPException(400, "File chưa tải lên đầy đủ.")
    try:
        report = await run_in_threadpool(inspect_archives, session["paths"])
        from fastapi.responses import PlainTextResponse
        if keep_session:
            session["created"] = time.time()
        return PlainTextResponse(report)
    except InspectError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if not keep_session:
            shutil.rmtree(session["workdir"], ignore_errors=True)
            UPLOAD_SESSIONS.pop(upload_id, None)


@app.post("/api/upload/{upload_id}/merge")
async def merge_chunked_upload(upload_id: str):
    session = _get_upload_session(upload_id)
    if any(item["received"] != item["size"] for item in session["files"]):
        raise HTTPException(400, "File chưa tải lên đầy đủ.")
    output_path = Path(session["workdir"]) / "aki-mod-merged.zip"
    try:
        await run_in_threadpool(merge_mod_archives, session["paths"], str(output_path))
    except ModPipelineError as exc:
        raise HTTPException(400, str(exc)) from exc
    UPLOAD_SESSIONS.pop(upload_id, None)
    return FileResponse(
        path=output_path,
        media_type="application/zip",
        filename="aki-mod-merged.zip",
        background=BackgroundTask(shutil.rmtree, session["workdir"], ignore_errors=True),
    )


@app.post("/api/upload/{upload_id}/aov-merge")
async def merge_aov_chunked_upload(upload_id: str):
    session = _get_upload_session(upload_id)
    if any(item["received"] != item["size"] for item in session["files"]):
        raise HTTPException(400, "File chưa tải lên đầy đủ.")
    output_path = Path(session["workdir"]) / "Aki-AOV-10Skin-VanTamKiem.zip"
    try:
        report = await run_in_threadpool(merge_aov_archives, session["paths"], str(output_path))
    except AOVMergeError as exc:
        raise HTTPException(400, str(exc)) from exc
    UPLOAD_SESSIONS.pop(upload_id, None)
    result_headers = {
        "X-AOV-Validation": "passed" if report.get("validation") else "incomplete",
        "X-AOV-Game-Tested": "true" if report.get("game_tested") else "false",
        "X-AOV-Base-Version": str(report.get("base_version", "unknown")),
        "X-AOV-VTK-Version": str(report.get("vtk_version", "unknown")),
        "X-AOV-AssetBundles": str(report.get("assetbundles", 0)),
        "X-AOV-New-Files": str(report.get("new_files", 0)),
    }
    return FileResponse(
        path=output_path,
        media_type="application/zip",
        filename="Aki-AOV-10Skin-VanTamKiem.zip",
        headers=result_headers,
        background=BackgroundTask(shutil.rmtree, session["workdir"], ignore_errors=True),
    )


@app.post("/api/drive/import")
async def import_drive_links(payload: dict = Body(...)):
    links = payload.get("links")
    if not isinstance(links, list) or not links or not all(
        isinstance(link, str) and link.strip() for link in links
    ):
        raise HTTPException(400, "Dán ít nhất một link Google Drive hợp lệ.")
    if len(links) > MAX_FILES:
        raise HTTPException(400, f"Tối đa {MAX_FILES} link Google Drive.")

    existing_upload_id = payload.get("upload_id")
    is_new_session = not existing_upload_id
    if is_new_session:
        _ensure_upload_capacity()
    if existing_upload_id:
        upload_id = str(existing_upload_id)
        session = _get_upload_session(upload_id)
        if any(item["received"] != item["size"] for item in session["files"]):
            raise HTTPException(400, "ZIP tải từ thiết bị chưa được tải lên đầy đủ.")
        workdir = Path(session["workdir"])
        files = session["files"]
        paths = session["paths"]
        original_count = len(files)
        total_bytes = sum(item["size"] for item in files)
        if original_count + len(links) > MAX_FILES:
            raise HTTPException(400, f"Tổng số ZIP không được vượt quá {MAX_FILES}.")
    else:
        upload_id = uuid.uuid4().hex
        workdir = Path(tempfile.mkdtemp(prefix="aki-aov-drive-"))
        files = []
        paths = []
        original_count = 0
        total_bytes = 0
        session = None

    added_paths = []
    try:
        for link in links:
            index = len(files) + 1
            destination = workdir / f"{index}_drive-import.zip"
            try:
                name, size = await run_in_threadpool(download_drive_zip, link, str(destination))
            except DriveImportError as exc:
                raise HTTPException(400, str(exc)) from exc
            total_bytes += size
            if total_bytes > MAX_INPUT_BYTES:
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "Tổng dung lượng ZIP đầu vào vượt quá 500 MB.")
            files.append({"name": name, "size": size, "received": size})
            paths.append(str(destination))
            added_paths.append(destination)

        if is_new_session:
            UPLOAD_SESSIONS[upload_id] = {
                "created": time.time(),
                "workdir": str(workdir),
                "files": files,
                "paths": paths,
            }
        else:
            session["created"] = time.time()
        return {
            "upload_id": upload_id,
            "files": [{"name": item["name"], "size": item["size"]} for item in files],
            "expires_in_seconds": UPLOAD_TTL_SECONDS,
        }
    except Exception:
        del files[original_count:]
        del paths[original_count:]
        for path in added_paths:
            Path(path).unlink(missing_ok=True)
        if is_new_session:
            shutil.rmtree(workdir, ignore_errors=True)
        raise


@app.get("/health")
def health():
    return {"status": "ok", "service": "Aki AOV Tools"}


@app.post("/api/assetbundle/roundtrip-check")
async def roundtrip_check_assetbundle(file: UploadFile = File(...)):
    """Check whether a UnityFS bundle survives an unchanged save/reopen cycle."""
    data = bytearray()
    try:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            if len(data) + len(chunk) > MAX_ASSETBUNDLE_BYTES:
                raise HTTPException(413, "AssetBundle vượt quá giới hạn kiểm tra round-trip 64 MiB.")
            data.extend(chunk)
        try:
            return await run_in_threadpool(verify_assetbundle_roundtrip, bytes(data))
        except AssetBundleInspectError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        await file.close()


@app.post("/api/merge")
async def merge(files: list[UploadFile] = File(...)):
    if not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"Chọn từ 1 đến {MAX_FILES} file ZIP.")

    workdir = Path(tempfile.mkdtemp(prefix="aki-aov-web-"))
    input_paths: list[str] = []
    total_bytes = 0
    try:
        for index, upload in enumerate(files, start=1):
            filename = upload.filename or ""
            if not filename.lower().endswith(".zip"):
                raise HTTPException(400, "Chỉ chấp nhận file .zip.")

            destination = workdir / f"{index}_{Path(filename).name}"
            with destination.open("wb") as target:
                while True:
                    chunk = await upload.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_INPUT_BYTES:
                        raise HTTPException(413, "Tổng dung lượng ZIP vượt quá 500 MB.")
                    target.write(chunk)
            await upload.close()
            input_paths.append(str(destination))

        output_path = workdir / "aki-mod-merged.zip"
        try:
            await run_in_threadpool(merge_mod_archives, input_paths, str(output_path))
        except ModPipelineError as exc:
            raise HTTPException(400, str(exc)) from exc

        return FileResponse(
            path=output_path,
            media_type="application/zip",
            filename="aki-mod-merged.zip",
            background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
        )
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise



@app.post("/api/inspect")
async def inspect(files: list[UploadFile] = File(...)):
    if not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"Chọn từ 1 đến {MAX_FILES} file ZIP.")
    workdir = Path(tempfile.mkdtemp(prefix="aki-aov-inspect-"))
    input_paths: list[str] = []
    total_bytes = 0
    try:
        for index, upload in enumerate(files, start=1):
            filename = upload.filename or ""
            if not filename.lower().endswith(".zip"):
                raise HTTPException(400, "Chỉ chấp nhận file .zip.")
            destination = workdir / f"{index}_{Path(filename).name}"
            with destination.open("wb") as target:
                while True:
                    chunk = await upload.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_INPUT_BYTES:
                        raise HTTPException(413, "Tổng dung lượng ZIP vượt quá 500 MB.")
                    target.write(chunk)
            await upload.close()
            input_paths.append(str(destination))
        try:
            report = await run_in_threadpool(inspect_archives, input_paths)
        except InspectError as exc:
            raise HTTPException(400, str(exc)) from exc
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(report)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
