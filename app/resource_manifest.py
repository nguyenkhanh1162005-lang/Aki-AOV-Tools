"""Build a read-only resource manifest from uploaded ZIP archives."""
from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .aov_format_detector import detect_resource_format
from .assetbundle_inspector import AssetBundleInspectError, inspect_assetbundle

MAX_FILES = 10
MAX_ENTRIES = 20000
MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_ENTRY_UNCOMPRESSED = 256 * 1024 * 1024
MAX_ASSETBUNDLE_DEPENDENCY_INSPECT_BYTES = 64 * 1024 * 1024
MAX_NESTED_ZIP_DEPTH = 1


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


def _is_metadata_noise(path: str) -> bool:
    normalized = path.replace("\\\\", "/")
    leaf = PurePosixPath(normalized).name
    return (
        normalized.startswith("__MACOSX/")
        or "/__MACOSX/" in normalized
        or leaf == ".DS_Store"
        or leaf.startswith("._")
    )


def _classify(path: str, detected: str) -> tuple[str, bool]:
    lower = path.lower()
    name = PurePosixPath(lower).name
    if _is_metadata_noise(path):
        return "metadata_noise", False
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


def _build_manifest_item(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    source: str,
    *,
    container_path: str | None = None,
    archive_depth: int = 0,
) -> dict[str, Any]:
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
    item = {
        "path": path,
        "sha256": _hash_entry(archive, info),
        "format": fmt,
        "detected_format": detected,
        "source": source,
        "size": info.file_size,
        "crc32": f"{info.CRC:08x}",
        "is_aov_resource": is_aov,
        "is_metadata_noise": fmt == "metadata_noise",
        "archive_depth": archive_depth,
    }
    if container_path:
        item["container_path"] = container_path
    version = _version(path)
    platform = _platform(path)
    if version:
        item["game_version"] = version
    if platform:
        item["platform"] = platform

    if detected == "Unity AssetBundle":
        item["dependency_inventory_complete"] = False
        item["unity_dependencies"] = []
        item["unity_external_paths"] = []
        if info.file_size <= MAX_ASSETBUNDLE_DEPENDENCY_INSPECT_BYTES:
            try:
                bundle_report = inspect_assetbundle(
                    archive.read(info), f"{source} :: {container_path or ''} :: {path}"
                )
                metadata = bundle_report.get("assetbundle_metadata", [])
                unity_dependencies = sorted({
                    str(dependency)
                    for record in metadata
                    for dependency in record.get("dependencies", [])
                    if isinstance(dependency, str) and dependency.strip()
                })
                external_inventory = bundle_report.get("external_references", [])
                external_paths = sorted({
                    str(reference.get("to"))
                    for reference in external_inventory
                    if isinstance(reference, dict)
                    and isinstance(reference.get("to"), str)
                    and reference.get("to").strip()
                })
                metadata_inventory_complete = (
                    bundle_report.get("assetbundle_metadata_count") == len(metadata)
                    and all(
                        record.get("dependency_count") == len(record.get("dependencies", []))
                        for record in metadata
                    )
                )
                external_inventory_complete = (
                    bundle_report.get("external_reference_count") == len(external_inventory)
                )
                item["unity_dependencies"] = unity_dependencies
                item["unity_external_paths"] = external_paths
                item["dependency_inventory_complete"] = bool(
                    bundle_report.get("assetbundle_metadata_complete")
                    and bundle_report.get("reference_scan_complete")
                    and metadata_inventory_complete
                    and external_inventory_complete
                )
                item["dependency_inspection"] = (
                    "complete" if item["dependency_inventory_complete"] else "partial"
                )
            except (AssetBundleInspectError, RuntimeError, ValueError, OSError) as exc:
                item["dependency_inspection_error"] = str(exc)[:240]
        else:
            item["dependency_inspection_error"] = (
                "AssetBundle vượt quá giới hạn phân tích dependency 64 MiB."
            )
    return item


def build_resource_manifest(paths: list[str]) -> dict[str, Any]:
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES:
        raise ResourceManifestError(f"Chọn từ 1 đến {MAX_FILES} file ZIP.")

    resources = []
    total_entries = 0
    total_uncompressed = 0
    archive_reports = []

    def account_entry(info: zipfile.ZipInfo) -> None:
        nonlocal total_entries, total_uncompressed
        total_entries += 1
        total_uncompressed += info.file_size
        if total_entries > MAX_ENTRIES:
            raise ResourceManifestError("Các ZIP có quá nhiều mục (tối đa 20.000).")
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
            raise ResourceManifestError("Tổng dung lượng giải nén vượt quá 1 GB.")
        if info.file_size > MAX_ENTRY_UNCOMPRESSED:
            raise ResourceManifestError(f"Resource vượt quá 256 MiB: {info.filename}")

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
                account_entry(info)
                item = _build_manifest_item(archive, info, source)
                resources.append(item)
                source_count += 1

                # Some AoV skin packs are wrapper ZIPs containing a Resources.zip.
                # Inspect one nested level so the manifest sees actual game resources,
                # while retaining the wrapper entry as a separate inventory record.
                if (
                    item["detected_format"] == "ZIP container"
                    and info.file_size > 0
                    and info.file_size <= MAX_ENTRY_UNCOMPRESSED
                    and MAX_NESTED_ZIP_DEPTH >= 1
                ):
                    try:
                        nested_bytes = archive.read(info)
                        with zipfile.ZipFile(io.BytesIO(nested_bytes), "r") as nested:
                            for nested_info in nested.infolist():
                                if nested_info.is_dir():
                                    continue
                                account_entry(nested_info)
                                nested_item = _build_manifest_item(
                                    nested,
                                    nested_info,
                                    source,
                                    container_path=item["path"],
                                    archive_depth=1,
                                )
                                resources.append(nested_item)
                                source_count += 1
                    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
                        raise ResourceManifestError(
                            f"ZIP lồng bên trong không hợp lệ: {item['path']}"
                        ) from exc
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
