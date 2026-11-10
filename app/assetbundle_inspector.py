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
    preload_pointer_inventory = []
    for index, pointer in enumerate(preload_table):
        if index >= MAX_REFERENCE_SCAN_OBJECTS:
            inspection_complete = False
            break
        item = inspect_pointer(pointer, "preload")
        preload_digest.update(
            f"{item['file_id']}:{item['path_id']}\n".encode("ascii")
        )
        preload_pointer_inventory.append(item)
        if len(preload_pointers) < MAX_REPORTED_REFERENCES:
            preload_pointers.append(item)

    container_paths = []
    container_records = []
    container_record_inventory = []
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
        record = {
            "path": _safe_text(key),
            "preload_index": preload_index,
            "preload_size": preload_size,
            "asset": pointer,
        }
        container_record_inventory.append(record)
        if len(container_records) < MAX_REPORTED_REFERENCES:
            container_records.append(record)

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
        "preload_pointer_inventory": preload_pointer_inventory,
        "container_records": container_records,
        "container_record_inventory": container_record_inventory,
        "main_asset": main_info,
        "inspection_complete": inspection_complete,
        "is_streamed_scene_assetbundle": bool(
            getattr(parsed, "m_IsStreamedSceneAssetBundle", False)
        ),
    }



def build_cross_cab_reference_merge_plan(
    base_cabs: dict[str, dict[str, Any]],
    overlay_cabs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a dry-run PathID/external-table/PPtr plan across serialized CABs.

    CAB keys must be stable normalized identities shared by external table
    entries. Each descriptor supplies path_ids, types, payload_hashes,
    externals and a complete PPtr reference inventory. No bytes are changed.
    """
    def normalize(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        path = value.strip().replace(chr(92), "/")
        while "//" in path:
            path = path.replace("//", "/")
        if path.startswith("/") or ":" in path or any(
            part in ("", ".", "..") for part in path.split("/")
        ):
            return None
        return path

    if not isinstance(base_cabs, dict) or not isinstance(overlay_cabs, dict):
        return {"ready": False, "reason": "CAB inventories must be mappings.", "cabs": {}}
    base = {}
    overlay = {}
    for source, target, label in (
        (base_cabs, base, "base"),
        (overlay_cabs, overlay, "overlay"),
    ):
        for raw_key, descriptor in source.items():
            key = normalize(raw_key)
            if key is None or key in target or not isinstance(descriptor, dict):
                return {"ready": False, "reason": f"{label} CAB identity is invalid or duplicated.", "cabs": {}}
            target[key] = descriptor

    for label, inventory in (("base", base), ("overlay", overlay)):
        for key, descriptor in inventory.items():
            if descriptor.get("inventories_complete") is not True:
                return {"ready": False, "reason": f"{label} CAB inventory is incomplete: {key}", "cabs": {}}
            if not all(isinstance(descriptor.get(field), expected) for field, expected in (
                ("path_ids", list), ("types", dict), ("payload_hashes", dict),
                ("externals", list), ("references", list),
            )):
                return {"ready": False, "reason": f"{label} CAB descriptor is malformed: {key}", "cabs": {}}

    path_plans = {}
    final_path_ids = {}
    for key, descriptor in overlay.items():
        base_descriptor = base.get(key, {})
        plan = build_path_id_remap_plan(
            base_descriptor.get("path_ids", []),
            descriptor["path_ids"],
            base_descriptor.get("types", {}),
            descriptor["types"],
            base_descriptor.get("payload_hashes", {}),
            descriptor["payload_hashes"],
            inventories_complete=True,
        )
        if not plan.get("ready"):
            return {"ready": False, "reason": f"PathID plan blocked for {key}: {plan.get('reason')}",
                    "blocked_cab": key, "cabs": {}}
        remap = plan["remap"]
        merged_ids = set(base_descriptor.get("path_ids", []))
        merged_ids.update(remap.get(str(value), value) for value in descriptor["path_ids"])
        path_plans[key] = plan
        final_path_ids[key] = merged_ids

    cab_plans = {}
    for key, descriptor in overlay.items():
        base_descriptor = base.get(key, {})
        external_plan = build_external_file_id_remap_plan(
            base_descriptor.get("externals", []), descriptor["externals"]
        )
        if not external_plan.get("ready"):
            return {"ready": False, "reason": f"External table plan blocked for {key}: {external_plan.get('reason')}",
                    "blocked_cab": key, "cabs": {}}

        external_path_id_remaps = {}
        external_target_path_ids = {}
        for old_file_id, external_path in enumerate(descriptor["externals"], start=1):
            target_key = normalize(external_path)
            final_file_id = external_plan["file_id_remap"].get(str(old_file_id))
            if target_key is None or final_file_id is None:
                continue
            if target_key not in final_path_ids:
                continue
            target_plan = path_plans[target_key]
            external_path_id_remaps[str(old_file_id)] = target_plan["remap"]
            external_target_path_ids[str(final_file_id)] = final_path_ids[target_key]

        local_ids = final_path_ids[key]
        pptr_plan = build_pptr_remap_plan(
            descriptor["references"],
            external_plan["file_id_remap"],
            path_plans[key]["remap"],
            external_path_id_remaps,
            local_target_path_ids=local_ids,
            external_target_path_ids=external_target_path_ids,
        )
        if not pptr_plan.get("ready"):
            return {"ready": False, "reason": f"PPtr plan blocked for {key}: {pptr_plan.get('reason')}",
                    "blocked_cab": key, "blocked_references": pptr_plan.get("blocked_references", []),
                    "cabs": {}}
        cab_plans[key] = {
            "path_id_plan": path_plans[key],
            "external_table_plan": external_plan,
            "pptr_plan": pptr_plan,
        }

    return {
        "ready": True,
        "cabs": cab_plans,
        "matched_base_cabs": sorted(set(base) & set(overlay)),
        "overlay_only_cabs": sorted(set(overlay) - set(base)),
        "scope": "cross-CAB reference preflight only; object bytes, metadata tables, sidecars and bundle serialization are not changed",
    }



def build_assetbundle_metadata_remap_plan(
    metadata: dict[str, Any],
    file_id_remap: dict[str, int],
    local_path_id_remap: dict[str, int],
    external_path_id_remaps: dict[str, dict[str, int]],
    *,
    local_target_path_ids: set[int],
    external_target_path_ids: dict[str, set[int]],
) -> dict[str, Any]:
    """Plan pointer rewrites for complete AssetBundle metadata inventories.

    Preload indices and sizes are preserved. No table is combined or serialized.
    """
    if not isinstance(metadata, dict) or not metadata.get("inspection_complete"):
        return {"ready": False, "reason": "AssetBundle metadata inventory is incomplete.",
                "rewritten": {}, "blocked_references": []}
    preload = metadata.get("preload_pointer_inventory")
    containers = metadata.get("container_record_inventory")
    if not isinstance(preload, list) or not isinstance(containers, list):
        return {"ready": False, "reason": "Complete preload/container pointer inventories are unavailable.",
                "rewritten": {}, "blocked_references": []}
    preload_count = metadata.get("preload_table_count")
    if not isinstance(preload_count, int) or preload_count != len(preload):
        return {"ready": False, "reason": "Preload pointer inventory count does not match metadata.",
                "rewritten": {}, "blocked_references": []}

    references = []
    locations = []
    for index, pointer in enumerate(preload):
        if not isinstance(pointer, dict):
            return {"ready": False, "reason": "Invalid preload pointer inventory entry.",
                    "rewritten": {}, "blocked_references": []}
        references.append({"file_id": pointer.get("file_id"), "path_id": pointer.get("path_id")})
        locations.append(("preload", index))

    for index, record in enumerate(containers):
        if not isinstance(record, dict) or not isinstance(record.get("asset"), dict):
            return {"ready": False, "reason": "Invalid Container pointer inventory entry.",
                    "rewritten": {}, "blocked_references": []}
        start = record.get("preload_index")
        size = record.get("preload_size")
        if (not isinstance(start, int) or not isinstance(size, int)
                or start < 0 or size < 0 or start + size > preload_count):
            return {"ready": False, "reason": "Container preload range is invalid.",
                    "rewritten": {}, "blocked_references": []}
        pointer = record["asset"]
        references.append({"file_id": pointer.get("file_id"), "path_id": pointer.get("path_id")})
        locations.append(("container", index))

    main_asset = metadata.get("main_asset")
    if main_asset is not None:
        if not isinstance(main_asset, dict) or not isinstance(main_asset.get("asset"), dict):
            return {"ready": False, "reason": "Invalid MainAsset pointer inventory entry.",
                    "rewritten": {}, "blocked_references": []}
        start = main_asset.get("preload_index")
        size = main_asset.get("preload_size")
        if (not isinstance(start, int) or not isinstance(size, int)
                or start < 0 or size < 0 or start + size > preload_count):
            return {"ready": False, "reason": "MainAsset preload range is invalid.",
                    "rewritten": {}, "blocked_references": []}
        pointer = main_asset["asset"]
        references.append({"file_id": pointer.get("file_id"), "path_id": pointer.get("path_id")})
        locations.append(("main_asset", 0))

    plan = build_pptr_remap_plan(
        references, file_id_remap, local_path_id_remap, external_path_id_remaps,
        local_target_path_ids=local_target_path_ids,
        external_target_path_ids=external_target_path_ids,
    )
    if not plan["ready"]:
        return {"ready": False, "reason": plan["reason"], "rewritten": {},
                "blocked_references": plan["blocked_references"]}

    rewritten = {"preload": [], "container": [], "main_asset": None}
    for (section, _index), pointer in zip(locations, plan["rewritten_references"]):
        value = {"file_id": pointer["file_id"], "path_id": pointer["path_id"]}
        if section == "preload":
            rewritten["preload"].append(value)
        elif section == "container":
            rewritten["container"].append(value)
        else:
            rewritten["main_asset"] = value
    return {
        "ready": True,
        "reason": "AssetBundle metadata pointers planned; preload ranges and indices are preserved.",
        "rewritten": rewritten,
        "preload_ranges_preserved": True,
        "container_entry_count": len(containers),
        "preload_pointer_count": len(preload),
        "scope": "metadata pointer plan only; no table concatenation, dependency rewrite or serialization",
    }




def build_assetbundle_metadata_merge_plan(
    base_metadata: dict[str, Any],
    overlay_metadata: dict[str, Any],
    overlay_rewritten: dict[str, Any],
) -> dict[str, Any]:
    """Plan concatenation of two compatible AssetBundle metadata inventories.

    Overlay pointers must already have passed build_assetbundle_metadata_remap_plan.
    Conflicting Container keys or two MainAsset records are rejected conservatively.
    """
    for label, metadata in (("base", base_metadata), ("overlay", overlay_metadata)):
        if not isinstance(metadata, dict) or not metadata.get("inspection_complete"):
            return {"ready": False, "reason": f"{label} metadata inventory is incomplete."}
        if not isinstance(metadata.get("preload_pointer_inventory"), list):
            return {"ready": False, "reason": f"{label} preload inventory is unavailable."}
        if not isinstance(metadata.get("container_record_inventory"), list):
            return {"ready": False, "reason": f"{label} Container inventory is unavailable."}
        if metadata.get("preload_table_count") != len(metadata["preload_pointer_inventory"]):
            return {"ready": False, "reason": f"{label} preload inventory count mismatch."}

    if not isinstance(overlay_rewritten, dict) or not overlay_rewritten.get("ready"):
        return {"ready": False, "reason": "Overlay metadata pointers have not passed remap validation."}
    rewritten = overlay_rewritten.get("rewritten")
    if not isinstance(rewritten, dict):
        return {"ready": False, "reason": "Overlay rewritten metadata is missing."}

    base_name = base_metadata.get("assetbundle_name", "")
    overlay_name = overlay_metadata.get("assetbundle_name", "")
    if base_name and overlay_name and base_name != overlay_name:
        return {"ready": False, "reason": "AssetBundle names differ; metadata ownership is ambiguous."}

    base_preload = base_metadata["preload_pointer_inventory"]
    overlay_preload = rewritten.get("preload")
    base_containers = base_metadata["container_record_inventory"]
    overlay_containers = overlay_metadata["container_record_inventory"]
    rewritten_containers = rewritten.get("container")
    if not all(isinstance(value, list) for value in (overlay_preload, rewritten_containers)):
        return {"ready": False, "reason": "Overlay preload or Container remap inventory is incomplete."}
    if len(overlay_preload) != len(overlay_metadata["preload_pointer_inventory"]):
        return {"ready": False, "reason": "Overlay preload remap count mismatch."}
    if len(rewritten_containers) != len(overlay_containers):
        return {"ready": False, "reason": "Overlay Container remap count mismatch."}
    for pointer in list(overlay_preload) + list(rewritten_containers):
        if (not isinstance(pointer, dict)
                or not isinstance(pointer.get("file_id"), int)
                or isinstance(pointer.get("file_id"), bool)
                or pointer["file_id"] < 0
                or not isinstance(pointer.get("path_id"), int)
                or isinstance(pointer.get("path_id"), bool)):
            return {"ready": False, "reason": "Overlay remapped metadata contains an invalid PPtr."}
    for record in base_containers:
        start = record.get("preload_index") if isinstance(record, dict) else None
        size = record.get("preload_size") if isinstance(record, dict) else None
        if (not isinstance(start, int) or not isinstance(size, int)
                or start < 0 or size < 0 or start + size > len(base_preload)
                or not isinstance(record.get("asset"), dict)):
            return {"ready": False, "reason": "Base Container preload range or pointer is invalid."}

    base_main = base_metadata.get("main_asset")
    overlay_main = rewritten.get("main_asset")
    if base_main is not None and overlay_main is not None:
        return {"ready": False, "reason": "Both bundles define MainAsset; automatic selection is unsafe."}
    if base_main is not None:
        start = base_main.get("preload_index") if isinstance(base_main, dict) else None
        size = base_main.get("preload_size") if isinstance(base_main, dict) else None
        if (not isinstance(start, int) or not isinstance(size, int)
                or start < 0 or size < 0 or start + size > len(base_preload)
                or not isinstance(base_main.get("asset"), dict)):
            return {"ready": False, "reason": "Base MainAsset preload range or pointer is invalid."}
    if overlay_main is not None and (
        not isinstance(overlay_main, dict)
        or not isinstance(overlay_main.get("file_id"), int)
        or isinstance(overlay_main.get("file_id"), bool)
        or overlay_main["file_id"] < 0
        or not isinstance(overlay_main.get("path_id"), int)
        or isinstance(overlay_main.get("path_id"), bool)
    ):
        return {"ready": False, "reason": "Overlay remapped MainAsset pointer is invalid."}

    base_keys = [record.get("path") for record in base_containers if isinstance(record, dict)]
    overlay_keys = [record.get("path") for record in overlay_containers if isinstance(record, dict)]
    if len(base_keys) != len(base_containers) or len(overlay_keys) != len(overlay_containers):
        return {"ready": False, "reason": "Container inventory contains invalid records."}
    if len(set(base_keys)) != len(base_keys) or len(set(overlay_keys)) != len(overlay_keys):
        return {"ready": False, "reason": "Duplicate Container keys exist within an input bundle."}
    collisions = sorted(set(base_keys) & set(overlay_keys))
    if collisions:
        return {"ready": False, "reason": "Container path collisions require an explicit replacement policy.",
                "container_path_collisions": collisions[:MAX_REPORTED_REFERENCES]}

    preload_offset = len(base_preload)
    merged_preload = list(base_preload) + list(overlay_preload)
    merged_containers = []
    for record in base_containers:
        merged_containers.append({
            "path": record["path"],
            "preload_index": record["preload_index"],
            "preload_size": record["preload_size"],
            "asset": dict(record["asset"]),
        })
    for record, pointer in zip(overlay_containers, rewritten_containers):
        start = record.get("preload_index")
        size = record.get("preload_size")
        if (not isinstance(start, int) or not isinstance(size, int)
                or start < 0 or size < 0 or start + size > len(overlay_preload)):
            return {"ready": False, "reason": "Overlay Container preload range is invalid."}
        merged_containers.append({
            "path": record["path"],
            "preload_index": start + preload_offset,
            "preload_size": size,
            "asset": dict(pointer),
        })

    merged_main = None
    if base_main is not None:
        merged_main = {
            "preload_index": base_main["preload_index"],
            "preload_size": base_main["preload_size"],
            "asset": dict(base_main["asset"]),
        }
    elif overlay_main is not None:
        overlay_main_record = overlay_metadata.get("main_asset")
        if not isinstance(overlay_main_record, dict):
            return {"ready": False, "reason": "Overlay MainAsset source metadata is missing."}
        start = overlay_main_record.get("preload_index")
        size = overlay_main_record.get("preload_size")
        if (not isinstance(start, int) or not isinstance(size, int)
                or start < 0 or size < 0 or start + size > len(overlay_preload)):
            return {"ready": False, "reason": "Overlay MainAsset preload range is invalid."}
        merged_main = {
            "preload_index": start + preload_offset,
            "preload_size": size,
            "asset": dict(overlay_main),
        }

    dependencies = []
    seen_dependencies = set()
    for dependency in list(base_metadata.get("dependencies", [])) + list(overlay_metadata.get("dependencies", [])):
        if not isinstance(dependency, str) or not dependency:
            return {"ready": False, "reason": "Dependency inventory contains an invalid entry."}
        if dependency not in seen_dependencies:
            seen_dependencies.add(dependency)
            dependencies.append(dependency)

    return {
        "ready": True,
        "reason": "Metadata table concatenation planned; no AssetBundle bytes were changed.",
        "assetbundle_name": base_name or overlay_name,
        "preload_table": merged_preload,
        "container_records": merged_containers,
        "main_asset": merged_main,
        "dependencies": dependencies,
        "preload_offset": preload_offset,
        "preload_count": len(merged_preload),
        "container_count": len(merged_containers),
        "scope": "metadata plan only; serialized AssetBundle object and CAB/resource files are not rewritten",
    }




def build_streamed_resource_merge_plan(
    base_resources: dict[str, int],
    overlay_resources: dict[str, int],
    base_streams: list[dict[str, Any]],
    overlay_streams: list[dict[str, Any]],
) -> dict[str, Any]:
    """Plan sidecar resource concatenation and m_StreamData offset rewrites.

    Inputs map normalized relative resource paths to verified byte lengths.
    Stream entries contain path, offset and size. This planner never reads,
    copies, or writes resource bytes; callers must verify actual file hashes.
    """
    def normalize_path(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        path = value.strip().replace(chr(92), "/")
        if not path or path.startswith("/") or ":" in path:
            return None
        parts = path.split("/")
        if any(part in ("", ".", "..") for part in parts):
            return None
        return "/".join(parts)

    def validate_resources(resources: dict[str, int], label: str) -> dict[str, int] | None:
        if not isinstance(resources, dict):
            return None
        normalized = {}
        for raw_path, length in resources.items():
            path = normalize_path(raw_path)
            if path is None or path in normalized:
                return None
            if not isinstance(length, int) or isinstance(length, bool) or length < 0:
                return None
            normalized[path] = length
        return normalized

    base = validate_resources(base_resources, "base")
    overlay = validate_resources(overlay_resources, "overlay")
    if base is None or overlay is None:
        return {"ready": False, "reason": "Resource paths or byte lengths are invalid or ambiguous."}

    merged_lengths = dict(base)
    overlay_offsets: dict[str, int] = {}
    path_mapping: dict[str, str] = {}
    for path, length in overlay.items():
        if path in merged_lengths:
            overlay_offsets[path] = merged_lengths[path]
            merged_lengths[path] += length
        else:
            overlay_offsets[path] = 0
            merged_lengths[path] = length
        path_mapping[path] = path

    def rewrite_streams(streams: list[dict[str, Any]], *, is_overlay: bool):
        if not isinstance(streams, list):
            return None, "StreamData inventory is unavailable."
        result = []
        for index, entry in enumerate(streams):
            if not isinstance(entry, dict):
                return None, "StreamData entry is invalid."
            path = normalize_path(entry.get("path"))
            offset = entry.get("offset")
            size = entry.get("size")
            if (path is None or path not in (overlay if is_overlay else base)
                    or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
                    or not isinstance(size, int) or isinstance(size, bool) or size < 0):
                return None, f"StreamData entry {index} has an invalid path, offset or size."
            source_length = (overlay if is_overlay else base)[path]
            if offset + size > source_length:
                return None, f"StreamData entry {index} exceeds its source resource file."
            final_offset = offset + (overlay_offsets[path] if is_overlay else 0)
            result.append({
                "path": path_mapping.get(path, path),
                "offset": final_offset,
                "size": size,
                "source_path": path,
            })
        return result, None

    base_plan, error = rewrite_streams(base_streams, is_overlay=False)
    if error:
        return {"ready": False, "reason": error}
    overlay_plan, error = rewrite_streams(overlay_streams, is_overlay=True)
    if error:
        return {"ready": False, "reason": error}

    return {
        "ready": True,
        "merged_resource_lengths": merged_lengths,
        "overlay_append_offsets": overlay_offsets,
        "base_streams": base_plan,
        "overlay_streams": overlay_plan,
        "scope": "offset/path plan only; resource bytes are not copied, hashed or written",
        "requires": "verify source sidecar identity and bytes, then rewrite every m_StreamData record before serialization",
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



def _assetbundle_roundtrip_snapshot(report: dict[str, Any]) -> tuple[Any, ...] | None:
    """Build a complete, deterministic snapshot or reject truncated inventories."""
    if (
        not report.get("reference_scan_complete")
        or not report.get("container_inventory_complete")
        or not report.get("assetbundle_metadata_complete")
        or report.get("object_reference_count", 0) > MAX_REPORTED_REFERENCES
        or report.get("external_reference_count", 0) > MAX_REPORTED_CONTAINERS
        or report.get("container_entry_count", 0) > MAX_REPORTED_CONTAINERS
        or report.get("assetbundle_metadata_count", 0) > MAX_REPORTED_CONTAINERS
    ):
        return None

    serialized = []
    for item in report.get("serialized_files", []):
        if (
            not item.get("object_id_inventory_complete")
            or not item.get("object_payload_hashes_complete")
        ):
            return None
        serialized.append((
            item["name"],
            item["unity_version"],
            item["target_platform"],
            item["object_count"],
            item["external_count"],
            tuple(sorted(item["object_path_ids"])),
            tuple(sorted(item["object_types_by_path_id"].items())),
            tuple(sorted(item["object_payload_hashes"].items())),
        ))

    metadata = []
    for item in report.get("assetbundle_metadata", []):
        if not item.get("inspection_complete") or not item.get("metadata_integrity_pass"):
            return None
        metadata.append((
            item["path_id"],
            item["assetbundle_name"],
            item["preload_table_count"],
            item["preload_table_sha256"],
            item["container_entry_count"],
            item["container_metadata_sha256"],
            item["dependency_count"],
            item["dependencies_sha256"],
            item["main_asset_sha256"],
        ))

    references = tuple(sorted(
        (
            item["from_file"],
            item["from_path_id"],
            item["file_id"],
            item["path_id"],
            item["target_file"],
        )
        for item in report.get("object_references", [])
    ))
    external_references = tuple(sorted(
        (item["from"], item["to"])
        for item in report.get("external_references", [])
    ))
    container_entries = tuple(sorted(
        (item["path"], item["type"], item["path_id"])
        for item in report.get("container_entries", [])
    ))
    return (
        report["serialized_file_count"],
        report["object_count"],
        tuple(sorted(serialized)),
        tuple(sorted(metadata)),
        references,
        external_references,
        container_entries,
        tuple(sorted((key, repr(value)) for key, value in report["reference_integrity"].items())),
    )


def verify_assetbundle_roundtrip(data: bytes) -> dict[str, Any]:
    """Save a UnityFS bundle unchanged, reopen it and compare complete semantic inventories."""
    original_report = inspect_assetbundle(data, "roundtrip-original")
    original_snapshot = _assetbundle_roundtrip_snapshot(original_report)
    if original_snapshot is None:
        return {
            "verified": False,
            "reason": "Dữ liệu gốc vượt giới hạn đối chiếu đầy đủ hoặc có inventory chưa hoàn chỉnh.",
            "original_size": len(data),
            "roundtrip_size": 0,
        }

    UnityPy_AOV = _load_unitypy_aov()
    try:
        env = UnityPy_AOV.load(data)
        top_files = list((getattr(env, "files", {}) or {}).values())
        if len(top_files) != 1:
            raise ValueError("Chỉ hỗ trợ round-trip một UnityFS bundle độc lập.")
        bundle = top_files[0]
        if getattr(bundle, "signature", None) != "UnityFS":
            raise ValueError("Bộ ghi hiện chỉ hỗ trợ UnityFS, không hỗ trợ UnityRaw/UnityWeb.")
        save = getattr(bundle, "save", None)
        if not callable(save):
            raise ValueError("Bundle không có hàm ghi lại.")
        roundtrip_data = bytes(save(packer="original"))
        if not roundtrip_data or len(roundtrip_data) > MAX_ASSETBUNDLE_BYTES:
            raise ValueError("Kích thước bundle sau khi ghi lại không hợp lệ hoặc vượt giới hạn.")
        roundtrip_report = inspect_assetbundle(roundtrip_data, "roundtrip-saved")
        roundtrip_snapshot = _assetbundle_roundtrip_snapshot(roundtrip_report)
        if roundtrip_snapshot is None:
            return {
                "verified": False,
                "reason": "Bản ghi lại có inventory không đầy đủ, không thể xác minh an toàn.",
                "original_size": len(data),
                "roundtrip_size": len(roundtrip_data),
            }
        return {
            "verified": original_snapshot == roundtrip_snapshot,
            "reason": (
                "Inventory và payload Object khớp sau khi ghi lại."
                if original_snapshot == roundtrip_snapshot
                else "Phát hiện sai lệch cấu trúc hoặc payload sau khi ghi lại."
            ),
            "original_size": len(data),
            "roundtrip_size": len(roundtrip_data),
        }
    except Exception as exc:
        return {
            "verified": False,
            "reason": f"Round-trip thất bại: {type(exc).__name__}: {exc}",
            "original_size": len(data),
            "roundtrip_size": 0,
        }


def build_path_id_remap_plan(
    base_path_ids: list[int],
    overlay_path_ids: list[int],
    base_types: dict[str, str],
    overlay_types: dict[str, str],
    base_payload_hashes: dict[str, str],
    overlay_payload_hashes: dict[str, str],
    *,
    inventories_complete: bool = True,
    allow_payload_deduplication: bool = False,
) -> dict[str, Any]:
    """Plan append-only PathID handling for one matched CAB; never mutates assets.

    Payload equality alone does not prove PPtr equivalence: identical bytes can
    resolve differently when external CAB tables or dependencies differ. Keep
    deduplication disabled unless a caller has independently proven that context.
    """
    if not inventories_complete:
        return {"ready": False, "reason": "Object inventory or payload hashes are incomplete.",
                "remap": {}, "deduplicate": {}, "blocked_path_ids": []}

    def valid_ids(values: list[int]) -> bool:
        return (
            isinstance(values, list)
            and all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and -(1 << 63) <= value < (1 << 63)
                for value in values
            )
            and len(values) == len(set(values))
        )

    if not valid_ids(base_path_ids) or not valid_ids(overlay_path_ids):
        return {"ready": False, "reason": "PathID inventory is invalid or duplicated.",
                "remap": {}, "deduplicate": {}, "blocked_path_ids": []}

    base_ids = set(base_path_ids)
    overlay_ids = set(overlay_path_ids)
    collisions = sorted(base_ids & overlay_ids)
    remap: dict[str, int] = {}
    deduplicate: dict[str, int] = {}
    blocked = []
    next_id = max([0, *base_ids, *overlay_ids]) + 1

    for path_id in collisions:
        key = str(path_id)
        base_type = base_types.get(key)
        overlay_type = overlay_types.get(key)
        base_hash = base_payload_hashes.get(key)
        overlay_hash = overlay_payload_hashes.get(key)
        if not base_type or base_type != overlay_type:
            blocked.append({"path_id": path_id, "reason": "Object type missing or incompatible"})
            continue
        if not base_hash or not overlay_hash:
            blocked.append({"path_id": path_id, "reason": "Object payload hash missing"})
            continue
        if base_hash == overlay_hash:
            if allow_payload_deduplication:
                deduplicate[key] = path_id
                continue
            blocked.append({
                "path_id": path_id,
                "reason": "Payload bytes match but PPtr/dependency equivalence is not proven",
            })
            continue
        if next_id >= (1 << 63):
            blocked.append({"path_id": path_id, "reason": "No free signed 64-bit PathID remains"})
            continue
        remap[key] = next_id
        next_id += 1

    if blocked:
        return {
            "ready": False,
            "reason": "At least one PathID collision cannot be resolved safely.",
            "remap": {},
            "deduplicate": {},
            "blocked_path_ids": blocked[:MAX_REPORTED_REFERENCES],
            "collision_count": len(collisions),
        }

    return {
        "ready": True,
        "reason": "Append-only PathID plan prepared for one matched CAB; references are not rewritten.",
        "remap": remap,
        "deduplicate": deduplicate,
        "blocked_path_ids": [],
        "collision_count": len(collisions),
        "new_object_count": len(remap),
        "deduplicated_object_count": len(deduplicate),
        "preserved_overlay_only_count": len(overlay_ids - base_ids),
        "scope": "single CAB; all local, external, Container, preload and metadata pointers must be rewritten before applying",
    }



def build_external_file_id_remap_plan(
    base_externals: list[str],
    overlay_externals: list[str],
) -> dict[str, Any]:
    """Plan 1-based PPtr file_id remapping by exact external path identity.

    This only plans the external table. It does not rewrite PPtrs or prove that
    a referenced CAB/resource exists in the final bundle.
    """
    def normalize(value: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip().replace(chr(92), "/")
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        return normalized

    base_paths = [normalize(value) for value in base_externals]
    overlay_paths = [normalize(value) for value in overlay_externals]
    if any(value is None for value in base_paths + overlay_paths):
        return {
            "ready": False,
            "reason": "External table contains an empty or invalid path.",
            "file_id_remap": {},
            "merged_externals": [],
            "appended_count": 0,
        }

    def duplicates(paths: list[str]) -> list[str]:
        seen: set[str] = set()
        repeated: set[str] = set()
        for value in paths:
            if value in seen:
                repeated.add(value)
            seen.add(value)
        return sorted(repeated)

    duplicate_base = duplicates(base_paths)
    duplicate_overlay = duplicates(overlay_paths)
    if duplicate_base or duplicate_overlay:
        return {
            "ready": False,
            "reason": "Duplicate external paths make PPtr file_id identity ambiguous.",
            "duplicate_base_paths": duplicate_base[:MAX_REPORTED_REFERENCES],
            "duplicate_overlay_paths": duplicate_overlay[:MAX_REPORTED_REFERENCES],
            "file_id_remap": {},
            "merged_externals": [],
            "appended_count": 0,
        }

    merged = list(base_paths)
    base_index = {path: index + 1 for index, path in enumerate(merged)}
    remap: dict[str, int] = {}
    for overlay_index, path in enumerate(overlay_paths, start=1):
        target_index = base_index.get(path)
        if target_index is None:
            merged.append(path)
            target_index = len(merged)
            base_index[path] = target_index
        remap[str(overlay_index)] = target_index

    return {
        "ready": True,
        "reason": "External PPtr file_id table remap planned; no pointers or files were changed.",
        "file_id_remap": remap,
        "merged_externals": merged,
        "appended_count": len(merged) - len(base_paths),
        "scope": "external table only; CAB existence, PathID targets and resource sidecars require separate validation",
    }



def build_pptr_remap_plan(
    references: list[dict[str, Any]],
    file_id_remap: dict[str, int],
    local_path_id_remap: dict[str, int],
    external_path_id_remaps: dict[str, dict[str, int]],
    *,
    local_target_path_ids: set[int],
    external_target_path_ids: dict[str, set[int]],
) -> dict[str, Any]:
    """Validate and plan PPtr rewrites without mutating any serialized object.

    file_id_remap maps overlay 1-based external IDs to final 1-based IDs.
    external_path_id_remaps is keyed by overlay external ID; each value maps
    old PathIDs to final PathIDs for that external CAB.
    """
    if not isinstance(references, list) or not isinstance(file_id_remap, dict):
        return {"ready": False, "reason": "Invalid PPtr reference inventory or file ID map.",
                "rewritten_references": [], "blocked_references": []}

    def valid_path_id(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and -(1 << 63) <= value < (1 << 63)

    rewritten = []
    blocked = []
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            blocked.append({"index": index, "reason": "Reference entry is not an object"})
            continue
        file_id = reference.get("file_id")
        path_id = reference.get("path_id")
        if not isinstance(file_id, int) or isinstance(file_id, bool) or file_id < 0 or not valid_path_id(path_id):
            blocked.append({"index": index, "reason": "PPtr file_id or PathID is invalid"})
            continue

        if file_id == 0:
            target_file_id = 0
            target_path_id = local_path_id_remap.get(str(path_id), path_id)
            if not valid_path_id(target_path_id) or target_path_id not in local_target_path_ids:
                blocked.append({"index": index, "file_id": file_id, "path_id": path_id,
                                "reason": "Local PPtr target is absent from the final CAB inventory"})
                continue
            target_name = "local"
        else:
            target_file_id = file_id_remap.get(str(file_id))
            if not isinstance(target_file_id, int) or isinstance(target_file_id, bool) or target_file_id <= 0:
                blocked.append({"index": index, "file_id": file_id, "path_id": path_id,
                                "reason": "External PPtr file_id has no valid final-table mapping"})
                continue
            target_path_id = external_path_id_remaps.get(str(file_id), {}).get(str(path_id), path_id)
            target_name = str(target_file_id)
            valid_targets = external_target_path_ids.get(target_name)
            if valid_targets is None or not valid_path_id(target_path_id) or target_path_id not in valid_targets:
                blocked.append({"index": index, "file_id": file_id, "path_id": path_id,
                                "reason": "External PPtr target is absent or not indexed in the final CAB inventory"})
                continue

        rewritten.append({
            "index": index,
            "file_id": target_file_id,
            "path_id": target_path_id,
            "target": target_name,
        })

    if blocked:
        return {
            "ready": False,
            "reason": "One or more PPtr targets cannot be resolved after remapping.",
            "rewritten_references": [],
            "blocked_references": blocked[:MAX_REPORTED_REFERENCES],
            "reference_count": len(references),
            "blocked_count": len(blocked),
        }
    return {
        "ready": True,
        "reason": "All supplied PPtrs resolve against the supplied final target inventories; no objects were changed.",
        "rewritten_references": rewritten,
        "blocked_references": [],
        "reference_count": len(references),
        "scope": "reference plan only; serialized typetrees, preload table, Container and bundle metadata remain untouched",
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

        path_id_plan = build_path_id_remap_plan(
            sorted(base_ids),
            sorted(overlay_ids),
            base_item["object_types_by_path_id"],
            overlay_item["object_types_by_path_id"],
            base_item["object_payload_hashes"],
            overlay_item["object_payload_hashes"],
            inventories_complete=(
                base_item["object_payload_hashes_complete"]
                and overlay_item["object_payload_hashes_complete"]
            ),
        )
        object_id_overlaps.append({
            "cab": basename,
            "path_id_merge_plan": path_id_plan,
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

    cross_bundle_reference_integrity_pass = (
        not cross_bundle_references["missing_path_ids"]
        and not cross_bundle_references["ambiguous_cab_names"]
        and not cross_bundle_references["index_incomplete"]
    )

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
        "cross_bundle_reference_integrity_pass": cross_bundle_reference_integrity_pass,
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
            and cross_bundle_reference_integrity_pass
        ),
        # Unity object references, preload tables, CAB remapping and bundle metadata
        # still need a version-specific adapter before any actual merge is safe.
        "merge_safe": False,
    }
