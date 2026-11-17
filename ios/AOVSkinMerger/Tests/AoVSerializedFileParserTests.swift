import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVSerializedFileParserTests: XCTestCase {
    func testInspectsMinimalUnity2020SerializedFileAndFindsAssetBundleObject() throws {
        let data = makeSerializedFile()
        let summary = try AoVSerializedFileParser.inspect(data)

        XCTAssertEqual(summary.formatVersion, 17)
        XCTAssertEqual(summary.unityVersion, "2020.3.34f1")
        XCTAssertEqual(summary.targetPlatform, 13)
        XCTAssertEqual(summary.typeCount, 1)
        XCTAssertEqual(summary.objectCount, 1)
        XCTAssertEqual(summary.serializedObjects.count, 1)
        XCTAssertEqual(summary.serializedObjects.first?.pathID, 1234)
        XCTAssertEqual(summary.externalReferences.count, 0)
        XCTAssertEqual(summary.assetBundleObjectCount, 1)
        XCTAssertEqual(summary.assetBundleObjects.first?.pathID, 1234)
        XCTAssertEqual(summary.assetBundleObjects.first?.classID, 142)
        XCTAssertGreaterThan(summary.assetBundleObjects.first?.byteSize ?? 0, 4)
        XCTAssertEqual(summary.assetBundleRecords.count, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.name, "catalog")
        XCTAssertEqual(summary.assetBundleRecords.first?.assetBundleName, "resource-packer")
        XCTAssertEqual(summary.assetBundleRecords.first?.dependencies, ["common", "actor/1"])
        XCTAssertEqual(summary.assetBundleRecords.first?.preloadReferences.first?.fileID, 0)
        XCTAssertEqual(summary.assetBundleRecords.first?.preloadReferences.first?.pathID, 7001)
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.count, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.key, "assets/hero.prefab")
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.preloadIndex, 0)
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.preloadSize, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.asset.pathID, 7002)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAsset.pathID, 7003)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAssetPreloadIndex, 0)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAssetPreloadSize, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.classCompatibilityIDs, [28])
        XCTAssertEqual(summary.assetBundleRecords.first?.classCompatibilityEntries.first?.info.asset.pathID, 7004)
        XCTAssertEqual(summary.assetBundleRecords.first?.classCompatibilityEntries.first?.info.preloadSize, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.trailingBytes, 0)
    }



    func testParsesUnity2020ExtendedSerializedFileHeader() throws {
        let data = makeSerializedFile(formatVersion: 22, littleEndian: false)
        let summary = try AoVSerializedFileParser.inspect(data)

        XCTAssertEqual(summary.formatVersion, 22)
        XCTAssertEqual(summary.fileSize, UInt64(data.count))
        XCTAssertEqual(summary.assetBundleRecords.first?.assetBundleName, "resource-packer")
        XCTAssertEqual(summary.assetBundleRecords.first?.dependencies, ["common", "actor/1"])
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.key, "assets/hero.prefab")
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.asset.pathID, 7002)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAsset.pathID, 7003)
    }

    func testParsesBigEndianSerializedFileAndAssetBundleObject() throws {
        let data = makeSerializedFile(littleEndian: false)
        let summary = try AoVSerializedFileParser.inspect(data)

        XCTAssertEqual(summary.formatVersion, 17)
        XCTAssertEqual(summary.targetPlatform, 13)
        XCTAssertEqual(summary.serializedObjects.count, 1)
        XCTAssertEqual(summary.externalReferences.count, 0)
        XCTAssertEqual(summary.assetBundleRecords.first?.name, "catalog")
        XCTAssertEqual(summary.assetBundleRecords.first?.assetBundleName, "resource-packer")
        XCTAssertEqual(summary.assetBundleRecords.first?.dependencies, ["common", "actor/1"])
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.key, "assets/hero.prefab")
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.asset.pathID, 7002)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAsset.pathID, 7003)
    }

    func testParsesUnity54ScriptCompatibilityEntries() throws {
        let data = makeSerializedFile(unityVersion: "5.4.0f3")
        let summary = try AoVSerializedFileParser.inspect(data)
        let record = try XCTUnwrap(summary.assetBundleRecords.first)

        XCTAssertEqual(record.scriptCompatibilityEntries.count, 1)
        XCTAssertEqual(record.scriptCompatibilityEntries.first?.first, 111)
        XCTAssertEqual(record.scriptCompatibilityEntries.first?.second, 222)
        XCTAssertEqual(record.classCompatibilityEntries.first?.info.asset.pathID, 7004)
        XCTAssertEqual(record.assetBundleName, "resource-packer")
    }

    func testRejectsTruncatedAssetBundleObject() {
        let data = makeSerializedFile(objectPayload: Data([0x01, 0x02]))
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data))
    }

    func testRefusesUnverifiedNoTargetBaseLayout() {
        let data = makeSerializedFile(targetPlatform: -2)
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data)) { error in
            guard let parserError = error as? AoVSerializedFileError,
                  case .unsupportedTargetPlatform(-2) = parserError else {
                return XCTFail("Expected unsupported target platform, got \(error)")
            }
        }
    }

    func testRejectsInvalidObjectRange() {
        let data = makeSerializedFile(objectByteStart: UInt32.max)
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data))
    }

    func testRejectsInvalidTypeIndex() {
        let data = makeSerializedFile(objectTypeIndex: 3)
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data))
    }

    func testRejectsUnknownFormatVersion() {
        let data = makeSerializedFile(formatVersion: 31)
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data))
    }

    func testRejectsTruncatedInput() {
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(Data(repeating: 0, count: 19)))
    }

    func testAssetBundleWriterRewritesAndExpandsAssetBundleObject() throws {
        let originalData = makeSerializedFile()
        let originalSummary = try AoVSerializedFileParser.inspect(originalData)
        let originalRecord = try XCTUnwrap(originalSummary.assetBundleRecords.first)
        let replacementRecord = safeRecordWithNullPointers(from: originalRecord)

        let rewrittenData = try AoVSerializedAssetBundleWriter.rewrite(
            serializedFile: originalData,
            assetBundleRecord: replacementRecord
        )
        let rewrittenSummary = try AoVSerializedFileParser.inspect(rewrittenData)
        let rewrittenRecord = try XCTUnwrap(rewrittenSummary.assetBundleRecords.first)
        let oldObject = try XCTUnwrap(originalSummary.assetBundleObjects.first)
        let newObject = try XCTUnwrap(rewrittenSummary.assetBundleObjects.first)

        XCTAssertGreaterThan(rewrittenData.count, originalData.count)
        XCTAssertEqual(rewrittenSummary.fileSize, UInt64(rewrittenData.count))
        XCTAssertEqual(rewrittenSummary.dataOffset, originalSummary.dataOffset)
        XCTAssertEqual(rewrittenRecord, replacementRecord)
        XCTAssertGreaterThan(newObject.byteSize, oldObject.byteSize)
        XCTAssertGreaterThan(newObject.byteOffset, oldObject.byteOffset)
        XCTAssertEqual(
            rewrittenData.subdata(in: Int(newObject.byteOffset)..<(Int(newObject.byteOffset) + Int(newObject.byteSize))),
            try AoVSerializedAssetBundleWriter.serialize(
                replacementRecord,
                formatVersion: rewrittenSummary.formatVersion,
                unityVersion: rewrittenSummary.unityVersion,
                littleEndian: rewrittenSummary.littleEndian
            )
        )
    }

    func testAssetBundleWriterSupportsExtendedBigEndianSerializedFile() throws {
        let originalData = makeSerializedFile(formatVersion: 22, littleEndian: false)
        let originalSummary = try AoVSerializedFileParser.inspect(originalData)
        let originalRecord = try XCTUnwrap(originalSummary.assetBundleRecords.first)
        let replacementRecord = safeRecordWithNullPointers(from: originalRecord)

        let rewrittenData = try AoVSerializedAssetBundleWriter.rewrite(
            serializedFile: originalData,
            assetBundleRecord: replacementRecord
        )
        let rewrittenSummary = try AoVSerializedFileParser.inspect(rewrittenData)

        XCTAssertFalse(rewrittenSummary.littleEndian)
        XCTAssertEqual(rewrittenSummary.formatVersion, 22)
        XCTAssertEqual(rewrittenSummary.fileSize, UInt64(rewrittenData.count))
        XCTAssertEqual(rewrittenSummary.assetBundleRecords.first, replacementRecord)
    }

    func testAssetBundleWriterRejectsInvalidPreloadRanges() throws {
        let summary = try AoVSerializedFileParser.inspect(makeSerializedFile())
        let original = try XCTUnwrap(summary.assetBundleRecords.first)
        var replacement = safeRecordWithNullPointers(from: original)
        let first = try XCTUnwrap(replacement.containerEntries.first)
        let invalidEntry = AoVSerializedAssetBundleEntry(
            key: first.key,
            preloadIndex: 2,
            preloadSize: 1,
            asset: first.asset
        )
        var invalidEntries = replacement.containerEntries
        invalidEntries[0] = invalidEntry
        replacement = copyRecord(replacement, containerEntries: invalidEntries)

        XCTAssertThrowsError(
            try AoVSerializedAssetBundleWriter.rewrite(
                serializedFile: makeSerializedFile(),
                assetBundleRecord: replacement
            )
        ) { error in
            guard let writerError = error as? AoVSerializedAssetBundleWriteError,
                  case .invalidPreloadRange = writerError else {
                return XCTFail("Expected invalid preload range rejection, got \(error)")
            }
        }
    }

    func testAssetBundleWriterRejectsUnresolvedLocalPointers() throws {
        let originalData = makeSerializedFile()
        let summary = try AoVSerializedFileParser.inspect(originalData)
        let original = try XCTUnwrap(summary.assetBundleRecords.first)
        var replacement = safeRecordWithNullPointers(from: original)
        replacement = copyRecord(
            replacement,
            preloadReferences: [
                AoVSerializedAssetPointer(fileID: 0, pathID: 999_999),
                replacement.preloadReferences[1]
            ]
        )

        XCTAssertThrowsError(
            try AoVSerializedAssetBundleWriter.rewrite(
                serializedFile: originalData,
                assetBundleRecord: replacement
            )
        ) { error in
            guard let writerError = error as? AoVSerializedAssetBundleWriteError,
                  case .unresolvedPointer = writerError else {
                return XCTFail("Expected unresolved PPtr rejection, got \(error)")
            }
        }
    }

    func testExternalCABAuditReadsObjectPathIDsFromSerializedFileBytes() throws {
        let targetSerializedFile = makeSerializedFile()
        let key = AoVAssetBundleCatalogMerger.externalTargetKey(
            guidHex: "ABCDEF0123456789",
            path: "CAB\\shared"
        )

        let audited = try AoVAssetBundleCatalogMerger.auditExternalTargetPathIDs(
            serializedFilesByExternalTargetKey: [key: targetSerializedFile]
        )

        XCTAssertEqual(audited[key], [1234])
    }

    func testUnityFSAssetBundleMergeExecutorWritesMergedClass142AndRoundTrips() throws {
        let currentSerializedBytes = makeSerializedFile(useResolvedPointers: true)
        let legacySerializedBytes = makeSerializedFile(useResolvedPointers: true)
        let currentSummary = try AoVSerializedFileParser.inspect(currentSerializedBytes)
        let legacySummary = try AoVSerializedFileParser.inspect(legacySerializedBytes)
        let currentRecord = try XCTUnwrap(currentSummary.assetBundleRecords.first)
        let legacySourceRecord = try XCTUnwrap(legacySummary.assetBundleRecords.first)
        let validLocalPointer = AoVSerializedAssetPointer(fileID: 0, pathID: 1234)

        let legacyRecord = AoVSerializedAssetBundleRecord(
            pathID: legacySourceRecord.pathID,
            name: legacySourceRecord.name,
            preloadReferences: legacySourceRecord.preloadReferences + [validLocalPointer],
            containerEntries: legacySourceRecord.containerEntries + [
                AoVSerializedAssetBundleEntry(
                    key: "assets/aki-remake-new.prefab",
                    preloadIndex: 1,
                    preloadSize: 1,
                    asset: validLocalPointer
                )
            ],
            mainAsset: legacySourceRecord.mainAsset,
            mainAssetPreloadIndex: legacySourceRecord.mainAssetPreloadIndex,
            mainAssetPreloadSize: legacySourceRecord.mainAssetPreloadSize,
            scriptCompatibilityEntries: legacySourceRecord.scriptCompatibilityEntries,
            classCompatibilityIDs: legacySourceRecord.classCompatibilityIDs,
            classCompatibilityEntries: legacySourceRecord.classCompatibilityEntries,
            runtimeCompatibility: legacySourceRecord.runtimeCompatibility,
            assetBundleName: legacySourceRecord.assetBundleName,
            dependencies: legacySourceRecord.dependencies,
            isStreamedSceneAssetBundle: legacySourceRecord.isStreamedSceneAssetBundle,
            trailingBytes: legacySourceRecord.trailingBytes,
            trailingData: legacySourceRecord.trailingData
        )

        XCTAssertEqual(currentRecord.containerEntries.map(\.key), ["assets/hero.prefab"])
        XCTAssertEqual(legacyRecord.containerEntries.count, 2)

        let unityFS = try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: "CAB-catalog", data: currentSerializedBytes, flags: 7)],
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2020.3.34f1"
        )
        XCTAssertTrue(unityFS.roundTripVerified)

        let execution = try AoVUnityFSAssetBundleMergeExecutor.merge(
            in: unityFS.data,
            serializedFileNodePath: "CAB-catalog",
            objectPathID: currentRecord.pathID,
            legacyRecord: legacyRecord,
            legacySerializedFile: legacySummary,
            availableCurrentBundleNames: Set(currentRecord.dependencies)
        )

        XCTAssertTrue(execution.roundTripVerified)
        XCTAssertTrue(execution.rewrite.roundTripVerified)
        XCTAssertEqual(execution.plan.addedContainerKeys, ["assets/aki-remake-new.prefab"])

        let verifiedBundle = try AoVUnityFSParser.inspect(execution.data)
        let verifiedNodes = try AoVUnityFSParser.extractNodes(
            execution.data,
            maximumExpandedBytes: 1_048_576,
            maximumNodeCount: 8
        )
        let verifiedNode = try XCTUnwrap(verifiedNodes.first(where: { $0.path == "CAB-catalog" }))
        let verifiedFile = try AoVSerializedFileParser.inspect(verifiedNode.data)
        let verifiedRecord = try XCTUnwrap(
            verifiedFile.assetBundleRecords.first(where: { $0.pathID == currentRecord.pathID })
        )

        XCTAssertEqual(verifiedBundle.nodes.map(\.path), ["CAB-catalog"])
        XCTAssertEqual(verifiedRecord, execution.plan.mergedRecord)
        XCTAssertEqual(verifiedRecord.containerEntries.map(\.key), [
            "assets/hero.prefab",
            "assets/aki-remake-new.prefab"
        ])
    }

    func testAssetBundleBatchExecutorMergesMultipleLegacyRecordsWithOneUnityFSRebuild() throws {
        let currentSerialized = makeSerializedFile(useResolvedPointers: true)
        let currentSummary = try AoVSerializedFileParser.inspect(currentSerialized)
        let currentRecord = try XCTUnwrap(currentSummary.assetBundleRecords.first)
        let pointer = AoVSerializedAssetPointer(fileID: 0, pathID: 1234)

        func legacyRecord(with key: String) -> AoVSerializedAssetBundleRecord {
            AoVSerializedAssetBundleRecord(
                pathID: currentRecord.pathID,
                name: currentRecord.name,
                preloadReferences: currentRecord.preloadReferences + [pointer],
                containerEntries: currentRecord.containerEntries + [
                    AoVSerializedAssetBundleEntry(
                        key: key,
                        preloadIndex: Int32(currentRecord.preloadReferences.count),
                        preloadSize: 1,
                        asset: pointer
                    )
                ],
                mainAsset: currentRecord.mainAsset,
                mainAssetPreloadIndex: currentRecord.mainAssetPreloadIndex,
                mainAssetPreloadSize: currentRecord.mainAssetPreloadSize,
                scriptCompatibilityEntries: currentRecord.scriptCompatibilityEntries,
                classCompatibilityIDs: currentRecord.classCompatibilityIDs,
                classCompatibilityEntries: currentRecord.classCompatibilityEntries,
                runtimeCompatibility: currentRecord.runtimeCompatibility,
                assetBundleName: currentRecord.assetBundleName,
                dependencies: currentRecord.dependencies,
                isStreamedSceneAssetBundle: currentRecord.isStreamedSceneAssetBundle,
                trailingBytes: currentRecord.trailingBytes,
                trailingData: currentRecord.trailingData
            )
        }

        let legacySummary = currentSummary
        let firstLegacy = legacyRecord(with: "assets/aki-batch-a.prefab")
        let secondLegacy = legacyRecord(with: "assets/aki-batch-b.prefab")
        let unityFS = try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: "CAB-catalog", data: currentSerialized, flags: 7)],
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2020.3.34f1",
            blockInfoLayout: .arenaOfValor
        )

        let report = try AoVUnityFSAssetBundleMergeExecutor.mergeBatch(
            in: unityFS.data,
            serializedFileNodePath: "CAB-catalog",
            legacyRecords: [
                AoVAssetBundleLegacyRecordInput(
                    source: "creator-a.zip",
                    record: firstLegacy,
                    serializedFile: legacySummary
                ),
                AoVAssetBundleLegacyRecordInput(
                    source: "creator-b.zip",
                    record: secondLegacy,
                    serializedFile: legacySummary
                )
            ],
            availableCurrentBundleNames: Set(currentRecord.dependencies)
        )

        XCTAssertTrue(report.roundTripVerified)
        XCTAssertTrue(report.rewrite.roundTripVerified)
        XCTAssertEqual(report.rewrittenObjectPathIDs, [currentRecord.pathID])
        XCTAssertEqual(report.plans.count, 2)
        XCTAssertEqual(report.rewrite.repack.blockCount, 1)

        let nodes = try AoVUnityFSParser.extractNodes(report.data)
        let node = try XCTUnwrap(nodes.first(where: { $0.path == "CAB-catalog" }))
        let verified = try AoVSerializedFileParser.inspect(node.data)
        let record = try XCTUnwrap(verified.assetBundleRecords.first)
        XCTAssertEqual(record.containerEntries.map(\.key), [
            "assets/hero.prefab",
            "assets/aki-batch-a.prefab",
            "assets/aki-batch-b.prefab"
        ])
        XCTAssertEqual(record.preloadReferences.count, 3)
    }

    private func safeRecordWithNullPointers(
        from record: AoVSerializedAssetBundleRecord
    ) -> AoVSerializedAssetBundleRecord {
        let nullPointer = AoVSerializedAssetPointer(fileID: 0, pathID: 0)
        var entries = record.containerEntries.map {
            AoVSerializedAssetBundleEntry(
                key: $0.key,
                preloadIndex: 0,
                preloadSize: 1,
                asset: nullPointer
            )
        }
        entries.append(
            AoVSerializedAssetBundleEntry(
                key: "assets/aki-remake-test.prefab",
                preloadIndex: 1,
                preloadSize: 1,
                asset: nullPointer
            )
        )
        let compatibility = record.classCompatibilityEntries.map {
            AoVSerializedClassCompatibilityEntry(
                classID: $0.classID,
                info: AoVSerializedAssetInfo(
                    preloadIndex: 0,
                    preloadSize: 1,
                    asset: nullPointer
                )
            )
        }
        return AoVSerializedAssetBundleRecord(
            pathID: record.pathID,
            name: record.name,
            preloadReferences: [nullPointer, nullPointer],
            containerEntries: entries,
            mainAsset: nullPointer,
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 2,
            scriptCompatibilityEntries: record.scriptCompatibilityEntries,
            classCompatibilityIDs: record.classCompatibilityIDs,
            classCompatibilityEntries: compatibility,
            runtimeCompatibility: record.runtimeCompatibility,
            assetBundleName: record.assetBundleName,
            dependencies: record.dependencies,
            isStreamedSceneAssetBundle: record.isStreamedSceneAssetBundle,
            trailingBytes: record.trailingBytes,
            trailingData: record.trailingData
        )
    }

    private func copyRecord(
        _ record: AoVSerializedAssetBundleRecord,
        preloadReferences: [AoVSerializedAssetPointer]? = nil,
        containerEntries: [AoVSerializedAssetBundleEntry]? = nil
    ) -> AoVSerializedAssetBundleRecord {
        AoVSerializedAssetBundleRecord(
            pathID: record.pathID,
            name: record.name,
            preloadReferences: preloadReferences ?? record.preloadReferences,
            containerEntries: containerEntries ?? record.containerEntries,
            mainAsset: record.mainAsset,
            mainAssetPreloadIndex: record.mainAssetPreloadIndex,
            mainAssetPreloadSize: record.mainAssetPreloadSize,
            scriptCompatibilityEntries: record.scriptCompatibilityEntries,
            classCompatibilityIDs: record.classCompatibilityIDs,
            classCompatibilityEntries: record.classCompatibilityEntries,
            runtimeCompatibility: record.runtimeCompatibility,
            assetBundleName: record.assetBundleName,
            dependencies: record.dependencies,
            isStreamedSceneAssetBundle: record.isStreamedSceneAssetBundle,
            trailingBytes: record.trailingBytes,
            trailingData: record.trailingData
        )
    }

    private func makeSerializedFile(
        formatVersion: UInt32 = 17,
        objectByteStart: UInt32 = 0,
        objectTypeIndex: Int32 = 0,
        targetPlatform: Int32 = 13,
        objectPayload: Data? = nil,
        littleEndian: Bool = true,
        unityVersion: String = "2020.3.34f1",
        useResolvedPointers: Bool = false
    ) -> Data {
        let payload = objectPayload ?? makeAssetBundleObject(
            littleEndian: littleEndian,
            unityVersion: unityVersion,
            useResolvedPointers: useResolvedPointers
        )
        var metadata = Data()
        metadata.append(Data((unityVersion + "\0").utf8))
        appendSerialized(targetPlatform, to: &metadata, littleEndian: littleEndian)
        metadata.append(0)
        appendSerialized(Int32(1), to: &metadata, littleEndian: littleEndian)
        appendSerialized(Int32(142), to: &metadata, littleEndian: littleEndian)
        metadata.append(0)
        appendSerialized(Int16(-1), to: &metadata, littleEndian: littleEndian)
        metadata.append(Data(repeating: 0, count: 16))
        if formatVersion >= 21 {
            appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian) // type dependencies
        }
        appendSerialized(Int32(1), to: &metadata, littleEndian: littleEndian)
        align4(&metadata)
        appendSerialized(Int64(1234), to: &metadata, littleEndian: littleEndian)
        if formatVersion >= 22 {
            appendSerialized(Int64(Int64(objectByteStart)), to: &metadata, littleEndian: littleEndian)
        } else {
            appendSerialized(objectByteStart, to: &metadata, littleEndian: littleEndian)
        }
        appendSerialized(UInt32(payload.count), to: &metadata, littleEndian: littleEndian)
        appendSerialized(objectTypeIndex, to: &metadata, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian) // script count
        appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian) // external count
        if formatVersion >= 20 {
            appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian) // reference types
        }
        metadata.append(0) // user information

        let headerSize = formatVersion >= 22 ? 48 : 20
        let dataOffset = ((headerSize + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + payload.count

        var file = Data()
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt32(fileSize), to: &file)
        appendBE(formatVersion, to: &file)
        appendBE(UInt32(dataOffset), to: &file)
        file.append(littleEndian ? 0 : 1)
        file.append(contentsOf: [0, 0, 0])

        if formatVersion >= 22 {
            appendBE(UInt32(metadata.count), to: &file)
            appendBE(UInt64(fileSize), to: &file)
            appendBE(UInt64(dataOffset), to: &file)
            appendBE(UInt64(0), to: &file)
        }

        file.append(metadata)
        while file.count < dataOffset { file.append(0) }
        file.append(payload)
        return file
    }

    private func makeAssetBundleObject(
        littleEndian: Bool,
        unityVersion: String,
        useResolvedPointers: Bool = false
    ) -> Data {
        let preloadPathID: Int64 = useResolvedPointers ? 1234 : 7001
        let containerPathID: Int64 = useResolvedPointers ? 1234 : 7002
        let mainAssetPathID: Int64 = useResolvedPointers ? 1234 : 7003
        let compatibilityPathID: Int64 = useResolvedPointers ? 1234 : 7004
        var object = Data()
        appendAlignedString("catalog", to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // preload count
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(preloadPathID, to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // container count
        appendAlignedString("assets/hero.prefab", to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian) // preload index
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // preload size
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(containerPathID, to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian) // main preload index
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // main preload size
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(mainAssetPathID, to: &object, littleEndian: littleEndian)
        if unityVersion.hasPrefix("5.4.") {
            appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
            appendSerialized(Int32(111), to: &object, littleEndian: littleEndian)
            appendSerialized(Int32(222), to: &object, littleEndian: littleEndian)
        }
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // class compatibility count
        appendSerialized(Int32(28), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(compatibilityPathID, to: &object, littleEndian: littleEndian)
        appendSerialized(UInt32(0), to: &object, littleEndian: littleEndian)
        appendAlignedString("resource-packer", to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(2), to: &object, littleEndian: littleEndian)
        appendAlignedString("common", to: &object, littleEndian: littleEndian)
        appendAlignedString("actor/1", to: &object, littleEndian: littleEndian)
        object.append(0)
        return object
    }

    private func appendAlignedString(_ value: String, to data: inout Data, littleEndian: Bool) {
        let bytes = Data(value.utf8)
        appendSerialized(Int32(bytes.count), to: &data, littleEndian: littleEndian)
        data.append(bytes)
        align4(&data)
    }

    private func appendSerialized(_ value: Int16, to data: inout Data, littleEndian: Bool) {
        let bits = UInt16(bitPattern: value)
        if littleEndian {
            data.append(UInt8(bits & 0xFF))
            data.append(UInt8((bits >> 8) & 0xFF))
        } else {
            data.append(UInt8((bits >> 8) & 0xFF))
            data.append(UInt8(bits & 0xFF))
        }
    }

    private func appendSerialized(_ value: Int32, to data: inout Data, littleEndian: Bool) {
        appendSerialized(UInt32(bitPattern: value), to: &data, littleEndian: littleEndian)
    }

    private func appendSerialized(_ value: UInt32, to data: inout Data, littleEndian: Bool) {
        if littleEndian {
            data.append(UInt8(value & 0xFF))
            data.append(UInt8((value >> 8) & 0xFF))
            data.append(UInt8((value >> 16) & 0xFF))
            data.append(UInt8((value >> 24) & 0xFF))
        } else {
            appendBE(value, to: &data)
        }
    }

    private func appendSerialized(_ value: Int64, to data: inout Data, littleEndian: Bool) {
        let bits = UInt64(bitPattern: value)
        if littleEndian {
            for shift in stride(from: 0, through: 56, by: 8) {
                data.append(UInt8((bits >> UInt64(shift)) & 0xFF))
            }
        } else {
            appendBE(bits, to: &data)
        }
    }

    private func align4(_ data: inout Data) {
        while data.count % 4 != 0 { data.append(0) }
    }

    private func appendBE(_ value: UInt32, to data: inout Data) {
        data.append(UInt8((value >> 24) & 0xFF))
        data.append(UInt8((value >> 16) & 0xFF))
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
    }

    private func appendBE(_ value: UInt64, to data: inout Data) {
        appendBE(UInt32((value >> 32) & 0xFFFF_FFFF), to: &data)
        appendBE(UInt32(value & 0xFFFF_FFFF), to: &data)
    }
}
