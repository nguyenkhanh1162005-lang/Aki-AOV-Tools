from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import zlib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .large_merger import MergeError, merge_zip_files
from .aov_merger import AOVMergeError, merge_aov_archives
from .kpm100_vtk_merger import merge_kpm100_vtk_archives
from .adapter_registry import get_adapter_capabilities

MAX_PROFILE_ENTRIES = 20_000
MAX_PROFILE_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_PROFILE_ENTRY = 512 * 1024 * 1024
MAX_INPUT_FILES = 10
MAX_TOTAL_INPUT_BYTES = 500 * 1024 * 1024


class ModPipelineError(ValueError):
    """Raised when an archive set cannot be merged by a verified adapter."""


AOV_MARKERS = {
    "resources.zip",
    "battlebank.bytes",
    "lobbybank.bytes",
    "heroskin_1.bytes",
    "heroskin_2.bytes",
    "commonactions.pkg.bytes",
    "resourcepackerinfosetall.assetbundle",
    "resourceverificationinfosetall.assetbundle",
}
AOV_EXTENSIONS = {".bytes", ".assetbundle", ".bnk", ".wem"}


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight_inputs(paths: list[str]) -> int:
    if not paths:
        raise ModPipelineError("Chưa có ZIP đầu vào.")
    if len(paths) > MAX_INPUT_FILES:
        raise ModPipelineError(f"Chỉ nhận tối đa {MAX_INPUT_FILES} ZIP mỗi lần.")
    try:
        total_bytes = sum(Path(path).stat().st_size for path in paths)
    except OSError as exc:
        raise ModPipelineError("Không đọc được thông tin một trong các ZIP đầu vào.") from exc
    if total_bytes > MAX_TOTAL_INPUT_BYTES:
        raise ModPipelineError("Tổng dung lượng ZIP đầu vào vượt quá 500 MB.")
    return total_bytes



def _source_family_hint(
    archive_name: str,
    *,
    actor_asset_ids: set[str] | None = None,
    actor_action_ids: set[str] | None = None,
    has_vtk_ifix: bool = False,
    has_595_assets: bool = False,
) -> tuple[str | None, str | None, int | None]:
    """Return a cautious source-family hint, its evidence, and an optional part number."""
    stem = Path(archive_name).stem.casefold()
    actors = set(actor_asset_ids or ()) | set(actor_action_ids or ())

    if re.search(r"(?<![a-z0-9])p\s*9(?![0-9])", stem) or re.search(r"(?<![0-9])9[\s_-]*skin(?![a-z])", stem):
        return "P9", "filename_hint", None
    if re.search(r"(?<![a-z0-9])p\s*10(?![0-9])", stem) or re.search(r"(?<![0-9])10[\s_-]*skin(?![a-z])", stem):
        return "P10", "filename_hint", None

    part_match = re.search(r"(?:^|[^a-z0-9])part[\s_-]*([1-3])(?:$|[^0-9])", stem)
    part_number = int(part_match.group(1)) if part_match else None

    if re.search(r"tamyn|robot|machao|ma\s*chao", stem) or "593" in actors:
        basis = "resource_signature" if "593" in actors else "filename_hint"
        return "TM", basis, None
    if "vtk" in stem:
        return "VTK", "filename_hint", part_number
    if has_vtk_ifix or has_595_assets or "595" in actors:
        return "VTK", "resource_signature", part_number
    if part_number is not None:
        return f"PART_{part_number}_CANDIDATE", "filename_hint", part_number
    return None, None, None


