from __future__ import annotations

import copy
import sys
from pathlib import Path


class AOVCatalogMergeError(ValueError):
    pass


def merge_resource_packer_tree(
    base_tree: dict,
    overlay_tree: dict,
    *,
    prefer_overlay_paths: set[str] | None = None,
) -> tuple[dict, dict]:
    """Merge ResourcePackerInfoSet* trees by PathInIFS and remap dependency edges."""
    if base_tree.get("m_Name") != overlay_tree.get("m_Name"):
        raise AOVCatalogMergeError("Tên ResourcePackerInfoSet không khớp.")
    base_rows = base_tree.get("AllResourcePackInfo")
    overlay_rows = overlay_tree.get("AllResourcePackInfo")
    if not isinstance(base_rows, list) or not isinstance(overlay_rows, list):
        raise AOVCatalogMergeError("Thiếu AllResourcePackInfo trong catalog.")

    merged_tree = copy.deepcopy(base_tree)
    rows = merged_tree["AllResourcePackInfo"]
    path_to_index: dict[str, int] = {}
    for index, row in enumerate(rows):
        path = row.get("PathInIFS")
        if not isinstance(path, str) or not path:
            raise AOVCatalogMergeError("PathInIFS trong base bị thiếu hoặc không hợp lệ.")
        if path in path_to_index:
            raise AOVCatalogMergeError(f"PathInIFS trùng trong base: {path}")
        path_to_index[path] = index

    overlay_to_merged: dict[int, int] = {}
    appended = []
    merged_hashes = 0
    metadata_conflicts = []
    metadata_overridden = []
    preferred = prefer_overlay_paths or set()
    for overlay_index, candidate in enumerate(overlay_rows):
        path = candidate.get("PathInIFS")
        if not isinstance(path, str) or not path:
            raise AOVCatalogMergeError("PathInIFS trong overlay bị thiếu hoặc không hợp lệ.")
        if path not in path_to_index:
            path_to_index[path] = len(rows)
            rows.append(copy.deepcopy(candidate))
            overlay_to_merged[overlay_index] = path_to_index[path]
            appended.append(path)
            continue

        target_index = path_to_index[path]
        overlay_to_merged[overlay_index] = target_index
        target = rows[target_index]
        for field in ("pkgType", "Tag"):
            if target.get(field) != candidate.get(field):
                raise AOVCatalogMergeError(
                    f"Catalog metadata không tương thích tại {path}: {field} khác nhau."
                )
        base_resources = target.get("ResourceInfos", [])
        overlay_resources = candidate.get("ResourceInfos", [])
        if not isinstance(base_resources, list) or not isinstance(overlay_resources, list):
            raise AOVCatalogMergeError(f"ResourceInfos không hợp lệ tại {path}.")
        known = {
            (item.get("HashCode"), item.get("Extension", ""))
            for item in base_resources
        }
        for item in overlay_resources:
            key = (item.get("HashCode"), item.get("Extension", ""))
            if key not in known:
                base_resources.append(copy.deepcopy(item))
                known.add(key)
                merged_hashes += 1
        target["ResourceInfos"] = base_resources
        target["Flags"] = int(target.get("Flags", 0)) | int(candidate.get("Flags", 0))
        if target.get("CrcXor") != candidate.get("CrcXor"):
            if path in preferred:
                target["CrcXor"] = candidate.get("CrcXor")
                metadata_overridden.append(path)
            else:
                metadata_conflicts.append(path)

    base_edges = merged_tree.get("AllParentIdexToChild", [])
    overlay_edges = overlay_tree.get("AllParentIdexToChild", [])
    if not isinstance(base_edges, list) or not isinstance(overlay_edges, list):
        raise AOVCatalogMergeError("AllParentIdexToChild không hợp lệ.")
    edge_set = set()
    edges = []
    for edge in base_edges:
        parent = int(edge["parentIdx"])
        child = int(edge["childIdx"])
        if parent < 0 or child < 0 or parent >= len(base_rows) or child >= len(base_rows):
            raise AOVCatalogMergeError("Quan hệ parent-child trong base vượt chỉ số catalog.")
        pair = (parent, child)
        if pair not in edge_set:
            edge_set.add(pair)
            edges.append({"parentIdx": parent, "childIdx": child})

    added_edges = 0
    for edge in overlay_edges:
        parent_old = int(edge["parentIdx"])
        child_old = int(edge["childIdx"])
        if parent_old not in overlay_to_merged or child_old not in overlay_to_merged:
            raise AOVCatalogMergeError("Quan hệ parent-child overlay tham chiếu chỉ số không tồn tại.")
        pair = (overlay_to_merged[parent_old], overlay_to_merged[child_old])
        if pair not in edge_set:
            edge_set.add(pair)
            edges.append({"parentIdx": pair[0], "childIdx": pair[1]})
            added_edges += 1

    merged_tree["AllParentIdexToChild"] = edges
    return merged_tree, {
        "set_name": base_tree.get("SetName", base_tree.get("m_Name")),
        "base_entries": len(base_rows) - len(appended),
        "overlay_entries": len(overlay_rows),
        "output_entries": len(rows),
        "appended_paths": appended,
        "new_resource_hashes": merged_hashes,
        "base_edges": len(base_edges),
        "overlay_edges": len(overlay_edges),
        "output_edges": len(edges),
        "new_edges": added_edges,
        "crc_metadata_conflicts_kept_base": metadata_conflicts,
        "crc_metadata_conflicts_overridden_with_overlay": metadata_overridden,
    }


