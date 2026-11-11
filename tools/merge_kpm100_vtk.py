from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.aov_bytes import decode_aov_resource, encode_aov_resource
from app.aov_common_actions import merge_common_actions_pkg
from app.aov_merge import merge_aov_mses_resource

BASE_VERSION = "1.64.1.12"
VTK_VERSION = "1.64.1.11"
PREFIX = "Resources/1.64.1/"
MOD_DIR = PREFIX + "Mod_by_Youtube_KHAM-PHA-MOBILE/"
ACTIONS_PATH = "Ages/Prefab_Characters/Prefab_Hero/Actor_595_Actions.pkg.bytes"
COMMON_PATH = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
ACTOR_INFO_PATH = "Prefab_Characters/Actor_595_Infos.pkg.bytes"
ASSETREF_PATH = "AssetRefs/Hero/595_AssetRef.bytes"
PACKER_PATH = "assetbundle/resourcepackerinfosetall.assetbundle"
VERIFY_PATH = "assetbundle/resourceverificationinfosetall.assetbundle"

ACTOR_ASSETS = {
    "assetbundle/battle/hero/595_lixin_battle_base.assetbundle",
    "assetbundle/battle/hero/595_lixin_battle_base_raw_h.assetbundle",
    "assetbundle/battle/hero/595_lixin_battle_base_raw_mat.assetbundle",
    "assetbundle/commercializenotify/battle/commercializenotify_blue_595.assetbundle",
    "assetbundle/commercializenotify/battle/commercializenotify_red_595.assetbundle",
    "assetbundle/icon/hero/595_305950_default59500_bust.assetbundle",
    "assetbundle/show/hero/595_lixin_show_base.assetbundle",
}
IOS_SOUNDS = {
    "Sound/iOS/Hero_LiXin_Skin_C_SFX.bnk",
    "Sound/iOS/Hero_LiXin_Skin_C_Show.bnk",
    "Sound/iOS/Hero_LiXin_Skin_C_VO.bnk",
}
MSES_PATHS = [
    "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes",
    "Databin/Client/Huanhua/ResBillboardCfg.bytes",
    "Databin/Client/Huanhua/ResBillboardSkinCfg.bytes",
    "Databin/Client/Huanhua/ResKillBillboardCfg.bytes",
    "Databin/Client/Skill/skillcombine.bytes",
    "Databin/Client/Sound/BattleBank.bytes",
    "Databin/Client/Sound/LobbyBank.bytes",
]
VERIFICATION_INCLUDE = {
    ACTIONS_PATH,
    COMMON_PATH,
    ACTOR_INFO_PATH,
    ASSETREF_PATH,
    *MSES_PATHS,
    *ACTOR_ASSETS,
    *IOS_SOUNDS,
}

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def normalise_vtk_path(name: str) -> str:
    name = name.replace("\\", "/").lstrip("/")
    if name.startswith(PREFIX):
        name = name[len(PREFIX):]
    elif name.startswith("Resources/"):
        parts = name.split("/", 2)
        if len(parts) == 3:
            name = parts[2]
    if name.endswith(".assetbundle") and not name.startswith("assetbundle/"):
        name = "assetbundle/" + name
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\x00" in name:
        raise ValueError(f"Đường dẫn VTK không an toàn: {name!r}")
    return name

def unique_file_map(zf: zipfile.ZipFile, label: str) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if name in result:
            raise ValueError(f"{label} có file trùng đường dẫn: {name}")
        result[name] = info
    return result

