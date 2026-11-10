import unittest

from app.aov_merger import AOVMergeError, _normalise_vtk_path


class VTKPathValidationTests(unittest.TestCase):
    def test_normalizes_supported_resource_prefix(self):
        self.assertEqual(
            _normalise_vtk_path(
                "Resources/1.64.1/assetbundle/battle/hero/595_example.assetbundle"
            ),
            "assetbundle/battle/hero/595_example.assetbundle",
        )

    def test_normalizes_backslashes(self):
        self.assertEqual(
            _normalise_vtk_path(r"Resources\\1.64.1\\Thanos\\HeroSkin_2.bytes"),
            "Thanos/HeroSkin_2.bytes",
        )

    def test_rejects_absolute_paths(self):
        for name in ("/etc/passwd", r"\\server\\share\\payload"):
            with self.subTest(name=name):
                with self.assertRaises(AOVMergeError):
                    _normalise_vtk_path(name)

    def test_rejects_traversal_and_drive_paths(self):
        for name in (
            "../outside.bytes",
            "Resources/1.64.1/../../outside.bytes",
            "Resources/1.64.1/folder/../outside.bytes",
            "C:/outside.bytes",
        ):
            with self.subTest(name=name):
                with self.assertRaises(AOVMergeError):
                    _normalise_vtk_path(name)


if __name__ == "__main__":
    unittest.main()
