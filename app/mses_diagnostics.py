"""Read-only, row-level diagnostics for overlapping AoV MSES resources.

This module never mutates resources and never grants merge authorization. It
reports schema, checksum, row-key, and byte-level overlap facts so a later
table-specific adapter can be reviewed against actual payloads.
"""
from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from .aov_bytes import AOVBytesError, decode_aov_resource
from .aov_mses import MSESError, parse_mses

_MAX_DIAGNOSTIC_TABLES = 100
_MAX_CONTAINER_BYTES = 256 * 1024 * 1024
_MAX_RESOURCE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_DECODED_BYTES = 128 * 1024 * 1024
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:\.\d+)?$")


class MSESDiagnosticsError(ValueError):
    """Raised when source archives cannot be inspected safely."""


def canonical_resource_path(path: str) -> str:
    """Remove only an explicit Resources/<semantic-version>/ root prefix."""
    normalized = path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    for index in range(len(parts) - 1):
        if (
            parts[index].casefold() == "resources"
            and _VERSION.fullmatch(parts[index + 1])
        ):
            remainder = parts[index + 2:]
            if remainder:
                return "/".join(remainder).casefold()
    return "/".join(parts).casefold()


def _read_manifest_resource(
    archive_path: str,
    item: dict[str, Any],
) -> bytes:
    resource_path = str(item.get("path") or "")
    container_path = item.get("container_path")
    with zipfile.ZipFile(archive_path, "r") as outer:
        if container_path:
            container_info = outer.getinfo(str(container_path))
            if container_info.file_size > _MAX_CONTAINER_BYTES:
                raise MSESDiagnosticsError(
                    f"Container lồng vượt giới hạn đọc: {container_path}"
                )
            nested_bytes = outer.read(container_info)
            with zipfile.ZipFile(io.BytesIO(nested_bytes), "r") as nested:
                info = nested.getinfo(resource_path)
                if info.file_size > _MAX_RESOURCE_BYTES:
                    raise MSESDiagnosticsError(
                        f"Resource vượt giới hạn đọc: {resource_path}"
                    )
                return nested.read(info)
        info = outer.getinfo(resource_path)
        if info.file_size > _MAX_RESOURCE_BYTES:
            raise MSESDiagnosticsError(
                f"Resource vượt giới hạn đọc: {resource_path}"
            )
        return outer.read(info)


def _index_rows(rows: tuple[bytes, ...]) -> tuple[dict[int, bytes], int]:
    indexed: dict[int, bytes] = {}
    duplicate_count = 0
    for row in rows:
        if len(row) < 4:
            duplicate_count += 1
            continue
        key = int.from_bytes(row[:4], "little")
        if key in indexed:
            duplicate_count += 1
            continue
        indexed[key] = row
    return indexed, duplicate_count