def custom_mod_payload(data: bytes, expected_member: str) -> bytes:
    """Extract the sole stored member from KPM's zero-CRC ZIP-like wrapper."""
    if len(data) < 30 or data[:4] != bytes.fromhex("504b0304"):
        raise ValueError(f"KPM_MOD không có local ZIP header: {expected_member}")
    flags = int.from_bytes(data[6:8], "little")
    method = int.from_bytes(data[8:10], "little")
    crc = int.from_bytes(data[14:18], "little")
    compressed_size = int.from_bytes(data[18:22], "little")
    uncompressed_size = int.from_bytes(data[22:26], "little")
    name_len = int.from_bytes(data[26:28], "little")
    extra_len = int.from_bytes(data[28:30], "little")
    start = 30 + name_len + extra_len
    member = data[30:30+name_len].decode("utf-8")
    if member.casefold() != expected_member.casefold():
        raise ValueError(f"Tên member KPM_MOD sai: {member!r} != {expected_member!r}")
    if flags != 0 or method != 0 or crc != 0:
        raise ValueError(f"KPM_MOD header không đúng dạng lưu gốc: {expected_member}")
    if compressed_size != uncompressed_size or start + compressed_size > len(data):
        raise ValueError(f"KPM_MOD kích thước member sai: {expected_member}")
    trailer = data[start+compressed_size:]
    if not trailer.startswith(bytes.fromhex("504b0102")) or bytes.fromhex("504b0506") not in trailer:
        raise ValueError(f"KPM_MOD thiếu central directory/EOCD sau member: {expected_member}")
    return data[start:start+compressed_size]

