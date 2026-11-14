from __future__ import annotations

import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .aov_catalog import AOVCatalogMergeError, merge_aov_metadata_assetbundle
from .aov_common_actions import (
    CommonActionsMergeError,
    analyze_common_actions_overlay_set,
    merge_common_actions_packages,
)
from .aov_merge import (
    AOVResourceMergeError,
    _ACTOR_OWNED_MSES_PATHS,
    _SUPPORTED_MSES_TABLES,
    merge_aov_mses_resource,
    merge_aov_mses_resource_chain,
)
from .aov_merger import (
    AOVMergeError,
    BASE_PREFIX,
    COMMON_ACTIONS_PATH,
    PACKER_PATH,
    VERIFICATION_PATH,
    _assert_archive_inventory,
    _decode,
    _encode,
    _find_outer_entries,
    _is_noise_path,
    _merge_nested_zip_package,
    _normalise_vtk_path,
    _publish_staged_archive,
    _validate_archive_paths,
    _write_streamed_entry,
)
from .ifix_merger import (
    IFixMergeError,
    merge_ifix_actor_scoped_patches,
    merge_ifix_patches,
    parse_ifix_patch,
    serialize_ifix_patch,
)


class AOVMultiMergeError(ValueError):
    pass


def _is_unresolved_wwise_payload(resource_name: str) -> bool:
    """Identify Wwise banks/media that cannot be admitted by a ZIP-path union."""
    return PurePosixPath(resource_name.replace("\\", "/")).suffix.casefold() in {
        ".bnk",
        ".wem",
    }


