"""Conservative AKI branding replacement for editable AoV text resources.

This module never edits Unity AssetBundles, MSES tables, IFix patches, Wwise banks,
or opaque bytes. It only rewrites exact branding strings in supported UTF text
files and AoV AES/Zstandard-wrapped text/package payloads. Source archives remain
untouched and every rewritten AoV wrapper is decoded again for verification.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .aov_bytes import AOVBytesError, decode_aov_resource, encode_aov_resource

MAX_ARCHIVE_ENTRIES = 20_000
MAX_ARCHIVE_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_ENTRY_UNCOMPRESSED = 256 * 1024 * 1024
MAX_NESTED_ZIP_DEPTH = 3
SPOOL_MEMORY_BYTES = 16 * 1024 * 1024
TEXT_EXTENSIONS = {
    ".lua", ".txt", ".xml", ".json", ".json5", ".ini", ".cfg", ".csv",
    ".md", ".yaml", ".yml",
}
# Notice cleanup/branding must never rewrite kill-notification or billboard
# resources. They are distinct from startup/login scripts even when their
# embedded text contains generic announcement/notify terms.
_PROTECTED_NOTICE_PATH_MARKERS = (
    "commercializenotify",
    "reskillbillboardcfg",
    "resbillboardcfg",
    "resbillboardskincfg",
    "killnotify",
    "kill_notification",
)


def _is_protected_notice_resource(resource_path: str) -> bool:
    normalized = resource_path.replace(chr(92), "/").casefold()
    return any(marker in normalized for marker in _PROTECTED_NOTICE_PATH_MARKERS)


ZIP_HEADERS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
AOV_AES_PREFIX = b"\x22\x4a\x67"
AOV_ZSTD_PREFIX = b"\x22\x4a\x00\xef"

BRANDING_REPLACEMENTS = (
    (
        "Mod được tạo bởi youtuber KIANA MOD AOV",
        "Mod được gộp bởi AKI MOD AOV",
    ),
    ("KIANA MOD AOV", "AKI MOD AOV"),
    ("Web Mod", ""),
    ("Youtube", ""),
)


class NoticeRewriteError(ValueError):
    """Raised when a requested branding rewrite cannot be safely completed."""


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    candidate = normalized[:-1] if normalized.endswith("/") else normalized
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.split("/"))
        or "\x00" in normalized
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise NoticeRewriteError(f"Đường dẫn ZIP không an toàn: {name}")
    return path.as_posix()


def _copy_limited(source: BinaryIO, destination: BinaryIO, limit: int) -> int:
    total = 0
    while True:
        block = source.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise NoticeRewriteError("Một tài nguyên vượt giới hạn đọc an toàn.")
        destination.write(block)
    return total


def _looks_like_zip(data: bytes) -> bool:
    return data.startswith(ZIP_HEADERS)


def _looks_like_aov_wrapper(data: bytes) -> bool:
    return data.startswith(AOV_AES_PREFIX) or data.startswith(AOV_ZSTD_PREFIX)


def _rewrite_text_bytes(data: bytes, resource_path: str, state: dict) -> tuple[bytes, bool]:
    """Replace exact branding strings only in bounded, printable UTF text."""
    decoded = None
    encoding = None
    for candidate in ("utf-8", "utf-16-le", "utf-16-be"):
        try:
            text = data.decode(candidate, errors="strict")
        except UnicodeDecodeError:
            continue
        if not text or "\x00" in text:
            continue
        printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
        if printable / max(1, len(text)) >= 0.94:
            decoded, encoding = text, candidate
            break
    if decoded is None or len(decoded) > MAX_ENTRY_UNCOMPRESSED:
        return data, False

    updated = decoded
    per_file = {}
    for old, new in BRANDING_REPLACEMENTS:
        count = updated.count(old)
        if count:
            updated = updated.replace(old, new)
            per_file[old] = count
            state["replacement_counts"][old] = (
                state["replacement_counts"].get(old, 0) + count
            )
    if updated == decoded:
        return data, False

    state["replacement_total"] += sum(per_file.values())
    output = updated.encode(encoding)
    if len(output) > MAX_ENTRY_UNCOMPRESSED:
        raise NoticeRewriteError(
            f"{resource_path}: tài nguyên sau thay chữ vượt giới hạn."
        )
    if output == data:
        return data, False
    state["modified_resources"].append({
        "path": resource_path,
        "replacements": per_file,
        "encoding": encoding,
        "input_bytes": len(data),
        "output_bytes": len(output),
    })
    return output, True


def _process_member(
    source: BinaryIO,
    member_name: str,
    logical_path: str,
    depth: int,
    state: dict,
) -> BinaryIO:
    """Return a seekable stream for the rewritten member (or its exact original)."""
    original = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES, mode="w+b")
    try:
        _copy_limited(source, original, MAX_ENTRY_UNCOMPRESSED)
        original.seek(0)
        if _is_protected_notice_resource(logical_path):
            state["protected_notice_resource_count"] += 1
            if len(state["protected_notice_resources"]) < 100:
                state["protected_notice_resources"].append(logical_path)
            return original

        prefix = original.read(8)
        original.seek(0)
        suffix = PurePosixPath(member_name.lower()).suffix

        if _looks_like_aov_wrapper(prefix):
            raw = original.read()
            try:
                decoded, mode = decode_aov_resource(raw, logical_path)
            except AOVBytesError as exc:
                raise NoticeRewriteError(
                    f"{logical_path}: wrapper AoV nhận diện được nhưng không giải mã được: {exc}"
                ) from exc

            rewritten_payload = decoded
            changed = False
            if _looks_like_zip(decoded):
                before = state["replacement_total"]
                nested_input = io.BytesIO(decoded)
                nested_output = tempfile.SpooledTemporaryFile(
                    max_size=SPOOL_MEMORY_BYTES, mode="w+b"
                )
                try:
                    _rewrite_zip_stream(
                        nested_input, nested_output, logical_path, depth + 1, state
                    )
                    changed = state["replacement_total"] > before
                    if changed:
                        nested_output.seek(0)
                        rewritten_payload = nested_output.read()
                finally:
                    nested_output.close()
            else:
                rewritten_payload, changed = _rewrite_text_bytes(
                    decoded, logical_path, state
                )

            if changed:
                encoded = encode_aov_resource(rewritten_payload, logical_path, mode)
                verified, verified_mode = decode_aov_resource(encoded, logical_path)
                if verified != rewritten_payload or verified_mode != mode:
                    raise NoticeRewriteError(
                        f"{logical_path}: round-trip AoV không khớp sau khi thay chữ."
                    )
                output = tempfile.SpooledTemporaryFile(
                    max_size=SPOOL_MEMORY_BYTES, mode="w+b"
                )
                output.write(encoded)
                output.seek(0)
                original.close()
                state["rewritten_aov_wrappers"] += 1
                return output
            original.seek(0)
            return original

        if _looks_like_zip(prefix):
            before = state["replacement_total"]
            nested_output = tempfile.SpooledTemporaryFile(
                max_size=SPOOL_MEMORY_BYTES, mode="w+b"
            )
            try:
                _rewrite_zip_stream(
                    original, nested_output, logical_path, depth + 1, state
                )
                if state["replacement_total"] > before:
                    nested_output.seek(0)
                    original.close()
                    state["rewritten_nested_zips"] += 1
                    return nested_output
            except zipfile.BadZipFile:
                if suffix == ".zip":
                    raise NoticeRewriteError(
                        f"{logical_path}: header ZIP có nhưng nội dung không hợp lệ."
                    )
            nested_output.close()
            original.seek(0)
            return original

        if suffix in TEXT_EXTENSIONS:
            data = original.read()
            rewritten, changed = _rewrite_text_bytes(data, logical_path, state)
            if changed:
                output = tempfile.SpooledTemporaryFile(
                    max_size=SPOOL_MEMORY_BYTES, mode="w+b"
                )
                output.write(rewritten)
                output.seek(0)
                original.close()
                return output

        original.seek(0)
        return original
    except Exception:
        original.close()
        raise


def _rewrite_zip_stream(
    source_stream: BinaryIO,
    destination_stream: BinaryIO,
    archive_label: str,
    depth: int,
    state: dict,
) -> None:
    if depth > MAX_NESTED_ZIP_DEPTH:
        raise NoticeRewriteError(
            f"{archive_label}: ZIP lồng vượt giới hạn {MAX_NESTED_ZIP_DEPTH} tầng."
        )
    source_stream.seek(0)
    destination_stream.seek(0)
    destination_stream.truncate(0)
    with zipfile.ZipFile(source_stream, "r") as source_zip:
        infos = source_zip.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise NoticeRewriteError(f"{archive_label}: ZIP có quá nhiều mục.")
        state["entry_count"] += len(infos)
        if state["entry_count"] > MAX_ARCHIVE_ENTRIES:
            raise NoticeRewriteError("Tổng số mục ZIP lồng vượt giới hạn.")
        expanded = sum(info.file_size for info in infos)
        state["expanded_bytes"] += expanded
        if expanded > MAX_ARCHIVE_UNCOMPRESSED or state["expanded_bytes"] > MAX_ARCHIVE_UNCOMPRESSED:
            raise NoticeRewriteError("Tổng dữ liệu ZIP giải nén vượt giới hạn 1 GiB.")
        if any(info.file_size > MAX_ENTRY_UNCOMPRESSED for info in infos):
            raise NoticeRewriteError("Có entry ZIP vượt giới hạn 256 MiB.")
        seen = set()
        for info in infos:
            canonical = _safe_member_name(info.filename)
            if canonical in seen:
                raise NoticeRewriteError(
                    f"{archive_label}: đường dẫn ZIP bị trùng: {canonical}"
                )
            seen.add(canonical)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise NoticeRewriteError(
                    f"{archive_label}: không hỗ trợ symbolic link: {canonical}"
                )
            if info.flag_bits & 1:
                raise NoticeRewriteError(
                    f"{archive_label}: không hỗ trợ entry có mật khẩu: {canonical}"
                )
        bad = source_zip.testzip()
        if bad:
            raise NoticeRewriteError(f"{archive_label}: CRC nguồn sai tại {bad}.")

        with zipfile.ZipFile(
            destination_stream, "w", allowZip64=True
        ) as output_zip:
            output_zip.comment = source_zip.comment
            for info in infos:
                if info.is_dir():
                    output_zip.writestr(info, b"")
                    continue
                member_path = f"{archive_label}!/{info.filename}"
                with source_zip.open(info, "r") as member_source:
                    rewritten = _process_member(
                        member_source, info.filename, member_path, depth, state
                    )
                try:
                    with output_zip.open(info, "w", force_zip64=True) as member_target:
                        shutil.copyfileobj(rewritten, member_target, length=1024 * 1024)
                finally:
                    rewritten.close()

    destination_stream.seek(0)
    with zipfile.ZipFile(destination_stream, "r") as verified_zip:
        if verified_zip.namelist() != [info.filename for info in infos]:
            raise NoticeRewriteError(
                f"{archive_label}: danh sách đường dẫn thay đổi sau khi ghi ZIP."
            )
        bad = verified_zip.testzip()
        if bad:
            raise NoticeRewriteError(f"{archive_label}: CRC đầu ra sai tại {bad}.")
    destination_stream.seek(0)


def rewrite_archive_branding(archive_paths: list[str], output_path: str) -> dict:
    """Create a separate ZIP with exact AKI branding replacements in editable text."""
    if len(archive_paths) != 1:
        raise NoticeRewriteError(
            "Chức năng thay thông báo hiện nhận đúng một ZIP đã gộp làm đầu vào."
        )
    source_path = Path(archive_paths[0])
    if not source_path.is_file():
        raise NoticeRewriteError("Không tìm thấy ZIP đầu vào.")
    source_size = source_path.stat().st_size
    if source_size <= 0 or source_size > 500 * 1024 * 1024:
        raise NoticeRewriteError("ZIP đầu vào phải lớn hơn 0 và không vượt 500 MB.")

    state = {
        "entry_count": 0,
        "expanded_bytes": 0,
        "replacement_total": 0,
        "replacement_counts": {},
        "modified_resources": [],
        "rewritten_aov_wrappers": 0,
        "rewritten_nested_zips": 0,
        "protected_notice_resource_count": 0,
        "protected_notice_resources": [],
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp.zip",
        dir=str(destination.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with source_path.open("rb") as source, temporary.open("w+b") as output:
            _rewrite_zip_stream(source, output, source_path.name, 0, state)
            output.flush()
            os.fsync(output.fileno())
        if state["replacement_total"] == 0:
            raise NoticeRewriteError(
                "Không tìm thấy chuỗi thương hiệu cần đổi trong tài nguyên văn bản AoV "
                "đã giải mã được. AssetBundle Unity, MSES, IFix và dữ liệu nhị phân "
                "opaque không được sửa bởi chức năng này."
            )
        with zipfile.ZipFile(temporary, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise NoticeRewriteError(f"ZIP đầu ra không qua CRC tại {bad}.")
        os.replace(temporary, destination)
        return {
            "input_name": source_path.name,
            "input_bytes": source_size,
            "input_sha256": _sha256_file(source_path),
            "output_bytes": destination.stat().st_size,
            "output_sha256": _sha256_file(destination),
            "replacement_total": state["replacement_total"],
            "replacement_counts": state["replacement_counts"],
            "modified_resource_count": len(state["modified_resources"]),
            "modified_resources": state["modified_resources"][:200],
            "rewritten_aov_wrappers": state["rewritten_aov_wrappers"],
            "rewritten_nested_zips": state["rewritten_nested_zips"],
            "protected_notice_resource_count": state["protected_notice_resource_count"],
            "protected_notice_resources": state["protected_notice_resources"],
            "structurally_validated": True,
            "game_tested": False,
            "limitations": (
                "Chỉ thay chuỗi chính xác trong UTF text và AoV AES/Zstandard wrapper "
                "giải mã được. Không sửa Unity AssetBundle, MSES, IFix, Wwise hoặc "
                "payload opaque; cần quét và thử trong game để xác nhận popup đã hết."
            ),
        }
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