def _build_target_set_report(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the requested six-archive set; it never authorizes a merge."""
    vtk_group_signature = (
        any(item.get("has_vtk_ifix") for item in profiles)
        and any(item.get("has_595_assets") for item in profiles)
    )
    expected = [
        ("P9", "P9 · 9 skin"),
        ("P10", "P10 · 10 skin"),
        ("TM", "TM · Tamyn - Robot"),
        ("VTK_PART_1", "VTK · Part 1"),
        ("VTK_PART_2", "VTK · Part 2"),
        ("VTK_PART_3", "VTK · Part 3"),
    ]
    assignments: dict[str, list[str]] = {key: [] for key, _ in expected}
    detected_inputs = []

    for item in profiles:
        family = item.get("source_family_hint")
        basis = item.get("source_family_basis")
        part_number = item.get("source_part_number")
        if family and family.startswith("PART_") and vtk_group_signature and item.get("looks_like_aov"):
            family = "VTK"
            basis = "filename_hint+group_actor595_signature"

        target_key = None
        if family in {"P9", "P10", "TM"}:
            target_key = family
        elif family == "VTK" and part_number in {1, 2, 3}:
            target_key = f"VTK_PART_{part_number}"
        if target_key in assignments:
            assignments[target_key].append(item["name"])

        detected_inputs.append({
            "name": item["name"],
            "family_hint": family,
            "basis": basis,
            "part_number": part_number,
            "actor_asset_ids": item.get("actor_asset_ids", []),
            "actor_action_ids": item.get("actor_action_ids", []),
        })

    summary = [
        {
            "id": key,
            "label": label,
            "count": len(assignments[key]),
            "files": assignments[key],
            "state": "đã nhận diện" if len(assignments[key]) == 1 else
                     "chưa có file" if not assignments[key] else
                     "có nhiều file trùng vai trò",
        }
        for key, label in expected
    ]
    missing = [item["label"] for item in summary if item["count"] == 0]
    duplicated = [item["label"] for item in summary if item["count"] > 1]
    complete = not missing and not duplicated and len(profiles) == 6

    return {
        "name": "P9 + P10 + TM Tamyn - Robot + VTK Part 1–3",
        "expected_archive_count": 6,
        "detected_archive_count": len(profiles),
        "input_set_complete": complete,
        "merge_supported": False,
        "merge_status": "not_supported_multi_mod",
        "vtk_parts_grouped_by_actor595_signature": vtk_group_signature,
        "sources": summary,
        "detected_inputs": detected_inputs,
        "missing": missing,
        "duplicates": duplicated,
        "blockers": [
            "Bộ ghép nhiều MOD P9 + P10 + TM + VTK chưa có adapter đã xác minh. Đây là báo cáo nhận diện nguồn, không phải xác nhận có thể xuất ZIP.",
            "Các xung đột IFix/MSES/CommonActions/metadata/soundbank và AssetBundle cần quy tắc parse/ghép riêng; không được tự chọn file thắng chỉ theo tên hoặc thời gian tải.",
        ],
        "evidence_note": (
            "P9/P10/TM/VTK là nhãn gợi ý từ tên ZIP hoặc chữ ký tài nguyên. "
            "Nhãn VTK Part 1–3 chỉ được gán khi có bằng chứng Actor 595 và HeroSkin_2 trong cả bộ nguồn."
        ),
    }


def _archive_profile(path: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            entries = archive.infolist()
            if len(entries) > MAX_PROFILE_ENTRIES:
                raise ModPipelineError(
                    f"ZIP có quá nhiều mục (tối đa {MAX_PROFILE_ENTRIES}): {Path(path).name}"
                )
            total_uncompressed = sum(info.file_size for info in entries)
            if total_uncompressed > MAX_PROFILE_UNCOMPRESSED:
                raise ModPipelineError(
                    f"ZIP vượt giới hạn giải nén 1 GiB: {Path(path).name}"
                )
            names = []
            seen_entry_paths: set[str] = set()
            for info in entries:
                normalized = info.filename.replace("\\", "/")
                # ZIP directory records conventionally end in one slash. Strip
                # that marker before validating segments, but reject ambiguous
                # separators and dot segments for both files and directories.
                candidate = normalized.rstrip("/") if info.is_dir() else normalized
                pure = PurePosixPath(candidate)
                raw_parts = candidate.split("/")
                mode = (info.external_attr >> 16) & 0xFFFF
                if (
                    not candidate
                    or pure.is_absolute()
                    or any(part in {"", ".", ".."} for part in raw_parts)
                    or "\x00" in normalized
                    or (pure.parts and pure.parts[0].endswith(":"))
                    or stat.S_ISLNK(mode)
                ):
                    raise ModPipelineError(f"Đường dẫn ZIP không an toàn hoặc không chuẩn hóa: {info.filename}")
                canonical_name = pure.as_posix()
                if canonical_name in seen_entry_paths:
                    raise ModPipelineError(
                        f"ZIP chứa đường dẫn trùng lặp hoặc không phân biệt được: {canonical_name}"
                    )
                seen_entry_paths.add(canonical_name)
                if info.is_dir():
                    continue
                if info.flag_bits & 1:
                    raise ModPipelineError(f"Không hỗ trợ ZIP mã hóa mật khẩu: {info.filename}")
                if info.file_size > MAX_PROFILE_ENTRY:
                    raise ModPipelineError(
                        f"ZIP vượt giới hạn giải nén 512 MiB: {info.filename}"
                    )
                names.append(canonical_name)
            bad = archive.testzip()
            if bad:
                raise ModPipelineError(f"ZIP lỗi CRC tại {bad}: {Path(path).name}")

            # Read only the directory metadata of one nested Resources.zip.
            # A spool file avoids keeping large nested archives entirely in RAM.
            nested_resource_names: list[str] = []
            resource_archives = [
                info for info in entries
                if not info.is_dir()
                and PurePosixPath(info.filename.replace(chr(92), "/")).name.casefold() == "resources.zip"
            ]
            nested_resource_entries = 0
            nested_resource_scan_warning = None
            if len(resource_archives) == 1:
                resource_info = resource_archives[0]
                if resource_info.file_size > MAX_PROFILE_ENTRY:
                    raise ModPipelineError(
                        f"Resources.zip vượt giới hạn 512 MiB: {Path(path).name}"
                    )
                with tempfile.SpooledTemporaryFile(
                    max_size=16 * 1024 * 1024, mode="w+b"
                ) as nested_stream:
                    with archive.open(resource_info, "r") as source_stream:
                        while True:
                            chunk = source_stream.read(1024 * 1024)
                            if not chunk:
                                break
                            nested_stream.write(chunk)
                    nested_stream.seek(0)
                    try:
                        with zipfile.ZipFile(nested_stream, "r") as nested:
                            nested_entries = nested.infolist()
                            nested_resource_entries = len(nested_entries)
                            if nested_resource_entries > MAX_PROFILE_ENTRIES:
                                raise ModPipelineError(
                                    f"Resources.zip có quá nhiều mục (tối đa {MAX_PROFILE_ENTRIES})."
                                )
                            nested_total = sum(item.file_size for item in nested_entries)
                            if nested_total > MAX_PROFILE_UNCOMPRESSED:
                                raise ModPipelineError(
                                    "Resources.zip vượt giới hạn giải nén 1 GiB."
                                )
                            nested_seen: set[str] = set()
                            for inner in nested_entries:
                                normalized = inner.filename.replace(chr(92), "/")
                                candidate = normalized.rstrip("/") if inner.is_dir() else normalized
                                pure = PurePosixPath(candidate)
                                raw_parts = candidate.split("/")
                                mode = (inner.external_attr >> 16) & 0xFFFF
                                if (
                                    not candidate
                                    or pure.is_absolute()
                                    or any(part in {"", ".", ".."} for part in raw_parts)
                                    or chr(0) in normalized
                                    or (pure.parts and pure.parts[0].endswith(":"))
                                    or stat.S_ISLNK(mode)
                                ):
                                    raise ModPipelineError(
                                        f"Đường dẫn trong Resources.zip không an toàn: {inner.filename}"
                                    )
                                canonical = pure.as_posix()
                                if canonical in nested_seen:
                                    raise ModPipelineError(
                                        f"Resources.zip có đường dẫn trùng lặp: {canonical}"
                                    )
                                nested_seen.add(canonical)
                                if inner.is_dir():
                                    continue
                                if inner.flag_bits & 1:
                                    raise ModPipelineError(
                                        f"Không hỗ trợ mục mã hóa mật khẩu trong Resources.zip: {inner.filename}"
                                    )
                                if inner.file_size > MAX_PROFILE_ENTRY:
                                    raise ModPipelineError(
                                        f"Mục Resources.zip vượt giới hạn 512 MiB: {inner.filename}"
                                    )
                                nested_resource_names.append(canonical)
                            nested_bad = nested.testzip()
                            if nested_bad:
                                raise ModPipelineError(
                                    f"Resources.zip lỗi CRC tại {nested_bad}: {Path(path).name}"
                                )
                    except ModPipelineError:
                        raise
                    except zipfile.BadZipFile:
                        # Some archives use the same filename for an opaque or
                        # non-ZIP payload. Keep it visible as uninspected rather
                        # than treating its filename as proof of valid contents.
                        nested_resource_scan_warning = (
                            "Resources.zip không mở được như ZIP; bỏ qua nhận diện tài nguyên bên trong."
                        )
                    except (OSError, RuntimeError, zlib.error) as exc:
                        raise ModPipelineError(
                            f"Không đọc được Resources.zip bên trong: {Path(path).name}"
                        ) from exc
    except ModPipelineError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
        raise ModPipelineError(f"Không đọc được ZIP: {Path(path).name}") from exc

    resource_names = names + nested_resource_names
    leaves = {PurePosixPath(name).name.lower() for name in resource_names}
    extensions = {PurePosixPath(name).suffix.lower() for name in resource_names}
    has_resource_tree = any(
        name.lower().startswith(("resources/", "databin/", "ages/", "thanos/", "assetbundle/"))
        for name in resource_names
    )
    looks_like_aov = bool(
        leaves.intersection(AOV_MARKERS)
        or (has_resource_tree and extensions.intersection(AOV_EXTENSIONS))
    )

    # A user-supplied wrapper must contain exactly one Resources.zip and one
    # skin list. Multiple wrapper candidates are kept visible to preflight but
    # are not silently collapsed into a single base archive.
    resources_zip_count = sum(
        PurePosixPath(name).name.casefold() == "resources.zip" for name in names
    )
    skin_list_count = sum(
        PurePosixPath(name).name.casefold() == "danhsachskin.txt" for name in names
    )
    has_mod_skin_container = resources_zip_count == 1 and skin_list_count == 1
    has_vtk_ifix = "heroskin_2.bytes" in leaves
    has_base_ifix = "heroskin_1.bytes" in leaves
    kpm_mod_prefix = "Resources/1.64.1/Mod_by_Youtube_KHAM-PHA-MOBILE/KPM_MOD_"
    kpm_mod_payload_count = sum(
        name.casefold().startswith(kpm_mod_prefix.casefold())
        for name in names
    )
    has_kpm_mod_payloads = kpm_mod_payload_count > 0
    has_kpm100_direct_base = (
        has_base_ifix
        and kpm_mod_payload_count >= 70
        and any(name.casefold() == (
            "Resources/1.64.1/assetbundle/resourcepackerinfosetall.assetbundle".casefold()
        ) for name in names)
    )
    actor_asset_ids: set[str] = set()
    for name in resource_names:
        if PurePosixPath(name).suffix.lower() != ".assetbundle":
            continue
        leaf = PurePosixPath(name).name
        actor_asset_ids.update(
            match.group(1)
            for match in re.finditer(r"(?:^|[_-])(\d{3})(?=[_.-]|$)", leaf)
        )
    has_595_assets = "595" in actor_asset_ids or any(
        "595_" in PurePosixPath(name).name.lower()
        or "595" in PurePosixPath(name).name.lower()
        for name in resource_names
        if PurePosixPath(name).suffix.lower() == ".assetbundle"
    )
    actor_action_ids: set[str] = set()
    for name in resource_names:
        match = re.search(
            r"(?:^|/)Actor[_-](\d{3})[_-]Actions\.pkg\.bytes$",
            name,
            re.IGNORECASE,
        )
        if match:
            actor_action_ids.add(match.group(1))
    has_actor593_content = "593" in actor_asset_ids or "593" in actor_action_ids

    source_family_hint, source_family_basis, source_part_number = _source_family_hint(
        Path(path).name,
        actor_asset_ids=actor_asset_ids,
        actor_action_ids=actor_action_ids,
        has_vtk_ifix=has_vtk_ifix,
        has_595_assets=has_595_assets,
    )

    if not looks_like_aov:
        source_role = "ordinary_zip"
    elif has_mod_skin_container:
        source_role = "aov_skin_mod_container_candidate"
    elif has_kpm100_direct_base:
        source_role = "aov_kpm100_direct_base_candidate"
    elif has_vtk_ifix and has_595_assets:
        source_role = "aov_actor595_mod_candidate"
    elif has_actor593_content:
        source_role = "aov_actor593_mod_candidate"
    else:
        source_role = "aov_mod_unclassified"

    return {
        "name": Path(path).name,
        "entries": len(entries),
        "uncompressed_bytes": total_uncompressed,
        "looks_like_aov": looks_like_aov,
        "source_role": source_role,
        "has_mod_skin_container": has_mod_skin_container,
        "resources_zip_count": resources_zip_count,
        "skin_list_count": skin_list_count,
        "actor_asset_ids": sorted(actor_asset_ids),
        "actor_action_ids": sorted(actor_action_ids),
        "nested_resource_entries": nested_resource_entries,
        "nested_resource_scan_warning": nested_resource_scan_warning,
        "has_actor593_content": has_actor593_content,
        "source_family_hint": source_family_hint,
        "source_family_basis": source_family_basis,
        "source_part_number": source_part_number,
        "has_vtk_ifix": has_vtk_ifix,
        "has_base_ifix": has_base_ifix,
        "has_kpm_mod_payloads": has_kpm_mod_payloads,
        "kpm_mod_payload_count": kpm_mod_payload_count,
        "has_kpm100_direct_base": has_kpm100_direct_base,
        "has_595_assets": has_595_assets,
        "extensions": sorted(extensions),
    }


def _select_adapter(profiles: list[dict[str, Any]]) -> str:
    if any(item["looks_like_aov"] for item in profiles):
        kpm_bases = [
            item for item in profiles
            if item.get("has_kpm100_direct_base")
        ]
        vtk_overlays = [
            item for item in profiles
            if item.get("source_role") == "aov_actor595_mod_candidate"
            and not item.get("has_kpm100_direct_base")
            and item.get("has_vtk_ifix")
            and item.get("has_595_assets")
        ]
        if (
            len(profiles) == 2
            and len(kpm_bases) == 1
            and len(vtk_overlays) == 1
            and all(item["looks_like_aov"] for item in profiles)
        ):
            return "aov_kpm100_vtk_actor595_1.64.1"

        mod_containers = [
            item for item in profiles if item["has_mod_skin_container"]
        ]
        # VTK may be split over several ZIPs, but every non-wrapper input must
        # itself look like a VTK/Actor595 part. A P9/P10/TM archive must never
        # become an overlay merely because another input contains Actor 595.
        vtk_parts = [item for item in profiles if item not in mod_containers]
        forbidden_families = {"P9", "P10", "TM"}
        allowed_vtk_families = {
            "VTK", "PART_1_CANDIDATE", "PART_2_CANDIDATE",
            "PART_3_CANDIDATE", None,
        }
        vtk_parts_are_scoped = bool(vtk_parts) and all(
            item.get("looks_like_aov")
            and not item.get("has_mod_skin_container")
            and item.get("source_family_hint") not in forbidden_families
            and item.get("source_family_hint") in allowed_vtk_families
            and set(item.get("actor_asset_ids", [])) <= {"595"}
            and set(item.get("actor_action_ids", [])) <= {"595"}
            for item in vtk_parts
        )
        # VTK is often distributed as multiple ZIP parts. Its IFix payload
        # and Actor 595 AssetBundles may therefore be in different input files.
        # The specialist merger still verifies the exact bundle inventory,
        # versions, dependencies and checksums before publishing an output.
        has_actor595_overlay = (
            any(item["has_vtk_ifix"] for item in vtk_parts)
            and any(item["has_595_assets"] for item in vtk_parts)
            and not any(item.get("has_actor593_content") for item in vtk_parts)
        )
        # The supported route is one user-supplied mod container plus only
        # user-supplied VTK Actor 595 parts, never a mixed set of other MODs.
        supported_pair = (
            len(mod_containers) == 1
            and vtk_parts_are_scoped
            and has_actor595_overlay
            and all(item["looks_like_aov"] for item in profiles)
        )
        if supported_pair:
            return "aov_mod_to_mod_vtk_actor595_1.64.1"

        names = ", ".join(item["name"] for item in profiles if item["looks_like_aov"])
        raise ModPipelineError(
            "Phát hiện tài nguyên AoV nhưng chưa có adapter ghép đã xác minh cho bộ này: "
            f"{names}. Không dùng gói game gốc làm nền và không ghi đè .bytes, "
            "AssetBundle hoặc soundbank theo kiểu ZIP thường. "
            "Adapter hiện có chỉ ghép gói mod 10 skin do người dùng cung cấp "
            "với mod Vấn Tâm Kiếm Actor 595 đúng phiên bản; các mod khác cần "
            "parser, serializer và quy tắc tương thích riêng."
        )
    return "generic_zip"


_PRESERVATION_GROUPS = (
    ("kill_notifications", "Thông báo hạ gục / billboard", 0),
    ("effects_recall", "Hiệu ứng / hoạt ảnh / biến về", 1),
    ("ui_buttons", "Giao diện / nút bấm / biểu tượng", 2),
    ("audio", "Âm thanh", 3),
)

def _build_preservation_gates(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate exact priority counts across the complete manifest."""
    grouped = {key: [] for key, _, _ in _PRESERVATION_GROUPS}
    grouped["other"] = []
    for resource in resources:
        label, _ = _resource_priority_hint(str(resource.get("path", "")))
        key = next((key for key, group_label, _ in _PRESERVATION_GROUPS if group_label == label), "other")
        grouped[key].append(resource)
    result = []
    for key, label, rank in _PRESERVATION_GROUPS + (("other", "Tài nguyên khác", 4),):
        items = grouped[key]
        counts = {status: sum(item.get("status") == status for item in items) for status in ("VERSION_CONFLICT", "NEEDS_RULE", "UNSUPPORTED", "IDENTICAL_DUPLICATE", "SAFE_MERGE", "IGNORED_METADATA")}
        unresolved = counts["VERSION_CONFLICT"] + counts["NEEDS_RULE"] + counts["UNSUPPORTED"]
        enriched = []
        adapter_counts = {}
        for item in items:
            guidance = _resource_merge_guidance(str(item.get("path", "")))
            adapter = guidance.get("adapter_state", "no_known_adapter")
            adapter_counts[adapter] = adapter_counts.get(adapter, 0) + 1
            if item.get("status") in {"VERSION_CONFLICT", "NEEDS_RULE", "UNSUPPORTED", "IDENTICAL_DUPLICATE"}:
                enriched.append({"path": item.get("path", ""), "status": item.get("status", "UNKNOWN"), "sources": item.get("sources", []), "adapter_state": adapter, "next_action": guidance.get("next_action", ""), "safety_rule": guidance.get("safety_rule", ""), "reasons": item.get("reasons", []), "missing_dependencies": item.get("missing_dependencies", [])})
        enriched.sort(key=lambda item: (0 if item["status"] == "VERSION_CONFLICT" else 1 if item["status"] == "NEEDS_RULE" else 2 if item["status"] == "UNSUPPORTED" else 3, item["path"]))
        state = "NOT_IN_MANIFEST" if not items else "REVIEW_REQUIRED" if unresolved else "NO_UNRESOLVED_PATH_CONFLICTS"
        result.append({"id": key, "label": label, "priority_rank": rank, "state": state, "path_count": len(items), "unresolved_count": unresolved, "identical_duplicate_count": counts["IDENTICAL_DUPLICATE"], "safe_merge_count": counts["SAFE_MERGE"], "adapter_states": dict(sorted(adapter_counts.items())), "status_counts": counts, "top_items": enriched[:12], "details_truncated": len(enriched) > 12, "merge_authorized": False, "game_tested": False})
    return result

def _resource_merge_guidance(resource_path: str) -> dict[str, str]:
    """Describe the narrowest known adapter for a path without granting merge permission."""
    path = resource_path.replace("\\", "/")
    lower = path.casefold()
    leaf = PurePosixPath(lower).name

    if leaf == "commonactions.pkg.bytes" or "commonactions.pkg.bytes/" in lower:
        return {
            "adapter_state": "partial_parser",
            "next_action": (
                "Có parser XML/track và ghép tuần tự nhiều overlay theo SkinOrAvatarList "
                "con trực tiếp. Cần xác minh từng XML của Actor 593/595; CheckSkinIdTick "
                "dựa trên condition-index chưa được hỗ trợ."
            ),
            "safety_rule": "Không thay toàn bộ CommonActions bằng một nguồn; xung đột ở file ngoài danh sách XML có parser phải dừng.",
        }

    if leaf in {"heroskin_1.bytes", "heroskin_2.bytes"}:
        return {
            "adapter_state": "version_locked_parser",
            "next_action": (
                "Có parser/serializer IFix, nhưng quy tắc ghép chỉ được xác minh trong "
                "adapter VTK Actor 595 đã khóa phiên bản. Chưa áp dụng cho P9/P10/TM."
            ),
            "safety_rule": "Không nối byte hoặc trộn patch IFix khác actor/build nếu chưa có fixture round-trip riêng.",
        }

    if leaf in {
        "resbillboardcfg.bytes",
        "resbillboardskincfg.bytes",
        "reskillbillboardcfg.bytes",
    }:
        return {
            "adapter_state": "version_locked_schema",
            "next_action": (
                "Có parser/serializer MSES v7 cho bảng Huanhua billboard trong "
                "adapter iOS 10 skin + VTK Actor 595 đã khóa phiên bản. Chỉ nhận "
                "khóa Actor 595 0x253 và chỉ khi bảng tương ứng đã có trong ZIP nền."
            ),
            "safety_rule": (
                "Không thêm bảng MSES khi thiếu schema nền; khóa 0x253 đã tồn tại "
                "nhưng khác payload hoặc có khóa mới ngoài allowlist thì phải dừng."
            ),
        }

    if leaf == "rescharactercomponent.bytes":
        return {
            "adapter_state": "source_validated_mses_union",
            "next_action": (
                "Đã xác minh trên cặp P9/P10 mẫu: MSES v7, 188 trường, checksum hợp lệ; "
                "520 khóa chung giống byte và chỉ nối khóa mới. Đây mới là adapter cấp bảng, "
                "chưa mở ghép hai wrapper hoàn chỉnh."
            ),
            "safety_rule": (
                "Chỉ hợp nhất cùng schema 188 trường; khóa trùng, hàng chung khác byte, "
                "checksum sai hoặc sai đường dẫn phải chặn."
            ),
        }

    if leaf == "resskinseniorlabelcfg.bytes":
        return {
            "adapter_state": "source_pinned_mses_union",
            "next_action": (
                "Có hai quy tắc hẹp: VTK Actor 595 dùng allowlist 59500; cặp P9/P10 mẫu "
                "chỉ nhận MSES v7/69 trường/cặp 8-98 hàng với chuỗi checksum cũ đã ghim, "
                "tập khóa 8 hàng là tập con và mọi hàng chung giống byte. Output phải tính lại checksum."
            ),
            "safety_rule": (
                "Không tự sửa checksum của nguồn khác; sai số hàng, schema, checksum ghim, "
                "hàng chung hoặc khóa không đúng quy tắc đều phải chặn."
            ),
        }

    if leaf in {"battlebank.bytes", "lobbybank.bytes", "skillcombine.bytes"}:
        return {
            "adapter_state": "version_locked_schema",
            "next_action": (
                "Có parser/serializer MSES v7; allowlist khóa chỉ có trong adapter "
                "VTK Actor 595. P9/P10 sample skillcombine giống hệt nhau, có thể dedupe; "
                "soundbank P9/P10/TM có nhiều hàng chung khác payload và chưa được ghép."
            ),
            "safety_rule": "Khác payload ở cùng khóa hoặc khóa mới ngoài allowlist phải chặn ghép.",
        }

    if leaf in {"kernellua.pkg.bytes", "stablesystems_3.pkg.bytes"}:
        return {
            "adapter_state": "nested_package_union_only",
            "next_action": (
                "Có thể hợp nhất ZIP/PKG lồng theo đường dẫn: giữ mục riêng, bỏ duplicate "
                "chỉ khi SHA-256 giống hệt; cần kiểm tra tiếp từng xung đột đường dẫn."
            ),
            "safety_rule": "Cùng đường dẫn bên trong nhưng payload khác nhau phải dừng; union không chứng minh tương thích Lua.",
        }

    if "resourcepackerinfosetall" in leaf or "resourceverificationinfosetall" in leaf:
        return {
            "adapter_state": "version_locked_metadata",
            "next_action": (
                "Có bộ cập nhật dependency/checksum đã xác minh cho VTK 595. "
                "Cần adapter metadata tổng hợp mới cho tất cả bundle P9/P10/TM/VTK."
            ),
            "safety_rule": "Không thêm AssetBundle nếu PackerInfo/Verification không phản ánh đúng path, ID và checksum.",
        }

    if lower.endswith(".assetbundle"):
        return {
            "adapter_state": "inspect_only",
            "next_action": (
                "Có thể đọc UnityFS header/object/dependency trong giới hạn hiện tại; "
                "chưa có bộ ghép/rewrite object hoặc dependency tổng quát."
            ),
            "safety_rule": "Không ghi đè hoặc ghép AssetBundle chỉ vì tên hay Actor ID giống nhau.",
        }

    if lower.endswith((".bnk", ".wem")) or "/sound/" in lower:
        return {
            "adapter_state": "inspect_only",
            "next_action": (
                "Có kiểm tra cấu trúc Wwise/BNK và inventory media; chưa có serializer "
                "remap Event/Action/HIRC/WEM cho nhiều skin."
            ),
            "safety_rule": "Không nối byte BNK hoặc thay bank iOS bằng bank Android.",
        }

    if "assetrefs/" in lower or leaf.endswith("_assetref.bytes"):
        return {
            "adapter_state": "needs_assetref_schema",
            "next_action": "Cần parser AssetRef và kiểm tra tham chiếu tới bundle/dependency tương ứng.",
            "safety_rule": "Không chép AssetRef độc lập nếu chưa kiểm tra object/path mà nó trỏ tới.",
        }

    if "actions.pkg.bytes" in leaf or "_infos.pkg.bytes" in leaf:
        return {
            "adapter_state": "package_candidate_only",
            "next_action": (
                "Đường dẫn actor riêng có thể là ứng viên thêm mới, nhưng cần parse package "
                "và kiểm tra tham chiếu/metadata trước khi đưa vào gói đích."
            ),
            "safety_rule": "Không coi việc tên file chưa trùng là đủ để xác nhận dependency hoàn chỉnh.",
        }

    return {
        "adapter_state": "no_known_adapter",
        "next_action": "Chưa có adapter chuyên biệt được xác minh cho đường dẫn này; tiếp tục chỉ kiểm kê.",
        "safety_rule": "Không tự ghi đè payload khác nội dung.",
    }


def _resource_priority_hint(resource_path: str) -> tuple[str, int]:
    """Heuristic UI grouping only; names do not prove a resource's runtime role."""
    path = resource_path.replace("\\", "/").casefold()
    if any(token in path for token in (
        "reskillbillboard", "resbillboard", "commercializenotify",
        "resskinseniorlabelcfg",
    )):
        return "Thông báo hạ gục / billboard", 0
    if any(token in path for token in (
        "commonactions", "heroskin_", "_actions.pkg", "resskinmotion",
        "assetbundle/battle/hero/", "assetbundle/show/hero/",
    )):
        return "Hiệu ứng / hoạt ảnh / biến về", 1
    if any(token in path for token in (
        "assetbundle/uisystem/", "assetrefs/", "headimage",
        "assetbundle/icon/", "battleotherui", "battlecommon_raw",
    )):
        return "Giao diện / nút bấm / biểu tượng", 2
    if any(token in path for token in (
        "/sound/", "bank.bytes", "sound.bytes", ".bnk", ".wem",
    )):
        return "Âm thanh", 3
    return "Tài nguyên khác", 4




_COMMON_ACTIONS_PATH_SUFFIX = (
    "ages/prefab_characters/prefab_hero/commonactions.pkg.bytes"
)
_COMMON_ACTIONS_MAX_BYTES = 256 * 1024 * 1024


def _is_common_actions_resource(path: str) -> bool:
    normalized = path.replace(chr(92), "/").lstrip("/").casefold()
    return normalized.endswith(_COMMON_ACTIONS_PATH_SUFFIX)


def _read_zip_member_limited(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    label: str,
    *,
    limit: int = _COMMON_ACTIONS_MAX_BYTES,
) -> bytes:
    if info.file_size > limit:
        raise ModPipelineError(
            f"{label} vượt giới hạn đọc {limit // (1024 * 1024)} MiB."
        )
    chunks = []
    total = 0
    with archive.open(info, "r") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            if total > limit:
                raise ModPipelineError(
                    f"{label} vượt giới hạn đọc {limit // (1024 * 1024)} MiB."
                )
            chunks.append(chunk)
    return b"".join(chunks)


def _collect_common_actions_overlays(
    archive_paths: list[str],
    profiles: list[dict[str, Any]] | None = None,
) -> list[tuple[str, bytes, set[str]]]:
    """Extract one CommonActions package per source, including nested Resources.zip."""
    found = []
    profile_by_name = {
        str(item.get("name")): item for item in (profiles or [])
    }
    for archive_path in archive_paths:
        source_name = Path(archive_path).name
        profile = profile_by_name.get(source_name, {})
        actor_prefixes = {
            str(value) for value in profile.get("actor_action_ids", [])
        }
        source_matches = []
        with zipfile.ZipFile(archive_path, "r") as outer:
            for info in outer.infolist():
                if info.is_dir():
                    continue
                if _is_common_actions_resource(info.filename):
                    payload = _read_zip_member_limited(
                        outer, info, f"{source_name}:{info.filename}"
                    )
                    source_matches.append((info.filename, payload))
                    continue

                normalized_name = info.filename.replace(chr(92), "/")
                if PurePosixPath(normalized_name).name.casefold() != "resources.zip":
                    continue
                if info.file_size > MAX_PROFILE_UNCOMPRESSED:
                    raise ModPipelineError(
                        f"{source_name}:{info.filename} vượt giới hạn Resources.zip lồng."
                    )
                with tempfile.TemporaryFile() as nested_stream:
                    copied = 0
                    with outer.open(info, "r") as source_stream:
                        for chunk in iter(
                            lambda: source_stream.read(1024 * 1024), b""
                        ):
                            copied += len(chunk)
                            if copied > MAX_PROFILE_UNCOMPRESSED:
                                raise ModPipelineError(
                                    f"{source_name}:{info.filename} vượt giới hạn giải nén."
                                )
                            nested_stream.write(chunk)
                    nested_stream.seek(0)
                    try:
                        with zipfile.ZipFile(nested_stream, "r") as nested:
                            for nested_info in nested.infolist():
                                if nested_info.is_dir() or not _is_common_actions_resource(
                                    nested_info.filename
                                ):
                                    continue
                                payload = _read_zip_member_limited(
                                    nested,
                                    nested_info,
                                    f"{source_name}:{nested_info.filename}",
                                )
                                source_matches.append(
                                    (f"{info.filename}!/{nested_info.filename}", payload)
                                )
                    except zipfile.BadZipFile as exc:
                        raise ModPipelineError(
                            f"{source_name}:{info.filename} không phải ZIP lồng hợp lệ."
                        ) from exc

        if len(source_matches) > 1:
            paths = ", ".join(item[0] for item in source_matches)
            raise ModPipelineError(
                f"{source_name} có nhiều CommonActions candidate ({paths}); "
                "không tự chọn một bản."
            )
        if source_matches:
            internal_path, payload = source_matches[0]
            found.append((
                f"{source_name} · {internal_path}",
                payload,
                actor_prefixes,
            ))
    return found


def _build_common_actions_overlay_assessment(
    archive_paths: list[str],
    profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        overlays = _collect_common_actions_overlays(archive_paths, profiles)
        from .aov_common_actions import analyze_common_actions_overlay_set

        return analyze_common_actions_overlay_set(overlays)
    except (OSError, zipfile.BadZipFile, ModPipelineError) as exc:
        return {
            "available": True,
            "state": "BLOCKED",
            "reason": str(exc),
            "merge_authorized": False,
            "full_merge_authorized": False,
            "game_tested": False,
        }


def analyze_aov_input_set(archive_paths: list[str]) -> dict[str, Any]:
    """Read-only preflight for a multi-ZIP AoV source set.

    This reports source roles and adapter blockers without mutating any archive
    or implying that an unsupported multi-MOD set can be merged.
    """
    total_input_bytes = _preflight_inputs(archive_paths)
    profiles = [_archive_profile(path) for path in archive_paths]
    total_entries = sum(item["entries"] for item in profiles)
    if total_entries > MAX_PROFILE_ENTRIES:
        raise ModPipelineError("Tổng số mục trong các ZIP vượt quá 20.000.")
    total_uncompressed = sum(item["uncompressed_bytes"] for item in profiles)
    if total_uncompressed > MAX_PROFILE_UNCOMPRESSED:
        raise ModPipelineError("Tổng dung lượng giải nén của các ZIP vượt quá 1 GiB.")

    role_counts: dict[str, int] = {}
    for item in profiles:
        role = str(item["source_role"])
        role_counts[role] = role_counts.get(role, 0) + 1

    wrapper_profiles = [item for item in profiles if item.get("has_mod_skin_container")]
    actor_ids = sorted({
        actor_id
        for item in profiles
        for actor_id in (
            item.get("actor_asset_ids", []) + item.get("actor_action_ids", [])
        )
    })
    target_set = _build_target_set_report(profiles)
    blockers: list[str] = []
    selected_adapter: str | None = None
    try:
        selected_adapter = _select_adapter(profiles)
    except ModPipelineError as exc:
        blockers.append(str(exc))

    if len(wrapper_profiles) > 1:
        blockers.insert(
            0,
            "Có nhiều MOD wrapper chứa Resources.zip + DanhSachSkin.txt "
            f"({len(wrapper_profiles)} gói). Adapter hiện tại chỉ hỗ trợ một wrapper nền; "
            "chưa có quy tắc đã xác minh để hợp nhất nhiều Resources.zip.",
        )

    malformed_wrappers = [
        item for item in profiles
        if (
            item.get("resources_zip_count", 0)
            or item.get("skin_list_count", 0)
        ) and not item.get("has_mod_skin_container")
    ]
    for item in malformed_wrappers:
        blockers.append(
            f"{item['name']}: cấu trúc wrapper không rõ ràng "
            f"(Resources.zip={item.get('resources_zip_count', 0)}, "
            f"DanhSachSkin.txt={item.get('skin_list_count', 0)}); "
            "cần đúng một file mỗi loại."
        )

    supported_aov_adapters = {
        "aov_kpm100_vtk_actor595_1.64.1",
        "aov_mod_to_mod_vtk_actor595_1.64.1",
    }
    if selected_adapter == "generic_zip":
        blockers.append(
            "Không phát hiện nguồn AoV. Bộ này chỉ phù hợp với chức năng ghép ZIP thông thường, "
            "không phải bộ ghép MOD Liên Quân."
        )

    resource_analysis: dict[str, Any]
    try:
        # Reuse the existing nested-resource manifest and compatibility planner.
        # This is advisory only: it classifies collisions but does not grant merge
        # permission or change the adapter selection above.
        from .compatibility_planner import (
            CompatibilityPlanError,
            plan_resource_compatibility,
        )
        from .resource_manifest import ResourceManifestError, build_resource_manifest
        from .mses_diagnostics import (
            MSESDiagnosticsError,
            analyze_mses_overlaps,
        )

        manifest = build_resource_manifest(archive_paths)
        plan = plan_resource_compatibility(manifest["resources"])
        mses_diagnostics = analyze_mses_overlaps(
            archive_paths, manifest["resources"]
        )
        format_counts: dict[str, int] = {}
        detected_format_counts: dict[str, int] = {}
        for resource in manifest["resources"]:
            resource_format = str(resource.get("format") or "unknown")
            detected_format = str(resource.get("detected_format") or "unknown")
            format_counts[resource_format] = format_counts.get(resource_format, 0) + 1
            detected_format_counts[detected_format] = detected_format_counts.get(detected_format, 0) + 1
        critical_statuses = {"VERSION_CONFLICT", "NEEDS_RULE", "UNSUPPORTED"}
        details = [
            item for item in plan["resources"]
            if item["status"] in critical_statuses
            or item["status"] == "IDENTICAL_DUPLICATE"
        ]
        for item in details:
            priority_group, priority_rank = _resource_priority_hint(item["path"])
            item["priority_group"] = priority_group
            item["priority_rank"] = priority_rank
            item["priority_hint_only"] = True
            item.update(_resource_merge_guidance(item["path"]))
        details.sort(
            key=lambda item: (
                item["priority_rank"],
                0 if item["status"] == "VERSION_CONFLICT" else
                1 if item["status"] == "NEEDS_RULE" else
                2 if item["status"] == "UNSUPPORTED" else 3,
                item["path"],
            )
        )
        guidance_counts: dict[str, int] = {}
        for item in details:
            state = item.get("adapter_state", "no_known_adapter")
            guidance_counts[state] = guidance_counts.get(state, 0) + 1
        preservation_gates = _build_preservation_gates(plan["resources"])
        resource_analysis = {
            "available": True,
            "advisory_only": True,
            "merge_authorized": False,
            "preservation_gates": preservation_gates,
            "mses_diagnostics": mses_diagnostics,
            "merge_guidance_counts": guidance_counts,
            "resource_count": manifest["resource_count"],
            "path_count": plan["path_count"],
            "unique_path_count": sum(
                1 for item in plan["resources"] if len(item["sources"]) == 1
            ),
            "summary": plan["summary"],
            "format_counts": dict(sorted(format_counts.items())),
            "detected_format_counts": dict(sorted(detected_format_counts.items())),
            "source_compatibility": plan["source_compatibility"],
            "important_paths": details[:200],
            "details_truncated": len(details) > 200,
            "priority_note": (
                "Nhóm ưu tiên được gợi ý từ tên đường dẫn để sắp xếp báo cáo; "
                "không thay thế việc parse payload hoặc xác nhận chức năng trong game."
            ),
        }
    except (ResourceManifestError, CompatibilityPlanError) as exc:
        resource_analysis = {
            "available": False,
            "advisory_only": True,
            "merge_authorized": False,
            "format_counts": {},
            "detected_format_counts": {},
            "error": str(exc),
        }

    ready = selected_adapter in supported_aov_adapters and not blockers
    general_analysis = {
        "scope": "all_uploaded_mod_package_types",
        "source_package_types": dict(sorted(role_counts.items())),
        "resource_formats": resource_analysis.get("format_counts", {}),
        "detected_formats": resource_analysis.get("detected_format_counts", {}),
        "actor_ids": actor_ids,
        "unclassified_aov_source_count": sum(
            1 for item in profiles
            if item.get("looks_like_aov") and not item.get("source_family_hint")
        ),
        "source_hints": [
            {
                "name": item["name"],
                "family_hint": item.get("source_family_hint"),
                "evidence": item.get("source_family_basis"),
                "role": item.get("source_role"),
                "actor_asset_ids": item.get("actor_asset_ids", []),
                "actor_action_ids": item.get("actor_action_ids", []),
            }
            for item in profiles
        ],
        "compatibility_summary": resource_analysis.get("summary", {}),
        "source_compatibility": resource_analysis.get("source_compatibility", {}),
        "selected_adapter": selected_adapter,
        "merge_ready": ready,
        "merge_policy": (
            "Chỉ ghép khi khớp adapter đã xác minh; định dạng mới vẫn được nhận diện/kiểm kê "
            "nhưng không tự động được cấp quyền ghi."
        ),
        "can_analyze_unlisted_mod_types": True,
        "can_merge_unlisted_mod_types": False,
        "note": (
            "Phân loại cấu trúc dựa trên nội dung ZIP và chữ ký tài nguyên; tên P9/P10/TM/VTK "
            "chỉ là gợi ý phụ. Actor ID không xác định được sẽ được giữ ở trạng thái chưa phân loại."
        ),
    }
    return {
        "schema_version": 2,
        "mode": "read_only_multi_mod_preflight",
        "analysis_scope": "all_mod_types", 
        "general_analysis": general_analysis,
        "ready_to_merge": ready,
        "selected_adapter": selected_adapter,
        "source_count": len(profiles),
        "total_input_bytes": total_input_bytes,
        "total_uncompressed_bytes": total_uncompressed,
        "role_counts": role_counts,
        "actor_asset_ids": actor_ids,
        "target_set": target_set,
        "profiles": profiles,
        "blockers": blockers,
        "resource_analysis": resource_analysis,
        "common_actions_overlay_assessment": _build_common_actions_overlay_assessment(
            archive_paths, profiles
        ),
        "game_tested": False,
        "note": (
            "Đạt bước chọn adapter theo cấu trúc; bộ ghép vẫn tự xác minh phiên bản, "
            "schema, checksum và dependency trước khi xuất ZIP."
            if ready else
            "Đây là phân tích nhiều loại MOD ở chế độ chỉ đọc. Không tạo ZIP nếu chưa có "
            "adapter đã xác minh; tài nguyên game trùng đường dẫn cần quy tắc riêng."
        ),
    }


def merge_mod_archives(archive_paths: list[str], output_path: str) -> dict[str, Any]:
    """Select a verified adapter and merge transactionally.

    AoV packs without a dedicated parser/serializer fail closed. The supported
    Actor 595 route is mod-to-mod and never uses the official game package.
    """
    total_input_bytes = _preflight_inputs(archive_paths)
    profiles = [_archive_profile(path) for path in archive_paths]
    total_profile_entries = sum(item["entries"] for item in profiles)
    if total_profile_entries > MAX_PROFILE_ENTRIES:
        raise ModPipelineError("Tổng số mục trong các ZIP vượt quá 20.000.")
    total_uncompressed = sum(item["uncompressed_bytes"] for item in profiles)
    if total_uncompressed > MAX_PROFILE_UNCOMPRESSED:
        raise ModPipelineError("Tổng dung lượng giải nén của các ZIP vượt quá 1 GiB.")
    adapter = _select_adapter(profiles)
    capability = next(
        (
            item for item in get_adapter_capabilities()["adapters"]
            if item["id"] == adapter
        ),
        None,
    )
    allowed_statuses = {"merge_supported", "merge_supported_version_locked"}
    if capability is None or capability["status"] not in allowed_statuses:
        status = capability["status"] if capability else "unregistered"
        raise ModPipelineError(
            f"Adapter {adapter} chưa được cấp quyền ghép (status={status}). "
            "Bộ xử lý sẽ không bỏ qua khóa an toàn."
        )

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp.zip", dir=str(destination.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink(missing_ok=True)

    try:
        if adapter == "aov_kpm100_vtk_actor595_1.64.1":
            base_path = next(
                path for path, profile in zip(archive_paths, profiles)
                if profile.get("has_kpm100_direct_base")
            )
            vtk_path = next(
                path for path, profile in zip(archive_paths, profiles)
                if profile.get("source_role") == "aov_actor595_mod_candidate"
                and not profile.get("has_kpm100_direct_base")
                and profile.get("has_vtk_ifix")
                and profile.get("has_595_assets")
            )
            result = merge_kpm100_vtk_archives(base_path, vtk_path, str(temporary))
        elif adapter == "aov_mod_to_mod_vtk_actor595_1.64.1":
            result = merge_aov_archives(archive_paths, str(temporary))
        else:
            size = merge_zip_files(archive_paths, str(temporary))
            result = {"output_bytes": size, "adapter": adapter}

        with zipfile.ZipFile(temporary, "r") as output_zip:
            bad = output_zip.testzip()
            if bad:
                raise ModPipelineError(f"ZIP kết quả lỗi CRC tại {bad}.")
        os.replace(temporary, destination)
        result.update({
            "adapter": adapter,
            "input_count": len(archive_paths),
            "input_bytes": total_input_bytes,
            "input_uncompressed_bytes": total_uncompressed,
            "input_profiles": profiles,
            "output_bytes": destination.stat().st_size,
            "structurally_validated": True,
            "game_compatibility_tested": False,
            "official_game_package_injected": False,
            "input_sha256": [
                {"name": Path(path).name, "sha256": _sha256_file(path)}
                for path in archive_paths
            ],
            "output_sha256": _sha256_file(str(destination)),
        })
        return result
    except (MergeError, AOVMergeError, ValueError) as exc:
        if isinstance(exc, ModPipelineError):
            raise
        raise ModPipelineError(str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)
