import hashlib
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_crypto import decode_aov_resource, encrypt_aov_resource
from app.aov_mses import parse_mses
from app.aov_package_merger import AOVPackageMergeError, merge_aov_archives
from app.aov_zstd import encode_aov_bytes


def make_mses(rows):
    body = b"".join(
        struct.pack("<I", len(row)) + row for row in rows
    )
    header = bytearray(140)
    header[:4] = b"MSES"
    struct.pack_into("<III", header, 4, 7, 59, len(rows))
    struct.pack_into("<I", header, 132, 140)
    header[96:128] = hashlib.md5(body).hexdigest().encode("ascii")
    return bytes(header) + body


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return str(path)


class AOVPackageMergerTests(unittest.TestCase):
    def test_merges_nested_resources_and_target_mses_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            resource_name = "Databin/Client/Sound/BattleBank.bytes"
            base_mses = make_mses([
                struct.pack("<I", 5950001) + b"old-sound",
                struct.pack("<I", 1230001) + b"keep-base",
            ])
            mod_mses = make_mses([
                struct.pack("<I", 5950001) + b"new-sound",
                struct.pack("<I", 5950002) + b"new-voice",
            ])
            base_resources_buffer = io.BytesIO()
            with zipfile.ZipFile(base_resources_buffer, "w", zipfile.ZIP_DEFLATED) as resources:
                resources.writestr(
                    "Resources/1.64.1/" + resource_name,
                    encrypt_aov_resource(resource_name, base_mses),
                )
            base_outer = make_zip(
                folder / "base.zip",
                {"mod/Resources.zip": base_resources_buffer.getvalue(), "mod/manifest.json": b"{}"},
            )

            actor_pkg_buffer = io.BytesIO()
            with zipfile.ZipFile(actor_pkg_buffer, "w", zipfile.ZIP_DEFLATED) as actor_pkg:
                actor_pkg.writestr("actor.txt", b"actor-595")
            mod_zip = make_zip(folder / "mod.zip", {
                "Ages/Prefab_Characters/Prefab_Hero/Actor_595_Actions.pkg.bytes": actor_pkg_buffer.getvalue(),
                resource_name: encode_aov_bytes(mod_mses),
                "assetbundle/hero595.assetbundle": b"new-bundle",
                "Sound/Android/ignored.bnk": b"android-only",
            })

            output = folder / "merged.zip"
            size, report = merge_aov_archives(
                [base_outer, mod_zip], str(output)
            )
            self.assertTrue(output.exists())
            self.assertEqual(size, output.stat().st_size)
            self.assertEqual(report.added_files, 2)
            self.assertEqual(report.skipped_android_files, 1)
            with zipfile.ZipFile(output) as outer:
                self.assertEqual(outer.read("mod/manifest.json"), b"{}")
                with zipfile.ZipFile(io.BytesIO(outer.read("mod/Resources.zip"))) as resources:
                    merged = decode_aov_resource(
                        resource_name,
                        resources.read("Resources/1.64.1/" + resource_name),
                    )
                    parsed = parse_mses(merged)
                    rows = {
                        struct.unpack_from("<I", row, 0)[0]: row
                        for row in parsed.rows
                    }
                    self.assertEqual(parsed.row_count, 3)
                    self.assertEqual(rows[5950001], struct.pack("<I", 5950001) + b"new-sound")
                    self.assertEqual(rows[5950002], struct.pack("<I", 5950002) + b"new-voice")
                    self.assertEqual(rows[1230001], struct.pack("<I", 1230001) + b"keep-base")
                    self.assertIn("Resources/1.64.1/assetbundle/hero595.assetbundle", resources.namelist())

    def test_unknown_conflicting_binary_asset_fails_without_output(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            resources_buffer = io.BytesIO()
            with zipfile.ZipFile(resources_buffer, "w", zipfile.ZIP_DEFLATED) as resources:
                resources.writestr("Resources/1.64.1/assetbundle/resourcepacker.assetbundle", b"base")
            base_outer = make_zip(folder / "base.zip", {
                "Resources.zip": resources_buffer.getvalue()
            })
            actor_pkg_buffer = io.BytesIO()
            with zipfile.ZipFile(actor_pkg_buffer, "w") as actor_pkg:
                actor_pkg.writestr("actor.txt", b"actor")
            mod_zip = make_zip(folder / "mod.zip", {
                "Ages/Prefab_Characters/Prefab_Hero/Actor_595_Actions.pkg.bytes": actor_pkg_buffer.getvalue(),
                "assetbundle/resourcepacker.assetbundle": b"incoming",
            })
            output = folder / "must-not-exist.zip"
            with self.assertRaisesRegex(AOVPackageMergeError, "xung đột nhị phân"):
                merge_aov_archives([base_outer, mod_zip], str(output))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