def merge_resource_verification_tree(base_tree: dict, overlay_tree: dict, *, prefer_overlay_paths: set[str] | None = None) -> tuple[dict, dict]:
    """Union verification path lists, retaining base checksums on conflicts."""
    if base_tree.get("m_Name") != overlay_tree.get("m_Name"):
        raise AOVCatalogMergeError("Tên ResourceVerificationInfoSet không khớp.")
    result = copy.deepcopy(base_tree)
    report = {}
    for field in ("AllZipVerificationInfo", "AllDatabinVerificationInfo"):
        base_rows = result.get(field, [])
        overlay_rows = overlay_tree.get(field, [])
        if not isinstance(base_rows, list) or not isinstance(overlay_rows, list):
            raise AOVCatalogMergeError(f"{field} không hợp lệ.")
        index = {}
        for i, row in enumerate(base_rows):
            path = row.get("PathInIFS")
            if path in index:
                raise AOVCatalogMergeError(f"Đường dẫn kiểm tra trùng trong base: {path}")
            index[path] = i
        appended = []
        conflicts = []
        overridden = []
        preferred = prefer_overlay_paths or set()
        for row in overlay_rows:
            path = row.get("PathInIFS")
            if path not in index:
                index[path] = len(base_rows)
                base_rows.append(copy.deepcopy(row))
                appended.append(path)
            elif base_rows[index[path]].get("CrcXor") != row.get("CrcXor"):
                if path in preferred:
                    base_rows[index[path]] = copy.deepcopy(row)
                    overridden.append(path)
                else:
                    conflicts.append(path)
        result[field] = base_rows
        report[field] = {
            "base_count": len(base_rows) - len(appended),
            "overlay_count": len(overlay_rows),
            "output_count": len(base_rows),
            "appended_paths": appended,
            "checksum_conflicts_kept_base": conflicts,
            "checksum_conflicts_overridden_with_overlay": overridden,
        }
    return result, report


def _unitypy_aov():
    vendor = Path(__file__).resolve().parent / "vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    try:
        import UnityPy_AOV
    except Exception as exc:
        raise AOVCatalogMergeError(
            "Thiếu parser UnityPy AoV hoặc dependency của nó."
        ) from exc
    return UnityPy_AOV


