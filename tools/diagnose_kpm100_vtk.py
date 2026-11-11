#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import PurePosixPath

BASE_ZIP = "incoming/Mod.VN.100.Skin.Ver.12.-.iOS.KHAM.PHA.MOBILE.zip"
VTK_ZIP = "incoming/VTK.IOS.VIP.zip"
PREFIX = "Resources/1.64.1/"
ACTION_PKG = PREFIX + "Ages/Prefab_Characters/Prefab_Hero/Actor_595_Actions.pkg.bytes"
PACKAGE_NAMES = (
    "Actor_595_Actions.pkg.bytes",
    "CommonActions.pkg.bytes",
    "KernelLua.pkg.bytes",
    "StableSystems_3.pkg.bytes",
)
IMPORTANT_LEAVES = {
    "HeroSkin_1.bytes", "HeroSkin_2.bytes", "ResSkinSeniorLabelCfg.bytes",
    "BattleBank.bytes", "LobbyBank.bytes", "skillcombine.bytes",
    "ResKillBillboardCfg.bytes", "ResPersonalButtonCfg.bytes",
    "595_AssetRef.bytes", "Actor_595_Infos.pkg.bytes",
}
OUT = []

def log(s=""):
    OUT.append(str(s))

def sha(data):
    return hashlib.sha256(data).hexdigest()

def read_zip(path, label):
    z = zipfile.ZipFile(path)
    bad = z.testzip()
    if bad:
        raise RuntimeError(f"{label} CRC failed: {bad}")
    log(f"{label}: bytes={os.path.getsize(path)} entries={len(z.infolist())} sha256={sha(open(path,'rb').read())}")
    return z

def safe_vtk_name(name):
    name = name.replace("\\", "/").lstrip("/")
    if name.startswith(PREFIX):
        name = name[len(PREFIX):]
    elif name.startswith("Resources/"):
        p = name.split("/", 2)
        if len(p) == 3:
            name = p[2]
    return name

def is_package(name):
    return PurePosixPath(name).name in PACKAGE_NAMES

def java_hash(text):
    value = 0
    for ch in text:
        value = ((value * 31) + ord(ch)) & 0xffffffff
    return value

def inner_zip(data, label):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        bad = z.testzip()
        if bad:
            log(f"  {label}: nested ZIP CRC ERROR {bad}")
            return None
        return z
    except Exception as exc:
        log(f"  {label}: not a ZIP ({type(exc).__name__}: {exc}); head={data[:24].hex()}")
        return None

base = read_zip(BASE_ZIP, "KPM100")
vtk = read_zip(VTK_ZIP, "VTK")

base_names = [i.filename for i in base.infolist() if not i.is_dir()]
vtk_entries = {}
for info in vtk.infolist():
    if info.is_dir():
        continue
    name = safe_vtk_name(info.filename)
    if name in vtk_entries:
        old = vtk_entries[name]
        if sha(vtk.read(old)) != sha(vtk.read(info)):
            log(f"DUPLICATE VTK PATH CONFLICT: {name}")
        continue
    vtk_entries[name] = info

log("\n=== KPM100 relevant top-level files ===")
for name in base_names:
    leaf = PurePosixPath(name).name
    if leaf in IMPORTANT_LEAVES or is_package(name) or leaf.startswith("KPM_MOD_"):
        info = base.getinfo(name)
        if leaf.startswith("KPM_MOD_"):
            continue
        log(f"{name}\t{info.file_size}\t{info.CRC:08x}")

log("\n=== VTK relevant top-level files ===")
for name, info in sorted(vtk_entries.items()):
    leaf = PurePosixPath(name).name
    if leaf in IMPORTANT_LEAVES or is_package(name) or leaf.endswith(".assetbundle") or leaf.startswith("KPM_MOD_"):
        log(f"{name}\t{info.file_size}\t{info.CRC:08x}")

log("\n=== Expected VTK Actor 595 bundles ===")
for name in sorted(k for k in vtk_entries if k.endswith(".assetbundle") and ("595" in k.lower() or "595" in PurePosixPath(k).name.lower())):
    info = vtk_entries[name]
    log(f"{name}\t{info.file_size}\t{info.CRC:08x}")

