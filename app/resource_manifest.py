"""Build a read-only resource manifest from uploaded ZIP archives."""
from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .aov_format_detector import detect_resource_format

MAX_FILES = 10
MAX_ENTRIES = 20000
MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_ENTRY_UNCOMPRESSED = 256 * 1024 * 1024


class ResourceManifestError(ValueError):
    pass


def _safe_path(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (not normalized or path.is_absolute() or ".." in path.parts
            or "\x00" in normalized or (path.parts and path.parts[0].endswith(":"))):
        raise ResourceManifestError(f"ZIP chứa đường dẫn không an toàn: {name}")
    return normalized


def _hash_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    try:
        with archive.open(info, "r") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except (RuntimeError, zipfile.BadZipFile, OSError, EOFError) as exc:
        raise ResourceManifestError(f"Không đọc/kiểm tra được resource: {info.filename}") from exc
    return digest.hexdigest()


def _classify(path: str, detected: str) -> tuple[str, bool]:
    lower = path.lower()
    name = PurePosixPath(lower).name
    is_aov = (
        "resources/" in lower
        or lower.endswith((".bytes", ".assetbundle", ".bnk", ".wem"))
        or any(token in lower for token in ("battlebank", "lobbybank", "heroskin"))
    )
    if name in {"battlebank.bytes", "lobbybank.bytes", "skillcombine.bytes",
                "resskinseniorlabelcfg.bytes"}:
        return "mses", True
    if "heroskin" in name or "ifix" in lower:
        return "ifix", True
    if lower.endswith(".assetbundle") or detected == "Unity AssetBundle":
        return "unity_assetbundle", True
    if lower.endswith(".bnk") or detected == "Wwise SoundBank":
        return "wwise_bnk", True
    if detected.startswith("AOV ") or detected == "Zstandard":
        return "aov_wrapper", True
    return ("aov_resource" if is_aov else "generic_zip"), is_aov


def _version(path: str) -> str | None:
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+(?:\.\d+)?)(?!\d)", path)
    return match.group(1) if match else None


def _platform(path: str) -> str | None:
    lower = path.lower()
    if re.search(r"(^|[/_.-])ios([/_.-]|$)", lower):
        return "ios"
    if re.search(r"(^|[/_.-])android([/_.-]|$)", lower):
        return "android"
    return None


def build_resource_manifest(paths: list[str]) -> dict[str, Any]:
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES:
        raise ResourceManifestError(f"Chọn từ 1 đến {MAX_FILES} file ZIP.")

    resources = []
    total_entries = 0
    total_uncompressed = 0
    archive_reports = []

    for archive_path in paths:
        source = Path(archive_path).name
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise ResourceManifestError(f"Không mở được ZIP: {source}") from exc

        source_count = 0
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                total_entries += 1
                source_count += 1
                total_uncompressed += info.file_size
                if total_entries > MAX_ENTRIES:
                    raise ResourceManifestError("Các ZIP có quá nhiều mục (tối đa 20.000).")
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                    raise ResourceManifestError("Tổng dung lượng giải nén vượt quá 1 GB.")
                if info.file_size > MAX_ENTRY_UNCOMPRESSED:
                    raise ResourceManifestError(f"Resource vượt quá 256 MiB: {info.filename}")
                path = _safe_path(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise ResourceManifestError(f"ZIP chứa symbolic link không an toàn: {path}")
                if info.flag_bits & 1:
                    raise ResourceManifestError(f"ZIP entry được mã hóa, không thể kiểm kê an toàn: {path}")
                try:
                    with archive.open(info, "r") as stream:
                        header = stream.read(32)
                except (RuntimeError, zipfile.BadZipFile, OSError, EOFError) as exc:
                    raise ResourceManifestError(f"Không đọc được header: {path}") from exc
                detected, _description = detect_resource_format(path, header)
                fmt, is_aov = _classify(path, detected)
                digest = _hash_entry(archive, info)
                item = {
                    "path": path,
                    "sha256": digest,
                    "format": fmt,
                    "detected_format": detected,
                    "source": source,
                    "size": info.file_size,
                    "crc32": f"{info.CRC:08x}",
                    "is_aov_resource": is_aov,
                }
                version = _version(path)
                platform = _platform(path)
                if version:
                    item["game_version"] = version
                if platform:
                    item["platform"] = platform
                resources.append(item)
        archive_reports.append({"source": source, "resource_count": source_count})

    return {
        "schema_version": 1,
        "read_only": True,
        "decrypted": False,
        "payloads_modified": False,
        "archive_count": len(paths),
        "resource_count": len(resources),
        "total_uncompressed_bytes": total_uncompressed,
        "archives": archive_reports,
        "resources": resources,
    }
