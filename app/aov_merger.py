from __future__ import annotations

import copy
import hashlib
import io
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from .aov_bytes import AOVBytesError, ZSTD_MAGIC, decode_aov_resource, encode_aov_resource
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


def _normalise_vtk_path(name: str) -> str:
    name = name.replace("\\", "/").lstrip("/")
    if name.endswith(".assetbundle") and not name.startswith("assetbundle/"):
        name = "assetbundle/" + name
    return name


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


def _decode_package_entry(data: bytes, name: str) -> tuple[bytes, str]:
    # CommonActions contains raw XML, Zstandard, and occasionally AES-wrapped entries.
    if data[:3] == b"\x22\x4a\x67" or (
        data[:2] == b"\x22\x4a" and data.find(ZSTD_MAGIC) >= 0
    ):
        return _decode(data, name)
    return data, "raw"


def _merge_bank(base_data: bytes, vtk_data: bytes, resource_name: str,
                required_vtk_strings: tuple[bytes, ...]) -> bytes:
    base_raw, base_mode = _decode(base_data, resource_name)
    vtk_raw, vtk_mode = _decode(vtk_data, resource_name)
    if not base_raw.startswith(b"MSES") or not vtk_raw.startswith(b"MSES"):
        raise AOVMergeError(f"{resource_name} không có header MSES hợp lệ.")
    if base_raw[8:16] != vtk_raw[8:16]:
        raise AOVMergeError(f"Schema hoặc số dòng {resource_name} không tương thích.")
    if any(value not in vtk_raw for value in required_vtk_strings):
        raise AOVMergeError(f"{resource_name} thiếu chuỗi âm thanh Vấn Tâm Kiếm cần thiết.")
    # The VTK table is the same schema and changes the LiXin sound aliases only.
    result = _encode(vtk_raw, resource_name, base_mode)
    decoded, decoded_mode = _decode(result, resource_name)
    if decoded != vtk_raw or decoded_mode != base_mode:
        raise AOVMergeError(f"Không xác minh được dữ liệu sau khi mã hóa {resource_name}.")
    return result


def _merge_skillcombine(base_data: bytes, vtk_data: bytes) -> bytes:
    base_raw, base_mode = _decode(base_data, "skillcombine")
    vtk_raw, _ = _decode(vtk_data, "skillcombine")
    if not base_raw.startswith(b"MSES") or not vtk_raw.startswith(b"MSES"):
        raise AOVMergeError("skillcombine không có header MSES hợp lệ.")
    if len(base_raw) < 140 or len(vtk_raw) < 140:
        raise AOVMergeError("Header skillcombine quá ngắn.")
    base_rows = int.from_bytes(base_raw[12:16], "little")
    vtk_rows = int.from_bytes(vtk_raw[12:16], "little")
    if base_rows != 14608 or vtk_rows != 14613:
        raise AOVMergeError("Số dòng skillcombine không đúng phiên bản được hỗ trợ.")
    base_body = base_raw[140:]
    vtk_body = vtk_raw[140:]
    if not vtk_body.startswith(base_body):
        raise AOVMergeError("skillcombine VTK không giữ nguyên dữ liệu nền 1.64.1.12.")
    # Keep the newer base schema/header and append the five VTK rows.
    merged = bytearray(base_raw[:140] + vtk_body)
    merged[12:16] = vtk_rows.to_bytes(4, "little")
    merged[96:128] = hashlib.md5(merged[140:]).hexdigest().encode("ascii")
    encoded = _encode(bytes(merged), "skillcombine", base_mode)
    check, _ = _decode(encoded, "skillcombine")
    if check != bytes(merged):
        raise AOVMergeError("Kiểm tra lại skillcombine sau mã hóa thất bại.")
    return encoded


def _parse_label_rows(raw: bytes) -> list[tuple[int, bytes]]:
    if not raw.startswith(b"MSES") or len(raw) < 140:
        raise AOVMergeError("ResSkinSeniorLabelCfg không có header MSES hợp lệ.")
    count = int.from_bytes(raw[12:16], "little")
    offset = 140
    rows: list[tuple[int, bytes]] = []
    keys: set[int] = set()
    if count > 100_000:
        raise AOVMergeError("Số dòng ResSkinSeniorLabelCfg không hợp lệ.")
    for _ in range(count):
        if offset + 12 > len(raw):
            raise AOVMergeError("ResSkinSeniorLabelCfg bị cắt cụt.")
        key = int.from_bytes(raw[offset + 4:offset + 8], "little")
        text_length = int.from_bytes(raw[offset + 8:offset + 12], "little")
        size = 32 + text_length
        row = raw[offset:offset + size]
        if len(row) != size:
            raise AOVMergeError("Dòng ResSkinSeniorLabelCfg bị cắt cụt.")
        if key in keys:
            raise AOVMergeError(f"ResSkinSeniorLabelCfg có ID trùng: {key}.")
        keys.add(key)
        rows.append((key, row))
        offset += size
    if offset != len(raw):
        raise AOVMergeError("ResSkinSeniorLabelCfg có dữ liệu dư không nhận diện được.")
    return rows


