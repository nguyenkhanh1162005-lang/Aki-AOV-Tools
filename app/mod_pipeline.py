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
    except ModPipelineError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
        raise ModPipelineError(f"Không đọc được ZIP: {Path(path).name}") from exc

    leaves = {PurePosixPath(name).name.lower() for name in names}
    extensions = {PurePosixPath(name).suffix.lower() for name in names}
    has_resource_tree = any(
        name.lower().startswith(("resources/", "databin/", "ages/", "thanos/", "assetbundle/"))
        for name in names
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
    for name in names:
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
        for name in names
        if PurePosixPath(name).suffix.lower() == ".assetbundle"
    )

    if not looks_like_aov:
        source_role = "ordinary_zip"
    elif has_mod_skin_container:
        source_role = "aov_skin_mod_container_candidate"
    elif has_kpm100_direct_base:
        source_role = "aov_kpm100_direct_base_candidate"
    elif has_vtk_ifix and has_595_assets:
        source_role = "aov_actor595_mod_candidate"
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
        for actor_id in item.get("actor_asset_ids", [])
    })
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