def _mono_behaviours(env):
    objects = {}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception as exc:
            raise AOVCatalogMergeError(
                f"Không đọc được MonoBehaviour path_id={obj.path_id}: {exc}"
            ) from exc
        name = tree.get("m_Name")
        if name:
            if name in objects:
                raise AOVCatalogMergeError(f"MonoBehaviour m_Name bị trùng: {name}")
            objects[name] = (obj, tree)
    return objects


def merge_aov_metadata_assetbundle(base_bytes: bytes, overlay_bytes: bytes, *, kind: str, prefer_overlay_paths: set[str] | None = None) -> tuple[bytes, dict]:
    """Merge AoV UnityFS metadata and preserve the source bundle's encryption flags."""
    parser = _unitypy_aov()
    try:
        base_env = parser.load(base_bytes)
        overlay_env = parser.load(overlay_bytes)
        base_objects = _mono_behaviours(base_env)
        overlay_objects = _mono_behaviours(overlay_env)
    except AOVCatalogMergeError:
        raise
    except Exception as exc:
        raise AOVCatalogMergeError(f"Không mở được assetbundle metadata: {exc}") from exc

    if kind == "resource_packer":
        merge_fn = merge_resource_packer_tree
    elif kind == "resource_verification":
        merge_fn = merge_resource_verification_tree
    else:
        raise AOVCatalogMergeError(f"Loại metadata không hỗ trợ: {kind}")

    reports = {}
    for name, (base_obj, base_tree) in base_objects.items():
        if name not in overlay_objects:
            continue
        _, overlay_tree = overlay_objects[name]
        if kind == "resource_packer" and "AllResourcePackInfo" in base_tree:
            if kind == "resource_verification":
                merged_tree, report = merge_resource_verification_tree(
                    base_tree, overlay_tree, prefer_overlay_paths=prefer_overlay_paths
                )
            else:
                merged_tree, report = merge_resource_packer_tree(
                    base_tree, overlay_tree, prefer_overlay_paths=prefer_overlay_paths
                )
            base_obj.save_typetree(merged_tree)
            reports[name] = report
        elif kind == "resource_verification" and (
            "AllZipVerificationInfo" in base_tree or "AllDatabinVerificationInfo" in base_tree
        ):
            merged_tree, report = merge_resource_verification_tree(
                base_tree, overlay_tree, prefer_overlay_paths=prefer_overlay_paths
            )
            base_obj.save_typetree(merged_tree)
            reports[name] = report

    if not reports:
        raise AOVCatalogMergeError("Không tìm thấy MonoBehaviour metadata tương ứng để ghép.")

    try:
        source_file = getattr(base_env, "file", None)
        if source_file is None:
            source_file = next(iter(base_env.files.values()))
        output = source_file.save(packer="original")
        verify_env = parser.load(output)
        verified_objects = _mono_behaviours(verify_env)
        for name, report in reports.items():
            if name not in verified_objects:
                raise AOVCatalogMergeError(f"Không tìm thấy metadata sau ghi: {name}")
            _, tree = verified_objects[name]
            if kind == "resource_packer":
                report["verified_output_entries"] = len(tree.get("AllResourcePackInfo", []))
                report["verified_output_edges"] = len(tree.get("AllParentIdexToChild", []))
            else:
                report["verified_output_zip_entries"] = len(tree.get("AllZipVerificationInfo", []))
                report["verified_output_databin_entries"] = len(tree.get("AllDatabinVerificationInfo", []))
    except AOVCatalogMergeError:
        raise
    except Exception as exc:
        raise AOVCatalogMergeError(f"Không ghi/xác minh được assetbundle metadata: {exc}") from exc

    return output, {
        "kind": kind,
        "base_bytes": len(base_bytes),
        "overlay_bytes": len(overlay_bytes),
        "output_bytes": len(output),
        "objects": reports,
        "verified": True,
    }