def _merge_res_skin(base_data: bytes, vtk_data: bytes) -> bytes:
    base_raw, base_mode = _decode(base_data, "ResSkinSeniorLabelCfg")
    vtk_raw, _ = _decode(vtk_data, "ResSkinSeniorLabelCfg")
    base_rows = _parse_label_rows(base_raw)
    vtk_rows = _parse_label_rows(vtk_raw)
    base_keys = {key for key, _ in base_rows}
    new_rows = [row for key, row in vtk_rows if key not in base_keys]
    if [int.from_bytes(row[4:8], "little") for row in new_rows] != [59500]:
        raise AOVMergeError("ResSkinSeniorLabelCfg không có đúng một dòng mới cho skin 59500.")
    merged = bytearray(base_raw[:140] + b"".join(row for _, row in base_rows) + b"".join(new_rows))
    merged[12:16] = (len(base_rows) + len(new_rows)).to_bytes(4, "little")
    merged[96:128] = hashlib.md5(merged[140:]).hexdigest().encode("ascii")
    encoded = _encode(bytes(merged), "ResSkinSeniorLabelCfg", base_mode)
    check, _ = _decode(encoded, "ResSkinSeniorLabelCfg")
    if len(_parse_label_rows(check)) != len(base_rows) + 1:
        raise AOVMergeError("Kiểm tra ResSkinSeniorLabelCfg sau ghép thất bại.")
    return encoded


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
    if len(merged["fixes"]) != 17 or len(merged["methods"]) != 25:
        raise AOVMergeError("Số FixInfo/method của HeroSkin sau ghép không đúng.")
    encoded = _encode(raw, "HeroSkin_1", base_mode)
    check, _ = _decode(encoded, "HeroSkin_1")
    parsed = parse_ifix_patch(check)
    if len(parsed["fixes"]) != 17 or len(parsed["methods"]) != 25:
        raise AOVMergeError("Không xác minh được IFix HeroSkin sau mã hóa.")
    return encoded, len(parsed["fixes"]), len(parsed["methods"])


def _merge_common_action_xml(base_raw: bytes, vtk_raw: bytes) -> tuple[bytes, list[str]]:
    try:
        base_root = ET.fromstring(base_raw)
        vtk_root = ET.fromstring(vtk_raw)
    except ET.ParseError as exc:
        raise AOVMergeError(f"CommonActions có XML lỗi: {exc}") from exc
    base_action = base_root.find("Action")
    vtk_action = vtk_root.find("Action")
    if base_action is None or vtk_action is None:
        raise AOVMergeError("CommonActions thiếu node Action.")

    def merge_root_list(tag: str, key_attr: str) -> None:
        base_list = base_root.find(tag)
        vtk_list = vtk_root.find(tag)
        if base_list is None or vtk_list is None:
            return
        keys = {item.attrib.get(key_attr) for item in list(base_list)}
        for item in list(vtk_list):
            key = item.attrib.get(key_attr)
            if key not in keys:
                base_list.append(copy.deepcopy(item))
                keys.add(key)

    merge_root_list("TemplateObjectList", "id")
    merge_root_list("RefParamList", "name")

    existing = {track.attrib.get("trackName") for track in base_action.findall("Track")}
    added: list[str] = []
    for track in vtk_action.findall("Track"):
        if not any(
            item.tag == "SkinOrAvatarList" and item.attrib.get("id") == "59500"
            for item in track.iter()
        ):
            continue
        name = track.attrib.get("trackName")
        if not name or name in existing:
            raise AOVMergeError(f"CommonActions có track trùng hoặc thiếu tên: {name!r}.")
        clone = copy.deepcopy(track)
        for parent in clone.iter():
            for child in list(parent):
                if child.tag == "SkinOrAvatarList" and child.attrib.get("id") != "59500":
                    parent.remove(child)
        if not any(
            item.tag == "SkinOrAvatarList" and item.attrib.get("id") == "59500"
            for item in clone.iter()
        ):
            continue
        base_action.append(clone)
        existing.add(name)
        added.append(name)

    try:
        base_length = float(base_action.attrib.get("length", "0"))
        vtk_length = float(vtk_action.attrib.get("length", "0"))
        base_action.set("length", f"{max(base_length, vtk_length):.3f}")
    except ValueError:
        pass

    output = ET.tostring(base_root, encoding="utf-8", xml_declaration=True)
    if base_raw.endswith(b"\n"):
        output += b"\n"
    return output, added


