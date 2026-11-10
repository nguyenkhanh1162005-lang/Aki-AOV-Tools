from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any


MAX_ASSETBUNDLE_BYTES = 64 * 1024 * 1024
MAX_REPORTED_OBJECT_TYPES = 32
MAX_REPORTED_CONTAINERS = 100
MAX_REFERENCE_SCAN_OBJECTS = 10_000
MAX_REFERENCE_SCAN_NODES = 500_000
MAX_REPORTED_REFERENCES = 200


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
    text = str(value or "").replace("\x00", "")
    return text[:limit]


def inspect_assetbundle(data: bytes, label: str = "AssetBundle") -> dict[str, Any]:
    """Read-only inventory of Unity serialized files, object types and external references."""
    if not data:
        raise AssetBundleInspectError("AssetBundle rỗng.")
    if len(data) > MAX_ASSETBUNDLE_BYTES:
        raise AssetBundleInspectError("AssetBundle vượt quá giới hạn phân tích sâu 64 MiB.")
    if not (data.startswith(b"UnityFS\x00") or data.startswith(b"UnityRaw\x00") or data.startswith(b"UnityWeb\x00")):
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
    object_references: list[dict[str, Any]] = []
    reference_scan_errors: list[str] = []
    reference_scan_nodes = 0
    reference_scan_complete = True
    serialized_summaries: list[dict[str, Any]] = []
    total_objects = 0

    for asset_file in serialized_files:
        file_name = _safe_text(getattr(asset_file, "name", ""))
        objects = getattr(asset_file, "objects", {}) or {}
        total_objects += len(objects)
        local_counts: Counter[str] = Counter()
        for obj in objects.values():
            object_type = getattr(getattr(obj, "type", None), "name", "Unknown")
            object_type = _safe_text(object_type, 80)
            local_counts[object_type] += 1
            object_type_counts[object_type] += 1

        externals = getattr(asset_file, "externals", []) or []
        external_names = [ _safe_text(getattr(external, "name", getattr(external, "path", ""))) for external in externals ]

        # Parse bounded typetrees to collect PPtr-shaped references without resolving
        # or changing any object. A truncated scan is explicitly marked incomplete.
        for obj in list(objects.values())[:MAX_REFERENCE_SCAN_OBJECTS]:
            try:
                tree = obj.read_typetree()
            except Exception as exc:
                reference_scan_complete = False
                if len(reference_scan_errors) < 20:
                    reference_scan_errors.append(
                        f"{file_name}:{getattr(obj, 'path_id', '?')}:{type(exc).__name__}"
                    )
                continue
            stack = [tree]
            while stack:
                node = stack.pop()
                reference_scan_nodes += 1
                if reference_scan_nodes > MAX_REFERENCE_SCAN_NODES:
                    reference_scan_complete = False
                    stack.clear()
                    break
                if isinstance(node, dict):
                    if "m_FileID" in node and "m_PathID" in node:
                        try:
                            file_id = int(node["m_FileID"])
                            path_id = int(node["m_PathID"])
                            target = "local" if file_id == 0 else (
                                external_names[file_id - 1]
                                if 0 < file_id <= len(external_names)
                                else "invalid-external-index"
                            )
                            object_references.append({
                                "from_file": file_name,
                                "from_path_id": _safe_text(getattr(obj, "path_id", "")),
                                "file_id": file_id,
                                "path_id": path_id,
                                "target_file": target,
                            })
                        except (TypeError, ValueError, OverflowError):
                            reference_scan_complete = False
                    else:
                        stack.extend(node.values())
                elif isinstance(node, (list, tuple)):
                    stack.extend(node)
        if len(objects) > MAX_REFERENCE_SCAN_OBJECTS:
            reference_scan_complete = False
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

    # Resolve PPtr targets only against serialized files already loaded in this
    # bundle. Missing external CABs are reported as dependencies, not assumed broken:
    # they may be supplied by another bundle or a sidecar resource.
    serialized_by_name = {
        Path(summary["name"]).name.lower(): asset_file
        for summary, asset_file in zip(serialized_summaries, serialized_files)
    }
    serialized_by_exact_name = {
        _safe_text(getattr(asset_file, "name", "")): asset_file
        for asset_file in serialized_files
    }
    reference_integrity = {
        "local_resolved": 0,
        "local_missing": [],
        "external_resolved": 0,
        "external_dependencies": [],
        "invalid_external_indices": [],
    }
    for reference in object_references:
        source_file = serialized_by_exact_name.get(reference["from_file"])
        file_id = reference["file_id"]
        path_id = reference["path_id"]
        if file_id == 0:
            if source_file is not None and path_id in (getattr(source_file, "objects", {}) or {}):
                reference_integrity["local_resolved"] += 1
            else:
                reference_integrity["local_missing"].append({
                    "from_file": reference["from_file"],
                    "from_path_id": reference["from_path_id"],
                    "path_id": path_id,
                })
        elif reference["target_file"] == "invalid-external-index":
            reference_integrity["invalid_external_indices"].append({
                "from_file": reference["from_file"],
                "from_path_id": reference["from_path_id"],
                "file_id": file_id,
                "path_id": path_id,
            })
        else:
            target = serialized_by_name.get(Path(reference["target_file"]).name.lower())
            if target is not None and path_id in (getattr(target, "objects", {}) or {}):
                reference_integrity["external_resolved"] += 1
            else:
                reference_integrity["external_dependencies"].append({
                    "from_file": reference["from_file"],
                    "from_path_id": reference["from_path_id"],
                    "target_file": reference["target_file"],
                    "path_id": path_id,
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
        "object_references": object_references[:MAX_REPORTED_REFERENCES],
        "object_reference_count": len(object_references),
        "reference_scan_complete": reference_scan_complete,
        "reference_scan_nodes": min(reference_scan_nodes, MAX_REFERENCE_SCAN_NODES),
        "reference_scan_errors": reference_scan_errors,
        "reference_integrity": reference_integrity,
        "container_entries": container_items,
        "container_entry_count": len(container),
        "container_inventory_complete": len(container) <= MAX_REPORTED_CONTAINERS,
        # External references may point to resource files as well as CAB files.
        # Do not label them unresolved until all bundle sidecar files are available.
        "external_paths": sorted({ref["to"] for ref in external_references}),
        "read_only": True,
    }



def compare_assetbundle_overlays(base_data: bytes, overlay_data: bytes) -> dict[str, Any]:
    """Compare two bundles conservatively; this never rewrites or certifies a merge."""
    base = inspect_assetbundle(base_data, "base")
    overlay = inspect_assetbundle(overlay_data, "overlay")

    base_versions = {item["unity_version"] for item in base["serialized_files"] if item["unity_version"]}
    overlay_versions = {item["unity_version"] for item in overlay["serialized_files"] if item["unity_version"]}
    base_platforms = {item["target_platform"] for item in base["serialized_files"] if item["target_platform"]}
    overlay_platforms = {item["target_platform"] for item in overlay["serialized_files"] if item["target_platform"]}

    base_container = {item["path"]: item["type"] for item in base["container_entries"]}
    overlay_container = {item["path"]: item["type"] for item in overlay["container_entries"]}
    shared_paths = set(base_container) & set(overlay_container)
    type_conflicts = sorted(
        path for path in shared_paths
        if base_container[path] != overlay_container[path]
    )

    inventory_complete = (
        base["container_inventory_complete"]
        and overlay["container_inventory_complete"]
    )
    version_match = bool(base_versions and overlay_versions and base_versions == overlay_versions)
    platform_match = bool(base_platforms and overlay_platforms and base_platforms == overlay_platforms)

    return {
        "unity_versions_match": version_match,
        "base_unity_versions": sorted(base_versions),
        "overlay_unity_versions": sorted(overlay_versions),
        "platforms_match": platform_match,
        "base_platforms": sorted(base_platforms),
        "overlay_platforms": sorted(overlay_platforms),
        "container_inventory_complete": inventory_complete,
        "shared_container_paths": len(shared_paths),
        "replacement_candidates": sorted(shared_paths - set(type_conflicts))[:MAX_REPORTED_CONTAINERS],
        "new_container_paths": sorted(set(overlay_container) - set(base_container))[:MAX_REPORTED_CONTAINERS],
        "container_type_conflicts": type_conflicts[:MAX_REPORTED_CONTAINERS],
        "external_paths_added": sorted(
            set(overlay["external_paths"]) - set(base["external_paths"])
        )[:MAX_REPORTED_CONTAINERS],
        "structural_checks_pass": (
            version_match and platform_match and inventory_complete and not type_conflicts
        ),
        # Unity object references, preload tables, CAB remapping and bundle metadata
        # still need a version-specific adapter before any actual merge is safe.
        "merge_safe": False,
    }
