from __future__ import annotations

import json
import os
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
from .adapter_registry import get_adapter_capabilities
from .compatibility_planner import CompatibilityPlanError, plan_resource_compatibility
from .resource_manifest import ResourceManifestError, build_resource_manifest
from .aov_bytes_archive import AOVBytesArchiveError, decode_archive_resources

app = FastAPI(title="Aki AOV Tools", version="0.2.0")
WEB = Path(__file__).resolve().parent.parent / "web"
CHUNK_SIZE = 1024 * 1024
UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024
UPLOAD_SESSIONS: dict[str, dict] = {}
UPLOAD_TTL_SECONDS = 3600
MAX_ACTIVE_UPLOAD_SESSIONS = 8
UPLOAD_STORAGE_DIR = (
    Path(os.environ["AOV_UPLOAD_STORAGE_DIR"]).expanduser()
    if os.environ.get("AOV_UPLOAD_STORAGE_DIR")
    else None
)
if UPLOAD_STORAGE_DIR is not None:
    UPLOAD_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _new_upload_workdir(prefix: str) -> Path:
    """Create upload workdirs on the configured persistent volume when enabled."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(UPLOAD_STORAGE_DIR) if UPLOAD_STORAGE_DIR else None))


def _persist_upload_session(upload_id: str, session: dict) -> None:
    if UPLOAD_STORAGE_DIR is None:
        return
    workdir = Path(session["workdir"])
    payload = {
        "schema_version": 1,
        "upload_id": upload_id,
        "created": session["created"],
        "files": session["files"],
        "paths": [Path(path).name for path in session["paths"]],
    }
    metadata = workdir / ".aki-upload-session.json"
    temporary = workdir / ".aki-upload-session.json.tmp"
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, metadata)


def _load_persistent_upload_sessions(storage_dir: Path) -> dict[str, dict]:
    """Restore only well-formed sessions whose files remain inside the storage root."""
    restored: dict[str, dict] = {}
    root = storage_dir.resolve()
    if not storage_dir.exists():
        return restored
    for workdir in storage_dir.iterdir():
        if len(restored) >= MAX_ACTIVE_UPLOAD_SESSIONS:
            break
        if workdir.is_symlink() or not workdir.is_dir():
            continue
        try:
            if workdir.resolve().parent != root:
                continue
            metadata = workdir / ".aki-upload-session.json"
            if metadata.is_symlink() or not metadata.is_file():
                continue
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            upload_id = payload["upload_id"]
            created = payload["created"]
            files = payload["files"]
            path_names = payload["paths"]
            if (
                payload.get("schema_version") != 1
                or not isinstance(upload_id, str)
                or len(upload_id) != 32
                or any(ch not in "0123456789abcdef" for ch in upload_id)
                or workdir.name != upload_id
                or isinstance(created, bool)
                or not isinstance(created, (int, float))
                or not isinstance(files, list)
                or not 1 <= len(files) <= MAX_FILES
                or not isinstance(path_names, list)
                or len(path_names) != len(files)
            ):
                raise ValueError("invalid upload session metadata")
            if time.time() - created > UPLOAD_TTL_SECONDS:
                shutil.rmtree(workdir, ignore_errors=True)
                continue
            total = 0
            safe_paths = []
            clean_files = []
            for index, (item, path_name) in enumerate(zip(files, path_names), 1):
                if not isinstance(item, dict):
                    raise ValueError("invalid file metadata")
                name, size, received = item.get("name"), item.get("size"), item.get("received")
                if (
                    not isinstance(name, str)
                    or Path(name).name != name
                    or not name.lower().endswith(".zip")
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                    or size <= 0
                    or isinstance(received, bool)
                    or not isinstance(received, int)
                    or not 0 <= received <= size
                    or not isinstance(path_name, str)
                    or Path(path_name).name != path_name
                    or path_name in {"", ".", ".."}
                ):
                    raise ValueError("invalid file entry")
                file_path = workdir / path_name
                if file_path.is_symlink():
                    raise ValueError("upload file cannot be a symlink")
                if file_path.exists():
                    actual_size = file_path.stat().st_size
                    if actual_size < received or actual_size > size:
                        raise ValueError("upload file size does not match session")
                    received = actual_size
                elif received:
                    raise ValueError("upload file is missing")
                total += size
                clean_files.append({"name": name, "size": size, "received": received})
                safe_paths.append(str(file_path))
            if total > MAX_INPUT_BYTES or upload_id in restored:
                raise ValueError("invalid upload session size or id")
            session = {
                "created": created,
                "workdir": str(workdir),
                "files": clean_files,
                "paths": safe_paths,
            }
            restored[upload_id] = session
            _persist_upload_session(upload_id, session)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            shutil.rmtree(workdir, ignore_errors=True)
    return restored


def _remove_upload_session_metadata(session: dict) -> None:
    if UPLOAD_STORAGE_DIR is not None:
        (Path(session["workdir"]) / ".aki-upload-session.json").unlink(missing_ok=True)
        (Path(session["workdir"]) / ".aki-upload-session.json.tmp").unlink(missing_ok=True)


if UPLOAD_STORAGE_DIR is not None:
    UPLOAD_SESSIONS.update(_load_persistent_upload_sessions(UPLOAD_STORAGE_DIR))


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
            _remove_upload_session_metadata(session)
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
    workdir = _new_upload_workdir("aki-aov-chunks-")
    UPLOAD_SESSIONS[upload_id] = {
        "created": time.time(), "workdir": str(workdir), "files": clean_files,
        "paths": [str(workdir / f"{i}_{item['name']}") for i, item in enumerate(clean_files, 1)]
    }
    _persist_upload_session(upload_id, UPLOAD_SESSIONS[upload_id])
    return {"upload_id": upload_id, "chunk_size": UPLOAD_CHUNK_BYTES}


@app.get("/api/upload/{upload_id}/status")
async def chunked_upload_status(upload_id: str):
    """Return received offsets so a browser can resume an interrupted upload."""
    session = _get_upload_session(upload_id)
    session["created"] = time.time()
    _persist_upload_session(upload_id, session)
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
        session["created"] = time.time()
        _persist_upload_session(upload_id, session)
        return {"received": offset, "size": item["size"]}
    if chunk_offset != offset:
        raise HTTPException(409, "Sai vị trí khối tải lên, hãy thử lại.")
    if offset + len(chunk) > item["size"]:
        raise HTTPException(400, "Khối tải lên vượt quá kích thước file.")
    with open(session["paths"][file_index], "ab") as target:
        target.write(chunk)
    item["received"] += len(chunk)
    session["created"] = time.time()
    _persist_upload_session(upload_id, session)
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
            _persist_upload_session(upload_id, session)
        return PlainTextResponse(report)
    except InspectError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if not keep_session:
            shutil.rmtree(session["workdir"], ignore_errors=True)
            UPLOAD_SESSIONS.pop(upload_id, None)


@app.post("/api/upload/{upload_id}/compatibility")
async def inspect_chunked_compatibility(upload_id: str):
    """Create a read-only manifest and compatibility plan for a completed chunked upload."""
    session = _get_upload_session(upload_id)
    if any(item["received"] != item["size"] for item in session["files"]):
        raise HTTPException(400, "File chưa tải lên đầy đủ.")
    try:
        manifest = await run_in_threadpool(build_resource_manifest, session["paths"])
        plan = await run_in_threadpool(plan_resource_compatibility, manifest["resources"])
        session["created"] = time.time()
        _persist_upload_session(upload_id, session)
        return {
            "manifest": manifest,
            "compatibility_plan": plan,
            "warning": "Báo cáo chỉ đọc và tư vấn; không giải mã, sửa payload hoặc cấp quyền ghép tài nguyên AoV.",
        }
    except (ResourceManifestError, CompatibilityPlanError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/upload/{upload_id}/decode-bytes")
async def decode_chunked_aov_bytes(upload_id: str):
    """Extract supported decoded AoV .bytes resources without modifying source ZIPs."""
    session = _get_upload_session(upload_id)
    if any(item["received"] != item["size"] for item in session["files"]):
        raise HTTPException(400, "File chưa tải lên đầy đủ.")
    output_path = Path(session["workdir"]) / "aki-aov-decoded-resources.zip"
    try:
        report = await run_in_threadpool(
            decode_archive_resources, session["paths"], str(output_path)
        )
    except AOVBytesArchiveError as exc:
        raise HTTPException(400, str(exc)) from exc
    _remove_upload_session_metadata(session)
    _remove_upload_session_metadata(session)
    UPLOAD_SESSIONS.pop(upload_id, None)
    return FileResponse(
        path=output_path,
        media_type="application/zip",
        filename="aki-aov-decoded-resources.zip",
        headers={
            "X-AOV-Decoded-Resources": str(len(report["decoded"])),
            "X-AOV-Unsupported-Resources": str(len(report["unsupported"])),
            "X-AOV-MSES-Tables": str(sum(
                item.get("payload", {}).get("payload_type") == "mses_v7"
                for item in report["decoded"]
            )),
            "X-AOV-IFix-Patches": str(sum(
                item.get("payload", {}).get("payload_type") == "ifix_patch"
                for item in report["decoded"]
            )),
            "X-AOV-Nested-Packages": str(report["nested_packages_expanded"]),
            "X-AOV-Game-Tested": "false",
        },
        background=BackgroundTask(shutil.rmtree, session["workdir"], ignore_errors=True),
    )


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
    _remove_upload_session_metadata(session)
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
        workdir = _new_upload_workdir("aki-aov-drive-")
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
            _persist_upload_session(upload_id, UPLOAD_SESSIONS[upload_id])
        else:
            session["created"] = time.time()
            _persist_upload_session(upload_id, session)
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


@app.get("/api/capabilities")
def adapter_capabilities():
    """Expose exact merge/inspection support; never imply universal mod merging."""
    return get_adapter_capabilities()


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


@app.post("/api/compatibility/inspect")
async def inspect_compatibility(files: list[UploadFile] = File(...)):
    """Inspect uploaded ZIPs and return a read-only manifest plus advisory conflict plan."""
    if not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"Chọn từ 1 đến {MAX_FILES} file ZIP.")
    workdir = Path(tempfile.mkdtemp(prefix="aki-aov-compatibility-"))
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
            manifest = await run_in_threadpool(build_resource_manifest, input_paths)
            plan = await run_in_threadpool(
                plan_resource_compatibility, manifest["resources"]
            )
        except (ResourceManifestError, CompatibilityPlanError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "manifest": manifest,
            "compatibility_plan": plan,
            "warning": "Báo cáo chỉ đọc và tư vấn; không giải mã, sửa payload hoặc cấp quyền ghép tài nguyên AoV.",
        }
    finally:
        for upload in files:
            await upload.close()
        shutil.rmtree(workdir, ignore_errors=True)


@app.post("/api/compatibility/plan")
async def compatibility_plan(payload: dict = Body(...)):
    """Build an advisory resource conflict plan; this endpoint never authorizes merging."""
    resources = payload.get("resources")
    try:
        return plan_resource_compatibility(resources)
    except CompatibilityPlanError as exc:
        raise HTTPException(400, str(exc)) from exc


if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
