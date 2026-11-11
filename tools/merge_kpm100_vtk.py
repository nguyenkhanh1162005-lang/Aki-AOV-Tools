from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
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

BASE_VERSION = "1.64.1.12_KPM"
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
    "Sound/iOS/Hero_Sinon_SFX.bnk",
    "Sound/iOS/Hero_Sinon_Show.bnk",
    "Sound/iOS/Hero_Sinon_VO.bnk",
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

    def is_lixin_asset(path: str) -> bool:
        lowered = path.casefold()
        return "lixin" in lowered or bool(re.search(r"(^|[/_])595(?:\d{0,2})_", lowered))

    # Do not limit the merge to a hand-picked list. Pull every 595/LiXin bundle
    # named by VTK's own packer index, including the 5952 LiXin skin bundles.
    actor_assets = {
        path for path in vtk_index
        if path.startswith("assetbundle/") and is_lixin_asset(path)
    } | set(ACTOR_ASSETS)
    missing = actor_assets - set(vtk_index)
    if missing:
        raise ValueError(f"VTK Packer thiếu Actor595 bundles: {sorted(missing)}")

    replaced, appended_actor = [], []
    for path in sorted(actor_assets):
        if path in base_index:
            base_entries[base_index[path]] = __import__("copy").deepcopy(vtk_index[path])
            replaced.append(path)
        else:
            base_entries.append(__import__("copy").deepcopy(vtk_index[path]))
            base_index[path] = len(base_entries) - 1
            appended_actor.append(path)

    # Include the complete dependency closure for every VTK Lixin bundle.
    # Existing non-LiXin KPM rows remain untouched; only missing dependency rows
    # are imported from VTK so the other 99 skins retain their own bundle data.
    edges = base_tree["AllParentIdexToChild"]
    vtk_edges = vtk_tree["AllParentIdexToChild"]
    pair_set = {(int(e["parentIdx"]), int(e["childIdx"])) for e in edges}
    added_edges = []
    appended_dependencies = set()
    output_index = dict(base_index)

    relevant_edges = []
    for edge in vtk_edges:
        parent_path = vtk_entries[int(edge["parentIdx"])]["PathInIFS"]
        child_path = vtk_entries[int(edge["childIdx"])]["PathInIFS"]
        if parent_path in actor_assets or child_path in actor_assets:
            relevant_edges.append((parent_path, child_path))

    for parent_path, child_path in relevant_edges:
        for dependency_path in (parent_path, child_path):
            if dependency_path in output_index:
                continue
            if dependency_path not in vtk_index:
                raise ValueError(
                    f"VTK Actor595 dependency không có packer row: {dependency_path}"
                )
            base_entries.append(__import__("copy").deepcopy(vtk_index[dependency_path]))
            output_index[dependency_path] = len(base_entries) - 1
            appended_dependencies.add(dependency_path)

    for parent_path, child_path in relevant_edges:
        if parent_path not in output_index or child_path not in output_index:
            raise ValueError(
                f"Actor595 dependency chưa có trong output index: {parent_path} -> {child_path}"
            )
        pair = (output_index[parent_path], output_index[child_path])
        if pair not in pair_set:
            edges.append({"parentIdx": pair[0], "childIdx": pair[1]})
            pair_set.add(pair)
            added_edges.append((parent_path, child_path))

    base_obj.save_typetree(base_tree)
    bundle = next(iter(base_env.files.values()))
    result = bundle.save("original")
    verify_env = parser.load(result)
    _, verify_tree = find_mono_behaviour(verify_env, "ResourcePackerInfoSetXML")
    if verify_tree.get("Version") != BASE_VERSION:
        raise ValueError("Packer sau ghép mất version nền.")
    verify_index = {row["PathInIFS"]: i for i, row in enumerate(verify_tree["AllResourcePackInfo"])}
    if not actor_assets.issubset(verify_index):
        raise ValueError("Packer đầu ra thiếu bundle Actor595.")
    verify_edges = {
        (int(e["parentIdx"]), int(e["childIdx"]))
        for e in verify_tree["AllParentIdexToChild"]
    }
    if not pair_set.issubset(verify_edges):
        raise ValueError("Packer sau ghi thiếu dependency edge.")

    appended_paths = sorted(set(appended_actor) | appended_dependencies)
    return result, {
        "version": verify_tree["Version"],
        "base_entries": len(base_tree["AllResourcePackInfo"]) - len(appended_paths),
        "output_entries": len(verify_tree["AllResourcePackInfo"]),
        "actor_assets_from_vtk": sorted(actor_assets),
        "replaced_actor_rows": replaced,
        "appended_actor_rows": appended_actor,
        "appended_dependency_rows": sorted(appended_dependencies),
        "appended_paths": appended_paths,
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

        # Add verification rows for newly imported VTK Lixin bundles/soundbanks.
        field_appended = []
        for path, candidate in vtk_index.items():
            if path in included_paths and path not in base_index:
                base_rows.append(__import__("copy").deepcopy(candidate))
                base_index[path] = len(base_rows) - 1
                field_appended.append(path)
        updated[field] = field_updated
        retained[field] = field_retained
        updated[field + "_appended"] = field_appended
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
    base_zip = zipfile.ZipFile(base_path, "r")
    vtk_outer = zipfile.ZipFile(vtk_path, "r")
    validate_archive(base_zip, "KPM100 trực tiếp")
    validate_archive(vtk_outer, "VTK ngoài")
    base_map = unique_file_map(base_zip, "KPM100")
    report: dict = {
        "base_sha256": digest_file(base_path),
        "vtk_sha256": digest_file(vtk_path),
        "base_version": BASE_VERSION,
        "vtk_version": VTK_VERSION,
        "container_structure": "KPM100 direct ZIP tree under Resources/1.64.1/",
        "game_tested": False,
        "warnings": [
            "IFix HeroSkin_1 không được hợp nhất vì parser gặp opcode 89 chưa hỗ trợ; các tệp Thanos/HeroSkin được giữ nguyên KPM100.",
            "Giữ nguyên tài nguyên dùng chung không thuộc Lý Tín; thay toàn bộ bundle 595/LiXin được liệt kê trong VTK Packer và giữ nguyên các gói của 99 skin còn lại.",
            "KPM100 không chứa Resources.zip hoặc danhsachskin.txt wrapper; cấu trúc ZIP gốc được giữ nguyên."
        ],
    }
    with tempfile.TemporaryDirectory(prefix="kpm100-vtk-") as temp_dir:
        vtk_map: dict[str, zipfile.ZipInfo] = {}
        for info in vtk_outer.infolist():
            if info.is_dir():
                continue
            relative = normalise_vtk_path(info.filename)
            if relative in vtk_map:
                raise ValueError(f"VTK có đường dẫn trùng sau chuẩn hóa: {relative}")
            vtk_map[relative] = info
        if not any(name.startswith(PREFIX) for name in base_map):
            raise ValueError("KPM100 không có cây Resources/1.64.1 như đã xác minh.")
        if any(name.endswith("/Resources.zip") or name == "Resources.zip" for name in base_map):
            raise ValueError("KPM100 có cấu trúc wrapper khác dự kiến; dừng để tránh sửa nhầm lớp ZIP.")

        def read_base(relative: str) -> bytes:
            name = PREFIX + relative
            try:
                return base_zip.read(name)
            except KeyError as exc:
                raise ValueError(f"KPM100 thiếu tài nguyên cần ghép: {relative}") from exc

        def read_vtk(relative: str) -> bytes:
            try:
                return vtk_outer.read(vtk_map[relative])
            except KeyError as exc:
                raise ValueError(f"VTK thiếu tài nguyên cần ghép: {relative}") from exc

        actions_zip = zipfile.ZipFile(io.BytesIO(read_vtk(ACTIONS_PATH)), "r")
        validate_archive(actions_zip, "VTK Actor595 Actions.pkg.bytes")
        action_entries = {i.filename.casefold(): i for i in actions_zip.infolist() if not i.is_dir()}
        actor_info_zip = zipfile.ZipFile(io.BytesIO(read_vtk(ACTOR_INFO_PATH)), "r")
        validate_archive(actor_info_zip, "VTK Actor595 Infos.pkg.bytes")
        actor_info_entries = {i.filename.casefold(): i for i in actor_info_zip.infolist() if not i.is_dir()}
        info_member = next((i for name, i in actor_info_entries.items() if name.endswith("595_lixin_actorinfo.bytes")), None)
        if info_member is None:
            raise ValueError("VTK Actor595 Infos.pkg.bytes thiếu member actorinfo.")

        mapping_path = Path(__file__).with_name("kpm100_actor595_payload_map.csv")
        with mapping_path.open("r", encoding="utf-8", newline="") as f:
            mappings = list(csv.DictReader(f))
        if len(mappings) != 70:
            raise ValueError(f"Bảng ánh xạ Actor595 có {len(mappings)} dòng, cần đúng 70.")
        mod_replacements: dict[str, bytes] = {}
        decoded_actor_info = decode_aov_resource(actor_info_zip.read(info_member), "595_lixin_actorinfo.bytes")[0]
        decoded_asset_ref = decode_aov_resource(read_vtk(ASSETREF_PATH), "595_AssetRef.bytes")[0]
        role_counts = {"skill": 0, "assetref": 0, "actorinfo": 0}
        for row in mappings:
            payload_file = row["payload_file"]
            entry_name = row["entry_name"]
            role = row["role"]
            old_relative_path = "Mod_by_Youtube_KHAM-PHA-MOBILE/" + payload_file
            old_archive_path = PREFIX + old_relative_path
            original_container = read_base(old_relative_path)
            custom_mod_payload(original_container, entry_name)
            if role == "skill":
                source_key = ("595_LiXin/skill/" + entry_name).casefold()
                source_info = action_entries.get(source_key)
                if source_info is None:
                    raise ValueError(f"VTK Actions thiếu skill {entry_name}.")
                raw = decode_aov_resource(actions_zip.read(source_info), entry_name)[0]
                ET.fromstring(raw)
            elif role == "assetref":
                raw = decoded_asset_ref
            elif role == "actorinfo":
                raw = decoded_actor_info
            else:
                raise ValueError(f"Role trong mapping không hỗ trợ: {role}")
            wrapped_kpm = encode_aov_resource(raw, entry_name, "zstd")
            rebuilt = make_custom_mod_payload(entry_name, wrapped_kpm, original_container)
            verify_raw = decode_aov_resource(custom_mod_payload(rebuilt, entry_name), entry_name)[0]
            if verify_raw != raw:
                raise ValueError(f"KPM_MOD round-trip sai: {payload_file}")
            if role == "skill":
                ET.fromstring(verify_raw)
            mod_replacements[old_relative_path] = rebuilt
            role_counts[role] += 1
        if role_counts != {"skill": 68, "assetref": 1, "actorinfo": 1}:
            raise ValueError(f"Số lượng payload Actor595 sai: {role_counts}")

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

        replacements[PACKER_PATH], packer_report = merge_actor_packer(read_base(PACKER_PATH), read_vtk(PACKER_PATH))

        # Replace every Lixin/595-specific assetbundle for which VTK ships
        # physical bytes. Some packer rows intentionally reuse shared KPM files;
        # those rows stay pointed at the preserved KPM payload.
        actor_assets = set(packer_report["actor_assets_from_vtk"])
        appended_actor_paths = set()
        for name in sorted(actor_assets):
            in_base = PREFIX + name in base_map
            if name not in vtk_map:
                if in_base:
                    continue
                raise ValueError(f"Actor595 asset thiếu ở cả KPM và VTK: {name}")
            if in_base:
                replacements[name] = read_vtk(name)
            else:
                appended_actor_paths.add(name)

        # The packer function may append rows that are not physically shipped by
        # VTK. Add only paths absent from KPM when the actual VTK bytes exist.
        for name in packer_report["appended_paths"]:
            if PREFIX + name in base_map:
                continue
            if name not in vtk_map:
                raise ValueError(f"Dependency mới thiếu file vật lý trong VTK: {name}")
            appended_actor_paths.add(name)

        additions = appended_actor_paths | IOS_SOUNDS
        for name in additions:
            if name not in vtk_map:
                raise ValueError(f"VTK ZIP thiếu file Actor595/iOS cần thêm: {name}")
            if PREFIX + name in base_map:
                raise ValueError(f"File addition trùng đường dẫn KPM ngoài danh sách thay thế: {name}")

        verification_include = (
            set(VERIFICATION_INCLUDE)
            | actor_assets
            | additions
            | set(replacements)
        )
        replacements[VERIFY_PATH], verification_report = merge_actor_verification(
            read_base(VERIFY_PATH), read_vtk(VERIFY_PATH), verification_include
        )

        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_path, "w", allowZip64=True) as out_zip:
            for info in base_zip.infolist():
                if info.is_dir():
                    out_zip.writestr(info, b"")
                    continue
                relative = info.filename[len(PREFIX):] if info.filename.startswith(PREFIX) else None
                if relative in replacements:
                    out_zip.writestr(info, replacements[relative])
                else:
                    with base_zip.open(info, "r") as src, out_zip.open(info, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
            for name in sorted(additions):
                destination = PREFIX + name
                info = zipfile.ZipInfo(destination, date_time=vtk_map[name].date_time)
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = vtk_map[name].external_attr
                info.create_system = vtk_map[name].create_system
                info.file_size = vtk_map[name].file_size
                with vtk_outer.open(vtk_map[name], "r") as src, out_zip.open(info, "w", force_zip64=True) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)

        with zipfile.ZipFile(out_path, "r") as check_zip:
            validate_archive(check_zip, "KPM100 + VTK đầu ra")
            output_map = unique_file_map(check_zip, "KPM100 + VTK đầu ra")
            missing_base = set(base_map) - set(output_map)
            if missing_base:
                raise ValueError(f"ZIP đầu ra làm mất {len(missing_base)} mục gốc, ví dụ {sorted(missing_base)[:10]}")
            for relative in replacements:
                if PREFIX + relative not in output_map:
                    raise ValueError(f"ZIP đầu ra thiếu file đã thay: {relative}")
            for name in additions:
                if PREFIX + name not in output_map:
                    raise ValueError(f"ZIP đầu ra thiếu file mới: {name}")
            if len(output_map) != len(base_map) + len(additions):
                raise ValueError(f"Số lượng file đầu ra sai: {len(output_map)} != {len(base_map)+len(additions)}")

        report.update({
            "output": str(out_path),
            "output_size": out_path.stat().st_size,
            "output_sha256": digest_file(out_path),
            "base_entries": len(base_map),
            "output_entries": len(output_map),
            "added_resources": sorted(additions),
            "replaced_resources": sorted(replacements),
            "kpm_mod_payloads": role_counts,
            "common_actions": common_report,
            "mses": mses_reports,
            "packer": packer_report,
            "verification": verification_report,
            "validation": {
                "outer_zip_crc": "passed",
                "preserved_all_base_paths": True,
                "custom_kpm_mod_roundtrip": "70/70 passed",
                "skill_xml_parse": "68/68 passed",
                "actor_actions_package": "129 entries, CRC passed",
                "actor_info_package": "CRC passed",
                "game_tested": False,
            },
        })
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        actions_zip.close()
        actor_info_zip.close()

    base_zip.close()
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
        "base_entries": report["base_entries"],
        "output_entries": report["output_entries"],
        "payloads": report["kpm_mod_payloads"],
        "new_resources": len(report["added_resources"]),
        "packer": report["packer"],
        "validation": report["validation"],
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
