"""Fail-closed compatibility planning for resource manifests.

This module only evaluates supplied metadata. It does not parse archives, decrypt
resources, rewrite payloads, or authorize the merge executor to bypass adapters.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_ALLOWED_STATUSES = {"SAFE_MERGE", "IDENTICAL_DUPLICATE", "NEEDS_RULE", "UNSUPPORTED", "VERSION_CONFLICT", "IGNORED_METADATA"}


class CompatibilityPlanError(ValueError):
    pass


def _clean_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompatibilityPlanError("Mỗi resource phải có path hợp lệ.")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    raw_parts = normalized.split("/")
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or "\x00" in normalized
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise CompatibilityPlanError(f"Đường dẫn resource không an toàn hoặc mơ hồ: {value}")
    return path.as_posix()


def _validate_resource(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise CompatibilityPlanError(f"Resource thứ {index + 1} không phải object.")
    path = _clean_path(item.get("path"))
    digest = item.get("sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise CompatibilityPlanError(f"SHA-256 không hợp lệ cho resource: {path}")
    fmt = item.get("format")
    if not isinstance(fmt, str) or not fmt.strip():
        raise CompatibilityPlanError(f"Thiếu format cho resource: {path}")
    source = item.get("source", f"input-{index + 1}")
    if not isinstance(source, str) or not source.strip():
        raise CompatibilityPlanError(f"Source không hợp lệ cho resource: {path}")
    dependencies = item.get("dependencies", [])
    if not isinstance(dependencies, list) or any(not isinstance(dep, str) or not dep.strip() for dep in dependencies):
        raise CompatibilityPlanError(f"Danh sách dependency không hợp lệ cho resource: {path}")
    unity_dependencies = item.get("unity_dependencies", [])
    unity_external_paths = item.get("unity_external_paths", [])
    for field_name, values in (("unity_dependencies", unity_dependencies),
                               ("unity_external_paths", unity_external_paths)):
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise CompatibilityPlanError(f"Danh sách {field_name} không hợp lệ cho resource: {path}")
    dependency_inventory_complete = item.get("dependency_inventory_complete", False)
    if not isinstance(dependency_inventory_complete, bool):
        raise CompatibilityPlanError(f"Cờ dependency_inventory_complete không hợp lệ cho resource: {path}")
    for field_name in ("game_version", "platform"):
        value = item.get(field_name)
        if value is not None and value != "" and (
            not isinstance(value, str) or not value.strip()
        ):
            raise CompatibilityPlanError(f"Metadata {field_name} không hợp lệ cho resource: {path}")
    for field_name in ("actor_id", "skin_id"):
        value = item.get(field_name)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (str, int))
            or (isinstance(value, str) and not value.strip())
            or (isinstance(value, int) and value < 0)
        ):
            raise CompatibilityPlanError(f"Metadata {field_name} không hợp lệ cho resource: {path}")
    for field_name in ("is_metadata_noise", "is_aov_resource"):
        value = item.get(field_name, False)
        if not isinstance(value, bool):
            raise CompatibilityPlanError(f"Cờ {field_name} không hợp lệ cho resource: {path}")
    result = dict(item)
    result.update({
        "path": path,
        "sha256": digest.lower(),
        "format": fmt.strip().lower(),
        "source": source,
        "dependencies": [_clean_path(dep) for dep in dependencies],
    })
    return result


def plan_resource_compatibility(resources: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a conservative per-path plan; this result is advisory, not merge authorization."""
    if not isinstance(resources, list) or not resources:
        raise CompatibilityPlanError("Cần ít nhất một resource để lập kế hoạch.")
    if len(resources) > 20000:
        raise CompatibilityPlanError("Quá nhiều resource để lập kế hoạch (tối đa 20.000).")

    validated = [_validate_resource(item, index) for index, item in enumerate(resources)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_paths = {item["path"] for item in validated}
    for item in validated:
        grouped[item["path"]].append(item)

    # Compare version/platform metadata across whole input archives as well as
    # per-path. Different archives can conflict even when their resource paths do
    # not overlap, so a path-only comparison would miss this case.
    source_profiles: dict[str, dict[str, set[str]]] = {}
    for item in validated:
        profile = source_profiles.setdefault(
            item["source"], {"game_versions": set(), "platforms": set()}
        )
        if item.get("game_version") not in (None, ""):
            profile["game_versions"].add(str(item["game_version"]))
        if item.get("platform") not in (None, ""):
            profile["platforms"].add(str(item["platform"]).lower())

    all_versions = {
        value for profile in source_profiles.values() for value in profile["game_versions"]
    }
    all_platforms = {
        value for profile in source_profiles.values() for value in profile["platforms"]
    }
    mixed_sources = [
        source for source, profile in sorted(source_profiles.items())
        if len(profile["game_versions"]) > 1 or len(profile["platforms"]) > 1
    ]
    source_conflicts = []
    if len(all_versions) > 1:
        source_conflicts.append("Các ZIP đầu vào có metadata phiên bản game khác nhau.")
    if len(all_platforms) > 1:
        source_conflicts.append("Các ZIP đầu vào có metadata nền tảng khác nhau.")
    if mixed_sources:
        source_conflicts.append(
            "Một hoặc nhiều ZIP chứa resource được gắn nhiều phiên bản/nền tảng: "
            + ", ".join(mixed_sources)
        )
    source_compatibility = {
        "status": "VERSION_CONFLICT" if source_conflicts else "NO_CONFLICT_DETECTED",
        "game_versions": sorted(all_versions),
        "platforms": sorted(all_platforms),
        "mixed_sources": mixed_sources,
        "sources": [
            {
                "source": source,
                "game_versions": sorted(profile["game_versions"]),
                "platforms": sorted(profile["platforms"]),
            }
            for source, profile in sorted(source_profiles.items())
        ],
        "reasons": source_conflicts,
    }

    results = []
    for path, entries in sorted(grouped.items()):
        versions = {str(item.get("game_version")) for item in entries if item.get("game_version") not in (None, "")}
        platforms = {str(item.get("platform")).lower() for item in entries if item.get("platform") not in (None, "")}
        actors = {str(item.get("actor_id")) for item in entries if item.get("actor_id") not in (None, "")}
        skins = {str(item.get("skin_id")) for item in entries if item.get("skin_id") not in (None, "")}
        hashes = {item["sha256"] for item in entries}
        formats = {item["format"] for item in entries}
        dependencies = sorted({dep for item in entries for dep in item["dependencies"]})
        missing_dependencies = [dep for dep in dependencies if dep not in all_paths]

        status = "NEEDS_RULE"
        reasons: list[str] = []
        if all(item.get("is_metadata_noise", False) for item in entries):
            status = "IGNORED_METADATA"
            reasons.append("Tệp phụ hệ điều hành (AppleDouble/macOS metadata), không phải tài nguyên game.")
        elif len(versions) > 1:
            status = "VERSION_CONFLICT"
            reasons.append("Các nguồn có game_version khác nhau.")
        elif len(platforms) > 1:
            status = "VERSION_CONFLICT"
            reasons.append("Các nguồn khác platform.")
        elif len(actors) > 1 or len(skins) > 1:
            status = "NEEDS_RULE"
            reasons.append("Actor ID hoặc Skin ID không đồng nhất; cần quy tắc remap riêng.")
        elif missing_dependencies:
            status = "NEEDS_RULE"
            reasons.append("Thiếu dependency: " + ", ".join(missing_dependencies))
        elif len(entries) > 1 and len(hashes) == 1:
            status = "IDENTICAL_DUPLICATE"
            reasons.append("Cùng đường dẫn và SHA-256; có thể giữ một bản byte-identical.")
        elif len(entries) > 1:
            status = "NEEDS_RULE"
            reasons.append("Cùng đường dẫn nhưng payload khác nhau; mặc định không ghi đè.")
        elif (
            entries[0]["format"] == "generic_zip"
            and not entries[0].get("is_aov_resource", False)
            and PurePosixPath(path.lower()).suffix in {".txt", ".md", ".rst", ".license"}
        ):
            status = "SAFE_MERGE"
            reasons.append(
                "Chỉ an toàn ở mức thêm tệp văn bản vào ZIP; không phải xác nhận tương thích trong game."
            )
        elif entries[0]["format"] == "generic_zip" and not entries[0].get("is_aov_resource", False):
            status = "NEEDS_RULE"
            reasons.append(
                "Tệp không thuộc định dạng AoV đã nhận diện nhưng có thể là tài nguyên runtime; "
                "chỉ ghép ZIP theo đường dẫn chưa chứng minh game sẽ dùng được."
            )
        elif entries[0].get("is_aov_resource", False):
            status = "UNSUPPORTED"
            reasons.append("Tài nguyên AoV cần adapter đúng định dạng, phiên bản và fixture; detector không cấp quyền ghép.")
        else:
            status = "NEEDS_RULE"
            reasons.append("Chưa có quy tắc ghép đã xác minh cho format này.")

        results.append({
            "path": path,
            "status": status,
            "sources": sorted({item["source"] for item in entries}),
            "formats": sorted(formats),
            "sha256_count": len(hashes),
            "game_versions": sorted(versions),
            "platforms": sorted(platforms),
            "actor_ids": sorted(actors),
            "skin_ids": sorted(skins),
            "dependencies": dependencies,
            "missing_dependencies": missing_dependencies,
            "unity_dependencies": sorted({value for item in entries for value in item.get("unity_dependencies", [])}),
            "unity_external_paths": sorted({value for item in entries for value in item.get("unity_external_paths", [])}),
            "dependency_inventory_complete": all(
                item.get("dependency_inventory_complete", False) for item in entries
                if item.get("format") == "unity_assetbundle"
            ) if any(item.get("format") == "unity_assetbundle" for item in entries) else None,
            "reasons": reasons,
        })

    counts = {status: sum(item["status"] == status for item in results) for status in sorted(_ALLOWED_STATUSES)}
    return {
        "schema_version": 1,
        "policy": "fail_closed",
        "advisory_only": True,
        "merge_authorized": False,
        "resource_count": len(validated),
        "path_count": len(results),
        "summary": counts,
        "source_compatibility": source_compatibility,
        "resources": results,
    }
