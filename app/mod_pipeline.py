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
                and PurePosixPath(info.filename.replace("\\\\", "/")).name.casefold() == "resources.zip"
            ]
            nested_resource_entries = 0
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
                                normalized = inner.filename.replace("\\\\", "/")
                                candidate = normalized.rstrip("/") if inner.is_dir() else normalized
                                pure = PurePosixPath(candidate)
                                raw_parts = candidate.split("/")
                                mode = (inner.external_attr >> 16) & 0xFFFF
                                if (
                                    not candidate
                                    or pure.is_absolute()
                                    or any(part in {"", ".", ".."} for part in raw_parts)
                                    or "\\x00" in normalized
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
                    except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
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
        # VTK is often distributed as multiple ZIP parts. Its IFix payload
        # and Actor 595 AssetBundles may therefore be in different input files.
        # Validate the evidence across the complete overlay set, then let the
        # specialist merger verify the exact required asset list and versions.
        has_actor595_overlay = (
            any(item["has_vtk_ifix"] for item in profiles)
            and any(item["has_595_assets"] for item in profiles)
        )
        # The supported route is user-supplied mod pack + user-supplied mod
        # overlay. The 10-skin archive is a mod container, not the official game.
        supported_pair = (
            len(mod_containers) == 1
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

        manifest = build_resource_manifest(archive_paths)
        plan = plan_resource_compatibility(manifest["resources"])
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
        details.sort(
            key=lambda item: (
                item["priority_rank"],
                0 if item["status"] == "VERSION_CONFLICT" else
                1 if item["status"] == "NEEDS_RULE" else
                2 if item["status"] == "UNSUPPORTED" else 3,
                item["path"],
            )
        )
        resource_analysis = {
            "available": True,
            "advisory_only": True,
            "merge_authorized": False,
            "resource_count": manifest["resource_count"],
            "path_count": plan["path_count"],
            "unique_path_count": sum(
                1 for item in plan["resources"] if len(item["sources"]) == 1
            ),
            "summary": plan["summary"],
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
            "error": str(exc),
        }

    ready = selected_adapter in supported_aov_adapters and not blockers
    return {
        "schema_version": 1,
        "mode": "read_only_aov_preflight",
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
        "game_tested": False,
        "note": (
            "Đạt bước chọn adapter theo cấu trúc; bộ ghép vẫn tự xác minh phiên bản, "
            "schema, checksum và dependency trước khi xuất ZIP."
            if ready else
            "Chỉ là báo cáo phân loại. Không tạo ZIP và không tự chọn file nền khi có "
            "nhiều MOD. Các tài nguyên AoV trùng đường dẫn cần quy tắc hợp nhất riêng."
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
