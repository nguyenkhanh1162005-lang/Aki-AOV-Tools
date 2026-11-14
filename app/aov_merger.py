from __future__ import annotations

import copy
import hashlib
import io
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .aov_bytes import AOVBytesError, decode_aov_resource, encode_aov_resource
from .aov_mses import MSESError, parse_mses
from .aov_merge import AOVResourceMergeError, merge_aov_mses_resource
from .aov_common_actions import (
    CommonActionsMergeError,
    analyze_common_actions_overlay_set,
    merge_common_actions_pkg,
)
from .ifix_merger import (
    IFixMergeError,
    merge_ifix_patches,
    merge_ifix_actor_scoped_patches,
    parse_ifix_patch,
    serialize_ifix_patch,
)

BASE_VERSION = "1.64.1.12"
VTK_VERSION = "1.64.1.11"
BASE_PREFIX = "Resources/1.64.1/"
MAX_AOV_INPUTS = 10
MAX_ARCHIVE_ENTRIES = 20_000
MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_SINGLE_ENTRY = 512 * 1024 * 1024
MAX_TOTAL_INPUT_BYTES = 500 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 1024 * 1024 * 1024

COMMON_ACTIONS_PATH = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
HUANHUA_TABLE_PATHS = (
    "Databin/Client/Huanhua/ResBillboardCfg.bytes",
    "Databin/Client/Huanhua/ResBillboardSkinCfg.bytes",
    "Databin/Client/Huanhua/ResKillBillboardCfg.bytes",
)
HERO_SKIN_BASE_PATH = "Thanos/HeroSkin_1.bytes"
HERO_SKIN_VTK_PATH = "Thanos/HeroSkin_2.bytes"
RES_SKIN_PATH = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
BATTLE_BANK_PATH = "Databin/Client/Sound/BattleBank.bytes"
LOBBY_BANK_PATH = "Databin/Client/Sound/LobbyBank.bytes"
SKILL_COMBINE_PATH = "Databin/Client/Skill/skillcombine.bytes"
PACKER_PATH = "assetbundle/resourcepackerinfosetall.assetbundle"
VERIFICATION_PATH = "assetbundle/resourceverificationinfosetall.assetbundle"

EXPECTED_ASSETBUNDLES = {
    "assetbundle/battle/hero/595_lixin_battle_base.assetbundle",
    "assetbundle/battle/hero/595_lixin_battle_base_raw_h.assetbundle",
    "assetbundle/battle/hero/595_lixin_battle_base_raw_mat.assetbundle",
    "assetbundle/commercializenotify/battle/commercializenotify_blue_595.assetbundle",
    "assetbundle/commercializenotify/battle/commercializenotify_red_595.assetbundle",
    "assetbundle/icon/hero/595_305950_default59500_bust.assetbundle",
    "assetbundle/uisystem/battle/battleotherui.assetbundle",
    "assetbundle/uisystem/battle/battlecommon_raw.assetbundle",
    "assetbundle/uisystem/atlas/primary/allshared.assetbundle",
    "assetbundle/show/hero/595_lixin_show_base.assetbundle",
}

class AOVMergeError(ValueError):
    """Raised when the supported 1.64.1.11 VTK pack cannot be safely merged."""


def _load_unitypy_aov():
    vendor = str(Path(__file__).resolve().parent / "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        import UnityPy_AOV
    except Exception as exc:  # pragma: no cover - depends on deployment dependencies
        raise AOVMergeError(
            "Máy chủ thiếu bộ đọc AssetBundle AoV. Kiểm tra UnityPy_AOV và gói sm4."
        ) from exc
    return UnityPy_AOV


def _find_outer_entries(zf: zipfile.ZipFile, expected_basename: str) -> list[zipfile.ZipInfo]:
    """Find one wrapper member by case-insensitive basename, independent of folder prefix."""
    expected = expected_basename.casefold()
    return [
        info for info in zf.infolist()
        if not info.is_dir()
        and PurePosixPath(info.filename.replace("\\", "/")).name.casefold() == expected
    ]


def _validate_archive_paths(zf: zipfile.ZipFile, label: str) -> None:
    entries = zf.infolist()
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise AOVMergeError(f"ZIP {label} vượt quá {MAX_ARCHIVE_ENTRIES} mục.")
    total_uncompressed = sum(info.file_size for info in entries)
    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
        raise AOVMergeError(f"ZIP {label} vượt quá giới hạn giải nén 1 GiB.")
    seen_paths: set[str] = set()
    for info in entries:
        if info.file_size > MAX_SINGLE_ENTRY:
            raise AOVMergeError(
                f"ZIP {label} có mục vượt quá 512 MiB: {info.filename}"
            )
        normalized = info.filename.replace("\\", "/")
        candidate = normalized[:-1] if info.is_dir() and normalized.endswith("/") else normalized
        path = PurePosixPath(candidate)
        raw_parts = candidate.split("/")
        mode = (info.external_attr >> 16) & 0xFFFF
        if (
            not candidate
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_parts)
            or "\x00" in normalized
            or (path.parts and path.parts[0].endswith(":"))
            or stat.S_ISLNK(mode)
        ):
            raise AOVMergeError(
                f"ZIP {label} chứa đường dẫn hoặc mục không an toàn/không chuẩn hóa: {info.filename}"
            )
        canonical_path = path.as_posix()
        if canonical_path in seen_paths:
            raise AOVMergeError(
                f"ZIP {label} có đường dẫn tệp bị lặp (trùng lặp) hoặc không phân biệt được: {canonical_path}"
            )
        seen_paths.add(canonical_path)


