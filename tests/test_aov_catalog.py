import unittest

from app.aov_catalog import (
    AOVCatalogMergeError,
    merge_resource_packer_tree,
    merge_resource_verification_tree,
)


def packer(name, rows, edges):
    return {
        "m_Name": name,
        "Version": "1.64.1.11",
        "AllResourcePackInfo": rows,
        "AllParentIdexToChild": edges,
    }


class AOVCatalogTests(unittest.TestCase):
    def test_merges_paths_hashes_and_remaps_edges(self):
        base = packer("ResourcePackerInfoSetXML", [
            {"PathInIFS": "a.assetbundle", "pkgType": 0, "CrcXor": 1, "Tag": "A", "Flags": 0,
             "ResourceInfos": [{"HashCode": 1, "Extension": ""}]},
            {"PathInIFS": "b.assetbundle", "pkgType": 0, "CrcXor": 2, "Tag": "B", "Flags": 0,
             "ResourceInfos": [{"HashCode": 2, "Extension": ""}]},
        ], [{"parentIdx": 0, "childIdx": 1}])
        overlay = packer("ResourcePackerInfoSetXML", [
            {"PathInIFS": "b.assetbundle", "pkgType": 0, "CrcXor": 99, "Tag": "B", "Flags": 4,
             "ResourceInfos": [{"HashCode": 2, "Extension": ""}, {"HashCode": 3, "Extension": "png"}]},
            {"PathInIFS": "c.assetbundle", "pkgType": 0, "CrcXor": 3, "Tag": "C", "Flags": 0,
             "ResourceInfos": [{"HashCode": 4, "Extension": ""}]},
        ], [{"parentIdx": 0, "childIdx": 1}])
        merged, report = merge_resource_packer_tree(base, overlay)
        self.assertEqual([x["PathInIFS"] for x in merged["AllResourcePackInfo"]],
                         ["a.assetbundle", "b.assetbundle", "c.assetbundle"])
        self.assertEqual(merged["AllResourcePackInfo"][1]["Flags"], 4)
        self.assertEqual(len(merged["AllResourcePackInfo"][1]["ResourceInfos"]), 2)
        self.assertEqual(merged["AllParentIdexToChild"], [
            {"parentIdx": 0, "childIdx": 1},
            {"parentIdx": 1, "childIdx": 2},
        ])
        self.assertEqual(report["appended_paths"], ["c.assetbundle"])
        self.assertEqual(report["crc_metadata_conflicts_kept_base"], ["b.assetbundle"])

    def test_rejects_incompatible_tags(self):
        base = packer("ResourcePackerInfoSetXML", [
            {"PathInIFS": "a", "pkgType": 0, "CrcXor": 1, "Tag": "A", "Flags": 0, "ResourceInfos": []}
        ], [])
        overlay = packer("ResourcePackerInfoSetXML", [
            {"PathInIFS": "a", "pkgType": 0, "CrcXor": 1, "Tag": "B", "Flags": 0, "ResourceInfos": []}
        ], [])
        with self.assertRaises(AOVCatalogMergeError):
            merge_resource_packer_tree(base, overlay)

    def test_merges_verification_lists_and_overrides_selected_checksum(self):
        base = {
            "m_Name": "ResourceVerificationInfoSetXML",
            "AllZipVerificationInfo": [
                {"PathInIFS": "a.zip", "CrcXor": 1},
                {"PathInIFS": "c.zip", "CrcXor": 5},
            ],
            "AllDatabinVerificationInfo": [
                {"PathInIFS": "a.bytes", "CrcXor": 2}
            ],
        }
        overlay = {
            "m_Name": "ResourceVerificationInfoSetXML",
            "AllZipVerificationInfo": [
                {"PathInIFS": "a.zip", "CrcXor": 99},
                {"PathInIFS": "b.zip", "CrcXor": 3},
                {"PathInIFS": "c.zip", "CrcXor": 55},
            ],
            "AllDatabinVerificationInfo": [
                {"PathInIFS": "b.bytes", "CrcXor": 4}
            ],
        }
        merged, report = merge_resource_verification_tree(base, overlay, prefer_overlay_paths={"a.zip"})
        self.assertEqual(merged["AllZipVerificationInfo"], [
            {"PathInIFS": "a.zip", "CrcXor": 99},
            {"PathInIFS": "c.zip", "CrcXor": 5},
            {"PathInIFS": "b.zip", "CrcXor": 3},
        ])
        self.assertEqual(len(merged["AllDatabinVerificationInfo"]), 2)
        self.assertEqual(report["AllZipVerificationInfo"]["checksum_conflicts_overridden_with_overlay"], ["a.zip"])
        self.assertEqual(report["AllZipVerificationInfo"]["checksum_conflicts_kept_base"], ["c.zip"])



    def test_overrides_selected_packer_crc_metadata(self):
        base = packer("ResourcePackerInfoSetXML", [
            {"PathInIFS": "owned.assetbundle", "pkgType": 0, "CrcXor": 10, "Tag": "A", "Flags": 0,
             "ResourceInfos": [{"HashCode": 1, "Extension": ""}]},
            {"PathInIFS": "unowned.assetbundle", "pkgType": 0, "CrcXor": 20, "Tag": "B", "Flags": 0,
             "ResourceInfos": [{"HashCode": 2, "Extension": ""}]},
        ], [])
        overlay = packer("ResourcePackerInfoSetXML", [
            {"PathInIFS": "owned.assetbundle", "pkgType": 0, "CrcXor": 99, "Tag": "A", "Flags": 0,
             "ResourceInfos": [{"HashCode": 3, "Extension": ""}]},
            {"PathInIFS": "unowned.assetbundle", "pkgType": 0, "CrcXor": 88, "Tag": "B", "Flags": 0,
             "ResourceInfos": [{"HashCode": 4, "Extension": ""}]},
        ], [])
        merged, report = merge_resource_packer_tree(
            base, overlay, prefer_overlay_paths={"owned.assetbundle"}
        )
        rows = {row["PathInIFS"]: row for row in merged["AllResourcePackInfo"]}
        self.assertEqual(rows["owned.assetbundle"]["CrcXor"], 99)
        self.assertEqual(rows["unowned.assetbundle"]["CrcXor"], 20)
        self.assertEqual(
            report["crc_metadata_conflicts_overridden_with_overlay"],
            ["owned.assetbundle"],
        )
        self.assertEqual(
            report["crc_metadata_conflicts_kept_base"],
            ["unowned.assetbundle"],
        )


if __name__ == "__main__":
    unittest.main()
