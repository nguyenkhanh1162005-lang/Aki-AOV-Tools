import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_merger import AOVMergeError, _validate_archive_paths


class AOVArchiveValidationTests(unittest.TestCase):
    def make_zip(self, entries):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "input.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload in entries:
                archive.writestr(name, payload)
        return path

    def test_rejects_duplicate_file_paths(self):
        path = self.make_zip([("Resources/data.bytes", b"first"),
                              ("Resources/data.bytes", b"second")])
        with zipfile.ZipFile(path) as archive:
            with self.assertRaisesRegex(AOVMergeError, "bị lặp"):
                _validate_archive_paths(archive, "test")

    def test_allows_distinct_paths(self):
        path = self.make_zip([("Resources/a.bytes", b"a"),
                              ("Resources/b.bytes", b"b")])
        with zipfile.ZipFile(path) as archive:
            _validate_archive_paths(archive, "test")


if __name__ == "__main__":
    unittest.main()