base_pkg_zips = {}
vtk_pkg_zips = {}
for pkg in PACKAGE_NAMES:
    bpaths = [n for n in base_names if PurePosixPath(n).name.lower() == pkg.lower()]
    vpaths = [n for n in vtk_entries if PurePosixPath(n).name.lower() == pkg.lower()]
    log(f"\n=== PACKAGE {pkg} ===")
    log(f"KPM paths: {bpaths}")
    log(f"VTK paths: {vpaths}")
    bz = inner_zip(base.read(bpaths[0]), f"KPM {pkg}") if len(bpaths) == 1 else None
    vz = inner_zip(vtk.read(vtk_entries[vpaths[0]]), f"VTK {pkg}") if len(vpaths) == 1 else None
    if bz: base_pkg_zips[pkg] = bz
    if vz: vtk_pkg_zips[pkg] = vz
    if bz and vz:
        bmap = {i.filename: i for i in bz.infolist() if not i.is_dir()}
        vmap = {i.filename: i for i in vz.infolist() if not i.is_dir()}
        same = sorted(set(bmap) & set(vmap))
        changed = [n for n in same if sha(bz.read(bmap[n])) != sha(vz.read(vmap[n]))]
        b_only = sorted(set(bmap) - set(vmap))
        v_only = sorted(set(vmap) - set(bmap))
        log(f"inner KPM={len(bmap)} VTK={len(vmap)} common={len(same)} identical={len(same)-len(changed)} changed={len(changed)} KPM-only={len(b_only)} VTK-only={len(v_only)}")
        for n in changed[:160]:
            log(f"  DIFF {n}\tKPM={bmap[n].file_size}\tVTK={vmap[n].file_size}")
        for n in v_only[:100]:
            log(f"  VTK_ONLY {n}\t{vmap[n].file_size}")
        for n in b_only[:100]:
            log(f"  KPM_ONLY {n}\t{bmap[n].file_size}")

log("\n=== KPM hashed payloads that may map to Actor595 Action XML ===")
kpm_mods = {}
for name in base_names:
    leaf = PurePosixPath(name).name
    m = re.fullmatch(r"KPM_MOD_([0-9A-Fa-f]{16})", leaf)
    if m:
        token = m.group(1).upper()
        kpm_mods[token] = (name, base.getinfo(name))
log(f"KPM_MOD total={len(kpm_mods)}")
payload_hash_index = defaultdict(list)
for token, (path, info) in sorted(kpm_mods.items()):
    # The second 32-bit half in the payload name is little-endian Java hash of the XML stem.
    half2 = token[8:16]
    payload_hash_index[half2].append((token, path, info))

for pkg in (ACTION_PKG.split("/")[-1],):
    if pkg in base_pkg_zips and pkg in vtk_pkg_zips:
        bz, vz = base_pkg_zips[pkg], vtk_pkg_zips[pkg]
        vmap = {i.filename: i for i in vz.infolist() if not i.is_dir()}
        names_seen = set()
        mapped = 0
        mapped_diff = 0
        mapped_equal = 0
        for name, vi in sorted(vmap.items()):
            stem = PurePosixPath(name).stem
            candidate_keys = {
                java_hash(variant).to_bytes(4, "little").hex().upper()
                for variant in {stem, stem.lower(), stem.upper()}
            }
            candidates = []
            for key in candidate_keys:
                candidates.extend(payload_hash_index.get(key, []))
            if not candidates:
                continue
            for token, path, info in candidates:
                names_seen.add(token)
                mapped += 1
                kpm_payload = base.read(path)
                vtk_xml = vz.read(vi)
                relation = "DIFF" if sha(kpm_payload) != sha(vtk_xml) else "SAME"
                if relation == "DIFF": mapped_diff += 1
                else: mapped_equal += 1
                log(f"MAP {token} -> {name}\tKPM_MOD={info.file_size}\tVTK_XML={len(vtk_xml)}\t{relation}\tKPM_SHA={sha(kpm_payload)[:16]}\tVTK_SHA={sha(vtk_xml)[:16]}")
        log(f"MAPPING summary: mapped KPM_MOD={len(names_seen)} pairs={mapped} differing={mapped_diff} identical={mapped_equal} unmapped_payloads={len(kpm_mods)-len(names_seen)}")
        unmapped = [ (token,path,info.file_size) for token,(path,info) in sorted(kpm_mods.items()) if token not in names_seen ]
        for token,path,size in unmapped[:100]:
            log(f"UNMAPPED {token}\t{path}\t{size}")

log("\n=== Exact Actor595 payload-map inspection ===")
import csv
from pathlib import Path
mapping_path = Path("tools/kpm100_actor595_payload_map.csv")
mapping_rows = list(csv.DictReader(mapping_path.open(encoding="utf-8", newline="")))
log(f"Verified payload-map rows={len(mapping_rows)}")
sys.path.insert(0, os.getcwd())
try:
    from app.aov_bytes import decode_aov_resource
