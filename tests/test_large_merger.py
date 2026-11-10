import tempfile
import unittest
import zipfile
from pathlib import Path

from app.large_merger import MergeError, merge_zip_files


class LargeMergerVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_zip(self, name, entries):
        path = self.root / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry_name, payload in entries.items():
                archive.writestr(entry_name, payload)
        return path

    def test_merge_keeps_all_unique_payloads_and_skips_identical_duplicates(self):
        first = self.make_zip("first.zip", {
            "skin/a.bytes": b"skin-data",
            "effects/recall.bytes": b"recall-data",
        })
        second = self.make_zip("second.zip", {
            "skin/a.bytes": b"skin-data",
            "audio/kill.ogg": b"audio-data",
        })
        output = self.root / "merged.zip"

        merge_zip_files([str(first), str(second)], str(output))

        with zipfile.ZipFile(output) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"skin/a.bytes", "effects/recall.bytes", "audio/kill.ogg"},
            )
            self.assertEqual(archive.read("skin/a.bytes"), b"skin-data")
            self.assertEqual(archive.read("effects/recall.bytes"), b"recall-data")
            self.assertEqual(archive.read("audio/kill.ogg"), b"audio-data")
            self.assertIsNone(archive.testzip())

    def test_conflicting_path_fails_without_replacing_existing_output(self):
        first = self.make_zip("first.zip", {"BattleBank.bytes": b"asset-from-one"})
        second = self.make_zip("second.zip", {"BattleBank.bytes": b"asset-from-two"})
        output = self.root / "merged.zip"
        output.write_bytes(b"previous-good-result")

        with self.assertRaisesRegex(MergeError, "Trùng tên nhưng nội dung khác nhau"):
            merge_zip_files([str(first), str(second)], str(output))

        self.assertEqual(output.read_bytes(), b"previous-good-result")

    def test_corrupt_input_is_rejected(self):
        corrupt = self.root / "corrupt.zip"
        corrupt.write_bytes(b"not a zip")
        output = self.root / "merged.zip"

        with self.assertRaises(MergeError):
            merge_zip_files([str(corrupt)], str(output))

        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
