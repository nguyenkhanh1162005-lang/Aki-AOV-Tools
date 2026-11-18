import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVModRemakeAnalyzerTests: XCTestCase {
    func testPackageAuditClassifiesDifferentModResourceFamilies() {
        let paths = [
            "Resources/2.0.0/assetbundle/resourcepackerinfosetall.assetbundle",
            "Resources/2.0.0/Databin/Actor/100_AssetRef.bytes",
            "Resources/2.0.0/Ages/CommonActions.pkg.bytes",
            "Resources/2.0.0/MSES/ActorTable.bytes",
            "Resources/2.0.0/IFix/Patch.bytes",
            "Resources/2.0.0/Databin/ResSkinSeniorLabelCfg.bytes",
            "Resources/2.0.0/Sound/Bank.bnk",
            "Resources/2.0.0/Sound/1001.wem",
            "Resources/2.0.0/Actor/Actions.pkg.bytes",
            "Resources/2.0.0/Databin/Actor/Effect.bytes",
            "Resources/2.0.0/Assets/globalgamemanagers.assets",
            "Resources/2.0.0/version.txt",
            "Resources/2.0.0/custom/unknown.bin"
        ]
        let digests = paths.map {
            ArchivePathDigest(
                archiveName: "generic-mod.zip",
                path: $0,
                sha256: String(repeating: "a", count: 64),
                uncompressedBytes: 128
            )
        }
        let inventory = ArchiveInventory(
            id: UUID(),
            fileName: "generic-mod.zip",
            fileSize: 1024,
            fileCount: paths.count,
            uncompressedBytes: UInt64(paths.count * 128),
            paths: paths,
            pathDigests: digests,
            unityFSBundles: [],
            unityFSUnrecognizedCount: 0,
            unityFSTooLargeCount: 0,
            unityFSInspectionFailures: []
        )

        let audit = AoVModPackageAudit.make(from: inventory, version: "2.0.0")
        let counts = Dictionary(uniqueKeysWithValues: audit.families.map {
            ($0.family, $0.fileCount)
        })

        XCTAssertEqual(audit.archiveName, "generic-mod.zip")
        XCTAssertEqual(audit.version, "2.0.0")
        XCTAssertEqual(counts[.runtimeCatalog], 1)
        XCTAssertEqual(counts[.assetReference], 1)
        XCTAssertEqual(counts[.commonActions], 1)
        XCTAssertEqual(counts[.mses], 1)
        XCTAssertEqual(counts[.ifix], 1)
        XCTAssertEqual(counts[.heroSkinConfig], 1)
        XCTAssertEqual(counts[.wwiseBank], 1)
        XCTAssertEqual(counts[.wwiseMedia], 1)
        XCTAssertEqual(counts[.actionPackage], 1)
        XCTAssertEqual(counts[.databin], 1)
        XCTAssertEqual(counts[.unitySerializedAsset], 1)
        XCTAssertEqual(counts[.metadata], 1)
        XCTAssertEqual(counts[.other], 1)
        XCTAssertEqual(audit.families.reduce(0) { $0 + $1.fileCount }, paths.count)
    }

    func testPathFindingKindsRemainSeparate() {
        XCTAssertEqual(AoVRemakePathKind.identical.rawValue, "identical")
        XCTAssertEqual(AoVRemakePathKind.changed.rawValue, "changed")
        XCTAssertEqual(AoVRemakePathKind.legacyOnly.rawValue, "legacyOnly")
    }

    func testReportNeverClaimsRebuildIsAvailable() {
        let emptyPacker = emptyCatalogReport(.packer)
        let emptyVerification = emptyCatalogReport(.verification)
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
            packerCatalogComparison: emptyPacker,
            verificationCatalogComparison: emptyVerification,
            runtimeCatalogComparison: emptyRuntimeCatalogReport(),
            runtimePackerMergePlan: nil,
            runtimeVerificationMergePlan: nil,
            catalogMergePlanFindings: [],
            warnings: []
        )

        XCTAssertFalse(report.rebuildAvailable)
    }

    func testCatalogComparisonMatchesBundleAndReportsChangedPointersAndDependencies() {
        let old = record(
            source: "old-mod.zip",
            bundle: "actor/1001",
            entries: [
                entry("Assets/Hero.prefab", pathID: 10, preloadIndex: 0, preloadSize: 4),
                entry("Assets/OldEffect.prefab", pathID: 11, preloadIndex: 4, preloadSize: 2)
            ],
            dependencies: ["common", "actor/shared"]
        )
        let current = record(
            source: "new-res.zip",
            bundle: "actor/1001",
            entries: [
                entry("Assets/Hero.prefab", pathID: 90, preloadIndex: 0, preloadSize: 6),
                entry("Assets/NewEffect.prefab", pathID: 91, preloadIndex: 6, preloadSize: 2)
            ],
            dependencies: ["common", "actor/shared", "shader/new"]
        )

        let report = AoVModRemakeAnalyzer.compareCatalogRecords(
            current: [current],
            legacy: [old],
            kind: .packer
        )

        XCTAssertEqual(report.mappedRecordCount, 1)
        XCTAssertEqual(report.missingCurrentRecordCount, 0)
        XCTAssertEqual(report.mappedContainerKeyCount, 1)
        XCTAssertEqual(report.legacyOnlyContainerKeyCount, 1)
        XCTAssertEqual(report.currentOnlyContainerKeyCount, 1)
        XCTAssertEqual(report.changedAssetPointerCount, 1)
        XCTAssertEqual(report.changedPreloadRangeCount, 1)
        XCTAssertEqual(report.dependenciesAddedInCurrentCount, 1)
        XCTAssertEqual(report.dependenciesRemovedInCurrentCount, 0)
        XCTAssertEqual(report.findings.first?.legacyOnlyKeySamples, ["Assets/OldEffect.prefab"])
        XCTAssertEqual(report.findings.first?.currentOnlyKeySamples, ["Assets/NewEffect.prefab"])
        XCTAssertEqual(report.findings.first?.changedPointerKeySamples, ["Assets/Hero.prefab"])
        XCTAssertEqual(report.findings.first?.status, .mappedCandidate)
    }

    func testCatalogComparisonFlagsMissingAndAmbiguousBundleRecords() {
        let missing = record(source: "mod.zip", bundle: "actor/missing", entries: [], dependencies: [])
        let duplicateA = record(source: "new-res.zip", bundle: "actor/duplicate", entries: [], dependencies: [])
        let duplicateB = record(source: "new-res.zip", bundle: "actor/duplicate", entries: [], dependencies: [])
        let oldDuplicate = record(source: "mod.zip", bundle: "actor/duplicate", entries: [], dependencies: [])

        let report = AoVModRemakeAnalyzer.compareCatalogRecords(
            current: [duplicateA, duplicateB],
            legacy: [missing, oldDuplicate],
            kind: .verification
        )

        XCTAssertEqual(report.missingCurrentRecordCount, 1)
        XCTAssertEqual(report.ambiguousRecordCount, 1)
        XCTAssertEqual(report.currentOnlyRecordCount, 0)
        XCTAssertEqual(Set(report.findings.map(\.status)), [.missingFromCurrent, .ambiguousCurrentMatch])
    }

    func testCatalogComparisonAuditsInvalidPreloadRangesAndCompatibilityChanges() {
        let old = record(
            source: "mod.zip",
            bundle: "actor/3003",
            entries: [entry("Assets/BadRange.prefab", pathID: 1, preloadIndex: 7, preloadSize: 2)],
            dependencies: ["common"],
            mainAssetPathID: 200,
            classCompatibilityIDs: [28]
        )
        let current = record(
            source: "new-res.zip",
            bundle: "actor/3003",
            entries: [entry("Assets/BadRange.prefab", pathID: 90, preloadIndex: 0, preloadSize: 1)],
            dependencies: ["common"],
            mainAssetPathID: 900,
            classCompatibilityIDs: [28, 114]
        )

        let report = AoVModRemakeAnalyzer.compareCatalogRecords(
            current: [current],
            legacy: [old],
            kind: .packer
        )

        XCTAssertEqual(report.invalidLegacyPreloadRangeCount, 1)
        XCTAssertEqual(report.invalidCurrentPreloadRangeCount, 0)
        XCTAssertEqual(report.changedMainAssetPointerCount, 1)
        XCTAssertEqual(report.changedClassCompatibilityCount, 1)
    }

    func testCatalogComparisonReportsUnresolvedPointerReferences() {
        let old = record(
            source: "mod.zip",
            bundle: "actor/4004",
            entries: [entry("Assets/Hero.prefab", pathID: 10)],
            dependencies: [],
            unresolvedLocalPointerCount: 2,
            invalidExternalPointerCount: 1
        )
        let current = record(
            source: "new-res.zip",
            bundle: "actor/4004",
            entries: [entry("Assets/Hero.prefab", pathID: 90)],
            dependencies: [],
            unresolvedLocalPointerCount: 0,
            invalidExternalPointerCount: 0
        )

        let report = AoVModRemakeAnalyzer.compareCatalogRecords(
            current: [current],
            legacy: [old],
            kind: .packer
        )

        XCTAssertEqual(report.unresolvedLegacyLocalPointerCount, 2)
        XCTAssertEqual(report.unresolvedCurrentLocalPointerCount, 0)
        XCTAssertEqual(report.invalidLegacyExternalPointerCount, 1)
        XCTAssertEqual(report.invalidCurrentExternalPointerCount, 0)
    }

    func testCatalogComparisonDoesNotSilentlyResolveDuplicateContainerKeys() {
        let old = record(
            source: "mod.zip",
            bundle: "actor/2002",
            entries: [
                entry("Assets/Duplicate.prefab", pathID: 1),
                entry("Assets/Duplicate.prefab", pathID: 2)
            ],
            dependencies: []
        )
        let current = record(
            source: "new-res.zip",
            bundle: "actor/2002",
            entries: [entry("Assets/Duplicate.prefab", pathID: 3)],
            dependencies: []
        )

        let report = AoVModRemakeAnalyzer.compareCatalogRecords(
            current: [current],
            legacy: [old],
            kind: .packer
        )

        XCTAssertEqual(report.ambiguousContainerKeyCount, 1)
        XCTAssertEqual(report.mappedContainerKeyCount, 0)
    }


    private func emptyRuntimeCatalogReport() -> AoVRuntimeCatalogComparisonReport {
        func section(_ title: String) -> AoVRuntimeCatalogSectionReport {
            AoVRuntimeCatalogSectionReport(
                title: title,
                currentCatalogVersions: [],
                legacyCatalogVersions: [],
                currentEntryCount: 0,
                legacyEntryCount: 0,
                uniqueLegacyPathCount: 0,
                identicalEntryCount: 0,
                changedEntryCount: 0,
                legacyOnlyEntryCount: 0,
                currentOnlyEntryCount: 0,
                duplicateCurrentPathCount: 0,
                duplicateLegacyPathCount: 0,
                conflictingLegacyPathCount: 0,
                invalidReferenceCount: 0,
                findings: []
            )
        }
        return AoVRuntimeCatalogComparisonReport(
            packerResources: section("Packer resources"),
            packerParentChildLinks: section("Packer parent-child links"),
            verificationZIP: section("Verification ZIP"),
            verificationDatabin: section("Verification Databin")
        )
    }

    private func emptyCatalogReport(_ kind: AoVRemakeCatalogKind) -> AoVRemakeCatalogKindReport {
        AoVRemakeCatalogKindReport(
            kind: kind,
            currentRecordCount: 0,
            legacyRecordCount: 0,
            mappedRecordCount: 0,
            missingCurrentRecordCount: 0,
            ambiguousRecordCount: 0,
            currentOnlyRecordCount: 0,
            mappedContainerKeyCount: 0,
            legacyOnlyContainerKeyCount: 0,
            currentOnlyContainerKeyCount: 0,
            ambiguousContainerKeyCount: 0,
            changedAssetPointerCount: 0,
            changedPreloadRangeCount: 0,
            invalidLegacyPreloadRangeCount: 0,
            invalidCurrentPreloadRangeCount: 0,
            changedMainAssetPointerCount: 0,
            changedClassCompatibilityCount: 0,
            unresolvedLegacyLocalPointerCount: 0,
            unresolvedCurrentLocalPointerCount: 0,
            invalidLegacyExternalPointerCount: 0,
            invalidCurrentExternalPointerCount: 0,
            dependenciesAddedInCurrentCount: 0,
            dependenciesRemovedInCurrentCount: 0,
            findings: []
        )
    }

    private func record(
        source: String,
        bundle: String,
        entries: [AoVRemakeCatalogEntrySnapshot],
        dependencies: [String],
        unresolvedLocalPointerCount: Int = 0,
        invalidExternalPointerCount: Int = 0,
        mainAssetPathID: Int64 = 200,
        classCompatibilityIDs: [Int32] = [28]
    ) -> AoVRemakeCatalogRecordSnapshot {
        AoVRemakeCatalogRecordSnapshot(
            sourceArchive: source,
            catalogPath: "assetbundle/resourcepackerinfosetall.assetbundle",
            nodePath: "CAB-catalog",
            serializedFormatVersion: 17,
            unityVersion: "2020.3.34f1",
            targetPlatform: 13,
            objectPathID: 100,
            name: bundle,
            assetBundleName: bundle,
            preloadCount: 8,
            invalidPreloadRangeCount: entries.filter { !$0.preloadRangeValid }.count,
            invalidPreloadRangeSamples: entries.filter { !$0.preloadRangeValid }.map(\.key),
            classCompatibilityIDs: classCompatibilityIDs,
            resolvedLocalPointerCount: 3,
            externalPointerCount: 1,
            nullPointerCount: 0,
            unresolvedLocalPointerCount: unresolvedLocalPointerCount,
            invalidExternalPointerCount: invalidExternalPointerCount,
            pointerAuditSamples: [],
            mainAssetFileID: 0,
            mainAssetPathID: mainAssetPathID,
            dependencies: dependencies,
            containerEntries: entries
        )
    }

    private func entry(
        _ key: String,
        pathID: Int64,
        preloadIndex: Int32 = 0,
        preloadSize: Int32 = 1
    ) -> AoVRemakeCatalogEntrySnapshot {
        AoVRemakeCatalogEntrySnapshot(
            key: key,
            preloadIndex: preloadIndex,
            preloadSize: preloadSize,
            assetFileID: 0,
            assetPathID: pathID,
            preloadRangeValid: preloadIndex >= 0 && preloadSize >= 0 && Int64(preloadIndex) + Int64(preloadSize) <= 8
        )
    }
}