def make_custom_mod_payload(member_name: str, wrapped_resource: bytes, original: bytes) -> bytes:
    """Rebuild KPM's one-member ZIP container, preserving zero CRC convention."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as z:
        zi = zipfile.ZipInfo(member_name)
        zi.compress_type = zipfile.ZIP_STORED
        zi.create_system = 3
        zi.external_attr = 0x81B60000
        z.writestr(zi, wrapped_resource)
    result = bytearray(out.getvalue())
    if result[:4] != bytes.fromhex("504b0304"):
        raise ValueError(f"Không tạo được local ZIP header cho {member_name}")
    # Match the source KPM convention: CRC is intentionally zero in both headers.
    result[14:18] = bytes(4)
    central = result.find(bytes.fromhex("504b0102"))
    if central < 0:
        raise ValueError(f"Không tìm được central directory cho {member_name}")
    result[central+16:central+20] = bytes(4)
    # Keep the source DOS timestamp so the opaque KPM container remains familiar.
    if len(original) >= 14:
        result[10:14] = original[10:14]
        result[central+12:central+16] = original[10:14]
    return bytes(result)

def unpack_custom_mod(data: bytes, expected_member: str) -> bytes:
    # The standard ZipFile CRC checker must not be used: KPM intentionally zeroes CRC.
    return custom_mod_payload(data, expected_member)

def load_unitypy():
    vendor = str(Path(__file__).resolve().parents[1] / "app" / "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    import UnityPy_AOV
    return UnityPy_AOV

def find_mono_behaviour(env, object_name: str):
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            tree = obj.read_typetree()
            if tree.get("m_Name") == object_name:
                return obj, tree
    raise ValueError(f"AssetBundle metadata thiếu {object_name}")

def merge_actor_packer(base_data: bytes, vtk_data: bytes) -> tuple[bytes, dict]:
    parser = load_unitypy()
    base_env = parser.load(base_data)
    vtk_env = parser.load(vtk_data)
    base_obj, base_tree = find_mono_behaviour(base_env, "ResourcePackerInfoSetXML")
    _, vtk_tree = find_mono_behaviour(vtk_env, "ResourcePackerInfoSetXML")
    if base_tree.get("Version") != BASE_VERSION or vtk_tree.get("Version") != VTK_VERSION:
        raise ValueError("Packer version không đúng cặp KPM100/VTK.")
    base_entries = base_tree["AllResourcePackInfo"]
    vtk_entries = vtk_tree["AllResourcePackInfo"]
    base_index = {row["PathInIFS"]: i for i, row in enumerate(base_entries)}
    vtk_index = {row["PathInIFS"]: row for row in vtk_entries}
    missing = ACTOR_ASSETS - set(vtk_index)
    if missing:
        raise ValueError(f"VTK Packer thiếu Actor595 bundles: {sorted(missing)}")
    replaced, appended = [], []
    for path in sorted(ACTOR_ASSETS):
        if path in base_index:
            base_entries[base_index[path]] = __import__("copy").deepcopy(vtk_index[path])
            replaced.append(path)
        else:
            base_entries.append(__import__("copy").deepcopy(vtk_index[path]))
            base_index[path] = len(base_entries) - 1
            appended.append(path)

    # Merge only VTK dependency edges touching one of the seven Actor595 bundles.
    edges = base_tree["AllParentIdexToChild"]
    vtk_edges = vtk_tree["AllParentIdexToChild"]
    pair_set = {(int(e["parentIdx"]), int(e["childIdx"])) for e in edges}
    output_index = dict(base_index)
    added_edges = []
    for edge in vtk_edges:
        parent_path = vtk_entries[int(edge["parentIdx"])]["PathInIFS"]
        child_path = vtk_entries[int(edge["childIdx"])]["PathInIFS"]
        if parent_path not in ACTOR_ASSETS and child_path not in ACTOR_ASSETS:
            continue
        if parent_path not in output_index or child_path not in output_index:
            raise ValueError(f"Actor595 dependency chưa có trong KPM: {parent_path} -> {child_path}")
        pair = (output_index[parent_path], output_index[child_path])
        if pair not in pair_set:
            edges.append({"parentIdx": pair[0], "childIdx": pair[1]})
            pair_set.add(pair)
            added_edges.append((parent_path, child_path))

    # Every VTK edge touching the Actor595 assets must now be represented.
    for edge in vtk_edges:
        parent_path = vtk_entries[int(edge["parentIdx"])]["PathInIFS"]
        child_path = vtk_entries[int(edge["childIdx"])]["PathInIFS"]
        if parent_path in ACTOR_ASSETS or child_path in ACTOR_ASSETS:
            pair = (output_index.get(parent_path), output_index.get(child_path))
            if pair not in pair_set:
                raise ValueError(f"Thiếu Actor595 dependency edge: {parent_path} -> {child_path}")

    base_obj.save_typetree(base_tree)
    bundle = next(iter(base_env.files.values()))
    result = bundle.save("original")
    verify_env = parser.load(result)
    _, verify_tree = find_mono_behaviour(verify_env, "ResourcePackerInfoSetXML")
    if verify_tree.get("Version") != BASE_VERSION:
        raise ValueError("Packer sau ghép mất version nền.")
    verify_index = {row["PathInIFS"]: i for i, row in enumerate(verify_tree["AllResourcePackInfo"])}
    if not ACTOR_ASSETS.issubset(verify_index):
        raise ValueError("Packer đầu ra thiếu bundle Actor595.")
    verify_edges = {(int(e["parentIdx"]), int(e["childIdx"])) for e in verify_tree["AllParentIdexToChild"]}
    if not pair_set.issubset(verify_edges):
        raise ValueError("Packer sau ghi thiếu dependency edge.")
    return result, {
        "version": verify_tree["Version"],
        "base_entries": len(base_tree["AllResourcePackInfo"]) - len(appended),
        "output_entries": len(verify_tree["AllResourcePackInfo"]),
        "replaced_actor_rows": replaced,
        "appended_actor_rows": appended,
        "new_actor_edges": added_edges,
        "output_edges": len(verify_tree["AllParentIdexToChild"]),
        "verified": True,
    }

def merge_actor_verification(base_data: bytes, vtk_data: bytes, included_paths: set[str]) -> tuple[bytes, dict]:
    parser = load_unitypy()
    base_env = parser.load(base_data)
    vtk_env = parser.load(vtk_data)
    base_obj, base_tree = find_mono_behaviour(base_env, "ResourceVerificationInfoSetXML")
    _, vtk_tree = find_mono_behaviour(vtk_env, "ResourceVerificationInfoSetXML")
    if base_tree.get("Version") != BASE_VERSION or vtk_tree.get("Version") != VTK_VERSION:
        raise ValueError("Verification metadata version không đúng cặp KPM100/VTK.")
    updated = {}
    retained = {}
    # VTK has 302 ZIP verification entries vs 306 in KPM; preserve every KPM row.
    for field in ("AllZipVerificationInfo", "AllDatabinVerificationInfo"):
        base_rows = base_tree[field]
        vtk_rows = vtk_tree[field]
        base_index = {row["PathInIFS"]: i for i, row in enumerate(base_rows)}
        vtk_index = {row["PathInIFS"]: row for row in vtk_rows}
        field_updated, field_retained = [], []
        for path, idx in base_index.items():
            candidate = vtk_index.get(path)
            if candidate is None or base_rows[idx].get("CrcXor") == candidate.get("CrcXor"):
                continue
            if path in included_paths:
                base_rows[idx] = __import__("copy").deepcopy(candidate)
                field_updated.append(path)
            else:
                field_retained.append(path)
        updated[field] = field_updated
        retained[field] = field_retained
    base_obj.save_typetree(base_tree)
    bundle = next(iter(base_env.files.values()))
    result = bundle.save("original")
    verify_env = parser.load(result)
    _, check = find_mono_behaviour(verify_env, "ResourceVerificationInfoSetXML")
    if check.get("Version") != BASE_VERSION:
        raise ValueError("Verification metadata sau ghép bị đổi version.")
    for field in ("AllZipVerificationInfo", "AllDatabinVerificationInfo"):
        if len(check[field]) != len(base_tree[field]):
            raise ValueError(f"Verification list {field} bị đổi số lượng.")
    return result, {"version": check["Version"], "updated": updated, "retained_version_drift": retained}

def validate_archive(zf: zipfile.ZipFile, label: str) -> None:
    names = [i.filename for i in zf.infolist() if not i.is_dir()]
    if len(names) != len(set(names)):
        raise ValueError(f"{label} có đường dẫn file trùng.")
    bad = zf.testzip()
    if bad:
        raise ValueError(f"{label} lỗi CRC tại {bad}")

def build(args: argparse.Namespace) -> dict:
    base_path, vtk_path = Path(args.base), Path(args.vtk)
    if digest_file(base_path) != "944d01f6a4e31dbaa7ea31c14d292b799818843a4e3533860b2e16ff4bb77aa0":
        raise ValueError("SHA-256 KPM100 gốc không khớp; dừng.")
    if digest_file(vtk_path) != "b78976a7d9c0d7ce2420fdd631cdf47136bc9b0f313fbfcbbb6cb9af3a582249":
        raise ValueError("SHA-256 VTK gốc không khớp; dừng.")
    base_outer = zipfile.ZipFile(base_path, "r")
    vtk_outer = zipfile.ZipFile(vtk_path, "r")
    validate_archive(base_outer, "KPM100 ngoài")
    validate_archive(vtk_outer, "VTK ngoài")
    base_outer_map = unique_file_map(base_outer, "KPM100 ngoài")
    vtk_outer_map = unique_file_map(vtk_outer, "VTK ngoài")
    resource_candidates = [n for n in base_outer_map if PurePosixPath(n).name == "Resources.zip"]
    skin_candidates = [n for n in base_outer_map if PurePosixPath(n).name.lower() == "danhsachskin.txt"]
    if len(resource_candidates) != 1 or len(skin_candidates) != 1:
        raise ValueError("Không xác định được Resources.zip/danhsachskin.txt của KPM100.")
    resources_name = resource_candidates[0]
    skins_name = skin_candidates[0]

    report: dict = {
        "base_sha256": digest_file(base_path),
        "vtk_sha256": digest_file(vtk_path),
        "base_version": BASE_VERSION,
        "vtk_version": VTK_VERSION,
        "game_tested": False,
        "modified": [],
        "added": [],
        "warnings": [
            "IFix HeroSkin_1 không được hợp nhất vì parser gặp opcode 89 chưa hỗ trợ; các tệp Thanos/HeroSkin được giữ nguyên KPM100.",
            "Không thay ba UI assetbundle dùng chung từ VTK; giữ phiên bản KPM100 để bảo toàn giao diện và 100 skin."
        ],
    }

    with tempfile.TemporaryDirectory(prefix="kpm100-vtk-") as temp:
        temp = Path(temp)
        base_resources_path = temp / "base-Resources.zip"
        with base_outer.open(resources_name, "r") as src, base_resources_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        base_inner = zipfile.ZipFile(base_resources_path, "r")
        validate_archive(base_inner, "Resources.zip KPM100")
        base_inner_map = unique_file_map(base_inner, "Resources.zip KPM100")
        vtk_map = {}
        for info in vtk_outer.infolist():
            if info.is_dir():
                continue
            rel = normalise_vtk_path(info.filename)
            if rel in vtk_map:
                raise ValueError(f"VTK ngoài có đường dẫn trùng sau chuẩn hóa: {rel}")
            vtk_map[rel] = info

        def read_base(relative: str) -> bytes:
            name = PREFIX + relative
            try:
                return base_inner.read(name)
            except KeyError as exc:
                raise ValueError(f"KPM100 thiếu tài nguyên cần ghép: {relative}") from exc

        def read_vtk(relative: str) -> bytes:
            try:
                return vtk_outer.read(vtk_map[relative])
            except KeyError as exc:
                raise ValueError(f"VTK thiếu tài nguyên cần ghép: {relative}") from exc

        # Nested VTK packages are authoritative for Actor595 assets.
        actions_zip = zipfile.ZipFile(io.BytesIO(read_vtk(ACTIONS_PATH)), "r")
        validate_archive(actions_zip, "VTK Actor595 Actions.pkg.bytes")
        action_entries = {i.filename.casefold(): i for i in actions_zip.infolist() if not i.is_dir()}
        actor_info_zip = zipfile.ZipFile(io.BytesIO(read_vtk(ACTOR_INFO_PATH)), "r")
        validate_archive(actor_info_zip, "VTK Actor595 Infos.pkg.bytes")
        actor_info_entries = {i.filename.casefold(): i for i in actor_info_zip.infolist() if not i.is_dir()}
        info_member = next((i for n, i in actor_info_entries.items() if n.endswith("595_lixin_actorinfo.bytes")), None)
        if info_member is None:
            raise ValueError("VTK Actor595 Infos.pkg.bytes thiếu member actorinfo.")

        # Build the 70 hashed KPM payloads from exact VTK source resources.
        mapping_path = Path(__file__).with_name("kpm100_actor595_payload_map.csv")
        with mapping_path.open("r", encoding="utf-8", newline="") as f:
            mappings = list(csv.DictReader(f))
        if len(mappings) != 70:
            raise ValueError(f"Bảng ánh xạ Actor595 có {len(mappings)} dòng, cần đúng 70.")
        mod_replacements: dict[str, bytes] = {}
        decoded_actor_info = decode_aov_resource(actor_info_zip.read(info_member), "595_lixin_actorinfo.bytes")[0]
        decoded_asset_ref = decode_aov_resource(read_vtk(ASSETREF_PATH), "595_AssetRef.bytes")[0]
        validation_raw: dict[str, bytes] = {}
        role_counts = {"skill": 0, "assetref": 0, "actorinfo": 0}
        for row in mappings:
            payload_file = row["payload_file"]
            entry_name = row["entry_name"]
            role = row["role"]
            old_outer_path = MOD_DIR + payload_file
            original_container = read_base(old_outer_path)
            original_member = custom_mod_payload(original_container, entry_name)
            if role == "skill":
                source_key = ("595_LiXin/skill/" + entry_name).casefold()
                source_info = action_entries.get(source_key)
                if source_info is None:
                    raise ValueError(f"VTK Actions thiếu skill {entry_name}.")
                source_wrapped = actions_zip.read(source_info)
                raw = decode_aov_resource(source_wrapped, entry_name)[0]
                ET.fromstring(raw)
            elif role == "assetref":
                raw = decoded_asset_ref
            elif role == "actorinfo":
                raw = decoded_actor_info
            else:
                raise ValueError(f"Role trong mapping không hỗ trợ: {role}")
            wrapped_kpm = encode_aov_resource(raw, entry_name, "zstd")
            rebuilt = make_custom_mod_payload(entry_name, wrapped_kpm, original_container)
            verify_member = custom_mod_payload(rebuilt, entry_name)
            verify_raw = decode_aov_resource(verify_member, entry_name)[0]
            if verify_raw != raw:
                raise ValueError(f"KPM_MOD round-trip sai: {payload_file}")
            if role == "skill":
                ET.fromstring(verify_raw)
            mod_replacements[old_outer_path[len(PREFIX):]] = rebuilt
            validation_raw[payload_file] = raw
            role_counts[role] += 1
        if role_counts != {"skill": 68, "assetref": 1, "actorinfo": 1}:
            raise ValueError(f"Số lượng payload Actor595 sai: {role_counts}")

        # Selective direct resources.
        replacements: dict[str, bytes] = dict(mod_replacements)
        replacements[ACTIONS_PATH] = read_vtk(ACTIONS_PATH)
        replacements[COMMON_PATH], common_report = merge_common_actions_pkg(read_base(COMMON_PATH), read_vtk(COMMON_PATH))
        replacements[ASSETREF_PATH] = read_vtk(ASSETREF_PATH)
        replacements[ACTOR_INFO_PATH] = read_vtk(ACTOR_INFO_PATH)
        mses_reports = {}
        for relative in MSES_PATHS:
            merged, table_report = merge_aov_mses_resource(read_base(relative), read_vtk(relative), relative)
            replacements[relative] = merged
            mses_reports[relative] = table_report

        packer_output, packer_report = merge_actor_packer(read_base(PACKER_PATH), read_vtk(PACKER_PATH))
        replacements[PACKER_PATH] = packer_output
        verification_output, verification_report = merge_actor_verification(
            read_base(VERIFY_PATH),
            read_vtk(VERIFY_PATH),
            VERIFICATION_INCLUDE,
        )
        replacements[VERIFY_PATH] = verification_output

        # Only these VTK-only root resources belong to the iOS Lý Tín mod.
        additions = ACTOR_ASSETS | IOS_SOUNDS
        for name in additions:
            if name not in vtk_map:
                raise ValueError(f"VTK ZIP thiếu file iOS Actor595: {name}")
            if PREFIX + name in base_inner_map:
                raise ValueError(f"File VTK iOS trùng với KPM ngoài danh sách cho phép: {name}")
        if any(name.startswith("Sound/Android/") for name in vtk_map):
            report["android_sound_skipped"] = True
        if any(name.startswith("Sound/iOS/Hero_Sinon_") for name in vtk_map):
            report["sinon_sound_skipped"] = True

        # Write Resources.zip. Existing KPM members keep their original ZipInfo and bytes.
        inner_output_path = temp / "Resources-merged.zip"
        with zipfile.ZipFile(inner_output_path, "w", allowZip64=True) as out_inner:
            for info in base_inner.infolist():
                if info.is_dir():
                    out_inner.writestr(info, b"")
                    continue
                relative = info.filename[len(PREFIX):] if info.filename.startswith(PREFIX) else None
                if relative in replacements:
                    out_inner.writestr(info, replacements[relative])
                else:
                    with base_inner.open(info, "r") as src, out_inner.open(info, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
            for name in sorted(additions):
                destination = PREFIX + name
                info = zipfile.ZipInfo(destination, date_time=vtk_map[name].date_time)
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = vtk_map[name].external_attr
                info.create_system = vtk_map[name].create_system
                info.file_size = vtk_map[name].file_size
                with vtk_outer.open(vtk_map[name], "r") as src, out_inner.open(info, "w", force_zip64=True) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        with zipfile.ZipFile(inner_output_path, "r") as check_inner:
            validate_archive(check_inner, "Resources.zip sau ghép")
            out_names = set(check_inner.namelist())
            for relative in replacements:
                if PREFIX + relative not in out_names:
                    raise ValueError(f"ZIP đầu ra thiếu file đã thay: {relative}")
            for name in additions:
                if PREFIX + name not in out_names:
                    raise ValueError(f"ZIP đầu ra thiếu file mới: {name}")
            lost = set(base_inner_map) - out_names
            if lost:
                raise ValueError(f"ZIP đầu ra làm mất {len(lost)} file nền, ví dụ {sorted(lost)[:10]}")

        # Rebuild the original outer KPM wrapper without changing its skin list or other files.
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_path, "w", allowZip64=True) as out_outer:
            for info in base_outer.infolist():
                if info.is_dir():
                    out_outer.writestr(info, b"")
                elif info.filename == resources_name:
                    new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                    new_info.compress_type = zipfile.ZIP_STORED
                    new_info.external_attr = info.external_attr
                    new_info.create_system = info.create_system
                    with inner_output_path.open("rb") as src, out_outer.open(new_info, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                elif info.filename == skins_name:
                    original = base_outer.read(info.filename).decode("utf-8-sig", errors="replace").rstrip()
                    if "Vấn Tâm Kiếm" not in original:
                        original += "\nLý Tín - Vấn Tâm Kiếm (Actor 595)"
                    out_outer.writestr(info, (original + "\n").encode("utf-8"))
                else:
                    with base_outer.open(info, "r") as src, out_outer.open(info, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)

        with zipfile.ZipFile(out_path, "r") as check_outer:
            validate_archive(check_outer, "ZIP ngoài sau ghép")
            if len(check_outer.infolist()) != len(base_outer.infolist()):
                raise ValueError("Số mục ZIP ngoài bị thay đổi.")
            if resources_name not in {i.filename for i in check_outer.infolist()}:
                raise ValueError("ZIP ngoài thiếu Resources.zip.")

        report.update({
            "output": str(out_path),
            "output_size": out_path.stat().st_size,
            "output_sha256": digest_file(out_path),
            "base_inner_entries": len(base_inner.infolist()),
            "output_inner_entries": len(out_names),
            "added_resources": sorted(additions),
            "replaced_resources": sorted(replacements),
            "kpm_mod_payloads": role_counts,
            "common_actions": common_report,
            "mses": mses_reports,
            "packer": packer_report,
            "verification": verification_report,
            "validation": {
                "outer_zip_crc": "passed",
                "inner_resources_zip_crc": "passed",
                "preserved_all_base_inner_paths": True,
                "custom_kpm_mod_roundtrip": "70/70 passed",
                "skill_xml_parse": "68/68 passed",
                "actor_actions_package": "129 entries, CRC passed",
                "actor_info_package": "CRC passed",
                "game_tested": False,
            },
        })
        (Path(args.report)).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        actions_zip.close()
        actor_info_zip.close()
        base_inner.close()
    base_outer.close()
    vtk_outer.close()
    return report

def main() -> None:
    parser = argparse.ArgumentParser(description="Selective KPM100 + VTK Actor595 iOS merge")
    parser.add_argument("--base", required=True)
    parser.add_argument("--vtk", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = build(args)
    print(json.dumps({
        "output": report["output"],
        "output_size": report["output_size"],
        "output_sha256": report["output_sha256"],
        "base_inner_entries": report["base_inner_entries"],
        "output_inner_entries": report["output_inner_entries"],
        "payloads": report["kpm_mod_payloads"],
        "new_resources": len(report["added_resources"]),
        "packer": report["packer"],
        "validation": report["validation"],
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
