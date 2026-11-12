from __future__ import annotations

import copy
import hashlib
import io
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .aov_bytes import AOVBytesError, decode_aov_resource, encode_aov_resource
from .aov_merge import AOVResourceMergeError, merge_aov_mses_resource
from .aov_common_actions import CommonActionsMergeError, merge_common_actions_pkg
from .ifix_merger import (
    IFixMergeError,
    merge_ifix_patches,
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
                replacements[HERO_SKIN_BASE_PATH], fix_count, method_count = _merge_ifix(
                    read_base(HERO_SKIN_BASE_PATH), read_vtk(HERO_SKIN_VTK_PATH)
                )
                try:
                    replacements[COMMON_ACTIONS_PATH], common_report = merge_common_actions_pkg(
                        read_base(COMMON_ACTIONS_PATH), read_vtk(COMMON_ACTIONS_PATH)
                    )
                except CommonActionsMergeError as exc:
                    raise AOVMergeError(f"CommonActions: {exc}") from exc

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
                    "KernelLua.pkg.bytes", "StableSystems_3.pkg.bytes",
                    COMMON_ACTIONS_PATH, RES_SKIN_PATH, BATTLE_BANK_PATH,
                    LOBBY_BANK_PATH, SKILL_COMBINE_PATH,
                }
                for name in vtk_sources:
                    if name.startswith("Sound/Android/"):
                        skip_vtk.add(name)
                verification_included_paths = (
                    set(vtk_sources) - skip_vtk
                ) | {
                    COMMON_ACTIONS_PATH, RES_SKIN_PATH, BATTLE_BANK_PATH,
                    LOBBY_BANK_PATH, SKILL_COMBINE_PATH,
                }

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
