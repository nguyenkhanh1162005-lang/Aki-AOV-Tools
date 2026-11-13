from __future__ import annotations

import hashlib
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_bytes import encode_aov_resource
from app.mses_diagnostics import analyze_mses_overlaps, canonical_resource_path
from app.resource_manifest import build_resource_manifest


def _mses_table(rows: list[bytes], field_count: int = 59) -> bytes:
    body = bytearray()
    for row in rows:
        body.extend(struct.pack("<I", len(row)))
        body.extend(row)
    header = bytearray(0x8C)
    header[:4] = b"MSES"
    struct.pack_into("<III", header, 4, 7, field_count, len(rows))
    header[0x60:0x80] = hashlib.md5(body).hexdigest().encode("ascii")
    return bytes(header + body)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class MSESDiagnosticsTests(unittest.TestCase):
    def test_canonical_path_strips_only_resources_version_root(self):
        self.assertEqual(
            canonical_resource_path(
                "Some Mod/Resources/1.64.1/Databin/Client/Sound/BattleBank.bytes"
            ),
            "databin/client/sound/battlebank.bytes",
        )
        self.assertEqual(
            canonical_resource_path("Databin/Client/Sound/BattleBank.bytes"),
            "databin/client/sound/battlebank.bytes",
        )
        self.assertEqual(
            canonical_resource_path("Resources/NotAVersion/thing.bytes"),
            "resources/notaversion/thing.bytes",
        )

    def test_compares_nested_aes_raw_and_zstd_mses_resources_without_authorizing_merge(self):
        name = "BattleBank.bytes"
        base_rows = [
            struct.pack("<I", 1) + b"shared-row",
            struct.pack("<I", 2) + b"base-payload",
        ]
        overlay_rows = [
            struct.pack("<I", 1) + b"shared-row",
            struct.pack("<I", 2) + b"changed-payload",
            struct.pack("<I", 3) + b"overlay-only",
        ]
        base_resource = encode_aov_resource(
            _mses_table(base_rows), name, "aes"
        )
        raw_overlay = bytearray(_mses_table(overlay_rows))
        raw_overlay[0x60:0x80] = b"0" * 32
        zstd_resource = encode_aov_resource(
            _mses_table(base_rows), name, "zstd"
        )
        nested_resources = _zip_bytes({
            "Resources/1.64.1/Databin/Client/Sound/BattleBank.bytes":
                base_resource,
        })
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            wrapper = folder / "P9.zip"
            direct = folder / "Tamyn.zip"
            vtk = folder / "VTK.zip"
            wrapper.write_bytes(_zip_bytes({"Resources.zip": nested_resources}))
            direct.write_bytes(_zip_bytes({
                "Tamyn/Resources/1.64.1/Databin/Client/Sound/BattleBank.bytes":
                    bytes(raw_overlay),
            }))
            vtk.write_bytes(_zip_bytes({
                "Databin/Client/Sound/BattleBank.bytes": zstd_resource,
            }))

            paths = [str(wrapper), str(direct), str(vtk)]
            manifest = build_resource_manifest(paths)
            report = analyze_mses_overlaps(paths, manifest["resources"])

        self.assertEqual(report["table_count"], 1)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["merge_authorized"])
        table = report["tables"][0]
        self.assertEqual(
            table["canonical_path"],
            "databin/client/sound/battlebank.bytes",
        )
        self.assertEqual(len(table["resources"]), 3)
        self.assertEqual(
            {resource["decode_mode"] for resource in table["resources"]},
            {"aes", "raw_mses", "zstd"},
        )

        p9_vs_tm, p9_vs_vtk, tm_vs_vtk = table["comparisons"]
        self.assertEqual(p9_vs_tm["common_keys"], 2)
        self.assertEqual(p9_vs_tm["identical_common_rows"], 1)
        self.assertEqual(p9_vs_tm["differing_common_rows"], 1)
        self.assertEqual(p9_vs_tm["right_only_keys"], 1)
        self.assertFalse(p9_vs_tm["checksums_valid"])
        self.assertFalse(p9_vs_tm["append_only_candidate"])
        self.assertFalse(p9_vs_tm["merge_authorized"])

        self.assertEqual(p9_vs_vtk["differing_common_rows"], 0)
        self.assertTrue(p9_vs_vtk["checksums_valid"])
        self.assertTrue(p9_vs_vtk["append_only_candidate"])

        self.assertEqual(tm_vs_vtk["differing_common_rows"], 1)
        self.assertEqual(tm_vs_vtk["right_only_keys"], 1)
        self.assertFalse(tm_vs_vtk["checksums_valid"])


if __name__ == "__main__":
    unittest.main()
