import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.mod_pipeline import _assess_asset_reference_sources


def _assetref_payload(label=b"fixture"):
    return (
        b"\\x31\\x0a\\x01\\x00"
        + b"\\x08\\x00\\x00\\x00ROOTC\\x00\\x00\\x00"
        + b"\\x02\\x00\\x00\\x00\\x0d\\x00\\x00\\x00\\x06\\x00\\x00\\x00"
        + b"JTCom."
        + b"\\x00\\x00\\x00\\x08\\x00\\x00\\x00"
        + b"TypeAssetRefAnalyser.AssetReferenceSet"
        + b"\\x04\\x00\\x00\\x00baseSubset\\x00"
        + label
    )


def _write_direct(path: Path, resources: dict[str, bytes]):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in resources.items():
            archive.writestr(name, payload)


def _write_wrapper(path: Path, resources: dict[str, bytes]):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as inner:
        for name, payload in resources.items():
            inner.writestr("Resources/1.64.1/" + name, payload)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as outer:
        outer.writestr("Mod/Resources.zip", buffer.getvalue())
        outer.writestr("Mod/DanhSachSkin.txt", "Test\\n")


class AssetReferencePipelineTests(unittest.TestCase):
    def test_distinct_actor_assetrefs_are_inventory_only_and_passthrough_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "p9.zip"
            overlay = Path(directory) / "vtK.zip"
            _write_wrapper(base, {
                "AssetRefs/Hero/593_AssetRef.bytes": _assetref_payload(b"actor-593"),
            })
            _write_direct(overlay, {
                "AssetRefs/Hero/595_AssetRef.bytes": _assetref_payload(b"actor-595"),
            })

            report = _assess_asset_reference_sources([str(base), str(overlay)])

            self.assertEqual(report["state"], "UNIQUE_PATHS_PASSTHROUGH")
            self.assertEqual(report["assetref_count"], 2)
            self.assertEqual(report["unique_path_count"], 2)
            self.assertEqual(report["actor_ids"], ["593", "595"])
            self.assertEqual(report["actor_count"], 2)
            self.assertEqual(report["conflict_count"], 0)
            self.assertTrue(report["passthrough_safe"])
            self.assertFalse(report["merge_supported"])
            self.assertEqual(report["recognized_count"], 2)

    def test_same_assetref_path_with_different_payloads_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.zip"
            overlay = Path(directory) / "overlay.zip"
            resource = "AssetRefs/Hero/593_AssetRef.bytes"
            _write_wrapper(base, {resource: _assetref_payload(b"base")})
            _write_direct(overlay, {resource: _assetref_payload(b"overlay")})

            report = _assess_asset_reference_sources([str(base), str(overlay)])

            self.assertEqual(report["state"], "BLOCKED")
            self.assertEqual(report["conflict_count"], 1)
            self.assertFalse(report["passthrough_safe"])
            self.assertEqual(report["conflicts"][0]["path"], resource)

    def test_identical_assetref_duplicates_are_counted_without_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.zip"
            overlay = Path(directory) / "overlay.zip"
            resource = "AssetRefs/Hero/593_AssetRef.bytes"
            payload = _assetref_payload()
            _write_wrapper(base, {resource: payload})
            _write_direct(overlay, {resource: payload})

            report = _assess_asset_reference_sources([str(base), str(overlay)])

            self.assertEqual(report["state"], "UNIQUE_PATHS_PASSTHROUGH")
            self.assertEqual(report["unique_path_count"], 1)
            self.assertEqual(report["identical_duplicate_count"], 1)
            self.assertEqual(report["conflict_count"], 0)


if __name__ == "__main__":
    unittest.main()
