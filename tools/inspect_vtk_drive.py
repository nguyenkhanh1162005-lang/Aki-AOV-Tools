from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.drive_import import download_drive_zip
from app.aov_bytes import decode_aov_resource
from tools.ifix_parse import PatchParser

DRIVE_URL = "https://drive.google.com/file/d/1JpKeA82ha9CmCeGL1pmAEfBhQWr19Mxe/view?usp=drivesdk"
report = {"download": {}, "archives": []}
metadata_report = {}


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(child) for child in value]
    if isinstance(value, bytes):
        return {"$bytes_hex": value.hex()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def inspect_bytes(name: str, payload: bytes) -> dict:
    record = {"name": name, "bytes": len(payload), "header_hex": payload[:16].hex()}
    try:
        decoded, mode = decode_aov_resource(payload, name)
        record["decode"] = {
            "ok": True,
            "mode": mode,
            "decoded_bytes": len(decoded),
            "decoded_prefix_hex": decoded[:24].hex(),
        }
        if decoded.startswith(b"MSES"):
            record["decode"]["mses_version"] = int.from_bytes(decoded[4:8], "little") if len(decoded) >= 8 else None
            record["decode"]["mses_header_hex"] = decoded[:32].hex()
    except Exception as exc:
        record["decode"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return record


with tempfile.TemporaryDirectory(prefix="aki-vtk-audit-") as temp:
    outer_path = Path(temp) / "vtk-outer.zip"
    name, size = download_drive_zip(DRIVE_URL, str(outer_path))
    report["download"] = {"name": name, "bytes": size}
    Path("vtk-source.zip").write_bytes(outer_path.read_bytes())

    with zipfile.ZipFile(outer_path) as outer:
        for info in outer.infolist():
            if info.is_dir():
                continue
            payload = outer.read(info)
            record = {
                "name": info.filename,
                "bytes": info.file_size,
                "crc32": f"{info.CRC:08x}",
            }

            if info.filename.lower().endswith(".bytes"):
                record.update(inspect_bytes(info.filename, payload))
                if info.filename.endswith("/Thanos/HeroSkin_2.bytes"):
                    decoded, mode = decode_aov_resource(payload, info.filename)
                    patch_path = Path(temp) / "vtk-heroskin.dec"
                    patch_path.write_bytes(decoded)
                    patch = PatchParser(str(patch_path)).parse()
                    record["ifix_patch"] = {
                        "encoding": mode,
                        "magic": f"0x{patch.magic:x}",
                        "bridge": patch.bridge_name,
                        "assembly": patch.assembly_string,
                        "wrappers_manager": patch.wrappers_manager_name,
                        "method_count": len(patch.methods),
                        "extern_type_count": len(patch.extern_types),
                        "extern_method_count": len(patch.extern_methods),
                        "fixes": [
                            {
                                "class": patch.extern_types[item["type_id"]] if 0 <= item["type_id"] < len(patch.extern_types) else str(item["type_id"]),
                                "method": item["name"],
                                "patch_id": item["patch_id"],
                            }
                            for item in patch.fix_infos
                        ],
                        "relevant_strings": [
                            value for value in patch.intern_strings
                            if any(term in value.lower() for term in ("595", "lixin", "skin", "battle", "resource", "youtube", "sinon"))
                        ][:100],
                    }

            if info.filename.lower().endswith(".assetbundle"):
                try:
                    import sys
                    sys.path.insert(0, "/tmp/AOV_UABE_2022/AssetbundleUtils")
                    import UnityPy_AOV
                    env = UnityPy_AOV.load(payload)
                    type_counts = Counter()
                    named = []
                    for obj in env.objects:
                        type_counts[obj.type.name] += 1
                        if obj.type.name == "AssetBundle":
                            try:
                                tree = obj.read_typetree()
                                record["assetbundle_catalog"] = {
                                    "path_id": obj.path_id,
                                    "container_entries": len(tree.get("m_Container", [])),
                                    "preload_entries": len(tree.get("m_PreloadTable", [])),
                                    "dependencies": list(tree.get("m_Dependencies", [])),
                                    "container_sample": [str(x[0]) for x in tree.get("m_Container", [])[:120]],
                                }
                            except Exception as exc:
                                record["assetbundle_catalog"] = {"error": f"{type(exc).__name__}: {exc}"}
                        if len(named) < 100:
                            try:
                                data = obj.read()
                                obj_name = getattr(data, "name", "") or getattr(data, "m_Name", "")
                                if obj_name:
                                    named.append({
                                        "type": obj.type.name,
                                        "path_id": obj.path_id,
                                        "name": str(obj_name)[:160],
                                    })
                            except Exception:
                                pass
                    if info.filename.endswith(("/resourcepackerinfosetall.assetbundle", "/resourceverificationinfosetall.assetbundle")):
                        mono_trees = []
                        for obj in env.objects:
                            if obj.type.name == "MonoBehaviour":
                                try:
                                    mono_trees.append({
                                        "path_id": obj.path_id,
                                        "tree": json_safe(obj.read_typetree()),
                                    })
                                except Exception as exc:
                                    mono_trees.append({
                                        "path_id": obj.path_id,
                                        "error": f"{type(exc).__name__}: {exc}",
                                    })
                        metadata_report[info.filename] = mono_trees
                    record["unity"] = {
                        "ok": True,
                        "object_count": len(env.objects),
                        "type_counts": dict(type_counts),
                        "named_objects_sample": named,
                    }
                except Exception as exc:
                    record["unity"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            # Several .pkg.bytes files are ZIP containers despite their extension.
            if payload.startswith(b"PK\\x03\\x04"):
                try:
                    import io
                    with zipfile.ZipFile(io.BytesIO(payload)) as package:
                        entries = []
                        for item in package.infolist():
                            if item.is_dir():
                                continue
                            entry = {
                                "name": item.filename,
                                "bytes": item.file_size,
                                "crc32": f"{item.CRC:08x}",
                            }
                            if item.filename.lower().endswith(".bytes"):
                                entry.update(inspect_bytes(item.filename, package.read(item)))
                            entries.append(entry)
                        record["package_zip"] = {"ok": True, "entries": entries}
                except Exception as exc:
                    record["package_zip"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            report["archives"].append(record)

Path("vtk-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
Path("vtk-assetbundle-metadata.json").write_text(json.dumps(metadata_report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