def _entry_hash(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skin_list_text(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    try:
        return archive.read(info).decode("utf-8-sig", errors="replace")
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise AOVMultiMergeError(
            f"Không đọc được danh sách skin: {info.filename}"
        ) from exc


def _source_resource_map(
    path: str,
    profile: dict[str, Any],
    *,
    handles: list[Any],
) -> dict[str, Any]:
    try:
        outer = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise AOVMultiMergeError(
            f"File nguồn không phải ZIP hợp lệ: {Path(path).name}"
        ) from exc
    handles.append(outer)
    _validate_archive_paths(outer, Path(path).name)
    bad = outer.testzip()
    if bad:
        raise AOVMultiMergeError(
            f"ZIP nguồn lỗi CRC tại {bad}: {Path(path).name}"
        )

    resources_entries = _find_outer_entries(outer, "Resources.zip")
    skin_entries = _find_outer_entries(outer, "DanhSachSkin.txt")
    if len(resources_entries) > 1 or len(skin_entries) > 1:
        raise AOVMultiMergeError(
            f"{Path(path).name}: wrapper có nhiều Resources.zip hoặc DanhSachSkin.txt."
        )

    inner = None
    inner_stream = None
    if resources_entries:
        resources_bytes = outer.read(resources_entries[0])
        inner_stream = io.BytesIO(resources_bytes)
        try:
            inner = zipfile.ZipFile(inner_stream, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise AOVMultiMergeError(
                f"{Path(path).name}: Resources.zip bên trong không hợp lệ."
            ) from exc
        handles.extend([inner, inner_stream])
        _validate_archive_paths(inner, f"{Path(path).name}/Resources.zip")
        inner_bad = inner.testzip()
        if inner_bad:
            raise AOVMultiMergeError(
                f"{Path(path).name}: Resources.zip lỗi CRC tại {inner_bad}."
            )
        source_archive = inner
    else:
        source_archive = outer

    resources: dict[str, tuple[zipfile.ZipFile, zipfile.ZipInfo]] = {}
    for info in source_archive.infolist():
        if info.is_dir() or _is_noise_path(info.filename):
            continue
        relative = _normalise_vtk_path(info.filename)
        if not relative or relative.casefold() in {"resources.zip", "danhsachskin.txt"}:
            continue
        if relative in resources:
            raise AOVMultiMergeError(
                f"{Path(path).name}: tài nguyên chuẩn hóa bị trùng đường dẫn {relative}."
            )
        resources[relative] = (source_archive, info)

    # A wrapper can also carry loose Resources/ paths alongside Resources.zip.
    if inner is not None:
        for info in outer.infolist():
            if info.is_dir() or _is_noise_path(info.filename):
                continue
            if info.filename in {
                resources_entries[0].filename,
                skin_entries[0].filename if skin_entries else None,
            }:
                continue
            if not info.filename.replace("\\", "/").startswith("Resources/"):
                continue
            relative = _normalise_vtk_path(info.filename)
            if relative in resources:
                left = resources[relative]
                if _entry_hash(*left) != _entry_hash(outer, info):
                    raise AOVMultiMergeError(
                        f"{Path(path).name}: hai bản tài nguyên khác nhau cùng chuẩn hóa về {relative}."
                    )
            else:
                resources[relative] = (outer, info)

    skin_text = (
        _skin_list_text(outer, skin_entries[0]) if skin_entries else ""
    )
    return {
        "path": path,
        "name": Path(path).name,
        "profile": profile,
        "outer": outer,
        "outer_resources_info": resources_entries[0] if resources_entries else None,
        "outer_skin_info": skin_entries[0] if skin_entries else None,
        "inner": inner,
        "inner_stream": inner_stream,
        "resources": resources,
        "skin_text": skin_text,
        "is_wrapper": bool(resources_entries and skin_entries),
        "has_resources_zip": bool(resources_entries),
    }


def _read_item(item: tuple[zipfile.ZipFile, zipfile.ZipInfo]) -> bytes:
    archive, info = item
    try:
        return archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise AOVMultiMergeError(
            f"Không đọc được payload tài nguyên {info.filename}."
        ) from exc


def _actor_ids(source: dict[str, Any]) -> set[str]:
    profile = source.get("profile") or {}
    return {str(value) for value in profile.get("actor_action_ids", []) if str(value).isdigit()}


def _outer_skin_lines(sources: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for source in sources:
        candidates = list((source.get("skin_text") or "").replace("\r", "\n").split("\n"))
        candidates.extend((source.get("profile") or {}).get("skin_name_hints", []))
        for raw in candidates:
            value = str(raw).strip().lstrip("-*•· ").strip()
            if not value or len(value) > 160:
                continue
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                lines.append(value)
    return lines


def _merge_ifix_payloads(
    base_data: bytes,
    overlays: list[bytes],
    resource_name: str,
) -> tuple[bytes, dict[str, Any]]:
    current = base_data
    reports = []
    for index, incoming in enumerate(overlays, start=1):
        try:
            base_decoded, base_mode = _decode(current, resource_name)
            incoming_decoded, _ = _decode(incoming, resource_name)
            base_patch = parse_ifix_patch(base_decoded)
            incoming_patch = parse_ifix_patch(incoming_decoded)
            try:
                merged_patch = merge_ifix_patches(base_patch, incoming_patch)
                route = "generic_index_remap_union"
            except IFixMergeError:
                merged_patch = merge_ifix_actor_scoped_patches(
                    base_patch, incoming_patch
                )
                route = "actor_scoped_verified_union"
            serialized = serialize_ifix_patch(merged_patch)
            verify_patch = parse_ifix_patch(serialized)
            if serialize_ifix_patch(verify_patch) != serialized:
                raise IFixMergeError("IFix sau serialize không round-trip chính xác.")
            current = _encode(serialized, resource_name, base_mode)
            check, _ = _decode(current, resource_name)
            if parse_ifix_patch(check) != verify_patch:
                raise IFixMergeError("IFix sau mã hóa không giữ nguyên patch.")
            reports.append({
                "overlay_index": index,
                "route": route,
                "fix_count": len(verify_patch["fixes"]),
                "method_count": len(verify_patch["methods"]),
            })
        except (IFixMergeError, AOVMergeError) as exc:
            raise AOVMultiMergeError(
                f"{resource_name}: không thể ghép IFix nguồn {index}: {exc}"
            ) from exc
    return current, {
        "adapter": "multi_source_ifix_merge",
        "resource": resource_name,
        "overlay_reports": reports,
        "verified": True,
    }


def _merge_common_actions(
    base_data: bytes,
    overlays: list[tuple[str, bytes, set[str]]],
    resource_name: str,
) -> tuple[bytes, dict[str, Any]]:
    try:
        assessment = analyze_common_actions_overlay_set(overlays)
        conflicts = assessment.get("unsupported_path_conflict_count", 0)
        if conflicts:
            first = assessment.get("unsupported_path_conflicts", [{}])[0]
            raise CommonActionsMergeError(
                f"có {conflicts} xung đột opaque; ví dụ {first.get('path', 'không rõ')}."
            )
        parser_overlays = []
        for source_name, payload, _ in overlays:
            report = next(
                (
                    item for item in assessment.get("packages", [])
                    if item.get("source") == source_name
                ),
                None,
            )
            skin_ids = set((report or {}).get("skin_ids", []))
            if skin_ids:
                parser_overlays.append((payload, skin_ids))
            elif payload != base_data:
                raise CommonActionsMergeError(
                    f"{source_name}: CommonActions khác nhưng không có SkinOrAvatarList "
                    "trực tiếp được parser hỗ trợ."
                )
        if not parser_overlays:
            raise CommonActionsMergeError(
                "Không tìm thấy track skin có thể hợp nhất trong overlay."
            )
        merged, report = merge_common_actions_packages(base_data, parser_overlays)
        if not report.get("verified"):
            raise CommonActionsMergeError("CommonActions không qua xác minh cuối.")
        return merged, {
            "adapter": "multi_overlay_actor_gated_common_actions",
            "resource": resource_name,
            "assessment": assessment,
            "merge_report": report,
            "verified": True,
        }
    except CommonActionsMergeError as exc:
        raise AOVMultiMergeError(
            f"{resource_name}: CommonActions chưa thể ghép an toàn: {exc}"
        ) from exc


def merge_multi_actor_aov_archives(
    archive_paths: list[str],
    output_path: str,
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge multiple user MOD archives using detected Actor inventories and typed adapters.

    Exactly one complete Resources.zip + DanhSachSkin.txt wrapper is selected as
    the output container. Other wrapper and loose-resource archives are overlays.
    Conflicting resources are admitted only by a matching parser/serializer.
    """
    if len(archive_paths) < 2 or len(archive_paths) != len(profiles):
        raise AOVMultiMergeError("Cần ít nhất hai nguồn AoV và profile tương ứng.")
    if len(archive_paths) > 10:
        raise AOVMultiMergeError("Tối đa 10 gói AoV trong một lượt ghép.")

    handles: list[Any] = []
    staged_output: Path | None = None
    try:
        total_bytes = sum(Path(path).stat().st_size for path in archive_paths)
        if total_bytes > 500 * 1024 * 1024:
            raise AOVMultiMergeError("Tổng dung lượng ZIP đầu vào vượt quá 500 MB.")

        sources = [
            _source_resource_map(path, profile, handles=handles)
            for path, profile in zip(archive_paths, profiles)
        ]
        wrappers = [index for index, item in enumerate(sources) if item["is_wrapper"]]
        if not wrappers:
            raise AOVMultiMergeError(
                "Không có gói nền Resources.zip + DanhSachSkin.txt. "
                "Cần ít nhất một wrapper làm container đầu ra."
            )

        def completeness(index: int) -> tuple[int, int, int, int]:
            resource_names = set(sources[index]["resources"])
            required = {
                PACKER_PATH,
                VERIFICATION_PATH,
                COMMON_ACTIONS_PATH,
                "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes",
            }
            return (
                len(required & resource_names),
                len(resource_names),
                int((sources[index]["profile"] or {}).get("uncompressed_bytes", 0)),
                -index,
            )

        base_index = max(wrappers, key=completeness)
        base_source = sources[base_index]
        if base_source["inner"] is None or base_source["outer_resources_info"] is None:
            raise AOVMultiMergeError("Nguồn nền được chọn không có Resources.zip hợp lệ.")

        # All resources must refer to one client version family. The exact
        # VTK 1.64.1.11 -> 1.64.1.12 adapter remains in its existing dedicated route.
        base_version_item = base_source["resources"].get("version.txt")
        if base_version_item is None:
            raise AOVMultiMergeError(
                f"{base_source['name']}: thiếu version.txt; không thể xác nhận build nền."
            )
        base_version = _read_item(base_version_item).strip()
        if not base_version:
            raise AOVMultiMergeError(
                f"{base_source['name']}: version.txt rỗng; không thể xác nhận build nền."
            )
        for source in sources:
            version_item = source["resources"].get("version.txt")
            if version_item is None:
                raise AOVMultiMergeError(
                    f"{source['name']}: thiếu version.txt; dừng ghép để tránh trộn khác build."
                )
            candidate = _read_item(version_item).strip()
            if not candidate or candidate != base_version:
                raise AOVMultiMergeError(
                    f"{source['name']}: version.txt khác nguồn nền "
                    f"({candidate!r} != {base_version!r})."
                )

        ordered_sources = [base_source] + [
            source for index, source in enumerate(sources) if index != base_index
        ]
        all_paths = sorted({
            path for source in ordered_sources for path in source["resources"]
        })
        replacements: dict[str, bytes] = {}
        added: dict[str, bytes] = {}
        resource_reports: dict[str, dict[str, Any]] = {}
        included_paths_by_source: dict[str, set[str]] = {
            source["name"]: set() for source in ordered_sources[1:]
        }
        asset_paths_by_source: dict[str, set[str]] = {
            source["name"]: set() for source in ordered_sources[1:]
        }
        metadata_paths = {PACKER_PATH, VERIFICATION_PATH}

        for relative in all_paths:
            providers = [
                source for source in ordered_sources
                if relative in source["resources"]
            ]
            if not providers:
                continue
            base_provider = providers[0]
            base_payload = _read_item(base_provider["resources"][relative])
            if len(providers) == 1:
                if base_provider is not base_source:
                    if _is_unresolved_wwise_payload(relative):
                        raise AOVMultiMergeError(
                            f"{relative}: Wwise BNK/WEM chỉ đang được kiểm tra cấu trúc; "
                            "chưa có bộ remap Bank/Media/HIRC và tham chiếu Event/Action. "
                            "Không thể tự thêm soundbank mới bằng ZIP union."
                        )
                    added[relative] = base_payload
                    included_paths_by_source[base_provider["name"]].add(relative)
                    if relative.startswith("assetbundle/") and relative not in metadata_paths:
                        asset_paths_by_source[base_provider["name"]].add(relative)
                continue

            payloads = [
                (source, _read_item(source["resources"][relative]))
                for source in providers[1:]
            ]
            distinct = []
            seen_hashes = {hashlib.sha256(base_payload).digest()}
            for source, payload in payloads:
                digest = hashlib.sha256(payload).digest()
                if digest in seen_hashes:
                    continue
                seen_hashes.add(digest)
                distinct.append((source, payload))
            if not distinct:
                continue

            if relative in metadata_paths:
                # Metadata is merged after the final physical-resource inventory
                # and per-source preferred checksum paths have been determined.
                continue

            if relative == COMMON_ACTIONS_PATH:
                overlay_items = [
                    (source["name"], payload, _actor_ids(source))
                    for source, payload in distinct
                ]
                merged, report = _merge_common_actions(
                    base_payload, overlay_items, relative
                )
                replacements[relative] = merged
                resource_reports[relative] = report
                for source, _ in distinct:
                    included_paths_by_source[source["name"]].add(relative)
                continue

            if relative in _ACTOR_OWNED_MSES_PATHS:
                base_actor_ids = _actor_ids(base_provider)
                overlay_chain = []
                overlay_sources = []
                for source, payload in distinct:
                    actor_ids = _actor_ids(source)
                    if not actor_ids:
                        raise AOVMultiMergeError(
                            f"{relative}: {source['name']} không có Actor_Action inventory."
                        )
                    overlay_chain.append((payload, actor_ids))
                    overlay_sources.append(source)
                if not base_actor_ids:
                    raise AOVMultiMergeError(
                        f"{relative}: nguồn nền {base_provider['name']} không có Actor_Action inventory."
                    )
                try:
                    merged, report = merge_aov_mses_resource_chain(
                        base_payload,
                        relative,
                        base_actor_ids,
                        overlay_chain,
                    )
                except AOVResourceMergeError as exc:
                    raise AOVMultiMergeError(str(exc)) from exc
                replacements[relative] = merged
                resource_reports[relative] = report
                for source in overlay_sources:
                    included_paths_by_source[source["name"]].add(relative)
                continue

            if relative in _SUPPORTED_MSES_TABLES:
                current = base_payload
                reports = []
                for index, (source, payload) in enumerate(distinct, start=1):
                    try:
                        current, report = merge_aov_mses_resource(
                            current, payload, relative
                        )
                    except AOVResourceMergeError as exc:
                        raise AOVMultiMergeError(
                            f"{relative}: không ghép được overlay {source['name']}: {exc}"
                        ) from exc
                    reports.append({"overlay": source["name"], "report": report})
                    included_paths_by_source[source["name"]].add(relative)
                replacements[relative] = current
                resource_reports[relative] = {
                    "adapter": "known_table_specific_mses_multi_overlay",
                    "overlay_reports": reports,
                    "verified": all(item["report"].get("verified") for item in reports),
                }
                continue

            lower = relative.casefold()
            if lower.endswith("heroskin_1.bytes") or lower.endswith("heroskin_2.bytes"):
                merged, report = _merge_ifix_payloads(
                    base_payload, [payload for _, payload in distinct], relative
                )
                replacements[relative] = merged
                resource_reports[relative] = report
                for source, _ in distinct:
                    included_paths_by_source[source["name"]].add(relative)
                continue

            if relative.casefold().endswith(".pkg.bytes"):
                current = base_payload
                reports = []
                for index, (source, payload) in enumerate(distinct, start=1):
                    try:
                        current, report = _merge_nested_zip_package(
                            current, payload, relative
                        )
                    except AOVMergeError as exc:
                        raise AOVMultiMergeError(
                            f"{relative}: package lồng xung đột ở {source['name']}: {exc}"
                        ) from exc
                    reports.append({"overlay": source["name"], "report": report})
                    included_paths_by_source[source["name"]].add(relative)
                replacements[relative] = current
                resource_reports[relative] = {
                    "adapter": "nested_package_multi_overlay_union",
                    "overlay_reports": reports,
                    "verified": all(item["report"].get("verified") for item in reports),
                }
                continue

            if relative.startswith("assetbundle/"):
                raise AOVMultiMergeError(
                    f"{relative}: hai nguồn có AssetBundle cùng đường dẫn nhưng payload khác. "
                    "Chưa có quy tắc hợp nhất object/dependency cho bundle này."
                )

            raise AOVMultiMergeError(
                f"{relative}: hai nguồn có nội dung khác nhau nhưng chưa có parser "
                "đã xác minh. Không ghi đè."
            )

        # Merge resource catalog metadata only after the physical resources have
        # been classified. The catalog remaps dependency indices by PathInIFS.
        if PACKER_PATH not in base_source["resources"]:
            raise AOVMultiMergeError("Nguồn nền thiếu ResourcePackerInfoSetAll AssetBundle.")
        if VERIFICATION_PATH not in base_source["resources"]:
            raise AOVMultiMergeError("Nguồn nền thiếu ResourceVerificationInfoSetAll AssetBundle.")

        current_packer = _read_item(base_source["resources"][PACKER_PATH])
        current_verification = _read_item(base_source["resources"][VERIFICATION_PATH])
        catalog_reports = []
        for source in ordered_sources[1:]:
            source_assets = asset_paths_by_source[source["name"]]
            source_included = included_paths_by_source[source["name"]]
            if not source_assets and not source_included:
                continue
            if VERIFICATION_PATH not in source["resources"]:
                raise AOVMultiMergeError(
                    f"{source['name']}: có tài nguyên mới/đã ghép nhưng thiếu Verification metadata."
                )
            if source_assets and PACKER_PATH not in source["resources"]:
                raise AOVMultiMergeError(
                    f"{source['name']}: có AssetBundle mới nhưng thiếu Packer metadata."
                )
            overlay_verification = _read_item(source["resources"][VERIFICATION_PATH])
            try:
                if source_assets:
                    overlay_packer = _read_item(source["resources"][PACKER_PATH])
                    current_packer, packer_report = merge_aov_metadata_assetbundle(
                        current_packer,
                        overlay_packer,
                        kind="resource_packer",
                        prefer_overlay_paths=source_assets,
                    )
                else:
                    packer_report = None
                current_verification, verification_report = merge_aov_metadata_assetbundle(
                    current_verification,
                    overlay_verification,
                    kind="resource_verification",
                    prefer_overlay_paths=source_included,
                )
            except AOVCatalogMergeError as exc:
                raise AOVMultiMergeError(
                    f"{source['name']}: không cập nhật được AssetBundle catalog: {exc}"
                ) from exc
            replacements[PACKER_PATH] = current_packer
            replacements[VERIFICATION_PATH] = current_verification
            catalog_reports.append({
                "source": source["name"],
                "packer": packer_report,
                "verification": verification_report,
            })

        # New AssetBundles cannot be shipped without a matching row in Packer.
        if any(asset_paths_by_source.values()) and not catalog_reports:
            raise AOVMultiMergeError(
                "Có AssetBundle mới nhưng không cập nhật được catalog Packer/Verification."
            )

        inner_base = base_source["inner"]
        assert inner_base is not None
        base_prefix = ""
        for info in inner_base.infolist():
            if info.is_dir():
                continue
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("Resources/"):
                parts = normalized.split("/", 2)
                if len(parts) == 3:
                    base_prefix = f"Resources/{parts[1]}/"
                    break
        temp_dir = Path(tempfile.mkdtemp(prefix="aki-aov-multi-"))
        try:
            inner_output_path = temp_dir / "Resources-merged.zip"
            original_names = {
                info.filename for info in inner_base.infolist()
            }
            normalized_base_names = {
                _normalise_vtk_path(name)
                for name in original_names
                if not name.endswith("/")
            }
            appended_names = {
                base_prefix + relative
                for relative in added
                if base_prefix + relative not in original_names
            }
            with zipfile.ZipFile(
                inner_output_path, "w", compression=zipfile.ZIP_DEFLATED,
                allowZip64=True
            ) as output_inner:
                for info in inner_base.infolist():
                    if info.is_dir():
                        output_inner.writestr(info, b"")
                        continue
                    relative = _normalise_vtk_path(info.filename)
                    if relative in replacements:
                        target = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                        target.compress_type = zipfile.ZIP_DEFLATED
                        target.external_attr = info.external_attr
                        target.create_system = info.create_system
                        output_inner.writestr(target, replacements[relative])
                    else:
                        with inner_base.open(info, "r") as src, output_inner.open(
                            info, "w", force_zip64=True
                        ) as dst:
                            import shutil
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
                for relative, payload in sorted(added.items()):
                    name = base_prefix + relative
                    if name in original_names:
                        raise AOVMultiMergeError(
                            f"Đường dẫn mới đã tồn tại trong ZIP nền: {name}."
                        )
                    output_inner.writestr(name, payload)

            with zipfile.ZipFile(inner_output_path, "r") as verify_inner:
                bad = verify_inner.testzip()
                if bad:
                    raise AOVMultiMergeError(
                        f"Resources.zip sau ghép lỗi CRC tại {bad}."
                    )
                expected_names = original_names | appended_names
                actual_names = {info.filename for info in verify_inner.infolist()}
                if actual_names != expected_names:
                    raise AOVMultiMergeError(
                        "Resources.zip đầu ra không giữ đủ inventory: "
                        f"thiếu={sorted(expected_names - actual_names)[:10]}, "
                        f"thừa={sorted(actual_names - expected_names)[:10]}."
                    )

            output_path_obj = Path(output_path)
            output_path_obj.parent.mkdir(parents=True, exist_ok=True)
            fd, staged_name = tempfile.mkstemp(
                prefix=f".{output_path_obj.name}.", suffix=".tmp",
                dir=str(output_path_obj.parent),
            )
            os.close(fd)
            staged_output = Path(staged_name)
            skin_lines = _outer_skin_lines(ordered_sources)
            with zipfile.ZipFile(
                staged_output, "w", compression=zipfile.ZIP_DEFLATED,
                allowZip64=True
            ) as output_outer:
                base_outer = base_source["outer"]
                resources_info = base_source["outer_resources_info"]
                skin_info = base_source["outer_skin_info"]
                for info in base_outer.infolist():
                    if info.is_dir():
                        output_outer.writestr(info, b"")
                    elif info.filename == resources_info.filename:
                        with open(inner_output_path, "rb") as src, output_outer.open(
                            info, "w", force_zip64=True
                        ) as dst:
                            import shutil
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
                    elif info.filename == skin_info.filename:
                        target = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                        target.compress_type = zipfile.ZIP_DEFLATED
                        target.external_attr = info.external_attr
                        target.create_system = info.create_system
                        output_outer.writestr(
                            target,
                            (("\n".join(skin_lines).rstrip() + "\n") if skin_lines else "").encode("utf-8"),
                        )
                    else:
                        with base_outer.open(info, "r") as src, output_outer.open(
                            info, "w", force_zip64=True
                        ) as dst:
                            import shutil
                            shutil.copyfileobj(src, dst, length=1024 * 1024)

            _publish_staged_archive(
                staged_output,
                output_path_obj,
                {info.filename for info in base_source["outer"].infolist()},
            )
            with zipfile.ZipFile(output_path_obj, "r") as result:
                bad = result.testzip()
                if bad:
                    raise AOVMultiMergeError(
                        f"ZIP ngoài đầu ra lỗi CRC tại {bad}."
                    )
            return {
                "output": str(output_path_obj),
                "adapter": "aov_multi_actor_auto_detect",
                "base_source": base_source["name"],
                "source_count": len(ordered_sources),
                "actor_action_ids": {
                    source["name"]: sorted(_actor_ids(source))
                    for source in ordered_sources
                },
                "added_resources": len(added),
                "replaced_resources": len(replacements),
                "resource_reports": resource_reports,
                "catalog_reports": catalog_reports,
                "skin_list_entries": len(skin_lines),
                "structurally_validated": True,
                "game_compatibility_tested": False,
                "verified": True,
            }
        finally:
            if staged_output is not None:
                staged_output.unlink(missing_ok=True)
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
    except (AOVMergeError, AOVCatalogMergeError) as exc:
        raise AOVMultiMergeError(str(exc)) from exc
    finally:
        for handle in reversed(handles):
            try:
                handle.close()
            except Exception:
                pass
