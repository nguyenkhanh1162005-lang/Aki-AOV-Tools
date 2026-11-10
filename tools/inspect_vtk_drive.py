from __future__ import annotations

import json
import tempfile
import zipfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.drive_import import download_drive_zip
from app.aov_bytes import decode_aov_resource, AOVBytesError

DRIVE_URL = "https://drive.google.com/file/d/1JpKeA82ha9CmCeGL1pmAEfBhQWr19Mxe/view?usp=drivesdk"
report = {"download": {}, "archives": []}

with tempfile.TemporaryDirectory(prefix="aki-vtk-audit-") as temp:
    outer_path = Path(temp) / "vtk-outer.zip"
    name, size = download_drive_zip(DRIVE_URL, str(outer_path))
    report["download"] = {"name": name, "bytes": size}
    Path("vtk-source.zip").write_bytes(outer_path.read_bytes())
    with zipfile.ZipFile(outer_path) as outer:
        for info in outer.infolist():
            if info.is_dir():
                continue
            record = {"name": info.filename, "bytes": info.file_size, "crc32": f"{info.CRC:08x}"}
            if info.filename.lower().endswith(".bytes"):
                payload = outer.read(info)
                record["header_hex"] = payload[:16].hex()
                try:
                    decoded, mode = decode_aov_resource(payload, info.filename)
                    record["decode"] = {"ok": True, "mode": mode, "decoded_bytes": len(decoded), "decoded_prefix_hex": decoded[:24].hex()}
                except Exception as exc:
                    record["decode"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if info.filename.lower().endswith(".assetbundle"):
                try:
                    import UnityPy
                    env = UnityPy.load(outer.read(info))
                    objects = []
                    for obj in env.objects:
                        try:
                            data = obj.read()
                            obj_name = getattr(data, "name", "") or getattr(data, "m_Name", "")
                        except Exception:
                            obj_name = ""
                        objects.append({"type": obj.type.name, "path_id": obj.path_id, "name": str(obj_name)[:160]})
                    record["unity_objects"] = objects
                except Exception as exc:
                    record["unity_parse"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                inner_path = Path(temp) / "vtk-inner.zip"
                inner_path.write_bytes(outer.read(info))
                with zipfile.ZipFile(inner_path) as inner:
                    entries = []
                    for item in inner.infolist():
                        if item.is_dir():
                            continue
                        entry = {"name": item.filename, "bytes": item.file_size, "crc32": f"{item.CRC:08x}"}
                        if item.filename.lower().endswith(".bytes"):
                            payload = inner.read(item)
                            entry["header_hex"] = payload[:16].hex()
                            try:
                                decoded, mode = decode_aov_resource(payload, item.filename)
                                entry["decode"] = {"ok": True, "mode": mode, "decoded_bytes": len(decoded), "decoded_prefix_hex": decoded[:24].hex()}
                            except Exception as exc:
                                entry["decode"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                        entries.append(entry)
                    record["inner_entries"] = entries
            report["archives"].append(record)

Path("vtk-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
