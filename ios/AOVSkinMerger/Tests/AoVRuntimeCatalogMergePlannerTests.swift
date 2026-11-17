import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVRuntimeCatalogMergePlannerTests: XCTestCase {
    func testPackerPlannerAppendsNewPathsAndRemapsParentChildIndexes() {
        let currentEntry = pack("assetbundle/current.assetbundle", crc: 10)
        let legacyNewEntry = pack("assetbundle/legacy-only.assetbundle", crc: 20)
        let current = packer(entries: [currentEntry], links: [])
        let legacy = packer(
            entries: [currentEntry, legacyNewEntry],
            links: [AoVPackInfoMapItem(parentIdx: 0, childIdx: 1)]
        )

        let plan = AoVRuntimeCatalogMergePlanner.planPacker(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertTrue(plan.safeToWrite)
        XCTAssertEqual(plan.addedPaths, ["assetbundle/legacy-only.assetbundle"])
        XCTAssertEqual(plan.unchangedPaths, ["assetbundle/current.assetbundle"])
        XCTAssertEqual(plan.addedParentChildLinkCount, 1)
        XCTAssertEqual(plan.candidateCatalog?.allResourcePackInfo.map(\.pathInIFS), [
            "assetbundle/current.assetbundle",
            "assetbundle/legacy-only.assetbundle"
        ])
        XCTAssertEqual(
            plan.candidateCatalog?.allParentIndexToChild,
            [AoVPackInfoMapItem(parentIdx: 0, childIdx: 1)]
        )
        XCTAssertEqual(plan.candidateCatalog?.version, current.version)
        XCTAssertEqual(plan.candidateCatalog?.svnRevision, current.svnRevision)
    }

    func testPackerPlannerBlocksChangedExistingBundleInsteadOfOverwriting() {
        let current = packer(entries: [pack("assetbundle/hero.assetbundle", crc: 10)], links: [])
        let legacy = packer(entries: [pack("assetbundle/hero.assetbundle", crc: 11)], links: [])

        let plan = AoVRuntimeCatalogMergePlanner.planPacker(
            current: current,
            legacy: [("old-mod.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertEqual(plan.changedExistingPaths, ["assetbundle/hero.assetbundle"])
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .changedExistingAssetBundle &&
            $0.path == "assetbundle/hero.assetbundle"
        })
    }

    func testPackerPlannerBlocksConflictingLegacyCatalogEntries() {
        let current = packer(entries: [], links: [])
        let first = packer(entries: [pack("assetbundle/conflict.assetbundle", crc: 1)], links: [])
        let second = packer(entries: [pack("assetbundle/conflict.assetbundle", crc: 2)], links: [])

        let plan = AoVRuntimeCatalogMergePlanner.planPacker(
            current: current,
            legacy: [("mod-a.zip", first), ("mod-b.zip", second)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .conflictingLegacyPath &&
            $0.path == "assetbundle/conflict.assetbundle"
        })
    }

    func testVerificationPlannerPreservesEqualZIPChecksumsAndRecalculatesDatabin() {
        let zipPath = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
        let databinPath = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        let payload = Data("new final databin payload".utf8)
        let current = verification(
            zip: [verify(zipPath, crc: 123)],
            databin: [verify(databinPath, crc: 456)]
        )
        let legacy = verification(
            zip: [verify(zipPath, crc: 123), verify("New/Legacy.pkg.bytes", crc: 789)],
            databin: [verify(databinPath, crc: 111)]
        )

        let plan = AoVRuntimeCatalogMergePlanner.planVerification(
            current: current,
            legacy: [("legacy.zip", legacy)],
            finalDatabinPayloads: [databinPath: payload],
            finalZIPChecksumValues: ["New/Legacy.pkg.bytes": 789]
        )

        XCTAssertTrue(plan.safeToWrite)
        XCTAssertEqual(plan.unchangedZIPPaths, [zipPath])
        XCTAssertEqual(plan.addedZIPPaths, ["New/Legacy.pkg.bytes"])
        XCTAssertEqual(plan.updatedDatabinPaths, [databinPath])
        XCTAssertEqual(
            plan.candidateCatalog?.allDatabinVerificationInfo.first?.crcXor,
            AoVRuntimeCatalogChecksum.verificationDatabinCRC32(payload)
        )
    }

    func testVerificationPlannerRequiresVerifiedChecksumForNewZIPEntries() {
        let path = "New/Legacy.pkg.bytes"
        let current = verification(zip: [], databin: [])
        let legacy = verification(zip: [verify(path, crc: 123)], databin: [])

        let plan = AoVRuntimeCatalogMergePlanner.planVerification(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .zipChecksumAlgorithmUnknown && $0.path == path
        })
    }

    func testVerificationPlannerBlocksUnknownZIPChecksumChange() {
        let path = "Ages/Actions/SysEvent.pkg.bytes"
        let current = verification(zip: [verify(path, crc: 123)], databin: [])
        let legacy = verification(zip: [verify(path, crc: 456)], databin: [])

        let plan = AoVRuntimeCatalogMergePlanner.planVerification(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .zipChecksumAlgorithmUnknown && $0.path == path
        })
    }

    func testVerificationPlannerRequiresFinalDatabinBytesForNewEntries() {
        let path = "Databin/Client/Actor/NewSkin.bytes"
        let current = verification(zip: [], databin: [])
        let legacy = verification(zip: [], databin: [verify(path, crc: 123)])

        let plan = AoVRuntimeCatalogMergePlanner.planVerification(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .databinPayloadRequired && $0.path == path
        })
    }

    private func pack(_ path: String, crc: Int64) -> AoVRuntimeResourcePackInfo {
        AoVRuntimeResourcePackInfo(
            pathInIFS: path,
            pkgType: 0,
            crcXor: crc,
            tag: "Hero",
            flags: 512,
            resourceInfos: [AoVRuntimeResourceItem(hashCode: crc, extensionName: "assetbundle")]
        )
    }

    private func packer(
        entries: [AoVRuntimeResourcePackInfo],
        links: [AoVPackInfoMapItem]
    ) -> AoVRuntimeResourcePackerCatalog {
        AoVRuntimeResourcePackerCatalog(
            name: "ResourcePackerInfoSetXML",
            version: "1.64.2.1",
            svnRevision: "1800000",
            setName: "ResourcePackerInfoSetXML",
            allResourcePackInfo: entries,
            allParentIndexToChild: links,
            trailingData: Data([0xAA])
        )
    }

    private func verify(_ path: String, crc: Int64) -> AoVRuntimeVerificationEntry {
        AoVRuntimeVerificationEntry(pathInIFS: path, crcXor: crc)
    }

    private func verification(
        zip: [AoVRuntimeVerificationEntry],
        databin: [AoVRuntimeVerificationEntry]
    ) -> AoVRuntimeResourceVerificationCatalog {
        AoVRuntimeResourceVerificationCatalog(
            name: "ResourceVerificationInfoSetXML",
            version: "1.64.2.1",
            svnRevision: "1800000",
            allZipVerificationInfo: zip,
            allDatabinVerificationInfo: databin,
            trailingData: Data([0x55])
        )
    }
}
