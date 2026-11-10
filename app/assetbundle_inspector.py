from __future__ import annotations

import sys
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any


MAX_ASSETBUNDLE_BYTES = 64 * 1024 * 1024
MAX_REPORTED_OBJECT_TYPES = 32
MAX_REPORTED_CONTAINERS = 100
MAX_REFERENCE_SCAN_OBJECTS = 10_000
MAX_REFERENCE_SCAN_NODES = 500_000
MAX_REPORTED_REFERENCES = 200
MAX_OBJECT_ID_INDEX_ENTRIES = 100_000
MAX_OBJECT_HASH_ENTRIES = 2_000
MAX_OBJECT_HASH_BYTES = 16 * 1024 * 1024
MAX_SINGLE_OBJECT_HASH_BYTES = 2 * 1024 * 1024


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
    indexed_object_ids = 0
    indexed_object_hashes = 0
    hashed_object_bytes = 0

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

        remaining_id_slots = max(0, MAX_OBJECT_ID_INDEX_ENTRIES - indexed_object_ids)
        indexed_objects = list(objects.items())[:remaining_id_slots]
        object_ids = [path_id for path_id, _ in indexed_objects]
        object_types_by_path_id = {
            str(path_id): _safe_text(getattr(getattr(obj, "type", None), "name", "Unknown"), 80)
            for path_id, obj in indexed_objects
        }
        indexed_object_ids += len(object_ids)

        # Hash a bounded amount of raw Object payload. get_raw_data() restores
        # the reader cursor and does not modify the serialized file.
        object_payload_hashes: dict[str, str] = {}
        object_payload_hashes_complete = len(objects) == 0
        if indexed_object_hashes < MAX_OBJECT_HASH_ENTRIES and hashed_object_bytes < MAX_OBJECT_HASH_BYTES:
            object_payload_hashes_complete = True
            for path_id, obj in objects.items():
                if indexed_object_hashes >= MAX_OBJECT_HASH_ENTRIES:
                    object_payload_hashes_complete = False
                    break
                declared_size = getattr(obj, "byte_size", None)
                if not isinstance(declared_size, int) or declared_size < 0 or declared_size > MAX_SINGLE_OBJECT_HASH_BYTES:
                    object_payload_hashes_complete = False
                    continue
                if hashed_object_bytes + declared_size > MAX_OBJECT_HASH_BYTES:
                    object_payload_hashes_complete = False
                    break
                try:
                    raw_reader = getattr(obj, "get_raw_data", None)
                    if callable(raw_reader):
                        raw_data = raw_reader()
                    elif getattr(obj, "data", None) is not None:
                        raw_data = bytes(obj.data)
                    else:
                        object_payload_hashes_complete = False
                        continue
                    raw_data = bytes(raw_data)
                    if len(raw_data) != declared_size:
                        object_payload_hashes_complete = False
                        continue
                    object_payload_hashes[str(path_id)] = hashlib.sha256(raw_data).hexdigest()
                    indexed_object_hashes += 1
                    hashed_object_bytes += len(raw_data)
                except Exception:
                    object_payload_hashes_complete = False
        else:
            object_payload_hashes_complete = False
        if len(object_payload_hashes) != len(objects):
            object_payload_hashes_complete = False

        serialized_summaries.append({
            "name": file_name,
            "object_path_ids": object_ids,
            "object_types_by_path_id": object_types_by_path_id,
            "object_id_inventory_complete": len(object_ids) == len(objects),
            "object_payload_hashes": object_payload_hashes,
            "object_payload_hashes_complete": object_payload_hashes_complete,
            "unity_version": _safe_text(getattr(asset_file, "unity_version", "")),
            "target_platform": _safe_text(getattr(getattr(asset_file, "target_platform", None), "name", "")),
            "object_count": len(objects),
            "object_types": dict(local_counts.most_common(MAX_REPORTED_OBJECT_TYPES)),
            "external_count": len(externals),
        })

    # Resolve PPtr targets only against serialized files already loaded in this
    # bundle. Missing external CABs are reported as dependencies, not assumed broken:
    # they may be supplied by another bundle or a sidecar resource.
    serialized_by_name: dict[str, list[Any]] = {}
    for summary, asset_file in zip(serialized_summaries, serialized_files):
        basename = Path(summary["name"]).name.lower()
        serialized_by_name.setdefault(basename, []).append(asset_file)
    serialized_by_exact_name = {
        _safe_text(getattr(asset_file, "name", "")): asset_file
        for asset_file in serialized_files
    }
    reference_integrity = {
        "local_resolved": 0,
        "local_missing": [],
        "external_resolved": 0,
        "external_dependencies": [],
        "external_ambiguous_targets": [],
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
            targets = serialized_by_name.get(Path(reference["target_file"]).name.lower(), [])
            if len(targets) > 1:
                reference_integrity["external_ambiguous_targets"].append({
                    "from_file": reference["from_file"],
                    "from_path_id": reference["from_path_id"],
                    "target_file": reference["target_file"],
                    "path_id": path_id,
                })
            elif targets and path_id in (getattr(targets[0], "objects", {}) or {}):
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

    # An external PPtr unresolved inside the overlay may be satisfied by a CAB
    # shipped in the base bundle. Match by CAB basename, then verify the PathID.
    base_cabs: dict[str, list[dict[str, Any]]] = {}
    for serialized in base["serialized_files"]:
        basename = Path(serialized["name"]).name.lower()
        base_cabs.setdefault(basename, []).append(serialized)

    overlay_cabs: dict[str, list[dict[str, Any]]] = {}
    for serialized in overlay["serialized_files"]:
        basename = Path(serialized["name"]).name.lower()
        overlay_cabs.setdefault(basename, []).append(serialized)

    object_id_overlaps = []
    object_id_inventory_incomplete = False
    ambiguous_object_id_cabs = []
    for basename in sorted(set(base_cabs) & set(overlay_cabs)):
        base_matches = base_cabs[basename]
        overlay_matches = overlay_cabs[basename]
        if len(base_matches) != 1 or len(overlay_matches) != 1:
            ambiguous_object_id_cabs.append(basename)
            continue
        base_item = base_matches[0]
        overlay_item = overlay_matches[0]
        if not base_item["object_id_inventory_complete"] or not overlay_item["object_id_inventory_complete"]:
            object_id_inventory_incomplete = True
            continue
        base_ids = set(base_item["object_path_ids"])
        overlay_ids = set(overlay_item["object_path_ids"])
        shared_ids = base_ids & overlay_ids
        object_type_conflict_ids = sorted(
            path_id for path_id in shared_ids
            if base_item["object_types_by_path_id"].get(str(path_id))
            != overlay_item["object_types_by_path_id"].get(str(path_id))
        )
        payload_identical_ids = []
        payload_changed_ids = []
        payload_unknown_ids = []
        if base_item["object_payload_hashes_complete"] and overlay_item["object_payload_hashes_complete"]:
            for path_id in shared_ids:
                base_hash = base_item["object_payload_hashes"].get(str(path_id))
                overlay_hash = overlay_item["object_payload_hashes"].get(str(path_id))
                if base_hash is None or overlay_hash is None:
                    payload_unknown_ids.append(path_id)
                elif base_hash == overlay_hash:
                    payload_identical_ids.append(path_id)
                else:
                    payload_changed_ids.append(path_id)
        else:
            payload_unknown_ids = list(shared_ids)

        object_id_overlaps.append({
            "cab": basename,
            "shared_path_id_count": len(shared_ids),
            "base_only_path_id_count": len(base_ids - overlay_ids),
            "overlay_only_path_id_count": len(overlay_ids - base_ids),
            "type_conflict_count": len(object_type_conflict_ids),
            "type_conflict_path_ids": object_type_conflict_ids[:MAX_REPORTED_REFERENCES],
            "payload_identical_count": len(payload_identical_ids),
            "payload_changed_count": len(payload_changed_ids),
            "payload_unknown_count": len(payload_unknown_ids),
            "payload_changed_path_ids": sorted(payload_changed_ids)[:MAX_REPORTED_REFERENCES],
        })

    object_id_types_match = (
        not object_id_inventory_incomplete
        and not ambiguous_object_id_cabs
        and all(item["type_conflict_count"] == 0 for item in object_id_overlaps)
    )

    cross_bundle_references = {
        "resolved": [],
        "missing_path_ids": [],
        "ambiguous_cab_names": [],
        "unknown_cabs": [],
        "index_incomplete": False,
    }
    for dependency in overlay["reference_integrity"]["external_dependencies"]:
        basename = Path(dependency["target_file"]).name.lower()
        candidates = base_cabs.get(basename, [])
        item = {
            "from_file": dependency["from_file"],
            "from_path_id": dependency["from_path_id"],
            "target_file": dependency["target_file"],
            "path_id": dependency["path_id"],
        }
        if not candidates:
            cross_bundle_references["unknown_cabs"].append(item)
        elif len(candidates) > 1:
            cross_bundle_references["ambiguous_cab_names"].append(item)
        else:
            candidate = candidates[0]
            if not candidate["object_id_inventory_complete"]:
                cross_bundle_references["index_incomplete"] = True
                cross_bundle_references["unknown_cabs"].append(item)
            elif dependency["path_id"] in candidate["object_path_ids"]:
                cross_bundle_references["resolved"].append(item)
            else:
                cross_bundle_references["missing_path_ids"].append(item)

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
        "object_id_overlaps": object_id_overlaps,
        "object_id_inventory_incomplete": object_id_inventory_incomplete,
        "ambiguous_object_id_cabs": ambiguous_object_id_cabs[:MAX_REPORTED_REFERENCES],
        "object_id_types_match": object_id_types_match,
        "cross_bundle_references": {
            "resolved_count": len(cross_bundle_references["resolved"]),
            "missing_path_id_count": len(cross_bundle_references["missing_path_ids"]),
            "ambiguous_cab_count": len(cross_bundle_references["ambiguous_cab_names"]),
            "unknown_cab_count": len(cross_bundle_references["unknown_cabs"]),
            "index_incomplete": cross_bundle_references["index_incomplete"],
            "resolved": cross_bundle_references["resolved"][:MAX_REPORTED_REFERENCES],
            "missing_path_ids": cross_bundle_references["missing_path_ids"][:MAX_REPORTED_REFERENCES],
            "ambiguous_cab_names": cross_bundle_references["ambiguous_cab_names"][:MAX_REPORTED_REFERENCES],
            "unknown_cabs": cross_bundle_references["unknown_cabs"][:MAX_REPORTED_REFERENCES],
        },
        "structural_checks_pass": (
            version_match and platform_match and inventory_complete and not type_conflicts
            and object_id_types_match
        ),
        # Unity object references, preload tables, CAB remapping and bundle metadata
        # still need a version-specific adapter before any actual merge is safe.
        "merge_safe": False,
    }
