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


def _inspect_assetbundle_object(asset_file: Any, obj: Any) -> dict[str, Any]:
    """Inspect Unity AssetBundle preload/container metadata without resolving or editing pointers."""
    parsed = obj.read(return_typetree_on_error=False)
    preload_table = getattr(parsed, "m_PreloadTable", []) or []
    entries = getattr(parsed, "m_ContainerEntries", None)
    if entries is None:
        entries = list((getattr(parsed, "m_Container", {}) or {}).items())

    external_names = [
        _safe_text(getattr(external, "name", getattr(external, "path", "")))
        for external in (getattr(asset_file, "externals", []) or [])
    ]
    local_objects = getattr(asset_file, "objects", {}) or {}
    invalid_preload_ranges = []
    invalid_pointer_indices = []
    missing_local_pointers = []
    external_pointers = []
    pointer_count = 0
    preload_digest = hashlib.sha256()
    container_digest = hashlib.sha256()
    inspection_complete = (
        len(preload_table) <= MAX_REFERENCE_SCAN_OBJECTS
        and len(entries) <= MAX_REFERENCE_SCAN_OBJECTS
    )

    def inspect_pointer(pointer: Any, source: str) -> dict[str, Any]:
        nonlocal pointer_count, inspection_complete
        pointer_count += 1
        file_id = int(getattr(pointer, "file_id", 0))
        path_id = int(getattr(pointer, "path_id", 0))
        result = {"source": source, "file_id": file_id, "path_id": path_id}
        if file_id == 0:
            if path_id not in local_objects and len(missing_local_pointers) < MAX_REPORTED_REFERENCES:
                missing_local_pointers.append(result)
            elif path_id not in local_objects:
                inspection_complete = False
        elif 0 < file_id <= len(external_names):
            result["target_file"] = external_names[file_id - 1]
            if len(external_pointers) < MAX_REPORTED_REFERENCES:
                external_pointers.append(result)
            else:
                inspection_complete = False
        else:
            if len(invalid_pointer_indices) < MAX_REPORTED_REFERENCES:
                invalid_pointer_indices.append(result)
            else:
                inspection_complete = False
        return result

    preload_pointers = []
    for index, pointer in enumerate(preload_table):
        if index >= MAX_REFERENCE_SCAN_OBJECTS:
            inspection_complete = False
            break
        item = inspect_pointer(pointer, "preload")
        preload_digest.update(
            f"{item['file_id']}:{item['path_id']}\n".encode("ascii")
        )
        if len(preload_pointers) < MAX_REPORTED_REFERENCES:
            preload_pointers.append(item)

    container_paths = []
    container_records = []
    for index, (key, asset_info) in enumerate(entries):
        if index >= MAX_REFERENCE_SCAN_OBJECTS:
            inspection_complete = False
            break
        container_paths.append(str(key))
        preload_index = int(getattr(asset_info, "preload_index", -1))
        preload_size = int(getattr(asset_info, "preload_size", -1))
        if (
            preload_index < 0
            or preload_size < 0
            or preload_index + preload_size > len(preload_table)
        ):
            if len(invalid_preload_ranges) < MAX_REPORTED_REFERENCES:
                invalid_preload_ranges.append({
                    "path": _safe_text(key),
                    "preload_index": preload_index,
                    "preload_size": preload_size,
                })
            else:
                inspection_complete = False
        pointer = inspect_pointer(getattr(asset_info, "asset", None), "container")
        container_digest.update(
            (
                f"{key}\0{preload_index}\0{preload_size}\0"
                f"{pointer['file_id']}\0{pointer['path_id']}\n"
            ).encode("utf-8", "replace")
        )
        if len(container_records) < MAX_REPORTED_REFERENCES:
            container_records.append({
                "path": _safe_text(key),
                "preload_index": preload_index,
                "preload_size": preload_size,
                "asset": pointer,
            })

    main_asset = getattr(parsed, "m_MainAsset", None)
    main_info = None
    if main_asset is not None:
        main_index = int(getattr(main_asset, "preload_index", -1))
        main_size = int(getattr(main_asset, "preload_size", -1))
        if main_index < 0 or main_size < 0 or main_index + main_size > len(preload_table):
            invalid_preload_ranges.append({
                "path": "<main-asset>",
                "preload_index": main_index,
                "preload_size": main_size,
            })
        main_info = {
            "preload_index": main_index,
            "preload_size": main_size,
            "asset": inspect_pointer(getattr(main_asset, "asset", None), "main-asset"),
        }

    path_counts = Counter(container_paths)
    metadata_integrity_pass = (
        not invalid_preload_ranges
        and not invalid_pointer_indices
        and not missing_local_pointers
        and not any(count > 1 for count in path_counts.values())
    )
    dependencies = getattr(parsed, "m_Dependencies", []) or []
    dependency_digest = hashlib.sha256()
    for dependency in dependencies:
        dependency_digest.update(str(dependency).encode("utf-8", "replace"))
        dependency_digest.update(b"\0")
    main_asset_digest = hashlib.sha256()
    if main_info is not None:
        main_asset_digest.update(
            (
                f"{main_info['preload_index']}:{main_info['preload_size']}:"
                f"{main_info['asset']['file_id']}:{main_info['asset']['path_id']}"
            ).encode("ascii")
        )
    return {
        "path_id": _safe_text(getattr(obj, "path_id", "")),
        "assetbundle_name": _safe_text(getattr(parsed, "m_AssetBundleName", "")),
        "dependencies": [_safe_text(item) for item in dependencies[:MAX_REPORTED_REFERENCES]],
        "dependency_count": len(dependencies),
        "dependencies_sha256": dependency_digest.hexdigest(),
        "main_asset_sha256": main_asset_digest.hexdigest(),
        "preload_table_count": len(preload_table),
        "container_entry_count": len(entries),
        "duplicate_container_path_count": sum(count - 1 for count in path_counts.values() if count > 1),
        "metadata_integrity_pass": metadata_integrity_pass,
        "preload_table_sha256": preload_digest.hexdigest(),
        "container_metadata_sha256": container_digest.hexdigest(),
        "invalid_preload_ranges": invalid_preload_ranges,
        "pointer_count": pointer_count,
        "invalid_pointer_indices": invalid_pointer_indices,
        "missing_local_pointers": missing_local_pointers,
        "external_pointers": external_pointers,
        "preload_pointers": preload_pointers,
        "container_records": container_records,
        "main_asset": main_info,
        "inspection_complete": inspection_complete,
        "is_streamed_scene_assetbundle": bool(
            getattr(parsed, "m_IsStreamedSceneAssetBundle", False)
        ),
    }

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
    assetbundle_metadata: list[dict[str, Any]] = []
    assetbundle_metadata_errors: list[str] = []
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
            if object_type == "AssetBundle":
                try:
                    assetbundle_metadata.append(_inspect_assetbundle_object(asset_file, obj))
                except Exception as exc:
                    if len(assetbundle_metadata_errors) < 20:
                        assetbundle_metadata_errors.append(
                            f"{file_name}:{getattr(obj, 'path_id', '?')}:{type(exc).__name__}"
                        )

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
        "assetbundle_metadata": assetbundle_metadata[:MAX_REPORTED_CONTAINERS],
        "assetbundle_metadata_count": len(assetbundle_metadata),
        "assetbundle_metadata_complete": (
            not assetbundle_metadata_errors
            and all(item["inspection_complete"] for item in assetbundle_metadata)
        ),
        "assetbundle_metadata_errors": assetbundle_metadata_errors,
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

    base_assetbundle_names: dict[str, list[dict[str, Any]]] = {}
    overlay_assetbundle_names: dict[str, list[dict[str, Any]]] = {}
    for metadata in base.get("assetbundle_metadata", []):
        name = metadata["assetbundle_name"].strip().lower()
        if name:
            base_assetbundle_names.setdefault(name, []).append(metadata)
    for metadata in overlay.get("assetbundle_metadata", []):
        name = metadata["assetbundle_name"].strip().lower()
        if name:
            overlay_assetbundle_names.setdefault(name, []).append(metadata)

    assetbundle_metadata_comparisons = []
    ambiguous_assetbundle_metadata_names = []
    for name in sorted(set(base_assetbundle_names) | set(overlay_assetbundle_names)):
        base_matches = base_assetbundle_names.get(name, [])
        overlay_matches = overlay_assetbundle_names.get(name, [])
        if not base_matches or not overlay_matches:
            continue
        if len(base_matches) != 1 or len(overlay_matches) != 1:
            ambiguous_assetbundle_metadata_names.append(name)
            continue
        base_meta = base_matches[0]
        overlay_meta = overlay_matches[0]
        assetbundle_metadata_comparisons.append({
            "assetbundle_name": name,
            "preload_count_match": (
                base_meta["preload_table_count"] == overlay_meta["preload_table_count"]
            ),
            "preload_table_match": (
                base_meta["preload_table_sha256"] == overlay_meta["preload_table_sha256"]
            ),
            "container_count_match": (
                base_meta["container_entry_count"] == overlay_meta["container_entry_count"]
            ),
            "container_metadata_match": (
                base_meta["container_metadata_sha256"] == overlay_meta["container_metadata_sha256"]
            ),
            "dependencies_match": (
                base_meta["dependency_count"] == overlay_meta["dependency_count"]
                and base_meta["dependencies_sha256"] == overlay_meta["dependencies_sha256"]
            ),
            "main_asset_match": (
                base_meta["main_asset_sha256"] == overlay_meta["main_asset_sha256"]
            ),
            "base_preload_count": base_meta["preload_table_count"],
            "overlay_preload_count": overlay_meta["preload_table_count"],
            "base_container_count": base_meta["container_entry_count"],
            "overlay_container_count": overlay_meta["container_entry_count"],
            "base_inspection_complete": base_meta["inspection_complete"],
            "overlay_inspection_complete": overlay_meta["inspection_complete"],
            "base_integrity_pass": base_meta["metadata_integrity_pass"],
            "overlay_integrity_pass": overlay_meta["metadata_integrity_pass"],
        })

    assetbundle_metadata_integrity_pass = all(
        item["metadata_integrity_pass"]
        for item in base.get("assetbundle_metadata", []) + overlay.get("assetbundle_metadata", [])
    ) and all(
        item["inspection_complete"]
        for item in base.get("assetbundle_metadata", []) + overlay.get("assetbundle_metadata", [])
    )

    base_assetbundle_name_set = set(base_assetbundle_names)
    overlay_assetbundle_name_set = set(overlay_assetbundle_names)
    unmatched_base_assetbundle_names = sorted(base_assetbundle_name_set - overlay_assetbundle_name_set)
    unmatched_overlay_assetbundle_names = sorted(overlay_assetbundle_name_set - base_assetbundle_name_set)

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
        "assetbundle_metadata_comparisons": assetbundle_metadata_comparisons,
        "ambiguous_assetbundle_metadata_names": ambiguous_assetbundle_metadata_names,
        "unmatched_base_assetbundle_names": unmatched_base_assetbundle_names,
        "unmatched_overlay_assetbundle_names": unmatched_overlay_assetbundle_names,
        "assetbundle_metadata_integrity_pass": assetbundle_metadata_integrity_pass,
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
            and assetbundle_metadata_integrity_pass
        ),
        # Unity object references, preload tables, CAB remapping and bundle metadata
        # still need a version-specific adapter before any actual merge is safe.
        "merge_safe": False,
    }
