import Foundation
import XCTest
@testable import AOVSkinMerger

final class AoVUnityFSAssetBundleMergeExecutorTests: XCTestCase {
    func testDetectsChangedObjectPayloadBehindAnUnchangedContainerPointer() throws {
        let nodes = objectNodes()
        let currentPayload = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: .object(["m_TestPathID": .signed(100)]),
            littleEndian: true
        )
        let legacyPayload = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: .object(["m_TestPathID": .signed(200)]),
            littleEndian: true
        )
        let currentData = makeSerializedFile(payload: currentPayload, nodes: nodes)
        let legacyData = makeSerializedFile(payload: legacyPayload, nodes: nodes)
        let currentSummary = try AoVSerializedFileParser.inspect(currentData)
        let legacySummary = try AoVSerializedFileParser.inspect(legacyData)
        let pointer = AoVSerializedAssetPointer(fileID: 0, pathID: 1001)
        let entry = makeEntry(pointer)
        let currentRecord = makeBundleRecord(entry: entry)
        let legacyRecord = makeBundleRecord(entry: entry)

        let changed = try AoVUnityFSAssetBundleMergeExecutor.changedContainerKeys(
            legacyRecord: legacyRecord,
            currentRecord: currentRecord,
            sourceData: legacyData,
            destinationData: currentData,
            sourceSummary: legacySummary,
            destinationSummary: currentSummary
        )

        XCTAssertEqual(changed, ["assets/hero.prefab"])
    }

    func testDoesNotReplaceContainerWhenSamePointerAndObjectBytesMatch() throws {
        let nodes = objectNodes()
        let payload = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: .object(["m_TestPathID": .signed(100)]),
            littleEndian: true
        )
        let data = makeSerializedFile(payload: payload, nodes: nodes)
        let summary = try AoVSerializedFileParser.inspect(data)
        let entry = makeEntry(AoVSerializedAssetPointer(fileID: 0, pathID: 1001))
        let record = makeBundleRecord(entry: entry)

        let changed = try AoVUnityFSAssetBundleMergeExecutor.changedContainerKeys(
            legacyRecord: record,
            currentRecord: record,
            sourceData: data,
            destinationData: data,
            sourceSummary: summary,
            destinationSummary: summary
        )

        XCTAssertTrue(changed.isEmpty)
    }

    func testImportsLegacyObjectAndReplacesExistingContainerEntryEndToEnd() throws {
        let nodes = objectNodes()
        let currentPayload = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: .object(["m_TestPathID": .signed(100)]),
            littleEndian: true
        )
        let legacyPayload = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: .object(["m_TestPathID": .signed(200)]),
            littleEndian: true
        )
        let currentRecord = makeAssetBundleRecord(rootPathID: 5001, assetPathID: 1001)
        let legacyRecord = makeAssetBundleRecord(rootPathID: 6001, assetPathID: 2002)
        let destinationSerializedFile = try makeSerializedFileWithBundleRecord(
            record: currentRecord,
            assetPathID: 1001,
            assetPayload: currentPayload,
            assetNodes: nodes
        )
        let legacySerializedFile = try makeSerializedFileWithBundleRecord(
            record: legacyRecord,
            assetPathID: 2002,
            assetPayload: legacyPayload,
            assetNodes: nodes
        )
        let destinationBundle = try makeUnityFS(
            serializedFile: destinationSerializedFile,
            nodePath: "CAB-actor-test"
        )

        let report = try AoVUnityFSAssetBundleMergeExecutor.mergeImportingLegacyObjects(
            in: destinationBundle,
            serializedFileNodePath: "CAB-actor-test",
            legacyInputs: [
                AoVAssetBundleLegacyObjectImportInput(
                    source: "legacy-synthetic-mod.zip",
                    serializedFile: legacySerializedFile,
                    legacyObjectPathID: 6001,
                    destinationObjectPathID: 5001
                )
            ],
            availableCurrentBundleNames: ["actor/test"]
        )

        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(report.importedObjectCount, 1)
        XCTAssertEqual(report.importedPathIDs.count, 1)
        XCTAssertEqual(report.plans.first?.replacedContainerKeys, ["assets/hero.prefab"])
        XCTAssertTrue(report.plans.first?.currentVersionRetainedPointerConflictKeys.isEmpty == true)

        let verifiedBundle = try AoVUnityFSParser.inspect(report.data)
        let nodesAfterMerge = try AoVUnityFSParser.extractNodes(
            report.data,
            maximumExpandedBytes: 512 * 1024 * 1024,
            maximumNodeCount: max(verifiedBundle.nodes.count, 1)
        )
        let serializedNode = try XCTUnwrap(nodesAfterMerge.first(where: {
            $0.path == "CAB-actor-test"
        }))
        let summary = try AoVSerializedFileParser.inspect(serializedNode.data)
        let finalRecord = try XCTUnwrap(summary.assetBundleRecords.first(where: {
            $0.pathID == 5001
        }))
        let importedPathID = try XCTUnwrap(report.importedPathIDs.first)
        XCTAssertEqual(finalRecord.containerEntries.first?.asset.pathID, importedPathID)
        XCTAssertTrue(summary.serializedObjectPathIDs.contains(importedPathID))

        let importedValue = try AoVSerializedFileParser.decodeObjectWithTypeTree(
            pathID: importedPathID,
            in: serializedNode.data,
            summary: summary
        )
        XCTAssertEqual(importedValue.objectValue?["m_TestPathID"]?.intValue, 200)
        let retainedBaseValue = try AoVSerializedFileParser.decodeObjectWithTypeTree(
            pathID: 1001,
            in: serializedNode.data,
            summary: summary
        )
        XCTAssertEqual(retainedBaseValue.objectValue?["m_TestPathID"]?.intValue, 100)
    }

    private func objectNodes() -> [AoVUnityTypeTreeNode] {
        [
            AoVUnityTypeTreeNode(type: "Texture2D", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_TestPathID", level: 1, byteSize: 8, metaFlags: 0)
        ]
    }

    private func makeEntry(_ pointer: AoVSerializedAssetPointer) -> AoVSerializedAssetBundleEntry {
        AoVSerializedAssetBundleEntry(
            key: "assets/hero.prefab",
            preloadIndex: 0,
            preloadSize: 0,
            asset: pointer
        )
    }

    private func makeBundleRecord(
        entry: AoVSerializedAssetBundleEntry
    ) -> AoVSerializedAssetBundleRecord {
        AoVSerializedAssetBundleRecord(
            pathID: 5001,
            name: "actor/change-detection",
            preloadReferences: [],
            containerEntries: [entry],
            mainAsset: AoVSerializedAssetPointer(fileID: 0, pathID: 0),
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 0,
            scriptCompatibilityEntries: [],
            classCompatibilityIDs: [],
            classCompatibilityEntries: [],
            runtimeCompatibility: 0,
            assetBundleName: "actor/change-detection",
            dependencies: [],
            isStreamedSceneAssetBundle: false,
            trailingBytes: 0,
            trailingData: Data()
        )
    }

    private func makeSerializedFile(payload: Data, nodes: [AoVUnityTypeTreeNode]) -> Data {
        var metadata = Data()
        metadata.append(Data("2022.3.5f1\\0".utf8))
        appendInt32(13, to: &metadata)
        metadata.append(1)
        appendInt32(1, to: &metadata)
        appendInt32(28, to: &metadata)
        metadata.append(0)
        appendInt16(-1, to: &metadata)
        metadata.append(Data(repeating: 0, count: 16))
        appendTypeTree(nodes, to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(1, to: &metadata)
        align4(&metadata)
        appendInt64(1001, to: &metadata)
        appendInt64(0, to: &metadata)
        appendUInt32(UInt32(payload.count), to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        metadata.append(0)

        let dataOffset = ((48 + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + payload.count
        var file = Data()
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt32(fileSize), to: &file)
        appendBE(UInt32(22), to: &file)
        appendBE(UInt32(dataOffset), to: &file)
        file.append(0)
        file.append(contentsOf: [0, 0, 0])
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt64(fileSize), to: &file)
        appendBE(UInt64(dataOffset), to: &file)
        appendBE(UInt64(0), to: &file)
        file.append(metadata)
        while file.count < dataOffset { file.append(0) }
        file.append(payload)
        return file
    }

    private func appendTypeTree(_ nodes: [AoVUnityTypeTreeNode], to data: inout Data) {
        var strings = Data()
        var offsets: [String: UInt32] = [:]
        func intern(_ value: String) -> UInt32 {
            if let offset = offsets[value] { return offset }
            let offset = UInt32(strings.count)
            strings.append(Data(value.utf8))
            strings.append(0)
            offsets[value] = offset
            return offset
        }
        let pairs = nodes.map { (intern($0.type), intern($0.name)) }
        appendInt32(Int32(nodes.count), to: &data)
        appendInt32(Int32(strings.count), to: &data)
        for (index, node) in nodes.enumerated() {
            appendInt16(1, to: &data)
            data.append(node.level)
            data.append(0)
            appendUInt32(pairs[index].0, to: &data)
            appendUInt32(pairs[index].1, to: &data)
            appendInt32(node.byteSize, to: &data)
            appendInt32(Int32(index), to: &data)
            appendUInt32(node.metaFlags, to: &data)
            appendUInt64(0, to: &data)
        }
        data.append(strings)
    }

    private func makeAssetBundleRecord(
        rootPathID: Int64,
        assetPathID: Int64
    ) -> AoVSerializedAssetBundleRecord {
        AoVSerializedAssetBundleRecord(
            pathID: rootPathID,
            name: "actor/test",
            preloadReferences: [],
            containerEntries: [
                AoVSerializedAssetBundleEntry(
                    key: "assets/hero.prefab",
                    preloadIndex: 0,
                    preloadSize: 0,
                    asset: AoVSerializedAssetPointer(fileID: 0, pathID: assetPathID)
                )
            ],
            mainAsset: AoVSerializedAssetPointer(fileID: 0, pathID: 0),
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 0,
            scriptCompatibilityEntries: [],
            classCompatibilityIDs: [],
            classCompatibilityEntries: [],
            runtimeCompatibility: 0,
            assetBundleName: "actor/test",
            dependencies: [],
            isStreamedSceneAssetBundle: false,
            trailingBytes: 0,
            trailingData: Data()
        )
    }

    private func makeSerializedFileWithBundleRecord(
        record: AoVSerializedAssetBundleRecord,
        assetPathID: Int64,
        assetPayload: Data,
        assetNodes: [AoVUnityTypeTreeNode]
    ) throws -> Data {
        let bundlePayload = try AoVSerializedAssetBundleWriter.serialize(
            record,
            formatVersion: 22,
            unityVersion: "2022.3.5f1",
            littleEndian: true
        )
        var dataSection = Data()
        let assetStart: Int64 = 0
        dataSection.append(assetPayload)
        while dataSection.count % 16 != 0 { dataSection.append(0) }
        let bundleStart = Int64(dataSection.count)
        dataSection.append(bundlePayload)

        var metadata = Data()
        metadata.append(Data("2022.3.5f1\\0".utf8))
        appendInt32(13, to: &metadata)
        metadata.append(1)
        appendInt32(2, to: &metadata)
        appendSerializedType(classID: 142, nodes: [], to: &metadata)
        appendSerializedType(classID: 28, nodes: assetNodes, to: &metadata)
        appendInt32(2, to: &metadata)
        align4(&metadata)
        appendInt64(assetPathID, to: &metadata)
        appendInt64(assetStart, to: &metadata)
        appendUInt32(UInt32(assetPayload.count), to: &metadata)
        appendInt32(1, to: &metadata)
        appendInt64(record.pathID, to: &metadata)
        appendInt64(bundleStart, to: &metadata)
        appendUInt32(UInt32(bundlePayload.count), to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        metadata.append(0)

        let dataOffset = ((48 + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + dataSection.count
        var file = Data()
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt32(fileSize), to: &file)
        appendBE(UInt32(22), to: &file)
        appendBE(UInt32(dataOffset), to: &file)
        file.append(0)
        file.append(contentsOf: [0, 0, 0])
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt64(fileSize), to: &file)
        appendBE(UInt64(dataOffset), to: &file)
        appendBE(UInt64(0), to: &file)
        file.append(metadata)
        while file.count < dataOffset { file.append(0) }
        file.append(dataSection)
        return file
    }

    private func appendSerializedType(
        classID: Int32,
        nodes: [AoVUnityTypeTreeNode],
        to data: inout Data
    ) {
        appendInt32(classID, to: &data)
        data.append(0)
        appendInt16(-1, to: &data)
        data.append(Data(repeating: 0, count: 16))
        appendTypeTree(nodes, to: &data)
        appendInt32(0, to: &data)
    }

    private func makeUnityFS(serializedFile: Data, nodePath: String) throws -> Data {
        try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: nodePath, data: serializedFile, flags: 7)],
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2022.3.5f1",
            blockInfoLayout: .arenaOfValor,
            aovEncryption: true
        ).data
    }

    private func appendInt16(_ value: Int16, to data: inout Data) {
        let bits = UInt16(bitPattern: value)
        data.append(UInt8(truncatingIfNeeded: bits))
        data.append(UInt8(truncatingIfNeeded: bits >> 8))
    }

    private func appendInt32(_ value: Int32, to data: inout Data) {
        appendUInt32(UInt32(bitPattern: value), to: &data)
    }

    private func appendUInt32(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 0, through: 24, by: 8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendInt64(_ value: Int64, to data: inout Data) {
        appendUInt64(UInt64(bitPattern: value), to: &data)
    }

    private func appendUInt64(_ value: UInt64, to data: inout Data) {
        for shift in stride(from: 0, through: 56, by: 8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendBE(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 24, through: 0, by: -8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendBE(_ value: UInt64, to data: inout Data) {
        for shift in stride(from: 56, through: 0, by: -8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func align4(_ data: inout Data) {
        while data.count % 4 != 0 { data.append(0) }
    }
}
