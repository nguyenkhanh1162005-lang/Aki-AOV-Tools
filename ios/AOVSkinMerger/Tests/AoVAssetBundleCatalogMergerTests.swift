import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVAssetBundleCatalogMergerTests: XCTestCase {
    func testAddsOnlyLegacyOnlyEntriesAndAppendsTheirPreloadSlice() throws {
        let pointer100 = AoVSerializedAssetPointer(fileID: 0, pathID: 100)
        let pointer200 = AoVSerializedAssetPointer(fileID: 0, pathID: 200)
        let current = record(
            bundle: "actor/1001",
            preloads: [pointer100],
            entries: [entry("assets/hero.prefab", index: 0, size: 1, pointer: pointer100)],
            dependencies: ["common"]
        )
        let legacy = record(
            bundle: "actor/1001",
            preloads: [pointer100, pointer200],
            entries: [
                entry("assets/hero.prefab", index: 0, size: 1, pointer: pointer100),
                entry("assets/legacy-effect.prefab", index: 1, size: 1, pointer: pointer200)
            ],
            dependencies: ["common", "common/fx"]
        )

        let summary = makeSummary(localPathIDs: [100, 200])
        let plan = try AoVAssetBundleCatalogMerger.plan(
            current: current,
            legacy: legacy,
            currentSerializedFile: summary,
            legacySerializedFile: summary,
            availableCurrentBundleNames: ["common", "common/fx", "actor/1001"]
        )

        XCTAssertEqual(plan.addedContainerKeys, ["assets/legacy-effect.prefab"])
        XCTAssertEqual(plan.alreadyPresentContainerKeys, ["assets/hero.prefab"])
        XCTAssertEqual(plan.addedPreloadReferenceCount, 1)
        XCTAssertEqual(plan.addedDependencies, ["common/fx"])
        XCTAssertEqual(plan.mergedRecord.preloadReferences, [pointer100, pointer200])
        XCTAssertEqual(plan.mergedRecord.containerEntries.count, 2)
        XCTAssertEqual(plan.mergedRecord.containerEntries.last?.preloadIndex, 1)
        XCTAssertEqual(plan.mergedRecord.containerEntries.last?.asset, pointer200)
        XCTAssertFalse(plan.externalTargetAuditRequired)
    }

    func testRemapsExternalFileIDByExactGUIDAndPathMatch() throws {
        let sourceReferences = [
            external("guid-old", path: "CAB/unused"),
            external("guid-shared", path: "CAB/shared")
        ]
        let destinationReferences = [
            external("guid-x", path: "CAB/x"),
            external("guid-y", path: "CAB/y"),
            external("guid-shared", path: "CAB/shared")
        ]
        let source = makeSummary(localPathIDs: [], externalReferences: sourceReferences)
        let destination = makeSummary(localPathIDs: [], externalReferences: destinationReferences)
        let oldPointer = AoVSerializedAssetPointer(fileID: 2, pathID: 7007)
        let current = record(bundle: "actor/2002", preloads: [], entries: [], dependencies: [])
        let legacy = record(
            bundle: "actor/2002",
            preloads: [oldPointer],
            entries: [entry("assets/new-effect.prefab", index: 0, size: 1, pointer: oldPointer)],
            dependencies: []
        )

        let plan = try AoVAssetBundleCatalogMerger.plan(
            current: current,
            legacy: legacy,
            currentSerializedFile: destination,
            legacySerializedFile: source,
            availableCurrentBundleNames: ["actor/2002"]
        )

        XCTAssertEqual(plan.remappedExternalPointerCount, 2)
        XCTAssertTrue(plan.externalTargetAuditRequired)
        XCTAssertEqual(plan.mergedRecord.preloadReferences.first?.fileID, 3)
        XCTAssertEqual(plan.mergedRecord.preloadReferences.first?.pathID, 7007)
        XCTAssertEqual(plan.mergedRecord.containerEntries.first?.asset.fileID, 3)
        XCTAssertEqual(plan.externalTargetAuditSamples.count, 2)
    }

    func testExternalPointersAreWritableOnlyWhenTargetCABContainsPathID() throws {
        let source = makeSummary(
            localPathIDs: [],
            externalReferences: [external("guid-shared", path: "CAB/shared")]
        )
        let destination = makeSummary(
            localPathIDs: [],
            externalReferences: [
                external("guid-x", path: "CAB/x"),
                external("guid-y", path: "CAB/y"),
                external("guid-shared", path: "CAB/shared")
            ]
        )
        let oldPointer = AoVSerializedAssetPointer(fileID: 1, pathID: 7007)
        let current = record(bundle: "actor/2500", preloads: [], entries: [], dependencies: [])
        let legacy = record(
            bundle: "actor/2500",
            preloads: [oldPointer],
            entries: [entry("assets/new-external.prefab", index: 0, size: 1, pointer: oldPointer)],
            dependencies: []
        )
        let key = AoVAssetBundleCatalogMerger.externalTargetKey(
            guidHex: "GUID-SHARED",
            path: "CAB\\shared"
        )

        let auditedPlan = try AoVAssetBundleCatalogMerger.plan(
            current: current,
            legacy: legacy,
            currentSerializedFile: destination,
            legacySerializedFile: source,
            availableCurrentBundleNames: ["actor/2500"],
            verifiedExternalTargetPathIDs: [key: [7007]]
        )

        XCTAssertFalse(auditedPlan.externalTargetAuditRequired)
        XCTAssertEqual(auditedPlan.remappedExternalPointerCount, 2)
        XCTAssertEqual(auditedPlan.mergedRecord.preloadReferences.first?.fileID, 3)
        XCTAssertEqual(auditedPlan.mergedRecord.preloadReferences.first?.pathID, 7007)

        let missingTargetPlan = try AoVAssetBundleCatalogMerger.plan(
            current: current,
            legacy: legacy,
            currentSerializedFile: destination,
            legacySerializedFile: source,
            availableCurrentBundleNames: ["actor/2500"],
            verifiedExternalTargetPathIDs: [key: [9999]]
        )
        XCTAssertTrue(missingTargetPlan.externalTargetAuditRequired)
        XCTAssertFalse(missingTargetPlan.externalTargetAuditSamples.isEmpty)
    }

    func testBlocksExternalPointerWhenGUIDAndPathDoNotMatch() {
        let source = makeSummary(
            localPathIDs: [],
            externalReferences: [external("guid-legacy", path: "CAB/legacy")]
        )
        let destination = makeSummary(
            localPathIDs: [],
            externalReferences: [external("guid-current", path: "CAB/current")]
        )
        let pointer = AoVSerializedAssetPointer(fileID: 1, pathID: 1234)
        let current = record(bundle: "actor/3003", preloads: [], entries: [], dependencies: [])
        let legacy = record(
            bundle: "actor/3003",
            preloads: [pointer],
            entries: [entry("assets/legacy.prefab", index: 0, size: 1, pointer: pointer)],
            dependencies: []
        )

        XCTAssertThrowsError(try AoVAssetBundleCatalogMerger.plan(
            current: current,
            legacy: legacy,
            currentSerializedFile: destination,
            legacySerializedFile: source,
            availableCurrentBundleNames: []
        )) { error in
            guard case AoVAssetBundleCatalogMergeError.unresolvedExternalReference = error else {
                return XCTFail("Expected unresolved external reference, got \(error)")
            }
        }
    }

    func testBlocksLegacyOnlyEntryWhenRequiredDependencyIsMissing() {
        let pointer = AoVSerializedAssetPointer(fileID: 0, pathID: 100)
        let summary = makeSummary(localPathIDs: [100])
        let current = record(bundle: "actor/4004", preloads: [], entries: [], dependencies: [])
        let legacy = record(
            bundle: "actor/4004",
            preloads: [pointer],
            entries: [entry("assets/new.prefab", index: 0, size: 1, pointer: pointer)],
            dependencies: ["common/missing"]
        )

        XCTAssertThrowsError(try AoVAssetBundleCatalogMerger.plan(
            current: current,
            legacy: legacy,
            currentSerializedFile: summary,
            legacySerializedFile: summary,
            availableCurrentBundleNames: []
        )) { error in
            guard case AoVAssetBundleCatalogMergeError.dependencyUnavailable("common/missing") = error else {
                return XCTFail("Expected missing dependency rejection, got \(error)")
            }
        }
    }

    func testBlocksLegacyEntryWithInvalidPreloadRange() {
        let pointer = AoVSerializedAssetPointer(fileID: 0, pathID: 100)
        let summary = makeSummary(localPathIDs: [100])
        let current = record(bundle: "actor/5005", preloads: [], entries: [], dependencies: [])
        let legacy = record(
            bundle: "actor/5005",
            preloads: [pointer],
            entries: [entry("assets/bad.prefab", index: 1, size: 1, pointer: pointer)],
            dependencies: []
        )

        XCTAssertThrowsError(try AoVAssetBundleCatalogMerger.plan(
            current: current,
            legacy: legacy,
            currentSerializedFile: summary,
            legacySerializedFile: summary,
            availableCurrentBundleNames: []
        )) { error in
            guard case AoVAssetBundleCatalogMergeError.invalidLegacyPreloadRange("assets/bad.prefab") = error else {
                return XCTFail("Expected invalid preload range rejection, got \(error)")
            }
        }
    }

    func testExistingKeyWithDifferentLegacyPointerIsRetainedFromCurrentRES() throws {
        let currentPointer = AoVSerializedAssetPointer(fileID: 0, pathID: 100)
        let legacyPointer = AoVSerializedAssetPointer(fileID: 0, pathID: 200)
        let currentSummary = makeSummary(localPathIDs: [100])
        let legacySummary = makeSummary(localPathIDs: [200])
        let current = record(
            bundle: "actor/6006",
            preloads: [currentPointer],
            entries: [entry("assets/hero.prefab", index: 0, size: 1, pointer: currentPointer)],
            dependencies: []
        )
        let legacy = record(
            bundle: "actor/6006",
            preloads: [legacyPointer],
            entries: [entry("assets/hero.prefab", index: 0, size: 1, pointer: legacyPointer)],
            dependencies: []
        )

        let plan = try AoVAssetBundleCatalogMerger.plan(
            current: current,
            legacy: legacy,
            currentSerializedFile: currentSummary,
            legacySerializedFile: legacySummary,
            availableCurrentBundleNames: ["actor/6006"]
        )

        XCTAssertTrue(plan.addedContainerKeys.isEmpty)
        XCTAssertEqual(plan.alreadyPresentContainerKeys, ["assets/hero.prefab"])
        XCTAssertEqual(plan.currentVersionRetainedPointerConflictKeys, ["assets/hero.prefab"])
        XCTAssertEqual(plan.mergedRecord.containerEntries.first?.asset, currentPointer)
    }

    private func makeSummary(
        localPathIDs: [Int64],
        externalReferences: [AoVSerializedFileExternalReference] = []
    ) -> AoVSerializedFileSummary {
        let objects = localPathIDs.map { pathID in
            AoVSerializedFileObject(
                pathID: pathID,
                classID: 28,
                typeID: 0,
                byteOffset: 0,
                byteSize: 1,
                byteStartFieldOffset: 0,
                byteSizeFieldOffset: 0
            )
        }
        return AoVSerializedFileSummary(
            formatVersion: 17,
            unityVersion: "2020.3.34f1",
            targetPlatform: 13,
            fileSize: 1,
            dataOffset: 0,
            littleEndian: true,
            fileSizeFieldOffset: 4,
            dataOffsetFieldOffset: 12,
            typeCount: 1,
            serializedTypes: [],
            objectCount: localPathIDs.count,
            serializedObjects: objects,
            assetBundleObjects: [],
            externalReferences: externalReferences,
            assetBundleRecords: [],
            resourcePackerCatalog: nil,
            resourceVerificationCatalog: nil
        )
    }

    private func record(
        bundle: String,
        preloads: [AoVSerializedAssetPointer],
        entries: [AoVSerializedAssetBundleEntry],
        dependencies: [String]
    ) -> AoVSerializedAssetBundleRecord {
        let nullPointer = AoVSerializedAssetPointer(fileID: 0, pathID: 0)
        return AoVSerializedAssetBundleRecord(
            pathID: 999,
            name: bundle,
            preloadReferences: preloads,
            containerEntries: entries,
            mainAsset: nullPointer,
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 0,
            scriptCompatibilityEntries: [],
            classCompatibilityIDs: [],
            classCompatibilityEntries: [],
            runtimeCompatibility: 0,
            assetBundleName: bundle,
            dependencies: dependencies,
            isStreamedSceneAssetBundle: false,
            trailingBytes: 0,
            trailingData: Data()
        )
    }

    private func entry(
        _ key: String,
        index: Int32,
        size: Int32,
        pointer: AoVSerializedAssetPointer
    ) -> AoVSerializedAssetBundleEntry {
        AoVSerializedAssetBundleEntry(
            key: key,
            preloadIndex: index,
            preloadSize: size,
            asset: pointer
        )
    }

    private func external(
        _ guid: String,
        path: String
    ) -> AoVSerializedFileExternalReference {
        AoVSerializedFileExternalReference(
            temporaryName: "",
            guidHex: guid,
            type: 0,
            path: path
        )
    }
}
