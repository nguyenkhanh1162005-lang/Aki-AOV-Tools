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
            legacy: [("legacy.zip", legacy)],
            verifiedFinalEntries: [
                "assetbundle/legacy-only.assetbundle": legacyNewEntry
            ]
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

    func testPackerPlannerBlocksDuplicatePathInsideOneLegacyCatalog() {
        let duplicated = pack("assetbundle/repeated.assetbundle", crc: 10)
        let current = packer(entries: [], links: [])
        let legacy = packer(entries: [duplicated, duplicated], links: [])

        let plan = AoVRuntimeCatalogMergePlanner.planPacker(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .duplicateLegacyPath &&
            $0.path == "assetbundle/repeated.assetbundle"
        })
    }

    func testVerificationPlannerBlocksDuplicateDatabinPathInsideOneLegacyCatalog() {
        let path = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        let duplicate = verify(path, crc: 123)
        let current = verification(zip: [], databin: [])
        let legacy = verification(zip: [], databin: [duplicate, duplicate])

        let plan = AoVRuntimeCatalogMergePlanner.planVerification(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .duplicateLegacyVerificationPath &&
            $0.path == path
        })
    }

    func testVerificationPlannerBlocksDuplicatePathInsideOneLegacyCatalog() {
        let path = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
        let duplicate = verify(path, crc: 123)
        let current = verification(zip: [], databin: [])
        let legacy = verification(zip: [duplicate, duplicate], databin: [])

        let plan = AoVRuntimeCatalogMergePlanner.planVerification(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .duplicateLegacyVerificationPath &&
            $0.path == path
        })
    }

    func testPackerPlannerBlocksNewPathWithoutVerifiedAssetBundlePayload() {
        let newEntry = pack("assetbundle/not-in-res.assetbundle", crc: 10)
        let current = packer(entries: [], links: [])
        let legacy = packer(entries: [newEntry], links: [])

        let plan = AoVRuntimeCatalogMergePlanner.planPacker(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .assetBundlePayloadRequired &&
            $0.path == "assetbundle/not-in-res.assetbundle"
        })
    }

    func testPackerPlannerUpdatesExistingPathOnlyWithVerifiedFinalBundle() {
        let currentEntry = pack("assetbundle/hero.assetbundle", crc: 10)
        let legacyEntry = pack("assetbundle/hero.assetbundle", crc: 11)
        let verifiedFinal = pack("assetbundle/hero.assetbundle", crc: 99)
        let current = packer(entries: [currentEntry], links: [])
        let legacy = packer(entries: [legacyEntry], links: [])

        let plan = AoVRuntimeCatalogMergePlanner.planPacker(
            current: current,
            legacy: [("legacy.zip", legacy)],
            verifiedFinalEntries: [verifiedFinal.pathInIFS: verifiedFinal]
        )

        XCTAssertTrue(plan.safeToWrite)
        XCTAssertEqual(plan.updatedPaths, ["assetbundle/hero.assetbundle"])
        XCTAssertEqual(plan.candidateCatalog?.allResourcePackInfo, [verifiedFinal])
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

    func testVerificationPlannerUsesOnlyExplicitlyVerifiedFinalChecksums() {
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
            verifiedFinalDatabinChecksumValues: [databinPath: 987654321],
            verifiedFinalZIPChecksumValues: ["New/Legacy.pkg.bytes": 789]
        )

        XCTAssertTrue(plan.safeToWrite)
        XCTAssertEqual(plan.unchangedZIPPaths, [zipPath])
        XCTAssertEqual(plan.addedZIPPaths, ["New/Legacy.pkg.bytes"])
        XCTAssertEqual(plan.updatedDatabinPaths, [databinPath])
        XCTAssertEqual(
            plan.candidateCatalog?.allDatabinVerificationInfo.first?.crcXor,
            987654321
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

    func testVerificationPlannerRequiresVerifiedChecksumForNewDatabinEntries() {
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
            $0.reason == .databinChecksumAlgorithmUnknown && $0.path == path
        })
    }

    func testVerificationPlannerBlocksChangedDatabinWithoutVerifiedChecksum() {
        let path = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        let current = verification(zip: [], databin: [verify(path, crc: 100)])
        let legacy = verification(zip: [], databin: [verify(path, crc: 200)])

        let plan = AoVRuntimeCatalogMergePlanner.planVerification(
            current: current,
            legacy: [("legacy.zip", legacy)]
        )

        XCTAssertFalse(plan.safeToWrite)
        XCTAssertNil(plan.candidateCatalog)
        XCTAssertTrue(plan.issues.contains {
            $0.reason == .databinChecksumAlgorithmUnknown && $0.path == path
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