def _merge_common_actions(base_data: bytes, vtk_data: bytes) -> tuple[bytes, int, int]:
    if not zipfile.is_zipfile(io.BytesIO(base_data)) or not zipfile.is_zipfile(io.BytesIO(vtk_data)):
        raise AOVMergeError("CommonActions.pkg.bytes không phải ZIP hợp lệ.")
    base_zip = zipfile.ZipFile(io.BytesIO(base_data))
    vtk_zip = zipfile.ZipFile(io.BytesIO(vtk_data))
    output = io.BytesIO()
    modified_count = 0
    added_track_count = 0
    vtk_names = set(vtk_zip.namelist())
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as merged_zip:
            base_names = set(base_zip.namelist())
            for info in base_zip.infolist():
                if info.is_dir():
                    merged_zip.writestr(info, b"")
                    continue
                data = base_zip.read(info.filename)
                if info.filename.endswith(".xml") and info.filename in vtk_names:
                    base_raw, base_mode = _decode_package_entry(data, info.filename)
                    vtk_raw, _ = _decode_package_entry(vtk_zip.read(info.filename), info.filename)
                    merged_raw, tracks = _merge_common_action_xml(base_raw, vtk_raw)
                    if tracks:
                        data = _encode(merged_raw, info.filename, base_mode) if base_mode != "raw" else merged_raw
                        modified_count += 1
                        added_track_count += len(tracks)
                merged_zip.writestr(info, data)
            for info in vtk_zip.infolist():
                if info.is_dir() or info.filename in base_names:
                    continue
                merged_zip.writestr(info, vtk_zip.read(info.filename))
    finally:
        base_zip.close()
        vtk_zip.close()
    if modified_count != 6 or added_track_count != 29:
        raise AOVMergeError(
            f"CommonActions delta không đúng: {modified_count} file, {added_track_count} track."
        )
    result = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as check:
        if check.testzip() is not None:
            raise AOVMergeError("CommonActions ZIP sau ghép bị lỗi CRC.")
    return result, modified_count, added_track_count


def _find_mono_behaviour(env: Any, name: str):
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            tree = obj.read_typetree()
            if tree.get("m_Name") == name:
                return obj, tree
    raise AOVMergeError(f"AssetBundle metadata thiếu object {name}.")


