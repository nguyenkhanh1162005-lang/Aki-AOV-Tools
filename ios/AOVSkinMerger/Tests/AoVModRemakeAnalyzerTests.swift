import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVModRemakeAnalyzerTests: XCTestCase {
    func testPathFindingKindsRemainSeparate() {
        XCTAssertEqual(AoVRemakePathKind.identical.rawValue, "identical")
        XCTAssertEqual(AoVRemakePathKind.changed.rawValue, "changed")
        XCTAssertEqual(AoVRemakePathKind.legacyOnly.rawValue, "legacyOnly")
    }

    func testReportNeverClaimsRebuildIsAvailable() {
        let report = AoVModRemakeReport(
            base: ArchiveInventory(
                id: UUID(), fileName: "new-res.zip", fileSize: 1, fileCount: 0,
                uncompressedBytes: 0, paths: [], pathDigests: [], unityFSBundles: [],
                unityFSUnrecognizedCount: 0, unityFSTooLargeCount: 0, unityFSInspectionFailures: []
            ),
            legacy: [],
            baseVersion: "new-build",
            legacyVersions: [:],
            identicalPaths: [],
            changedPaths: [],
            legacyOnlyPaths: [],
            baseOnlyPathCount: 0,
            baseHasPackerCatalog: true,
            baseHasVerificationCatalog: true,
            legacyHasPackerCatalog: [],
            legacyHasVerificationCatalog: [],
            warnings: []
        )

        XCTAssertFalse(report.rebuildAvailable)
    }
}
