from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any

from .aov_bytes import AOVBytesError, decode_aov_resource


MAX_ASSET_REFERENCE_BYTES = 256 * 1024 * 1024
_ASSET_REF_FILENAME = re.compile(r"\d+_assetref\.bytes$", re.IGNORECASE)
_ASSET_REF_TYPE = b"TypeAssetRefAnalyser.AssetReferenceSet"


class AssetReferenceSetError(ValueError):
    """Raised when an AssetReferenceSet cannot be safely identified."""


def is_asset_reference_set_path(resource_name: str) -> bool:
    normalized = resource_name.replace("\\", "/")
    parts = [part.casefold() for part in PurePosixPath(normalized).parts]
    filename = PurePosixPath(normalized).name
    return (
        "assetrefs" in parts
        and "hero" in parts
        and _ASSET_REF_FILENAME.fullmatch(filename) is not None
    )


def _has_asset_reference_set_signature(payload: bytes) -> bool:
    root_offset = payload.find(b"ROOTC", 0, 64)
    if root_offset < 0:
        return False
    type_offset = payload.find(
        _ASSET_REF_TYPE,
        root_offset,
        min(len(payload), root_offset + 1024),
    )
    if type_offset < 0:
        return False
    return payload.find(
        b"baseSubset",
        type_offset,
        min(len(payload), type_offset + 2048),
    ) >= 0


def inspect_asset_reference_set(resource_name: str, data: bytes) -> dict[str, Any]:
    """Recognize and decode an AoV AssetReferenceSet without rewriting it.

    This is deliberately an inspection-only adapter. It validates the wrapper
    and the serialized root/type markers, but does not pretend to understand or
    reserialize the nested baseSubset/skinSubset collection graph.
    """
    if not is_asset_reference_set_path(resource_name):
        raise AssetReferenceSetError("Đường dẫn không phải AssetRefs/Hero/*_AssetRef.bytes.")
    if not isinstance(data, bytes):
        raise TypeError("AssetRef data phải là bytes.")
    if len(data) > MAX_ASSET_REFERENCE_BYTES:
        raise AssetReferenceSetError("AssetRef vượt giới hạn 256 MiB.")

    try:
        decoded, wrapper_mode = decode_aov_resource(data, resource_name)
    except AOVBytesError as exc:
        if not _has_asset_reference_set_signature(data):
            raise AssetReferenceSetError(
                f"Không giải mã/nhận dạng được AssetReferenceSet: {exc}"
            ) from exc
        decoded, wrapper_mode = data, "raw"

    if len(decoded) > MAX_ASSET_REFERENCE_BYTES:
        raise AssetReferenceSetError("AssetRef sau giải mã vượt giới hạn 256 MiB.")
    if not _has_asset_reference_set_signature(decoded):
        raise AssetReferenceSetError(
            "Payload không có cấu trúc ROOTC/AssetReferenceSet/baseSubset đã nhận dạng."
        )

    filename = PurePosixPath(resource_name.replace("\\", "/")).name
    actor_match = re.match(r"(\d+)_AssetRef\.bytes$", filename, re.IGNORECASE)
    actor_id = actor_match.group(1) if actor_match else None

    candidates: set[str] = set()
    for match in re.finditer(rb"(?<![A-Za-z0-9_])[Vv][A-Za-z0-9_./-]{6,240}", decoded):
        value = match.group().decode("ascii", errors="ignore")
        if "/" in value:
            candidates.add(value)

    return {
        "format": "aov_asset_reference_set",
        "adapter": "asset_reference_set_inspection_only",
        "recognized": True,
        "wrapper_mode": wrapper_mode,
        "actor_id": actor_id,
        "decoded_bytes": len(decoded),
        "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
        "has_base_subset": True,
        "has_skin_subset": b"skinSubset" in decoded,
        "reference_path_candidates": len(candidates),
        "sample_references": sorted(candidates)[:5],
        "merge_supported": False,
        "preserved_unchanged": False,
        "game_tested": False,
    }