except Exception as exc:
    decode_aov_resource = None
    log(f"AOV codec unavailable in diagnostic: {type(exc).__name__}: {exc}")

for row in mapping_rows:
    payload_name = row["payload_file"]
    entry_name = row["entry_name"]
    payload_path = PREFIX + "Mod_by_Youtube_KHAM-PHA-MOBILE/" + payload_name
    try:
        payload = base.read(payload_path)
    except KeyError:
        log(f"PAYLOAD_MISSING {payload_name}")
        continue
    if row["role"] == "skill":
        vtk_zip = vtk_pkg_zips.get("Actor_595_Actions.pkg.bytes")
        vtk_target = "595_LiXin/skill/" + entry_name
        if not vtk_zip:
            log(f"VTK_PACKAGE_MISSING {entry_name}")
            continue
        try:
            vmap = {i.filename.lower():i for i in vtk_zip.infolist() if not i.is_dir()}
            info = vmap.get(vtk_target.lower())
            if info is None:
                log(f"VTK_ENTRY_MISSING {vtk_target}")
                continue
            vtk_data = vtk_zip.read(info)
        except KeyError:
            log(f"VTK_ENTRY_MISSING {vtk_target}")
            continue
    elif row["role"] == "assetref":
        info = vtk_entries.get("AssetRefs/Hero/595_AssetRef.bytes")
        vtk_data = vtk.read(info) if info else b""
    elif row["role"] == "actorinfo":
        info = vtk_entries.get("Prefab_Characters/Actor_595_Infos.pkg.bytes")
        vtk_data = vtk.read(info) if info else b""
    else:
        vtk_data = b""
    # KPM_MOD is a one-member ZIP with intentionally zero CRC fields.
    # Read its member bytes from the local header, then decode the AoV wrapper.
    member_name = ""
    member_data = b""
    if payload[:4] == bytes.fromhex("504b0304") and len(payload) >= 30:
        nlen = int.from_bytes(payload[26:28], "little")
        elen = int.from_bytes(payload[28:30], "little")
        member_name = payload[30:30+nlen].decode("utf-8", errors="replace")
        data_at = 30 + nlen + elen
        csize = int.from_bytes(payload[18:22], "little")
        if data_at + csize <= len(payload):
            member_data = payload[data_at:data_at+csize]
    decoded_base = None
    decoded_vtk = None
    if member_data.startswith(bytes.fromhex("224a00ef")):
        import pyzstd
        from app.aov_zstd_dict import ZSTD_DICT
        frame = member_data[member_data.find(bytes.fromhex("28b52ffd")):]
        variant_inputs = [
            ("repo_raw_full", ZSTD_DICT),
            ("repo_raw_tail8", ZSTD_DICT[8:]),
            ("repo_raw_tail4", ZSTD_DICT[4:]),
            ("repo_raw_tail12", ZSTD_DICT[12:]),
        ]
        variant_results = []
        for variant_name, raw_dictionary in variant_inputs:
            try:
                dictionary = pyzstd.ZstdDict(raw_dictionary, is_raw=True)
                raw_test = pyzstd.decompress(frame, dictionary)
                variant_results.append(f"{variant_name}=OK:{len(raw_test)}")
            except Exception as exc:
                variant_results.append(f"{variant_name}={type(exc).__name__}:{str(exc)[:90]}")
        try:
            raw_test = pyzstd.decompress(frame)
            variant_results.append(f"no_dict=OK:{len(raw_test)}")
        except Exception as exc:
            variant_results.append(f"no_dict={type(exc).__name__}:{str(exc)[:90]}")
        log(f"KPM_ZSTD_VARIANTS {payload_name} {variant_results}")
    base_mode = None
    vtk_mode = None
    decode_errors = []
    if not member_data:
        decode_errors.append("KPM_MOD local payload missing")
    else:
        try:
            decoded_base, base_mode = decode_aov_resource(member_data, entry_name)
        except Exception as exc:
            decode_errors.append(f"KPM decode: {type(exc).__name__}: {exc}")
    if row["role"] in ("skill", "assetref"):
        if vtk_data:
            vtk_name = entry_name if row["role"] == "skill" else "595_AssetRef.bytes"
            try:
                decoded_vtk, vtk_mode = decode_aov_resource(vtk_data, vtk_name)
            except Exception as exc:
                decode_errors.append(f"VTK decode: {type(exc).__name__}: {exc}")
    elif row["role"] == "actorinfo" and vtk_data:
        decoded_vtk = vtk_data
    parse_base = parse_vtk = "n/a"
    if row["role"] == "skill":
        import xml.etree.ElementTree as ET
        for label, raw in (("base", decoded_base), ("vtk", decoded_vtk)):
            if raw is not None:
                try:
                    ET.fromstring(raw)
                    if label == "base": parse_base = "XML_OK"
                    else: parse_vtk = "XML_OK"
                except Exception as exc:
                    if label == "base": parse_base = f"XML_ERROR:{type(exc).__name__}"
                    else: parse_vtk = f"XML_ERROR:{type(exc).__name__}"
    base_pkg_match = False
    base_pkg_size = None
    if row["role"] == "skill" and decoded_base is not None:
        bz = base_pkg_zips.get(ACTION_PKG.split("/")[-1])
        if bz:
            bmap = {i.filename.lower():i for i in bz.infolist() if not i.is_dir()}
            bi = bmap.get(("595_LiXin/skill/" + entry_name).lower())
            if bi:
                base_pkg_size = bi.file_size
                base_pkg_match = sha(decoded_base) == sha(bz.read(bi))
    log(
        f"PAYLOAD {payload_name} role={row['role']} entry={entry_name} "
        f"wrapper_member={member_name} wrapped_bytes={len(member_data)} "
        f"base_mode={base_mode} base_decoded={len(decoded_base) if decoded_base is not None else None} "
        f"vtk_mode={vtk_mode} vtk_decoded={len(decoded_vtk) if decoded_vtk is not None else None} "
        f"base_xml={parse_base} vtk_xml={parse_vtk} base_pkg_size={base_pkg_size} "
        f"base_payload_equals_base_package={base_pkg_match} "
        f"decoded_same={decoded_base is not None and decoded_vtk is not None and sha(decoded_base)==sha(decoded_vtk)} "
        f"errors={decode_errors}"
    )


