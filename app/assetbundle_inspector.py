from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any


MAX_ASSETBUNDLE_BYTES = 64 * 1024 * 1024
MAX_REPORTED_OBJECT_TYPES = 32
MAX_REPORTED_CONTAINERS = 100


class AssetBundleInspectError(ValueError):
    """Raised when an AssetBundle cannot be safely inspected."""


def _load_unitypy_aov():
    vendor = str(Path(__file__).resolve().parent / "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        import UnityPy_AOV
    except Exception as exc:  # pragma: no cover - deployment dependency
        raise AssetBundleInspectError(
            "Máy chủ thiếu bộ đọc AssetBundle AoV (UnityPy_AOV)."
        ) from exc
    return UnityPy_AOV


def _safe_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\\x00", "")
    return text[:limit]


def inspect_assetbundle(data: bytes, label: str = "AssetBundle") -> dict[str, Any]:
    """Read-only inventory of Unity serialized files, object types and external references."""
    if not data:
        raise AssetBundleInspectError("AssetBundle rỗng.")
    if len(data) > MAX_ASSETBUNDLE_BYTES:
        raise AssetBundleInspectError("AssetBundle vượt quá giới hạn phân tích sâu 64 MiB.")
    if not (data.startswith(b"UnityFS\\x00") or data.startswith(b"UnityRaw\\x00") or data.startswith(b"UnityWeb\\x00")):
        raise AssetBundleInspectError("Không nhận diện được chữ ký UnityFS/UnityRaw/UnityWeb.")

    UnityPy_AOV = _load_unitypy_aov()
    try:
        env = UnityPy_AOV.load(data)
        serialized_files = list(getattr(env, "assets", []) or [])
        container = getattr(env, "container", {}) or {}
        top_files = getattr(env, "files", {}) or {}
    except Exception as exc:
        raise AssetBundleInspectError(
            f"Không phân tích được AssetBundle: {type(exc).__name__}: {exc}"
        ) from exc

    object_type_counts: Counter[str] = Counter()
    external_references: list[dict[str, str]] = []
    serialized_summaries: list[dict[str, Any]] = []
    total_objects = 0
    seen_cabs: set[str] = set()

    for asset_file in serialized_files:
        file_name = _safe_text(getattr(asset_file, "name", ""))
        seen_cabs.add(file_name)
        objects = getattr(asset_file, "objects", {}) or {}
        total_objects += len(objects)
        local_counts: Counter[str] = Counter()
        for obj in objects.values():
            object_type = getattr(getattr(obj, "type", None), "name", "Unknown")
            object_type = _safe_text(object_type, 80)
            local_counts[object_type] += 1
            object_type_counts[object_type] += 1

        externals = getattr(asset_file, "externals", []) or []
        for external in externals:
            external_path = _safe_text(getattr(external, "path", ""))
            if external_path:
                external_references.append({
                    "from": file_name,
                    "to": external_path,
                })

        serialized_summaries.append({
            "name": file_name,
            "unity_version": _safe_text(getattr(asset_file, "unity_version", "")),
            "target_platform": _safe_text(getattr(getattr(asset_file, "target_platform", None), "name", "")),
            "object_count": len(objects),
            "object_types": dict(local_counts.most_common(MAX_REPORTED_OBJECT_TYPES)),
            "external_count": len(externals),
        })

    container_items: list[dict[str, str]] = []
    for path, obj in list(container.items())[:MAX_REPORTED_CONTAINERS]:
        object_type = getattr(getattr(obj, "type", None), "name", "Unknown")
        path_id = getattr(obj, "path_id", None)
        container_items.append({
            "path": _safe_text(path),
            "type": _safe_text(object_type, 80),
            "path_id": _safe_text(path_id),
        })

    return {
        "label": _safe_text(label),
        "bundle_files": len(top_files),
        "serialized_files": serialized_summaries,
        "serialized_file_count": len(serialized_files),
        "object_count": total_objects,
        "object_type_counts": dict(object_type_counts.most_common(MAX_REPORTED_OBJECT_TYPES)),
        "external_references": external_references[:MAX_REPORTED_CONTAINERS],
        "external_reference_count": len(external_references),
        "container_entries": container_items,
        "container_entry_count": len(container),
        "unresolved_external_paths": sorted({
            ref["to"] for ref in external_references
            if Path(ref["to"]).name.lower() not in {Path(name).name.lower() for name in seen_cabs}
        }),
        "read_only": True,
    }