def _inspect_one(
    archive_path: str,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    path = str(item.get("path") or "")
    if int(item.get("size") or 0) > _MAX_RESOURCE_BYTES:
        return {
            "source": item.get("source"),
            "source_identity": (
                str(item.get("source") or "")
                + (" :: " + str(item.get("container_path")) if item.get("container_path") else "")
            ),
            "path": path,
            "decode_ok": False,
            "parse_ok": False,
            "error": "Resource vượt giới hạn chẩn đoán MSES 32 MiB.",
        }
    raw = _read_manifest_resource(archive_path, item)
    if raw.startswith(b"MSES"):
        decoded, mode = raw, "raw_mses"
    else:
        if (
            raw.startswith(b"\\x22\\x4a\\x67")
            or raw.startswith(b"\\x22\\x4a\\x00\\xef")
        ) and len(raw) >= 8:
            expected_size = int.from_bytes(raw[4:8], "little")
            if expected_size > _MAX_RESOURCE_BYTES:
                return {
                    "source": item.get("source"),
                    "source_identity": (
                        str(item.get("source") or "")
                        + (" :: " + str(item.get("container_path")) if item.get("container_path") else "")
                    ),
                    "path": path,
                    "decode_ok": False,
                    "parse_ok": False,
                    "error": "Dữ liệu sau giải mã vượt giới hạn chẩn đoán MSES 32 MiB.",
                }
        try:
            decoded, mode = decode_aov_resource(
                raw, PurePosixPath(path).name
            )
        except AOVBytesError:
            # Many repeated .bytes resources are opaque, non-MSES formats.
            # Do not mislabel them as failed MSES parses.
            return None
    if not decoded.startswith(b"MSES"):
        return None

    try:
        table = parse_mses(decoded, verify_checksum=False)
    except MSESError as exc:
        return {
            "source": item.get("source"),
            "source_identity": (
                str(item.get("source") or "")
                + (" :: " + str(item.get("container_path")) if item.get("container_path") else "")
            ),
            "path": path,
            "decode_mode": mode,
            "decode_ok": True,
            "parse_ok": False,
            "error": str(exc),
        }

    rows_by_key, duplicate_count = _index_rows(table.rows)
    return {
        "source": item.get("source"),
        "source_identity": (
            str(item.get("source") or "")
            + (" :: " + str(item.get("container_path")) if item.get("container_path") else "")
        ),
        "path": path,
        "decode_mode": mode,
        "decode_ok": True,
        "parse_ok": True,
        "version": table.version,
        "field_count": table.field_count,
        "row_count": len(table.rows),
        "checksum_valid": table.checksum_valid,
        "unique_row_keys": duplicate_count == 0 and len(rows_by_key) == len(table.rows),
        "duplicate_or_short_key_count": duplicate_count,
        "rows_by_key": rows_by_key,
    }


def analyze_mses_overlaps(
    archive_paths: list[str],
    resources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Decode and compare same-path MSES tables across distinct input archives."""
    archives_by_name: dict[str, list[str]] = defaultdict(list)
    for archive_path in archive_paths:
        archives_by_name[Path(archive_path).name].append(archive_path)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in resources:
        path = str(item.get("path") or "")
        if not path.casefold().endswith(".bytes"):
            continue
        canonical = canonical_resource_path(path)
        identity = (
            str(item.get("source") or "")
            + (" :: " + str(item.get("container_path")) if item.get("container_path") else "")
        )
        if not canonical or not identity:
            continue
        grouped[canonical].append(item)

    diagnostics = []
    inspected_resources = 0
    skipped_ambiguous_sources = 0
    for canonical_path, entries in sorted(grouped.items()):
        identities = {
            str(item.get("source") or "")
            + (" :: " + str(item.get("container_path")) if item.get("container_path") else "")
            for item in entries
        }
        if len(identities) < 2:
            continue
        inspected = []
        group_decoded_bytes = 0
        for item in entries:
            source_name = str(item.get("source") or "")
            source_paths = archives_by_name.get(source_name, [])
            if len(source_paths) != 1:
                skipped_ambiguous_sources += 1
                continue
            if group_decoded_bytes >= _MAX_TOTAL_DECODED_BYTES:
                result = {
                    "source": source_name,
                    "source_identity": (
                        source_name
                        + (" :: " + str(item.get("container_path")) if item.get("container_path") else "")
                    ),
                    "path": str(item.get("path") or ""),
                    "decode_ok": False,
                    "parse_ok": False,
                    "error": "Đã đạt giới hạn chẩn đoán tổng 128 MiB cho nhóm resource.",
                }
            else:
                try:
                    result = _inspect_one(source_paths[0], item)
                except (
                    OSError, zipfile.BadZipFile, KeyError, RuntimeError,
                    EOFError, MSESDiagnosticsError,
                ) as exc:
                    result = {
                        "source": source_name,
                        "source_identity": (
                            source_name
                            + (" :: " + str(item.get("container_path")) if item.get("container_path") else "")
                        ),
                        "path": str(item.get("path") or ""),
                        "decode_ok": False,
                        "parse_ok": False,
                        "error": str(exc)[:240],
                    }
            if result is not None:
                group_decoded_bytes += int(item.get("size") or 0)
                inspected_resources += 1
                inspected.append(result)

        parsed = [item for item in inspected if item.get("parse_ok")]
        if len(parsed) < 2 and not any(not item.get("parse_ok") for item in inspected):
            continue

        table_report = {
            "canonical_path": canonical_path,
            "resources": [],
            "comparisons": [],
            "read_only": True,
            "merge_authorized": False,
        }
        for item in inspected:
            entry = {key: value for key, value in item.items() if key != "rows_by_key"}
            table_report["resources"].append(entry)

        for left_index in range(len(parsed)):
            for right_index in range(left_index + 1, len(parsed)):
                left = parsed[left_index]
                right = parsed[right_index]
                left_rows = left["rows_by_key"]
                right_rows = right["rows_by_key"]
                left_keys = set(left_rows)
                right_keys = set(right_rows)
                common = left_keys & right_keys
                differing = sum(
                    left_rows[key] != right_rows[key] for key in common
                )
                left_only = len(left_keys - right_keys)
                right_only = len(right_keys - left_keys)
                same_schema = (
                    left["version"] == right["version"]
                    and left["field_count"] == right["field_count"]
                )
                keys_unique = (
                    left["unique_row_keys"] and right["unique_row_keys"]
                )
                both_checksums_valid = (
                    left["checksum_valid"] and right["checksum_valid"]
                )
                table_report["comparisons"].append({
                    "left": left["source_identity"],
                    "right": right["source_identity"],
                    "version_match": left["version"] == right["version"],
                    "schema_match": same_schema,
                    "field_count_left": left["field_count"],
                    "field_count_right": right["field_count"],
                    "row_count_left": left["row_count"],
                    "row_count_right": right["row_count"],
                    "common_keys": len(common),
                    "identical_common_rows": len(common) - differing,
                    "differing_common_rows": differing,
                    "left_only_keys": left_only,
                    "right_only_keys": right_only,
                    "checksums_valid": both_checksums_valid,
                    "unique_row_keys": keys_unique,
                    "append_only_candidate": bool(
                        same_schema
                        and keys_unique
                        and both_checksums_valid
                        and differing == 0
                    ),
                    "merge_authorized": False,
                })
        diagnostics.append(table_report)
        if len(diagnostics) >= _MAX_DIAGNOSTIC_TABLES:
            break

    return {
        "available": True,
        "read_only": True,
        "merge_authorized": False,
        "table_count": len(diagnostics),
        "inspected_resource_count": inspected_resources,
        "skipped_ambiguous_source_count": skipped_ambiguous_sources,
        "tables": diagnostics,
        "note": (
            "MSES được giải mã/kiểm tra theo row key chỉ để chẩn đoán. "
            "append_only_candidate không phải quyền ghép; adapter riêng và kiểm tra cuối vẫn bắt buộc."
        ),
    }