def _merge_packer_metadata(base_data: bytes, vtk_data: bytes,
                           verification_base: bytes, verification_vtk: bytes,
                           asset_paths: set[str]) -> tuple[bytes, int, int]:
    UnityPy_AOV = _load_unitypy_aov()
    try:
        base_env = UnityPy_AOV.load(base_data)
        vtk_env = UnityPy_AOV.load(vtk_data)
        verify_base_env = UnityPy_AOV.load(verification_base)
        verify_vtk_env = UnityPy_AOV.load(verification_vtk)
        base_obj, base_tree = _find_mono_behaviour(base_env, "ResourcePackerInfoSetXML")
        _, vtk_tree = _find_mono_behaviour(vtk_env, "ResourcePackerInfoSetXML")
        _, base_verify = _find_mono_behaviour(verify_base_env, "ResourceVerificationInfoSetXML")
        _, vtk_verify = _find_mono_behaviour(verify_vtk_env, "ResourceVerificationInfoSetXML")
    except AOVMergeError:
        raise
    except Exception as exc:
        raise AOVMergeError(f"Không đọc được AssetBundle metadata: {type(exc).__name__}: {exc}") from exc

    if base_tree.get("Version") != BASE_VERSION or vtk_tree.get("Version") != VTK_VERSION:
        raise AOVMergeError("Phiên bản ResourcePackerInfoSetXML không đúng cặp 1.64.1.12/1.64.1.11.")
    if base_verify.get("AllZipVerificationInfo") != vtk_verify.get("AllZipVerificationInfo"):
        raise AOVMergeError("Danh sách ZipVerification giữa hai phiên bản không khớp.")
    if base_verify.get("AllDatabinVerificationInfo") != vtk_verify.get("AllDatabinVerificationInfo"):
        raise AOVMergeError("Danh sách DatabinVerification giữa hai phiên bản không khớp.")

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
    return merged, replaced, len(new_paths)


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
        archives: list[tuple[str, zipfile.ZipFile]] = []
        for path in archive_paths:
            try:
                zf = zipfile.ZipFile(path)
                bad = zf.testzip()
                if bad:
                    zf.close()
                    raise AOVMergeError(f"ZIP đầu vào lỗi CRC tại {bad}: {Path(path).name}")
            except Exception as exc:
                raise AOVMergeError(f"File đầu vào không phải ZIP hợp lệ: {Path(path).name}") from exc
            opened.append(zf)
            archives.append((path, zf))

        base_candidates = []
        for path, zf in archives:
            names = zf.namelist()
            if any(name.endswith("/Resources.zip") or name == "Resources.zip" for name in names) and any(
                (name.endswith("/danhsachskin.txt") or name == "danhsachskin.txt") for name in names
            ):
                base_candidates.append((path, zf))
        if len(base_candidates) != 1:
            raise AOVMergeError("Không xác định được duy nhất ZIP nền có Resources.zip và danhsachskin.txt.")
        base_path, base_outer = base_candidates[0]
        vtk_archives = [(path, zf) for path, zf in archives if path != base_path]
        if not vtk_archives:
            raise AOVMergeError("Chưa có ZIP Vấn Tâm Kiếm.")

        resources_entries = [
            info for info in base_outer.infolist()
            if info.filename.endswith("/Resources.zip") or info.filename == "Resources.zip"
        ]
        skin_entries = [info for info in base_outer.infolist() if info.filename.endswith("/danhsachskin.txt") or info.filename == "danhsachskin.txt"]
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
                    previous = vtk_sources[name][1]
                    if previous.CRC != info.CRC or previous.file_size != info.file_size:
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
                replacements[COMMON_ACTIONS_PATH], common_files, common_tracks = _merge_common_actions(
                    read_base(COMMON_ACTIONS_PATH), read_vtk(COMMON_ACTIONS_PATH)
                )
                replacements[RES_SKIN_PATH] = _merge_res_skin(
                    read_base(RES_SKIN_PATH), read_vtk(RES_SKIN_PATH)
                )
                replacements[BATTLE_BANK_PATH] = _merge_bank(
                    read_base(BATTLE_BANK_PATH), read_vtk(BATTLE_BANK_PATH), "BattleBank",
                    (b"Hero_LiXin_Skin_C_SFX", b"Hero_LiXin_Skin_C_VO"),
                )
                replacements[LOBBY_BANK_PATH] = _merge_bank(
                    read_base(LOBBY_BANK_PATH), read_vtk(LOBBY_BANK_PATH), "LobbyBank",
                    (b"Hero_LiXin_Skin_C_Show",),
                )
                replacements[SKILL_COMBINE_PATH] = _merge_skillcombine(
                    read_base(SKILL_COMBINE_PATH), read_vtk(SKILL_COMBINE_PATH)
                )
                replacements[PACKER_PATH] = _merge_packer_metadata(
                    read_base(PACKER_PATH),
                    read_vtk(PACKER_PATH),
                    read_base(VERIFICATION_PATH),
                    read_vtk(VERIFICATION_PATH),
                    asset_paths,
                )[0]

                skip_vtk = {
                    PACKER_PATH, VERIFICATION_PATH, HERO_SKIN_VTK_PATH,
                    "KernelLua.pkg.bytes", "StableSystems_3.pkg.bytes",
                    COMMON_ACTIONS_PATH, RES_SKIN_PATH, BATTLE_BANK_PATH,
                    LOBBY_BANK_PATH, SKILL_COMBINE_PATH,
                }
                for name in vtk_sources:
                    if name.startswith("Sound/Android/"):
                        skip_vtk.add(name)

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
                    bad = check_inner.testzip()
                    if bad:
                        raise AOVMergeError(f"Resources.zip sau ghép lỗi CRC tại {bad}")
                    final_names = set(check_inner.namelist())
                    for relative in replacements:
                        if BASE_PREFIX + relative not in final_names:
                            raise AOVMergeError(f"ZIP kết quả thiếu file đã ghép: {relative}")
                    for asset_path in asset_paths:
                        if BASE_PREFIX + asset_path not in final_names:
                            raise AOVMergeError(f"ZIP kết quả thiếu assetbundle: {asset_path}")

                # Rebuild the original wrapper, retaining its folder layout and skin list.
                output_path_obj = Path(output_path)
                output_path_obj.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(output_path_obj, "w") as out_outer:
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

                with zipfile.ZipFile(output_path_obj) as check_outer:
                    bad = check_outer.testzip()
                    if bad:
                        raise AOVMergeError(f"ZIP ngoài sau ghép lỗi CRC tại {bad}")

                packer_env = _load_unitypy_aov().load(replacements[PACKER_PATH])
                _, packer_tree = _find_mono_behaviour(packer_env, "ResourcePackerInfoSetXML")
                return {
                    "output": str(output_path_obj),
                    "base_version": BASE_VERSION,
                    "vtk_version": VTK_VERSION,
                    "ifix_fixes": fix_count,
                    "ifix_methods": method_count,
                    "common_actions_files": common_files,
                    "common_actions_tracks": common_tracks,
                    "assetbundles": len(asset_paths),
                    "new_files": len(added_files),
                    "packer_entries": len(packer_tree["AllResourcePackInfo"]),
                    "validation": "ZIP/CRC, IFix index, MSES tables, CommonActions XML and PackerInfo passed",
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