log("\n=== Selective package diffs ===")
for pkg in PACKAGE_NAMES:
    if pkg not in base_pkg_zips or pkg not in vtk_pkg_zips:
        continue
    bz, vz = base_pkg_zips[pkg], vtk_pkg_zips[pkg]
    bmap = {i.filename:i for i in bz.infolist() if not i.is_dir()}
    vmap = {i.filename:i for i in vz.infolist() if not i.is_dir()}
    for name in sorted(set(bmap)&set(vmap)):
        if sha(bz.read(bmap[name])) != sha(vz.read(vmap[name])):
            log(f"PKG_DIFF {pkg}\t{name}\tKPM={bmap[name].file_size}\tVTK={vmap[name].file_size}")

log("\n=== Search KPM archive for ZSTD dictionary and codec references ===")
magic_hits = []
id_hits = []
named = []
for info in base.infolist():
    if info.is_dir():
        continue
    leaf = PurePosixPath(info.filename).name.lower()
    if any(token in leaf for token in ("zstd", "dict", "codec", "compress")):
        named.append((info.filename, info.file_size))
    try:
        with base.open(info, "r") as stream:
            head = stream.read(min(info.file_size, 1024 * 1024))
    except Exception:
        continue
    if bytes.fromhex("37a430ec") in head:
        magic_hits.append((info.filename, info.file_size, head.find(bytes.fromhex("37a430ec"))))
    if bytes.fromhex("e755430b") in head:
        id_hits.append((info.filename, info.file_size, head.find(bytes.fromhex("e755430b"))))
log(f"ZSTD named candidates={named[:100]}")
log(f"ZSTD dictionary magic hits={magic_hits[:100]}")
log(f"ZSTD dictionary ID hits={id_hits[:100]}")

log("\n=== KPM/VTK same path conflicts ===")
v_by_full = {}
for name, info in vtk_entries.items():
    full = PREFIX + name
    v_by_full[full] = info
conflicts = []
additions = []
for name in sorted(v_by_full):
    vi = v_by_full[name]
    if name in base_names:
        bi = base.getinfo(name)
        if sha(base.read(bi)) != sha(vtk.read(vi)):
            conflicts.append(name)
    else:
        additions.append(name)
log(f"overlaps differing={len(conflicts)} additions={len(additions)}")
for name in conflicts:
    log(f"CONFLICT {name}\tKPM={base.getinfo(name).file_size}\tVTK={v_by_full[name].file_size}")
log("\n=== END ===")
with open("diagnostic-report.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(OUT) + "\n")
print("\n".join(OUT))