def _normalise_vtk_path(name: str) -> str:
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    raw_parts = name.split("/")
    if (
        not name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or "\x00" in name
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise AOVMergeError(f"Đường dẫn tài nguyên VTK không an toàn: {name}")
    name = name.lstrip("/")
    if name.startswith("Resources/1.64.1/"):
        name = name[len("Resources/1.64.1/"):]
    elif name.startswith("Resources/"):
        parts = name.split("/", 2)
        if len(parts) == 3:
            name = parts[2]
    if name.endswith(".assetbundle") and not name.startswith("assetbundle/"):
        name = "assetbundle/" + name
    return name


def _zip_entry_sha256(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    try:
        with archive.open(info, "r") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, RuntimeError, zipfile.BadZipFile, EOFError) as exc:
        raise AOVMergeError(
            f"Không thể so sánh nội dung tài nguyên ZIP: {info.filename}"
        ) from exc
    return digest.hexdigest()


def _is_noise_path(name: str) -> bool:
    leaf = name.rsplit("/", 1)[-1]
    return (
        name.startswith("__MACOSX/")
        or leaf == ".DS_Store"
        or leaf.startswith("._")
        or "/__MACOSX/" in name
    )


def _decode(data: bytes, resource_name: str) -> tuple[bytes, str]:
    try:
        return decode_aov_resource(data, resource_name)
    except AOVBytesError as exc:
        raise AOVMergeError(f"Không giải mã được tài nguyên {resource_name}: {exc}") from exc


def _encode(data: bytes, resource_name: str, mode: str) -> bytes:
    try:
        return encode_aov_resource(data, resource_name, mode)
    except AOVBytesError as exc:
        raise AOVMergeError(f"Không mã hóa lại được tài nguyên {resource_name}: {exc}") from exc


def _decode_nested_package(data: bytes, resource_name: str) -> tuple[bytes, str]:
    """Accept either a raw ZIP/PKG payload or a supported AoV-wrapped payload."""
    probe = io.BytesIO(data)
    if zipfile.is_zipfile(probe):
        return data, "raw_zip"
    return _decode(data, resource_name)


def _encode_nested_package(data: bytes, resource_name: str, mode: str) -> bytes:
    """Preserve a raw ZIP payload or re-apply the original AoV wrapper."""
    if mode == "raw_zip":
        return data
    return _encode(data, resource_name, mode)


def _merge_nested_zip_package(
    base_data: bytes,
    overlay_data: bytes,
    resource_name: str,
) -> tuple[bytes, dict[str, Any]]:
    """Union a nested ZIP package without dropping conflicting inner resources.

    Entries unique to the overlay are retained. Duplicate paths are discarded
    only when SHA-256 matches; different payloads at the same inner path fail
    closed because no semantic parser is available for that member.
    """
    base_raw, base_mode = _decode_nested_package(base_data, resource_name)
    overlay_raw, overlay_mode = _decode_nested_package(overlay_data, resource_name)

    try:
        base_buffer = io.BytesIO(base_raw)
        overlay_buffer = io.BytesIO(overlay_raw)
        base_zip = zipfile.ZipFile(base_buffer, "r")
        overlay_zip = zipfile.ZipFile(overlay_buffer, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise AOVMergeError(
            f"{resource_name}: payload sau giải mã không phải ZIP hợp lệ."
        ) from exc

    try:
        _validate_archive_paths(base_zip, f"{resource_name} nền")
        _validate_archive_paths(overlay_zip, f"{resource_name} overlay")
        bad_base = base_zip.testzip()
        bad_overlay = overlay_zip.testzip()
        if bad_base or bad_overlay:
            raise AOVMergeError(
                f"{resource_name}: CRC package lỗi "
                f"(nền={bad_base or 'OK'}, overlay={bad_overlay or 'OK'})."
            )

        base_entries = {
            info.filename: info for info in base_zip.infolist() if not info.is_dir()
        }
        overlay_entries = {
            info.filename: info for info in overlay_zip.infolist() if not info.is_dir()
        }
        base_digests = {
            name: _zip_entry_sha256(base_zip, info)
            for name, info in base_entries.items()
        }
        added_names: list[str] = []
        identical_names: list[str] = []
        for name, info in overlay_entries.items():
            if name not in base_entries:
                added_names.append(name)
                continue
            digest = _zip_entry_sha256(overlay_zip, info)
            if digest == base_digests[name]:
                identical_names.append(name)
                continue
            raise AOVMergeError(
                f"{resource_name}: xung đột package bên trong tại '{name}'. "
                "Cùng đường dẫn nhưng nội dung khác; chưa có parser chuyên biệt "
                "nên không thể chọn bản ghi an toàn."
            )

        output_buffer = io.BytesIO()
        try:
            with zipfile.ZipFile(
                output_buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as merged_zip:
                for source_zip, entries in (
                    (base_zip, base_entries),
                    (overlay_zip, {name: overlay_entries[name] for name in added_names}),
                ):
                    for name, source_info in entries.items():
                        target = zipfile.ZipInfo(name, date_time=source_info.date_time)
                        target.compress_type = zipfile.ZIP_DEFLATED
                        target.external_attr = source_info.external_attr
                        target.create_system = source_info.create_system
                        with source_zip.open(source_info, "r") as source, merged_zip.open(
                            target, "w", force_zip64=True
                        ) as destination:
                            _copy_stream(source, destination)
        except (OSError, RuntimeError, zipfile.BadZipFile, EOFError) as exc:
            raise AOVMergeError(
                f"{resource_name}: không đóng gói lại được ZIP lồng."
            ) from exc

        merged_raw = output_buffer.getvalue()
        # Serialize using the base package's container mode. The overlay
        # may be a raw ZIP even when the base package is AoV-wrapped, or vice versa.
        encoded = _encode_nested_package(merged_raw, resource_name, base_mode)
        check_raw, check_mode = _decode_nested_package(encoded, resource_name)
        if check_mode != base_mode:
            raise AOVMergeError(
                f"{resource_name}: kiểu bọc thay đổi sau khi mã hóa lại."
            )
        with zipfile.ZipFile(io.BytesIO(check_raw), "r") as check_zip:
            _validate_archive_paths(check_zip, f"{resource_name} sau ghép")
            bad = check_zip.testzip()
            actual = {
                info.filename: info
                for info in check_zip.infolist()
                if not info.is_dir()
            }
            expected_names = set(base_entries) | set(added_names)
            if bad or set(actual) != expected_names:
                raise AOVMergeError(
                    f"{resource_name}: ZIP lồng sau ghép không giữ đủ inventory "
                    f"(CRC={bad or 'OK'})."
                )
            for name, info in actual.items():
                expected_source = (
                    base_zip if name in base_entries else overlay_zip
                )
                expected_info = (
                    base_entries[name] if name in base_entries else overlay_entries[name]
                )
                expected_digest = _zip_entry_sha256(expected_source, expected_info)
                if _zip_entry_sha256(check_zip, info) != expected_digest:
                    raise AOVMergeError(
                        f"{resource_name}: SHA-256 package sau ghép không khớp: {name}."
                    )

        return encoded, {
            "verified": True,
            "base_entries": len(base_entries),
            "overlay_entries": len(overlay_entries),
            "added_entries": len(added_names),
            "identical_duplicates": len(identical_names),
            "added_paths": sorted(added_names),
            "identical_paths": sorted(identical_names),
            "conflicts": 0,
            "encoding": base_mode,
            "overlay_encoding": overlay_mode,
        }
    finally:
        base_zip.close()
        overlay_zip.close()
        base_buffer.close()
        overlay_buffer.close()


def _merge_ifix(base_data: bytes, vtk_data: bytes) -> tuple[bytes, int, int]:
    base_raw, base_mode = _decode(base_data, "HeroSkin_1")
    vtk_raw, _ = _decode(vtk_data, "HeroSkin_2")
    try:
        base_patch = parse_ifix_patch(base_raw)
        vtk_patch = parse_ifix_patch(vtk_raw)
        if serialize_ifix_patch(base_patch) != base_raw or serialize_ifix_patch(vtk_patch) != vtk_raw:
            raise AOVMergeError("IFix round-trip không khớp dữ liệu gốc.")
        merged = merge_ifix_patches(base_patch, vtk_patch)
        raw = serialize_ifix_patch(merged)
    except IFixMergeError as exc:
        raise AOVMergeError(f"Không ghép được IFix HeroSkin: {exc}") from exc
    expected_fixes = len(base_patch["fixes"]) + len(vtk_patch["fixes"])
    expected_methods = len(base_patch["methods"]) + len(vtk_patch["methods"])
    if (
        len(merged["fixes"]) != expected_fixes
        or len(merged["methods"]) != expected_methods
    ):
        raise AOVMergeError(
            "IFix sau ghép bị mất bản ghi: "
            f"FixInfo {len(merged['fixes'])}/{expected_fixes}, "
            f"methods {len(merged['methods'])}/{expected_methods}."
        )
    encoded = _encode(raw, "HeroSkin_1", base_mode)
    check, _ = _decode(encoded, "HeroSkin_1")
    parsed = parse_ifix_patch(check)
    if serialize_ifix_patch(parsed) != raw:
        raise AOVMergeError("IFix sau mã hóa không round-trip byte-for-byte.")
    if (
        len(parsed["fixes"]) != expected_fixes
        or len(parsed["methods"]) != expected_methods
    ):
        raise AOVMergeError(
            "IFix sau mã hóa bị mất bản ghi: "
            f"FixInfo {len(parsed['fixes'])}/{expected_fixes}, "
            f"methods {len(parsed['methods'])}/{expected_methods}."
        )
    return encoded, len(parsed["fixes"]), len(parsed["methods"])


def _find_mono_behaviour(env: Any, name: str):
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            tree = obj.read_typetree()
            if tree.get("m_Name") == name:
                return obj, tree
    raise AOVMergeError(f"AssetBundle metadata thiếu object {name}.")


def _merge_packer_metadata(base_data: bytes, vtk_data: bytes,
                           asset_paths: set[str]) -> tuple[bytes, int, int, int]:
    UnityPy_AOV = _load_unitypy_aov()
    try:
        base_env = UnityPy_AOV.load(base_data)
        vtk_env = UnityPy_AOV.load(vtk_data)
        base_obj, base_tree = _find_mono_behaviour(base_env, "ResourcePackerInfoSetXML")
        _, vtk_tree = _find_mono_behaviour(vtk_env, "ResourcePackerInfoSetXML")
    except AOVMergeError:
        raise
    except Exception as exc:
        raise AOVMergeError(f"Không đọc được ResourcePackerInfoSetXML: {type(exc).__name__}: {exc}") from exc

    if base_tree.get("Version") != BASE_VERSION or vtk_tree.get("Version") != VTK_VERSION:
        raise AOVMergeError("Phiên bản ResourcePackerInfoSetXML không đúng cặp 1.64.1.12/1.64.1.11.")

    base_entries = base_tree["AllResourcePackInfo"]
    base_count = len(base_entries)
    vtk_entries = vtk_tree["AllResourcePackInfo"]
    base_index = {entry["PathInIFS"]: i for i, entry in enumerate(base_entries)}
    vtk_index = {entry["PathInIFS"]: entry for entry in vtk_entries}
    missing = asset_paths - set(vtk_index)
    if missing:
        raise AOVMergeError("Packer metadata thiếu assetbundle: " + ", ".join(sorted(missing)))

    replaced = 0
    new_paths: list[str] = []
    for path in sorted(asset_paths):
        if path in base_index:
            base_entries[base_index[path]] = copy.deepcopy(vtk_index[path])
            replaced += 1
        else:
            new_paths.append(path)

    new_set = set(new_paths)
    for entry in vtk_entries:
        if entry["PathInIFS"] in new_set:
            base_entries.append(copy.deepcopy(entry))

    base_map = base_tree["AllParentIdexToChild"]
    base_map_count = len(base_map)
    vtk_map = vtk_tree["AllParentIdexToChild"]
    if base_map != vtk_map[:base_map_count]:
        raise AOVMergeError("Bảng parent-child của AssetBundle không giữ nguyên chỉ số nền.")
    base_map.extend(copy.deepcopy(vtk_map[base_map_count:]))
    if len(new_paths) != 3 or len(vtk_map) - base_map_count != 7 or replaced != 7:
        raise AOVMergeError("Số lượng thay đổi PackerInfo không đúng bộ Vấn Tâm Kiếm.")

    try:
        base_obj.save_typetree(base_tree)
        bundle = next(iter(base_env.files.values()))
        merged = bundle.save("original")
        check_env = UnityPy_AOV.load(merged)
        _, check_tree = _find_mono_behaviour(check_env, "ResourcePackerInfoSetXML")
    except Exception as exc:
        raise AOVMergeError(f"Không ghi lại được ResourcePackerInfoSetXML: {type(exc).__name__}: {exc}") from exc
    if len(check_tree["AllResourcePackInfo"]) != base_count + 3:
        raise AOVMergeError("PackerInfo sau ghép có số entry không đúng.")
    if len(check_tree["AllParentIdexToChild"]) != base_map_count + 7:
        raise AOVMergeError("PackerInfo sau ghép có số parent-child link không đúng.")
    return merged, replaced, len(new_paths), len(check_tree["AllResourcePackInfo"])


def _merge_verification_metadata(base_data: bytes, vtk_data: bytes,
                                 included_paths: set[str]) -> tuple[bytes, list[str], list[str]]:
    UnityPy_AOV = _load_unitypy_aov()
    try:
        base_env = UnityPy_AOV.load(base_data)
        vtk_env = UnityPy_AOV.load(vtk_data)
        base_obj, base_tree = _find_mono_behaviour(base_env, "ResourceVerificationInfoSetXML")
        _, vtk_tree = _find_mono_behaviour(vtk_env, "ResourceVerificationInfoSetXML")
    except AOVMergeError:
        raise
    except Exception as exc:
        raise AOVMergeError(f"Không đọc được ResourceVerificationInfoSetXML: {type(exc).__name__}: {exc}") from exc

    if base_tree.get("Version") != BASE_VERSION or vtk_tree.get("Version") != VTK_VERSION:
        raise AOVMergeError("Phiên bản ResourceVerificationInfoSetXML không đúng cặp 1.64.1.12/1.64.1.11.")

    updated_paths: set[str] = set()
    retained_version_drift: set[str] = set()
    for list_name in ("AllZipVerificationInfo", "AllDatabinVerificationInfo"):
        base_records = base_tree[list_name]
        vtk_records = vtk_tree[list_name]
        base_index = {record["PathInIFS"]: i for i, record in enumerate(base_records)}
        vtk_index = {record["PathInIFS"]: record for record in vtk_records}
        if set(base_index) != set(vtk_index):
            raise AOVMergeError(f"Danh sách {list_name} giữa hai phiên bản có đường dẫn khác nhau.")
        for path, index in base_index.items():
            if base_records[index]["CrcXor"] == vtk_index[path]["CrcXor"]:
                continue
            if path in included_paths:
                base_records[index] = copy.deepcopy(vtk_index[path])
                updated_paths.add(path)
            else:
                # Keep 1.64.1.12 values for unrelated files not shipped in the VTK overlay.
                retained_version_drift.add(path)

    try:
        base_obj.save_typetree(base_tree)
        bundle = next(iter(base_env.files.values()))
        merged = bundle.save("original")
        check_env = UnityPy_AOV.load(merged)
        _, check_tree = _find_mono_behaviour(check_env, "ResourceVerificationInfoSetXML")
    except Exception as exc:
        raise AOVMergeError(f"Không ghi lại được ResourceVerificationInfoSetXML: {type(exc).__name__}: {exc}") from exc
    if check_tree.get("Version") != BASE_VERSION:
        raise AOVMergeError("ResourceVerificationInfoSetXML bị đổi phiên bản nền ngoài ý muốn.")
    for list_name in ("AllZipVerificationInfo", "AllDatabinVerificationInfo"):
        if len(check_tree[list_name]) != len(base_tree[list_name]):
            raise AOVMergeError(f"{list_name} bị thay đổi số lượng ngoài ý muốn.")
    return merged, sorted(updated_paths), sorted(retained_version_drift)


def _merge_huanhua_billboard_tables(
    base_resources: dict[str, bytes],
    overlay_resources: dict[str, bytes],
) -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    """Merge the three Actor 595 billboard MSES tables by the pinned key allowlist.

    The table-specific MSES adapter admits only source-verified key 0x253.
    These resources must already exist in the base wrapper; adding a standalone
    table without its base schema/dependency context is not considered safe.
    """
    unknown_paths = set(overlay_resources) - set(HUANHUA_TABLE_PATHS)
    if unknown_paths:
        raise AOVMergeError(
            "VTK chứa bảng Huanhua chưa có adapter: "
            + ", ".join(sorted(unknown_paths))
        )

    replacements: dict[str, bytes] = {}
    reports: dict[str, dict[str, Any]] = {}
    for relative, overlay_data in sorted(overlay_resources.items()):
        if relative not in base_resources:
            raise AOVMergeError(
                f"{relative}: VTK có bảng billboard Actor 595 nhưng ZIP nền thiếu bảng; "
                "không thể thêm MSES độc lập khi chưa có schema nền."
            )
        try:
            merged, report = merge_aov_mses_resource(
                base_resources[relative],
                overlay_data,
                relative,
            )
        except AOVResourceMergeError as exc:
            raise AOVMergeError(str(exc)) from exc
        if not report.get("verified"):
            raise AOVMergeError(
                f"{relative}: bộ kiểm tra MSES không xác nhận được bảng billboard sau ghép."
            )
        replacements[relative] = merged
        reports[relative] = report
    return replacements, reports


def _assert_archive_inventory(
    archive: zipfile.ZipFile,
    expected_names: set[str],
    label: str,
) -> None:
    """Require an output ZIP to retain the exact expected path inventory."""
    try:
        _validate_archive_paths(archive, label)
        actual_names = [info.filename for info in archive.infolist()]
        if set(actual_names) != expected_names or len(actual_names) != len(expected_names):
            missing = sorted(expected_names - set(actual_names))
            unexpected = sorted(set(actual_names) - expected_names)
            raise AOVMergeError(
                f"{label} không giữ nguyên danh sách tài nguyên. "
                f"Thiếu={missing[:20]}; thừa={unexpected[:20]}."
            )
        bad = archive.testzip()
        if bad:
            raise AOVMergeError(f"{label} lỗi CRC tại {bad}.")
    except AOVMergeError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, EOFError) as exc:
        raise AOVMergeError(f"Không thể xác minh cấu trúc {label}.") from exc


def _publish_staged_archive(
    staged_path: Path,
    output_path: Path,
    expected_names: set[str],
) -> None:
    """Validate a completed archive before atomically replacing the destination."""
    if staged_path.stat().st_size > MAX_TOTAL_OUTPUT_BYTES:
        raise AOVMergeError("ZIP kết quả vượt quá giới hạn 1 GiB.")
    with zipfile.ZipFile(staged_path) as archive:
        _assert_archive_inventory(archive, expected_names, "ZIP ngoài sau ghép")
    os.replace(staged_path, output_path)


def _copy_stream(source, destination) -> None:
    shutil.copyfileobj(source, destination, length=1024 * 1024)


def _write_streamed_entry(out_zip: zipfile.ZipFile, name: str, source_zip: zipfile.ZipFile,
                          source_info: zipfile.ZipInfo) -> None:
    info = zipfile.ZipInfo(name, date_time=source_info.date_time)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = source_info.external_attr
    info.create_system = source_info.create_system
    info.file_size = source_info.file_size
    with source_zip.open(source_info, "r") as src, out_zip.open(info, "w", force_zip64=True) as dst:
        _copy_stream(src, dst)


HUANHUA_BASE_TABLE_SHA256 = {
    "Databin/Client/Huanhua/ResBillboardCfg.bytes":
        "fa1bb68ac64ff3d87d598abddf256f261d85b88cedd012b8f47be9c71d8ed562",
    "Databin/Client/Huanhua/ResBillboardSkinCfg.bytes":
        "e91f2f439b7e8a5e6d98e4347db169f03115135db8ee786f72980d1af9868ea8",
    "Databin/Client/Huanhua/ResKillBillboardCfg.bytes":
        "d45fd98b9c050dd94112154db1a87eb6122b17b37fa376b6ed886c5f62c36b74",
}
HUANHUA_BASE_TABLE_SCHEMA = {
    "Databin/Client/Huanhua/ResBillboardCfg.bytes": (32, 67),
    "Databin/Client/Huanhua/ResBillboardSkinCfg.bytes": (16, 67),
    "Databin/Client/Huanhua/ResKillBillboardCfg.bytes": (48, 67),
}


def seed_huanhua_base_tables(
    base_wrapper_path: str, donor_path: str, output_path: str
) -> dict[str, Any]:
    """Insert hash-pinned 1.64.1.12 base billboard tables into a MOD wrapper.

    The VTK 1.64.1.11 pack carries only Actor 595's MSES delta. The 1.64.1.12
    table bytes are supplied separately and pinned by SHA-256. Their MSES
    envelope, checksum, field count, row count and unique row keys are checked
    before insertion. The subsequent VTK merger appends only the verified
    Actor 595 key and updates ResourceVerification metadata.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="aki-aov-huanhua-seed-"))
    opened: list[zipfile.ZipFile] = []
    staged: Path | None = None
    try:
        try:
            donor = zipfile.ZipFile(donor_path, "r")
            opened.append(donor)
            wrapper = zipfile.ZipFile(base_wrapper_path, "r")
            opened.append(wrapper)
        except (OSError, zipfile.BadZipFile) as exc:
            raise AOVMergeError("Không mở được ZIP donor Huanhua hoặc wrapper nền.") from exc

        for label, archive in (("Huanhua donor", donor), ("wrapper MOD", wrapper)):
            _validate_archive_paths(archive, label)
            bad = archive.testzip()
            if bad:
                raise AOVMergeError(f"{label} lỗi CRC tại {bad}.")

        donor_entries = {
            info.filename: info for info in donor.infolist() if not info.is_dir()
        }
        version_path = BASE_PREFIX + "version.txt"
        expected_donor_entries = {
            version_path,
            *(BASE_PREFIX + relative for relative in HUANHUA_BASE_TABLE_SHA256),
        }
        if set(donor_entries) != expected_donor_entries:
            raise AOVMergeError(
                "Donor Huanhua phải chứa đúng version.txt và ba bảng nền đã ghim."
            )
        donor_version = donor.read(donor_entries[version_path]).decode(
            "utf-8-sig", errors="replace"
        ).strip()
        if donor_version.split("|", 1)[0] != BASE_VERSION:
            raise AOVMergeError(
                f"Donor Huanhua sai build {donor_version!r}; cần {BASE_VERSION}."
            )

        donor_payloads: dict[str, bytes] = {}
        for relative, expected_hash in HUANHUA_BASE_TABLE_SHA256.items():
            target = BASE_PREFIX + relative
            payload = donor.read(donor_entries[target])
            actual_hash = hashlib.sha256(payload).hexdigest()
            if actual_hash != expected_hash:
                raise AOVMergeError(
                    f"Donor Huanhua không khớp SHA-256 đã kiểm chứng: {relative}."
                )
            try:
                decoded, _mode = decode_aov_resource(payload, relative)
                table = parse_mses(decoded)
            except (AOVBytesError, MSESError) as exc:
                raise AOVMergeError(
                    f"Donor Huanhua không phải MSES hợp lệ tại {relative}: {exc}"
                ) from exc
            fields, rows = HUANHUA_BASE_TABLE_SCHEMA[relative]
            keys = [int.from_bytes(row[:4], "little") for row in table.rows]
            if (
                table.version != 7
                or table.field_count != fields
                or len(table.rows) != rows
                or not table.checksum_valid
                or len(set(keys)) != rows
            ):
                raise AOVMergeError(
                    f"Donor Huanhua sai schema, số hàng, khóa hoặc checksum: {relative}."
                )
            donor_payloads[relative] = payload

        resources_entries = _find_outer_entries(wrapper, "Resources.zip")
        skin_entries = _find_outer_entries(wrapper, "DanhSachSkin.txt")
        if len(resources_entries) != 1 or len(skin_entries) != 1:
            raise AOVMergeError(
                "Wrapper nền phải có đúng một Resources.zip và một DanhSachSkin.txt."
            )
        resources_info = resources_entries[0]
        inner_path = temp_dir / "base-Resources.zip"
        with wrapper.open(resources_info, "r") as source, open(inner_path, "wb") as target:
            _copy_stream(source, target)

        appended: list[str] = []
        inner_output = temp_dir / "Resources-with-Huanhua-base.zip"
        with zipfile.ZipFile(inner_path, "r") as inner:
            _validate_archive_paths(inner, "Resources.zip nền")
            bad = inner.testzip()
            if bad:
                raise AOVMergeError(f"Resources.zip nền lỗi CRC tại {bad}.")
            inner_names = {
                info.filename for info in inner.infolist()
            }
            if version_path not in inner_names:
                raise AOVMergeError("Wrapper nền thiếu version.txt 1.64.1.12.")
            base_version = inner.read(version_path).decode(
                "utf-8-sig", errors="replace"
            ).strip()
            if base_version != donor_version:
                raise AOVMergeError(
                    f"Version donor {donor_version!r} khác wrapper nền {base_version!r}."
                )
            for relative, payload in donor_payloads.items():
                target_name = BASE_PREFIX + relative
                if target_name in inner_names:
                    existing = inner.read(target_name)
                    if hashlib.sha256(existing).digest() != hashlib.sha256(payload).digest():
                        raise AOVMergeError(
                            f"Wrapper nền đã có bảng Huanhua khác nội dung: {relative}."
                        )

            with zipfile.ZipFile(
                inner_output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as output_inner:
                for info in inner.infolist():
                    if info.is_dir():
                        output_inner.writestr(info, b"")
                    else:
                        with inner.open(info, "r") as source, output_inner.open(
                            info, "w", force_zip64=True
                        ) as target:
                            _copy_stream(source, target)
                for relative, payload in sorted(donor_payloads.items()):
                    target_name = BASE_PREFIX + relative
                    if target_name not in inner_names:
                        output_inner.writestr(target_name, payload)
                        appended.append(target_name)

        with zipfile.ZipFile(inner_output, "r") as check_inner:
            _validate_archive_paths(check_inner, "Resources.zip sau bổ sung Huanhua base")
            bad = check_inner.testzip()
            expected_names = inner_names | set(appended)
            actual_names = {info.filename for info in check_inner.infolist()}
            if bad or actual_names != expected_names:
                raise AOVMergeError(
                    "Resources.zip sau bổ sung không giữ đúng inventory hoặc CRC."
                )
            for relative, payload in donor_payloads.items():
                target_name = BASE_PREFIX + relative
                if check_inner.read(target_name) != payload:
                    raise AOVMergeError(
                        f"Không round-trip được bảng Huanhua nền: {relative}."
                    )

        output_path_obj = Path(output_path)
        output_path_obj.parent.mkdir(parents=True, exist_ok=True)
        fd, staged_name = tempfile.mkstemp(
            prefix=f".{output_path_obj.name}.", suffix=".tmp.zip",
            dir=str(output_path_obj.parent),
        )
        os.close(fd)
        staged = Path(staged_name)
        try:
            with zipfile.ZipFile(
                staged, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as output_outer:
                for info in wrapper.infolist():
                    if info.is_dir():
                        output_outer.writestr(info, b"")
                    elif info.filename == resources_info.filename:
                        with open(inner_output, "rb") as source, output_outer.open(
                            info, "w", force_zip64=True
                        ) as target:
                            _copy_stream(source, target)
                    else:
                        with wrapper.open(info, "r") as source, output_outer.open(
                            info, "w", force_zip64=True
                        ) as target:
                            _copy_stream(source, target)
            _publish_staged_archive(
                staged, output_path_obj,
                {info.filename for info in wrapper.infolist()},
            )
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

        return {
            "version": donor_version,
            "appended_paths": sorted(appended),
            "table_sha256": {
                relative: hashlib.sha256(payload).hexdigest()
                for relative, payload in donor_payloads.items()
            },
            "verified": True,
        }
    except AOVMergeError:
        raise
    except Exception as exc:
        raise AOVMergeError(
            f"Không thể bổ sung bảng Huanhua nền: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        for archive in reversed(opened):
            try:
                archive.close()
            except Exception:
                pass
        shutil.rmtree(temp_dir, ignore_errors=True)


def merge_aov_archives(archive_paths: list[str], output_path: str) -> dict[str, Any]:
    """Merge the supported iOS 10-skin base pack with the Vấn Tâm Kiếm Actor 595 pack."""
    if not 2 <= len(archive_paths) <= MAX_AOV_INPUTS:
        raise AOVMergeError("Cần 1 ZIP nền 10 skin và ít nhất 1 ZIP Vấn Tâm Kiếm.")

    opened: list[zipfile.ZipFile] = []
    try:
        total_input_bytes = sum(Path(path).stat().st_size for path in archive_paths)
        if total_input_bytes > MAX_TOTAL_INPUT_BYTES:
            raise AOVMergeError("Tổng dung lượng ZIP đầu vào vượt quá 500 MB.")
        archives: list[tuple[str, zipfile.ZipFile]] = []
        total_entries = 0
        total_uncompressed = 0
        for path in archive_paths:
            try:
                zf = zipfile.ZipFile(path)
            except Exception as exc:
                raise AOVMergeError(f"File đầu vào không phải ZIP hợp lệ: {Path(path).name}") from exc
            opened.append(zf)
            entries = zf.infolist()
            total_entries += len(entries)
            total_uncompressed += sum(info.file_size for info in entries)
            if total_entries > MAX_ARCHIVE_ENTRIES:
                raise AOVMergeError("Tổng số mục ZIP vượt quá 20.000.")
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                raise AOVMergeError("Tổng dung lượng giải nén vượt quá 1 GiB.")
            _validate_archive_paths(zf, Path(path).name)
            try:
                bad = zf.testzip()
            except Exception as exc:
                raise AOVMergeError(
                    f"Không thể kiểm tra CRC ZIP: {Path(path).name}"
                ) from exc
            if bad:
                raise AOVMergeError(f"ZIP đầu vào lỗi CRC tại {bad}: {Path(path).name}")
            archives.append((path, zf))

        base_candidates = []
        for path, zf in archives:
            # Mod containers in the wild use different casing and directory prefixes
            # for these two wrapper files. Match by case-insensitive basename while
            # still requiring exactly one of each before any payload is changed.
            if _find_outer_entries(zf, "Resources.zip") and _find_outer_entries(zf, "DanhSachSkin.txt"):
                base_candidates.append((path, zf))
        if len(base_candidates) != 1:
            raise AOVMergeError("Không xác định được duy nhất ZIP nền có Resources.zip và danhsachskin.txt.")
        base_path, base_outer = base_candidates[0]
        vtk_archives = [(path, zf) for path, zf in archives if path != base_path]
        if not vtk_archives:
            raise AOVMergeError("Chưa có ZIP Vấn Tâm Kiếm.")

        resources_entries = _find_outer_entries(base_outer, "Resources.zip")
        skin_entries = _find_outer_entries(base_outer, "DanhSachSkin.txt")
        if len(resources_entries) != 1 or len(skin_entries) != 1:
            raise AOVMergeError("ZIP nền có cấu trúc Resources.zip không rõ ràng.")
        resources_info = resources_entries[0]
        skin_info = skin_entries[0]

        vtk_sources: dict[str, tuple[zipfile.ZipFile, zipfile.ZipInfo]] = {}
        for _, zf in vtk_archives:
            for info in zf.infolist():
                if info.is_dir() or _is_noise_path(info.filename):
                    continue
                name = _normalise_vtk_path(info.filename)
                if name in vtk_sources:
                    previous_archive, previous = vtk_sources[name]
                    if (
                        previous.file_size != info.file_size
                        or previous.CRC != info.CRC
                        or _zip_entry_sha256(previous_archive, previous)
                        != _zip_entry_sha256(zf, info)
                    ):
                        raise AOVMergeError(f"Các phần VTK có file trùng nhưng khác nội dung: {name}")
                    continue
                vtk_sources[name] = (zf, info)

        asset_paths = {
            name for name in vtk_sources
            if name.endswith(".assetbundle")
            and name not in {PACKER_PATH, VERIFICATION_PATH}
        }
        if asset_paths != EXPECTED_ASSETBUNDLES:
            missing = EXPECTED_ASSETBUNDLES - asset_paths
            extra = asset_paths - EXPECTED_ASSETBUNDLES
            raise AOVMergeError(
                "Bộ assetbundle Vấn Tâm Kiếm không đúng phiên bản. "
                f"Thiếu={sorted(missing)}; thừa={sorted(extra)}"
            )

        def read_vtk(name: str) -> bytes:
            if name not in vtk_sources:
                raise AOVMergeError(f"Thiếu file VTK bắt buộc: {name}")
            zf, info = vtk_sources[name]
            with zf.open(info, "r") as stream:
                return stream.read()

        temp_dir = Path(tempfile.mkdtemp(prefix="aki-aov-merge-"))
        try:
            base_inner_path = temp_dir / "base-Resources.zip"
            with base_outer.open(resources_info, "r") as src, open(base_inner_path, "wb") as dst:
                _copy_stream(src, dst)
            base_inner = zipfile.ZipFile(base_inner_path)
            try:
                _validate_archive_paths(base_inner, "Resources.zip bên trong gói mod")
                bad_inner = base_inner.testzip()
                if bad_inner:
                    raise AOVMergeError(
                        f"Resources.zip của gói mod lỗi CRC tại {bad_inner}"
                    )
                base_names = set(base_inner.namelist())
                required_base = [
                    BASE_PREFIX + HERO_SKIN_BASE_PATH,
                    BASE_PREFIX + COMMON_ACTIONS_PATH,
                    BASE_PREFIX + RES_SKIN_PATH,
                    BASE_PREFIX + BATTLE_BANK_PATH,
                    BASE_PREFIX + LOBBY_BANK_PATH,
                    BASE_PREFIX + SKILL_COMBINE_PATH,
                    BASE_PREFIX + PACKER_PATH,
                    BASE_PREFIX + VERIFICATION_PATH,
                ]
                if any(name not in base_names for name in required_base):
                    raise AOVMergeError("ZIP nền không chứa đủ tài nguyên 1.64.1.12 cần ghép.")

                def read_base(relative: str) -> bytes:
                    try:
                        return base_inner.read(BASE_PREFIX + relative)
                    except KeyError as exc:
                        raise AOVMergeError(f"ZIP nền thiếu file {relative}") from exc

                replacements: dict[str, bytes] = {}
                base_huanhua_resources = {
                    relative: read_base(relative)
                    for relative in HUANHUA_TABLE_PATHS
                    if BASE_PREFIX + relative in base_names
                }
                vtk_huanhua_resources = {
                    relative: read_vtk(relative)
                    for relative in HUANHUA_TABLE_PATHS
                    if relative in vtk_sources
                }
                huanhua_replacements, huanhua_reports = _merge_huanhua_billboard_tables(
                    base_huanhua_resources,
                    vtk_huanhua_resources,
                )
                replacements.update(huanhua_replacements)

                replacements[HERO_SKIN_BASE_PATH], fix_count, method_count = _merge_ifix(
                    read_base(HERO_SKIN_BASE_PATH), read_vtk(HERO_SKIN_VTK_PATH)
                )
                try:
                    replacements[COMMON_ACTIONS_PATH], common_report = merge_common_actions_pkg(
                        read_base(COMMON_ACTIONS_PATH), read_vtk(COMMON_ACTIONS_PATH)
                    )
                except CommonActionsMergeError as exc:
                    raise AOVMergeError(f"CommonActions: {exc}") from exc

                nested_pkg_reports: dict[str, dict[str, Any]] = {}
                for relative in ("KernelLua.pkg.bytes", "StableSystems_3.pkg.bytes"):
                    if relative in vtk_sources and BASE_PREFIX + relative in base_names:
                        replacements[relative], nested_pkg_reports[relative] = _merge_nested_zip_package(
                            read_base(relative), read_vtk(relative), relative
                        )

                mses_reports = {}
                for relative in (RES_SKIN_PATH, BATTLE_BANK_PATH, LOBBY_BANK_PATH, SKILL_COMBINE_PATH):
                    try:
                        merge_options = {"allow_invalid_base_checksum": True} if relative == RES_SKIN_PATH else {}
                        replacements[relative], mses_reports[relative] = merge_aov_mses_resource(
                            read_base(relative), read_vtk(relative), relative, **merge_options
                        )
                    except AOVResourceMergeError as exc:
                        raise AOVMergeError(str(exc)) from exc

                skip_vtk = {
                    PACKER_PATH, VERIFICATION_PATH, HERO_SKIN_VTK_PATH,
                    COMMON_ACTIONS_PATH, RES_SKIN_PATH, BATTLE_BANK_PATH,
                    LOBBY_BANK_PATH, SKILL_COMBINE_PATH,
                }
                skip_vtk.update(huanhua_reports)
                for name in vtk_sources:
                    if name.startswith("Sound/Android/"):
                        skip_vtk.add(name)
                skip_vtk.update(nested_pkg_reports)
                verification_included_paths = (
                    set(vtk_sources) - skip_vtk
                ) | set(nested_pkg_reports) | {
                    COMMON_ACTIONS_PATH, RES_SKIN_PATH, BATTLE_BANK_PATH,
                    LOBBY_BANK_PATH, SKILL_COMBINE_PATH,
                } | set(huanhua_reports)

                (replacements[PACKER_PATH], packer_replaced, packer_added,
                 packer_entry_count) = _merge_packer_metadata(
                    read_base(PACKER_PATH), read_vtk(PACKER_PATH), asset_paths
                )
                (replacements[VERIFICATION_PATH], verification_updated,
                 verification_version_drift) = _merge_verification_metadata(
                    read_base(VERIFICATION_PATH), read_vtk(VERIFICATION_PATH),
                    verification_included_paths,
                )

                common_files = len(common_report["merged_files"])
                common_tracks = sum(
                    len(item.get("replaced_tracks", [])) + len(item.get("appended_tracks", []))
                    for item in common_report["merged_files"].values()
                )

                inner_output_path = temp_dir / "Resources-merged.zip"
                added_files: list[str] = []
                with zipfile.ZipFile(inner_output_path, "w") as out_inner:
                    for info in base_inner.infolist():
                        if info.is_dir():
                            out_inner.writestr(info, b"")
                            continue
                        relative = (
                            info.filename[len(BASE_PREFIX):]
                            if info.filename.startswith(BASE_PREFIX) else None
                        )
                        if relative in replacements:
                            out_inner.writestr(info, replacements[relative])
                        else:
                            with base_inner.open(info, "r") as src, out_inner.open(
                                info, "w", force_zip64=True
                            ) as dst:
                                _copy_stream(src, dst)

                    for name, (source_zip, source_info) in sorted(vtk_sources.items()):
                        if name in skip_vtk:
                            continue
                        destination = BASE_PREFIX + name
                        if destination in base_names:
                            raise AOVMergeError(f"File VTK va ZIP nền trùng ngoài danh sách cho phép: {destination}")
                        _write_streamed_entry(out_inner, destination, source_zip, source_info)
                        added_files.append(destination)

                with zipfile.ZipFile(inner_output_path) as check_inner:
                    expected_inner_names = base_names | set(added_files)
                    _assert_archive_inventory(
                        check_inner, expected_inner_names, "Resources.zip sau ghép"
                    )
                    final_names = set(check_inner.namelist())
                    for relative in replacements:
                        if BASE_PREFIX + relative not in final_names:
                            raise AOVMergeError(f"ZIP kết quả thiếu file đã ghép: {relative}")
                    for asset_path in asset_paths:
                        if BASE_PREFIX + asset_path not in final_names:
                            raise AOVMergeError(f"ZIP kết quả thiếu assetbundle: {asset_path}")

                # Write to a sibling temporary file and publish atomically only
                # after every structural check succeeds. A failed merge must not
                # leave a truncated ZIP at the caller's requested output path.
                output_path_obj = Path(output_path)
                output_path_obj.parent.mkdir(parents=True, exist_ok=True)
                fd, staged_name = tempfile.mkstemp(
                    prefix=f".{output_path_obj.name}.",
                    suffix=".tmp",
                    dir=str(output_path_obj.parent),
                )
                os.close(fd)
                staged_output = Path(staged_name)
                try:
                    with zipfile.ZipFile(staged_output, "w") as out_outer:
                        for info in base_outer.infolist():
                            if info.is_dir():
                                out_outer.writestr(info, b"")
                            elif info.filename == resources_info.filename:
                                with open(inner_output_path, "rb") as src, out_outer.open(
                                    info, "w", force_zip64=True
                                ) as dst:
                                    _copy_stream(src, dst)
                            elif info.filename == skin_info.filename:
                                original = base_outer.read(info.filename).decode("utf-8-sig", errors="replace").rstrip()
                                if "Vấn Tâm Kiếm" not in original:
                                    original += "\nLý Tín - Vấn Tâm Kiếm (Actor 595)"
                                out_outer.writestr(info, (original + "\n").encode("utf-8"))
                            else:
                                with base_outer.open(info, "r") as src, out_outer.open(
                                    info, "w", force_zip64=True
                                ) as dst:
                                    _copy_stream(src, dst)

                    # Validate the serialized packer metadata before publishing.
                    packer_env = _load_unitypy_aov().load(replacements[PACKER_PATH])
                    _, packer_tree = _find_mono_behaviour(packer_env, "ResourcePackerInfoSetXML")
                    _publish_staged_archive(
                        staged_output,
                        output_path_obj,
                        {info.filename for info in base_outer.infolist()},
                    )
                finally:
                    staged_output.unlink(missing_ok=True)
                return {
                    "output": str(output_path_obj),
                    "base_version": BASE_VERSION,
                    "vtk_version": VTK_VERSION,
                    "ifix_fixes": fix_count,
                    "ifix_methods": method_count,
                    "common_actions_files": common_files,
                    "common_actions_tracks": common_tracks,
                    "common_actions_report": common_report,
                    "mses_reports": mses_reports,
                    "huanhua_billboard_reports": huanhua_reports,
                    "nested_pkg_reports": nested_pkg_reports,
                    "assetbundles": len(asset_paths),
                    "new_files": len(added_files),
                    "packer_entries": packer_entry_count,
                    "packer_replaced": packer_replaced,
                    "packer_added": packer_added,
                    "verification_updated": verification_updated,
                    "verification_version_drift_kept": verification_version_drift,
                    "validation": "ZIP/CRC, IFix index, MSES tables, CommonActions XML, PackerInfo and ResourceVerification passed",
                    "game_tested": False,
                }
            finally:
                base_inner.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except AOVMergeError:
        raise
    except Exception as exc:
        raise AOVMergeError(f"Không thể ghép AOV: {type(exc).__name__}: {exc}") from exc
    finally:
        for zf in opened:
            try:
                zf.close()
            except Exception:
                pass


def _sha256_file_for_aov(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


P9_ARCHIVE_SHA256 = "77dcb4f270c4be00d2cbce8194a57a388420eeb6e430f0edae7bc8e812cf2e84"
P10_ARCHIVE_SHA256 = "325c6c96baf931cd2b3cd33b65c944e994f3c48bbe2c22eab08d7a338d04b03e"
P9_ACTOR_IDS = {"107", "130", "137", "163", "509", "510", "518", "540", "597"}
P10_ACTOR_IDS = {"111", "132", "150", "157", "167", "190", "524", "567", "598", "599"}

P9_P10_ACTOR_MSES_PATHS = {
    "Databin/Client/Actor/heroSkin.bytes",
    "Databin/Client/Motion/ResSkinMotionBaseCfg.bytes",
    "Databin/Client/Shop/HeroSkinShop.bytes",
    "Databin/Client/Skill/skillmark.bytes",
    "Databin/Client/Skill/liteBulletCfg.bytes",
    "Databin/Client/Sound/BattleBank.bytes",
    "Databin/Client/Sound/LobbyBank.bytes",
    "Databin/Client/Sound/LobbySound.bytes",
    "Databin/Client/Sound/ChatSound.bytes",
    "Databin/Client/Sound/CoupleSound.bytes",
    "Databin/Client/Sound/HeroSound.bytes",
}


def p9_p10_actor_id_from_packer_path(path: str) -> str | None:
    """Read the first three actor digits in an exact P9/P10 skin bundle path."""
    normalized = path.replace(chr(92), "/").casefold()
    if not normalized.startswith("assetbundle/battle/skin/"):
        return None
    filename = normalized.rsplit("/", 1)[-1]
    match = re.match(r"^(\d{3})\d*_.+_battle(?:_low)?\.assetbundle$", filename)
    if not match:
        return None
    actor_id = match.group(1)
    return actor_id if actor_id in P9_ACTOR_IDS | P10_ACTOR_IDS else None


def _merge_p9_p10_packer_metadata(p10_data: bytes, p9_data: bytes) -> tuple[bytes, dict]:
    """Select the 410 changed PackerInfo skin rows by the owning P9/P10 Actor.

    P9 and P10 have the same 1.64.1.12 path order and dependency edge array.
    Only source-owned skin-bundle ResourceInfos differ, so no dependency index
    needs to be rewritten for this exact pair.
    """
    # ResourcePackerInfo is a raw UnityFS AssetBundle, not an AES/.bytes
    # resource. Parse and serialize it directly; the source hash pins its exact
    # raw UnityFS payload.
    p10_raw = p10_data
    p9_raw = p9_data
    if (
        hashlib.sha256(p10_data).hexdigest()
        != "b87d60ef87d5d49dacad2cdb32536e06bd933ac4e2f8649e6336855609b39d56"
        or hashlib.sha256(p9_data).hexdigest()
        != "6784ca2038e4f141dba82d4b8bc71cd0b7b7998efa3adf9d61447335bf9e06f5"
    ):
        raise AOVMergeError("PackerInfo không khớp đúng payload P9/P10 đã kiểm chứng.")
    UnityPy_AOV = _load_unitypy_aov()
    try:
        p10_env = UnityPy_AOV.load(p10_raw)
        p9_env = UnityPy_AOV.load(p9_raw)
        p10_obj, p10_tree = _find_mono_behaviour(
            p10_env, "ResourcePackerInfoSetXML"
        )
        _, p9_tree = _find_mono_behaviour(
            p9_env, "ResourcePackerInfoSetXML"
        )
    except AOVMergeError:
        raise
    except Exception as exc:
        raise AOVMergeError(
            f"Không đọc được PackerInfo P9/P10: {type(exc).__name__}: {exc}"
        ) from exc

    if p10_tree.get("Version") != BASE_VERSION or p9_tree.get("Version") != BASE_VERSION:
        raise AOVMergeError("PackerInfo P9/P10 không cùng phiên bản 1.64.1.12.")
    p10_entries = p10_tree["AllResourcePackInfo"]
    p9_entries = p9_tree["AllResourcePackInfo"]
    if len(p10_entries) != 22298 or len(p9_entries) != 22298:
        raise AOVMergeError("PackerInfo P9/P10 không có đúng 22.298 entry.")
    p10_paths = [item["PathInIFS"] for item in p10_entries]
    p9_paths = [item["PathInIFS"] for item in p9_entries]
    if p10_paths != p9_paths or len(set(p10_paths)) != len(p10_paths):
        raise AOVMergeError("Thứ tự PathInIFS P9/P10 khác nhau; không thể giữ nguyên dependency index.")
    p10_links = p10_tree["AllParentIdexToChild"]
    p9_links = p9_tree["AllParentIdexToChild"]
    if len(p10_links) != 60243 or p10_links != p9_links:
        raise AOVMergeError("Đồ thị dependency PackerInfo P9/P10 không giống hệt nhau.")

    changed = 0
    selected_p9 = 0
    selected_p10 = 0
    p9_actor_ids = set(P9_ACTOR_IDS)
    p10_actor_ids = set(P10_ACTOR_IDS)
    for index, (p10_entry, p9_entry) in enumerate(zip(p10_entries, p9_entries)):
        if p10_entry == p9_entry:
            continue
        changed += 1
        path = p10_entry["PathInIFS"]
        actor_id = p9_p10_actor_id_from_packer_path(path)
        if actor_id is None:
            raise AOVMergeError(
                f"PackerInfo có xung đột ngoài đường dẫn skin đã xác minh: {path}"
            )
        if actor_id in p9_actor_ids and actor_id not in p10_actor_ids:
            p10_entries[index] = copy.deepcopy(p9_entry)
            selected_p9 += 1
        elif actor_id in p10_actor_ids and actor_id not in p9_actor_ids:
            selected_p10 += 1
        else:
            raise AOVMergeError(
                f"PackerInfo không xác định được duy nhất Actor sở hữu {path}."
            )
    if (changed, selected_p9, selected_p10) != (410, 164, 246):
        raise AOVMergeError(
            "Số PackerInfo theo Actor thay đổi so với fixture "
            f"(changed={changed}, P9={selected_p9}, P10={selected_p10})."
        )

    try:
        p10_obj.save_typetree(p10_tree)
        if len(p10_env.files) != 1:
            raise AOVMergeError("PackerInfo P10 không có đúng một serialized AssetBundle.")
        serialized = next(iter(p10_env.files.values())).save("original")
        check_env = UnityPy_AOV.load(serialized)
        _, check_tree = _find_mono_behaviour(
            check_env, "ResourcePackerInfoSetXML"
        )
    except AOVMergeError:
        raise
    except Exception as exc:
        raise AOVMergeError(
            f"Không serialize được PackerInfo P9/P10: {type(exc).__name__}: {exc}"
        ) from exc

    check_entries = check_tree["AllResourcePackInfo"]
    if (
        check_tree.get("Version") != BASE_VERSION
        or [item["PathInIFS"] for item in check_entries] != p10_paths
        or check_tree["AllParentIdexToChild"] != p10_links
        or len(check_entries) != 22298
    ):
        raise AOVMergeError("PackerInfo round-trip làm thay đổi path hoặc dependency graph.")
    output = serialized
    verify_env = UnityPy_AOV.load(output)
    _, verify_tree = _find_mono_behaviour(
        verify_env, "ResourcePackerInfoSetXML"
    )
    if (
        [item["PathInIFS"] for item in verify_tree["AllResourcePackInfo"]] != p10_paths
        or verify_tree["AllParentIdexToChild"] != p10_links
    ):
        raise AOVMergeError("PackerInfo UnityFS round-trip không giữ nguyên dependency graph.")
    return output, {
        "version": BASE_VERSION,
        "entry_count": len(check_entries),
        "dependency_link_count": len(check_tree["AllParentIdexToChild"]),
        "changed_entries": changed,
        "selected_p9_actor_entries": selected_p9,
        "selected_p10_actor_entries": selected_p10,
        "dependency_graph_unchanged": True,
        "verified": True,
    }


def _merge_p9_p10_ifix_resource(
    p10_data: bytes, p9_data: bytes, resource_name: str
) -> tuple[bytes, dict]:
    p10_raw, p10_mode = decode_aov_resource(p10_data, resource_name)
    p9_raw, _ = decode_aov_resource(p9_data, resource_name)
    try:
        p10_patch = parse_ifix_patch(p10_raw)
        p9_patch = parse_ifix_patch(p9_raw)
        if (
            serialize_ifix_patch(p10_patch) != p10_raw
            or serialize_ifix_patch(p9_patch) != p9_raw
        ):
            raise AOVMergeError("IFix P9/P10 source không round-trip chính xác.")
        merged = merge_ifix_actor_scoped_patches(p10_patch, p9_patch)
        merged_raw = serialize_ifix_patch(merged)
        output = encode_aov_resource(merged_raw, resource_name, p10_mode)
        verify_raw, _ = decode_aov_resource(output, resource_name)
        verified = parse_ifix_patch(verify_raw)
        if serialize_ifix_patch(verified) != merged_raw:
            raise AOVMergeError("IFix sau ghép không round-trip byte-for-byte.")
    except IFixMergeError as exc:
        raise AOVMergeError(f"{resource_name}: {exc}") from exc
    return output, {
        "fixes": len(verified["fixes"]),
        "methods": len(verified["methods"]),
        "encoding": p10_mode,
        "verified": True,
    }


def merge_p9_p10_archives(
    archive_paths: list[str], output_path: str
) -> dict[str, Any]:
    """Merge the exact iOS P9 and P10 skin wrappers into one 19-skin wrapper.

    All source bytes are pinned by SHA-256. Every differing resource must pass its
    source-specific MSES, IFix, CommonActions, or PackerInfo adapter. Unknown
    conflicts are rejected; no stock game package is injected.
    """
    if len(archive_paths) != 2:
        raise AOVMergeError("Adapter P9/P10 chỉ nhận đúng hai ZIP nguồn.")
    opened: list[zipfile.ZipFile] = []
    try:
        total_input_bytes = sum(Path(path).stat().st_size for path in archive_paths)
        if total_input_bytes > MAX_TOTAL_INPUT_BYTES:
            raise AOVMergeError("Tổng ZIP P9/P10 vượt giới hạn 500 MB.")
        archives = []
        for path in archive_paths:
            if _sha256_file_for_aov(path) not in {P9_ARCHIVE_SHA256, P10_ARCHIVE_SHA256}:
                raise AOVMergeError(
                    f"{Path(path).name}: SHA-256 không thuộc cặp P9/P10 đã kiểm chứng."
                )
            try:
                zf = zipfile.ZipFile(path, "r")
            except Exception as exc:
                raise AOVMergeError(f"Không mở được ZIP P9/P10: {Path(path).name}") from exc
            opened.append(zf)
            _validate_archive_paths(zf, Path(path).name)
            bad = zf.testzip()
            if bad:
                raise AOVMergeError(f"ZIP P9/P10 lỗi CRC tại {bad}.")
            resources = _find_outer_entries(zf, "Resources.zip")
            skins = _find_outer_entries(zf, "DanhSachSkin.txt")
            if len(resources) != 1 or len(skins) != 1:
                raise AOVMergeError("Mỗi ZIP P9/P10 phải có đúng một Resources.zip và DanhSachSkin.txt.")
            digest = _sha256_file_for_aov(path)
            archives.append({
                "path": path,
                "zip": zf,
                "digest": digest,
                "source": "P9" if digest == P9_ARCHIVE_SHA256 else "P10",
                "resources_info": resources[0],
                "skin_info": skins[0],
            })
        if {item["source"] for item in archives} != {"P9", "P10"}:
            raise AOVMergeError("Cần đúng một nguồn P9 và một nguồn P10.")
        p10 = next(item for item in archives if item["source"] == "P10")
        p9 = next(item for item in archives if item["source"] == "P9")

        def read_inner(source):
            raw = source["zip"].read(source["resources_info"])
            inner = zipfile.ZipFile(io.BytesIO(raw), "r")
            _validate_archive_paths(inner, f"Resources.zip {source['source']}")
            bad = inner.testzip()
            if bad:
                inner.close()
                raise AOVMergeError(
                    f"Resources.zip {source['source']} lỗi CRC tại {bad}."
                )
            files = {
                info.filename: (info, inner.read(info))
                for info in inner.infolist() if not info.is_dir()
            }
            if any(not name.startswith(BASE_PREFIX) for name in files):
                inner.close()
                raise AOVMergeError(
                    f"Resources.zip {source['source']} có path ngoài build {BASE_PREFIX}."
                )
            return raw, inner, files

        p10_raw, p10_inner, p10_files = read_inner(p10)
        p9_raw, p9_inner, p9_files = read_inner(p9)
        try:
            p10_actors = {
                match.group(1)
                for name in p10_files
                if (match := re.search(
                    r"(?:^|/)Actor_(\d{3})_Actions\.pkg\.bytes$",
                    name, re.IGNORECASE
                ))
            }
            p9_actors = {
                match.group(1)
                for name in p9_files
                if (match := re.search(
                    r"(?:^|/)Actor_(\d{3})_Actions\.pkg\.bytes$",
                    name, re.IGNORECASE
                ))
            }
            if p10_actors != P10_ACTOR_IDS or p9_actors != P9_ACTOR_IDS:
                raise AOVMergeError("Actor_Action inventory không khớp bộ P9/P10 đã ghim.")
            version_path = BASE_PREFIX + "version.txt"
            if (
                version_path not in p10_files
                or version_path not in p9_files
                or p10_files[version_path][1] != p9_files[version_path][1]
            ):
                raise AOVMergeError("version.txt P9/P10 không giống nhau.")

            p10_names = set(p10_files)
            p9_names = set(p9_files)
            common_path = BASE_PREFIX + COMMON_ACTIONS_PATH
            packer_path = BASE_PREFIX + PACKER_PATH
            verification_path = BASE_PREFIX + VERIFICATION_PATH
            if any(path not in p10_names or path not in p9_names for path in (
                common_path,
                packer_path,
                verification_path,
                BASE_PREFIX + "Thanos/HeroSkin_1.bytes",
                BASE_PREFIX + "Thanos/HeroSkin_2.bytes",
            )):
                raise AOVMergeError("P9/P10 thiếu tài nguyên bắt buộc để ghép.")

            p10_common = p10_files[common_path][1]
            p9_common = p9_files[common_path][1]
            if (
                hashlib.sha256(p10_common).hexdigest()
                != "2ec3ca0a185850566c4001305e6fd367bbfd616d459d04adc4b2fa88a458405f"
                or hashlib.sha256(p9_common).hexdigest()
                != "2e0e1fce641b5310c70d19a63637381de3af5ec938c92488e7e1b16afcdb87ca"
            ):
                raise AOVMergeError("CommonActions P9/P10 không khớp hai package đã kiểm chứng.")
            # The exact source pair differs at exactly three XML tracks, all
            # covered by the actor-gated parser. Do not let an opaque semantic
            # subset or a new file silently pass through strict_conflicts.
            try:
                with zipfile.ZipFile(io.BytesIO(p10_common), "r") as p10_ca, zipfile.ZipFile(
                    io.BytesIO(p9_common), "r"
                ) as p9_ca:
                    p10_common_names = [info.filename for info in p10_ca.infolist() if not info.is_dir()]
                    p9_common_names = [info.filename for info in p9_ca.infolist() if not info.is_dir()]
                    if (
                        len(p10_common_names) != 785
                        or len(p9_common_names) != 785
                        or len(set(p10_common_names)) != 785
                        or len(set(p9_common_names)) != 785
                        or set(p10_common_names) != set(p9_common_names)
                    ):
                        raise AOVMergeError("CommonActions P9/P10 không giữ đúng 785 path duy nhất.")
                    common_differences = {
                        name for name in p10_common_names
                        if p10_ca.read(name) != p9_ca.read(name)
                    }
            except zipfile.BadZipFile as exc:
                raise AOVMergeError("CommonActions P9/P10 không phải ZIP lồng hợp lệ.") from exc
            expected_common_differences = {
                "commonresource/Back.xml",
                "commonresource/HasteE1.xml",
                "commonresource/HasteE1_leave.xml",
            }
            if common_differences != expected_common_differences:
                raise AOVMergeError(
                    "CommonActions P9/P10 có path xung đột ngoài ba XML đã kiểm chứng: "
                    + ", ".join(sorted(common_differences ^ expected_common_differences))
                )

            common_dry_run = analyze_common_actions_overlay_set([
                ("P9", p9_common, set(P9_ACTOR_IDS))
            ])
            if (
                not common_dry_run.get("parser_verified")
                or common_dry_run.get("unsupported_path_conflict_count") != 0
                or not common_dry_run.get("skin_ids")
            ):
                raise AOVMergeError(
                    "CommonActions P9 không qua dry-run hoặc còn path conflict ngoài parser."
                )
            try:
                merged_common, common_report = merge_common_actions_pkg(
                    p10_common,
                    p9_common,
                    special_ids=set(common_dry_run["skin_ids"]),
                    strict_conflicts=True,
                )
            except CommonActionsMergeError as exc:
                raise AOVMergeError(f"CommonActions P9/P10: {exc}") from exc
            if not common_report.get("verified"):
                raise AOVMergeError("CommonActions P9/P10 không qua xác minh track.")
            replacements: dict[str, bytes] = {
                COMMON_ACTIONS_PATH: merged_common,
            }

            for relative in ("Thanos/HeroSkin_1.bytes", "Thanos/HeroSkin_2.bytes"):
                full_path = BASE_PREFIX + relative
                if p10_files[full_path][1] == p9_files[full_path][1]:
                    continue
                replacements[relative], _ = _merge_p9_p10_ifix_resource(
                    p10_files[full_path][1],
                    p9_files[full_path][1],
                    relative,
                )

            mses_reports = {}
            for relative in sorted({
                name[len(BASE_PREFIX):]
                for name in p10_names & p9_names
                if name.endswith(".bytes")
                and p10_files[name][1] != p9_files[name][1]
            }):
                if relative in {
                    COMMON_ACTIONS_PATH,
                    "Thanos/HeroSkin_1.bytes",
                    "Thanos/HeroSkin_2.bytes",
                }:
                    continue
                full_path = BASE_PREFIX + relative
                options = {}
                if relative == RES_SKIN_PATH:
                    options["allow_p9_p10_label_union"] = True
                elif relative in P9_P10_ACTOR_MSES_PATHS:
                    options["base_actor_action_ids"] = set(P10_ACTOR_IDS)
                    options["overlay_actor_action_ids"] = set(P9_ACTOR_IDS)
                try:
                    merged, report = merge_aov_mses_resource(
                        p10_files[full_path][1],
                        p9_files[full_path][1],
                        relative,
                        **options,
                    )
                except AOVResourceMergeError as exc:
                    raise AOVMergeError(str(exc)) from exc
                if not report.get("verified"):
                    raise AOVMergeError(f"{relative}: adapter MSES không xác minh được output.")
                replacements[relative] = merged
                mses_reports[relative] = report

            if p10_files[verification_path][1] != p9_files[verification_path][1]:
                raise AOVMergeError("ResourceVerification P9/P10 khác nhau ngoài fixture.")
            replacements[PACKER_PATH], packer_report = _merge_p9_p10_packer_metadata(
                p10_files[packer_path][1], p9_files[packer_path][1]
            )

            # Require every differing shared path to have a dedicated merger.
            conflict_paths = {
                name for name in p10_names & p9_names
                if p10_files[name][1] != p9_files[name][1]
            }
            handled_paths = {BASE_PREFIX + item for item in replacements}
            if conflict_paths != handled_paths:
                unresolved = sorted(conflict_paths - handled_paths)
                unexpected = sorted(handled_paths - conflict_paths)
                raise AOVMergeError(
                    f"Inventory P9/P10 chưa được xử lý đúng. "
                    f"Chưa xử lý={unresolved[:12]}; thay đổi thừa={unexpected[:12]}."
                )
            if len(conflict_paths) != 18:
                raise AOVMergeError(
                    f"Số path xung đột P9/P10 thay đổi ({len(conflict_paths)} != 18)."
                )

            directories = {
                info.filename
                for inner in (p10_inner, p9_inner)
                for info in inner.infolist() if info.is_dir()
            }
            inner_output = io.BytesIO()
            written_dirs = set()
            with zipfile.ZipFile(
                inner_output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as out_inner:
                for info in p10_inner.infolist():
                    if info.is_dir():
                        if info.filename not in written_dirs:
                            out_inner.writestr(info, b"")
                            written_dirs.add(info.filename)
                        continue
                    relative = info.filename[len(BASE_PREFIX):]
                    payload = replacements.get(relative, p10_files[info.filename][1])
                    out_inner.writestr(info, payload)
                for info in p9_inner.infolist():
                    if info.is_dir():
                        if info.filename not in written_dirs:
                            out_inner.writestr(info, b"")
                            written_dirs.add(info.filename)
                    elif info.filename not in p10_names:
                        out_inner.writestr(info, p9_files[info.filename][1])

            inner_bytes = inner_output.getvalue()
            with zipfile.ZipFile(io.BytesIO(inner_bytes), "r") as check_inner:
                expected_inner = set(p10_inner.namelist()) | set(p9_inner.namelist())
                _assert_archive_inventory(
                    check_inner, expected_inner, "Resources.zip P9+P10 sau ghép"
                )
                for relative in replacements:
                    if BASE_PREFIX + relative not in check_inner.namelist():
                        raise AOVMergeError(f"Resources.zip sau ghép thiếu {relative}.")

            p10_skin_text = p10["zip"].read(p10["skin_info"]).decode(
                "utf-8-sig", errors="strict"
            )
            p9_skin_text = p9["zip"].read(p9["skin_info"]).decode(
                "utf-8-sig", errors="strict"
            )
            p10_skin_lines = [line.strip() for line in p10_skin_text.splitlines() if line.strip()]
            p9_skin_lines = [line.strip() for line in p9_skin_text.splitlines() if line.strip()]
            if (
                len(p10_skin_lines) != 10
                or len(p9_skin_lines) != 9
                or len(set(p10_skin_lines)) != 10
                or len(set(p9_skin_lines)) != 9
            ):
                raise AOVMergeError("DanhSachSkin nguồn không có đúng 10 mục P10 và 9 mục P9.")
            skin_lines = []
            for line in (p10_skin_text + "\n" + p9_skin_text).splitlines():
                line = line.strip()
                if line and line not in skin_lines:
                    skin_lines.append(line)
            if len(skin_lines) != 19:
                raise AOVMergeError(
                    f"DanhSachSkin sau union có {len(skin_lines)} mục thay vì 19."
                )
            merged_skin_text = ("\n".join(skin_lines) + "\n").encode("utf-8")

            destination = Path(output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, staged_name = tempfile.mkstemp(
                prefix=f".{destination.stem}.",
                suffix=".tmp.zip",
                dir=str(destination.parent),
            )
            os.close(fd)
            staged = Path(staged_name)
            try:
                with zipfile.ZipFile(staged, "w", allowZip64=True) as out_outer:
                    for info in p10["zip"].infolist():
                        if info.is_dir():
                            out_outer.writestr(info, b"")
                        elif info.filename == p10["resources_info"].filename:
                            out_outer.writestr(info, inner_bytes)
                        elif info.filename == p10["skin_info"].filename:
                            out_outer.writestr(info, merged_skin_text)
                        else:
                            out_outer.writestr(info, p10["zip"].read(info))
                _publish_staged_archive(
                    staged,
                    destination,
                    {info.filename for info in p10["zip"].infolist()},
                )
            finally:
                staged.unlink(missing_ok=True)

            return {
                "output": str(destination),
                "adapter": "aov_p9_p10_skin_union_1.64.1",
                "base_version": BASE_VERSION,
                "source_archives": {"P9": P9_ARCHIVE_SHA256, "P10": P10_ARCHIVE_SHA256},
                "actors": sorted(P9_ACTOR_IDS | P10_ACTOR_IDS),
                "skin_count": 19,
                "resource_paths": len(p10_names | p9_names),
                "conflicting_paths_merged": len(conflict_paths),
                "common_actions_files": len(common_report.get("merged_files", {})),
                "common_actions_report": common_report,
                "ifix_reports": {
                    relative: {
                        "fixes": len(parse_ifix_patch(
                            decode_aov_resource(replacements[relative], relative)[0]
                        )["fixes"]),
                        "methods": len(parse_ifix_patch(
                            decode_aov_resource(replacements[relative], relative)[0]
                        )["methods"]),
                    }
                    for relative in ("Thanos/HeroSkin_1.bytes", "Thanos/HeroSkin_2.bytes")
                    if relative in replacements
                },
                "mses_reports": mses_reports,
                "packer_report": packer_report,
                "resource_verification_unchanged": True,
                "output_bytes": destination.stat().st_size,
                "validation": (
                    "outer/inner ZIP CRC and inventory, CommonActions tracks, IFix, "
                    "MSES checksums, PackerInfo dependency graph and AES round-trip passed"
                ),
                "structurally_validated": True,
                "game_tested": False,
            }
        finally:
            p10_inner.close()
            p9_inner.close()
    except AOVMergeError:
        raise
    except (AOVBytesError, CommonActionsMergeError, IFixMergeError, ValueError) as exc:
        if isinstance(exc, AOVMergeError):
            raise
        raise AOVMergeError(
            f"Không thể ghép P9/P10: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        for zf in opened:
            zf.close()

