import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.aov_bytes import decode_aov_resource, encrypt_aes_zstd_resource
from app.notice_rewriter import NoticeRewriteError, rewrite_archive_branding


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", allowZip64=True) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class NoticeBrandingRewriteTests(unittest.TestCase):
    def test_rewrites_exact_branding_inside_nested_resources_zip_and_aov_bytes(self):
        original_text = (
            "KIANA MOD AOV\n"
            "Mod được tạo bởi youtuber KIANA MOD AOV\n"
            "Web Mod\n"
            "Youtube\n"
        ).encode("utf-8")
        encrypted = encrypt_aes_zstd_resource(
            original_text, "Branding.lua.bytes"
        )
        resources = _zip_bytes({
            "Scripts/Branding.lua.bytes": encrypted,
            "binary/opaque.assetbundle": b"UnityFS\x00opaque",
        })
        outer = _zip_bytes({"Aki-AOV-21Skin-Merged/Resources.zip": resources})

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "source.zip"
            output = folder / "aki-branding.zip"
            source.write_bytes(outer)

            report = rewrite_archive_branding([str(source)], str(output))

            self.assertTrue(report["structurally_validated"])
            self.assertFalse(report["game_tested"])
            self.assertEqual(report["replacement_counts"]["KIANA MOD AOV"], 1)
            self.assertEqual(
                report["replacement_counts"][
                    "Mod được tạo bởi youtuber KIANA MOD AOV"
                ],
                1,
            )
            self.assertEqual(report["replacement_counts"]["Web Mod"], 1)
            self.assertEqual(report["replacement_counts"]["Youtube"], 1)
            self.assertEqual(report["rewritten_aov_wrappers"], 1)
            self.assertEqual(source.read_bytes(), outer)

            with zipfile.ZipFile(output, "r") as archive:
                nested_bytes = archive.read(
                    "Aki-AOV-21Skin-Merged/Resources.zip"
                )
                self.assertIsNone(archive.testzip())
            with zipfile.ZipFile(io.BytesIO(nested_bytes), "r") as resources_zip:
                encoded = resources_zip.read("Scripts/Branding.lua.bytes")
                self.assertEqual(
                    resources_zip.read("binary/opaque.assetbundle"),
                    b"UnityFS\x00opaque",
                )
                self.assertIsNone(resources_zip.testzip())
            decoded, mode = decode_aov_resource(
                encoded, "Scripts/Branding.lua.bytes"
            )
            self.assertEqual(mode, "aes+zstd")
            text = decoded.decode("utf-8")
            self.assertIn("AKI MOD AOV", text)
            self.assertIn("Mod được gộp bởi AKI MOD AOV", text)
            self.assertNotIn("KIANA MOD AOV", text)
            self.assertNotIn("Web Mod", text)
            self.assertNotIn("Youtube", text)

    def test_rewrites_plain_lua_text_without_touching_opaque_binary(self):
        outer = _zip_bytes({
            "Resources/startup.lua": (
                'title = "KIANA MOD AOV"\\nsite = "Web Mod"\\n'
            ).encode("utf-8"),
            "Resources/unknown.bytes": b"\\x00\\x01\\x02opaque",
        })
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "source.zip"
            output = folder / "output.zip"
            source.write_bytes(outer)

            report = rewrite_archive_branding([str(source)], str(output))

            self.assertGreater(report["replacement_total"], 0)
            with zipfile.ZipFile(output, "r") as archive:
                self.assertIn(b"AKI MOD AOV", archive.read("Resources/startup.lua"))
                self.assertEqual(
                    archive.read("Resources/unknown.bytes"),
                    b"\\x00\\x01\\x02opaque",
                )
                self.assertIsNone(archive.testzip())

    def test_no_matching_text_fails_without_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "source.zip"
            output = folder / "output.zip"
            source.write_bytes(_zip_bytes({"Resources/startup.lua": b"title='Hello'"}))

            with self.assertRaisesRegex(NoticeRewriteError, "Không tìm thấy chuỗi"):
                rewrite_archive_branding([str(source)], str(output))
            self.assertFalse(output.exists())

    def test_requires_exactly_one_merged_input_zip(self):
        with self.assertRaisesRegex(NoticeRewriteError, "đúng một ZIP"):
            rewrite_archive_branding(["one.zip", "two.zip"], "out.zip")


if __name__ == "__main__":
    unittest.main()
