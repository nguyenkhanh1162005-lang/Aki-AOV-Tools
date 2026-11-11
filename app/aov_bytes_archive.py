"""Decode supported AoV-wrapped .bytes resources from uploaded ZIP archives.

This is a diagnostic extraction step. It does not merge resources or modify the
uploaded archives.
"""
from __future__ import annotations

import io
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .aov_bytes import AOVBytesError, decode_aov_resource
from .aov_mses import MSESError, parse_mses

MAX_ARCHIVE_ENTRIES = 20000
MAX_ARCHIVE_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_ENTRY_UNCOMPRESSED = 256 * 1024 * 1024
MAX_DECODED_RESOURCE = 256 * 1024 * 1024
MAX_NESTED_ZIP_DEPTH = 2


class AOVBytesArchiveError(ValueError):
    pass


def _safe_member_path(name: str) -> str:
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
        raise AOVBytesArchiveError(f"Đường dẫn ZIP không an toàn: {name}")
    return path.as_posix()


def _expected_decoded_size(data: bytes) -> int | None:
    if len(data) < 8 or data[:2] != b"\x22\x4a":
        return None
    return int.from_bytes(data[4:8], "little")


def _inspect_decoded_payload(data: bytes) -> dict[str, Any]:
    if data.startswith(b"MSES"):
        try:
            table = parse_mses(data, verify_checksum=False)
        except MSESError as exc:
            return {"payload_type": "mses_invalid", "reason": str(exc)}
        return {
            "payload_type": "mses_v7",
            "version": table.version,
            "field_count": table.field_count,
            "row_count": len(table.rows),
            "checksum_valid": table.checksum_valid,
        }
    stream = io.BytesIO(data)
    if zipfile.is_zipfile(stream):
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream, "r") as package:
                return {
                    "payload_type": "zip_package",
                    "entry_count": len(package.infolist()),
                    "uncompressed_bytes": sum(
                        item.file_size for item in package.infolist()
                    ),
                }
        except zipfile.BadZipFile:
            return {"payload_type": "zip_invalid"}
    return {"payload_type": "opaque_binary"}


def decode_archive_resources(input_paths: list[str], output_path: str) -> dict[str, Any]:
    if not input_paths:
        raise AOVBytesArchiveError("Chưa có ZIP đầu vào.")
    report: dict[str, Any] = {
        "decoded": [],
        "unsupported": [],
        "ignored_files": 0,
        "total_entries": 0,
        "decoded_bytes": 0,
        "nested_packages_expanded": 0,
        "nested_packages_not_expanded": [],
        "advisory_only": True,
        "merge_authorized": False,
        "game_tested": False,
    }
    output = Path(output_path)
    seen_output_paths: set[str] = set()
    total_uncompressed = 0

    def process_archive(archive: zipfile.ZipFile, prefix: str, depth: int) -> None:
        nonlocal total_uncompressed
        infos = archive.infolist()
        report["total_entries"] += len(infos)
        if report["total_entries"] > MAX_ARCHIVE_ENTRIES:
            raise AOVBytesArchiveError("ZIP vượt giới hạn 20.000 mục.")
        seen_names: set[str] = set()
        for info in infos:
            path = _safe_member_path(info.filename)
            if path in seen_names:
                raise AOVBytesArchiveError(f"ZIP có đường dẫn trùng lặp: {path}")
            seen_names.add(path)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode) or info.flag_bits & 1:
                raise AOVBytesArchiveError(f"ZIP chứa mục liên kết hoặc mã hóa không hỗ trợ: {path}")
            if info.is_dir():
                continue
            if info.file_size > MAX_ENTRY_UNCOMPRESSED:
                raise AOVBytesArchiveError(f"ZIP entry vượt giới hạn 256 MiB: {path}")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED:
                raise AOVBytesArchiveError("Tổng dữ liệu giải nén vượt giới hạn 1 GiB.")
            try:
                raw = archive.read(info)
            except (RuntimeError, zipfile.BadZipFile, OSError, EOFError) as exc:
                raise AOVBytesArchiveError(f"Không đọc được ZIP entry: {path}") from exc

            if path.lower().endswith(".zip") and depth < MAX_NESTED_ZIP_DEPTH:
                try:
                    with zipfile.ZipFile(io.BytesIO(raw), "r") as nested:
                        process_archive(nested, f"{prefix}/{path}", depth + 1)
                except zipfile.BadZipFile:
                    report["ignored_files"] += 1
                continue

            if not path.lower().endswith(".bytes"):
                report["ignored_files"] += 1
                continue
            expected_size = _expected_decoded_size(raw)
            if expected_size is None:
                report["unsupported"].append({
                    "source": prefix,
                    "path": path,
                    "reason": "Header không thuộc định dạng AoV đã nhận diện.",
                })
                continue
            if expected_size > MAX_DECODED_RESOURCE:
                raise AOVBytesArchiveError(
                    f"Tài nguyên sau giải mã vượt giới hạn 256 MiB: {path}"
                )
            try:
                decoded, mode_name = decode_aov_resource(raw, path)
            except AOVBytesError as exc:
                report["unsupported"].append({
                    "source": prefix,
                    "path": path,
                    "reason": str(exc),
                })
                continue
            output_name = f"decoded/{prefix}/{path}"
            if output_name in seen_output_paths:
                raise AOVBytesArchiveError(f"Đường dẫn đầu ra bị trùng: {output_name}")
            seen_output_paths.add(output_name)
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as target:
                target.writestr(output_name, decoded)
            report["decoded"].append({
                "source": prefix,
                "path": path,
                "output_path": output_name,
                "mode": mode_name,
                "original_size": len(raw),
                "decoded_size": len(decoded),
                "payload": _inspect_decoded_payload(decoded),
            })
            report["decoded_bytes"] += len(decoded)

            # AoV package resources such as CommonActions.pkg.bytes can contain
            # another ZIP after their outer AES/Zstandard wrapper is removed.
            # Keep the decoded package itself and inspect its members as well.
            if path.lower().endswith(".pkg.bytes"):
                package_stream = io.BytesIO(decoded)
                if zipfile.is_zipfile(package_stream):
                    if depth >= MAX_NESTED_ZIP_DEPTH:
                        report["nested_packages_not_expanded"].append({
                            "source": prefix,
                            "path": path,
                            "reason": "Đã đạt giới hạn độ sâu ZIP lồng.",
                        })
                    else:
                        package_stream.seek(0)
                        try:
                            with zipfile.ZipFile(package_stream, "r") as nested_package:
                                process_archive(
                                    nested_package,
                                    f"{prefix}/{path}::decoded",
                                    depth + 1,
                                )
                            report["nested_packages_expanded"] += 1
                        except zipfile.BadZipFile as exc:
                            raise AOVBytesArchiveError(
                                f"Package sau giải mã có cấu trúc ZIP lỗi: {path}"
                            ) from exc

    try:
        if output.exists():
            output.unlink()
        for index, input_path in enumerate(input_paths, start=1):
            label = f"archive_{index}_{Path(input_path).name}"
            try:
                with zipfile.ZipFile(input_path, "r") as archive:
                    process_archive(archive, label, 0)
            except zipfile.BadZipFile as exc:
                raise AOVBytesArchiveError(f"File đầu vào không phải ZIP hợp lệ: {Path(input_path).name}") from exc
        if not report["decoded"]:
            raise AOVBytesArchiveError(
                "Không tìm thấy tài nguyên .bytes nào giải mã thành công trong các ZIP đã tải lên."
            )
        with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as target:
            target.writestr(
                "decode-report.json",
                json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
            )
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return report
