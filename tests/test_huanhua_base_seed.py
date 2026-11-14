from __future__ import annotations

import hashlib
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.aov_bytes import encrypt_aes_resource
from app.aov_merger import seed_huanhua_base_tables
from app.aov_mses import MSESTable, build_mses

PREFIX = "Resources/1.64.1/"
VERSION = PREFIX + "version.txt"
TABLES = (
    "Databin/Client/Huanhua/ResBillboardCfg.bytes",
    "Databin/Client/Huanhua/ResBillboardSkinCfg.bytes",
    "Databin/Client/Huanhua/ResKillBillboardCfg.bytes",
)


def _encoded_table(path: str, keys: tuple[int, ...]) -> bytes:
    header = bytearray(0x8C)
    header[:4] = b"MSES"
    table = MSESTable(7, 2, tuple(), bytes(header), True)
    rows = [struct.pack("<I", key) + b"data" for key in keys]
    return encrypt_aes_resource(build_mses(table, rows), path)


def _make_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def _make_wrapper(path: Path, entries: dict[str, bytes]) -> None:
    inner_buffer = io.BytesIO()
    with zipfile.ZipFile(inner_buffer, "w", compression=zipfile.ZIP_DEFLATED) as inner:
        for name, data in entries.items():
            inner.writestr(PREFIX + name, data)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as outer:
        outer.writestr("Mod/Resources.zip", inner_buffer.getvalue())
        outer.writestr("Mod/DanhSachSkin.txt", "Base skin\n")


class HuanhuaBaseSeedTests(unittest.TestCase):
    def _fixture(self, directory: Path):
        donor_entries = {VERSION: b"1.64.1.12|1793129"}
        payloads = {}
        for index, relative in enumerate(TABLES, start=1):
            payload = _encoded_table(relative, (1000 + index, 2000 + index, 3000 + index))
            donor_entries[PREFIX + relative] = payload
            payloads[relative] = payload
        hashes = {
            relative: hashlib.sha256(payload).hexdigest()
            for relative, payload in payloads.items()
        }
        wrapper_entries = {"version.txt": donor_entries[VERSION]}
        base = directory / "base-wrapper.zip"
        donor = directory / "huanhua-donor.zip"
        output = directory / "seeded-wrapper.zip"
        _make_wrapper(base, wrapper_entries)
        _make_zip(donor, donor_entries)
        return base, donor, output, hashes, payloads

    def test_seeds_only_hash_pinned_mses_tables_and_preserves_outer_wrapper(self):
        with tempfile.TemporaryDirectory() as temp:
            base, donor, output, hashes, payloads = self._fixture(Path(temp))
            with (
                patch("app.aov_merger.HUANHUA_BASE_TABLE_SHA256", hashes),
                patch(
                    "app.aov_merger.HUANHUA_BASE_TABLE_SCHEMA",
                    {relative: (2, 3) for relative in TABLES},
                ),
            ):
                report = seed_huanhua_base_tables(str(base), str(donor), str(output))

            self.assertTrue(report["verified"])
            self.assertEqual(set(report["appended_paths"]), {PREFIX + path for path in TABLES})
            with zipfile.ZipFile(output) as outer:
                self.assertIsNone(outer.testzip())
                self.assertEqual(
                    {i.filename for i in outer.infolist()},
                    {"Mod/Resources.zip", "Mod/DanhSachSkin.txt"},
                )
                inner_bytes = outer.read("Mod/Resources.zip")
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                self.assertIsNone(inner.testzip())
                for relative, payload in payloads.items():
                    self.assertEqual(inner.read(PREFIX + relative), payload)

    def test_rejects_donor_with_wrong_mses_payload_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            base, donor, output, _hashes, _payloads = self._fixture(Path(temp))
            wrong_hashes = {relative: "0" * 64 for relative in TABLES}
            with patch("app.aov_merger.HUANHUA_BASE_TABLE_SHA256", wrong_hashes):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    seed_huanhua_base_tables(str(base), str(donor), str(output))
            self.assertFalse(output.exists())

    def test_rejects_donor_from_another_game_build(self):
        with tempfile.TemporaryDirectory() as temp:
            base, donor, output, hashes, _payloads = self._fixture(Path(temp))
            _make_zip(donor, {
                VERSION: b"1.64.1.11|1793128",
                **{
                    PREFIX + relative: _payload
                    for relative, _payload in hashes.items()
                },
            })
            with (
                patch("app.aov_merger.HUANHUA_BASE_TABLE_SHA256", hashes),
                patch(
                    "app.aov_merger.HUANHUA_BASE_TABLE_SCHEMA",
                    {relative: (2, 3) for relative in TABLES},
                ),
            ):
                with self.assertRaisesRegex(ValueError, "sai build"):
                    seed_huanhua_base_tables(str(base), str(donor), str(output))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
