import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVUnityFSContentRewriterTests: XCTestCase {
    func testReplacesOneNodeAndPreservesOtherNodes() throws {
        let originalNodes = [
            AoVUnityFSRepackNode(path: "CAB-catalog", data: Data("catalog-old".utf8), flags: 4),
            AoVUnityFSRepackNode(path: "CAB-assets", data: Data("asset-data-stays".utf8), flags: 0),
            AoVUnityFSRepackNode(path: "assets/skin.bin", data: Data([1, 2, 3]), flags: 2)
        ]
        let original = try AoVUnityFSRepacker.repack(
            originalNodes,
            formatVersion: 7,
            playerVersion: "5.0.0",
            engineVersion: "2021.3.2f1"
        ).data

        let replacement = Data("catalog-rebuilt-with-a-larger-record".utf8)
        let report = try AoVUnityFSContentRewriter.replaceNode(
            in: original,
            nodePath: "CAB-catalog",
            with: replacement
        )

        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(report.targetNodePath, "CAB-catalog")
        XCTAssertEqual(report.originalNodeBytes, UInt64("catalog-old".utf8.count))
        XCTAssertEqual(report.rewrittenNodeBytes, UInt64(replacement.count))

        let info = try AoVUnityFSParser.inspect(report.payload)
        XCTAssertEqual(info.nodes.map(\.path), originalNodes.map(\.path))
        let nodes = try AoVUnityFSParser.extractNodes(report.payload)
        XCTAssertEqual(nodes.map(\.flags), originalNodes.map(\.flags))
        let byPath = Dictionary(uniqueKeysWithValues: nodes.map { ($0.path, $0.data) })
        XCTAssertEqual(byPath["CAB-catalog"], replacement)
        XCTAssertEqual(byPath["CAB-assets"], Data("asset-data-stays".utf8))
        XCTAssertEqual(byPath["assets/skin.bin"], Data([1, 2, 3]))
    }

    func testRepackerEncryptsAndParserRoundTripsLegacyUnityCNHeader() throws {
        let nodes = [
            AoVUnityFSRepackNode(
                path: "CAB-legacy",
                data: Data("legacy encrypted UnityFS payload".utf8),
                flags: 7
            )
        ]
        let report = try AoVUnityFSRepacker.repack(
            nodes,
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2020.3.33f1",
            blockInfoLayout: .arenaOfValor,
            aovEncryption: true
        )

        let info = try AoVUnityFSParser.inspect(report.payload)
        let extracted = try AoVUnityFSParser.extractNodes(report.payload)

        XCTAssertTrue(info.usesAoVEncryption)
        XCTAssertNotEqual(info.flags & 0x200, 0)
        XCTAssertEqual(info.flags & 0x400, 0)
        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(extracted.map(\.path), ["CAB-legacy"])
        XCTAssertEqual(extracted.first?.data, nodes.first?.data)
    }

    func testRepackerEncryptsAndParserRoundTripsCurrentUnityAOVHeader() throws {
        let nodes = [
            AoVUnityFSRepackNode(
                path: "CAB-current",
                data: Data("current encrypted UnityFS payload".utf8),
                flags: 9
            )
        ]
        let report = try AoVUnityFSRepacker.repack(
            nodes,
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2022.3.5f1",
            blockInfoLayout: .arenaOfValor,
            aovEncryption: true
        )

        let info = try AoVUnityFSParser.inspect(report.payload)
        let extracted = try AoVUnityFSParser.extractNodes(report.payload)

        XCTAssertTrue(info.usesAoVEncryption)
        XCTAssertNotEqual(info.flags & 0x400, 0)
        XCTAssertEqual(info.flags & 0x200, 0)
        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(extracted.map(\.path), ["CAB-current"])
        XCTAssertEqual(extracted.first?.data, nodes.first?.data)
    }

    func testRewriterRetainsAOVEncryptionWhileReplacingNode() throws {
        let originalNodes = [
            AoVUnityFSRepackNode(path: "CAB-catalog", data: Data("encrypted-old".utf8), flags: 4),
            AoVUnityFSRepackNode(path: "CAB-assets", data: Data("assets-preserved".utf8), flags: 3)
        ]
        let original = try AoVUnityFSRepacker.repack(
            originalNodes,
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2022.3.5f1",
            blockInfoLayout: .arenaOfValor,
            aovEncryption: true
        ).data

        let replacement = Data("encrypted-new-catalog-with-extra-bytes".utf8)
        let report = try AoVUnityFSContentRewriter.replaceNode(
            in: original,
            nodePath: "CAB-catalog",
            with: replacement
        )

        let info = try AoVUnityFSParser.inspect(report.payload)
        let nodes = try AoVUnityFSParser.extractNodes(report.payload)
        XCTAssertTrue(report.roundTripVerified)
        XCTAssertTrue(info.usesAoVEncryption)
        XCTAssertNotEqual(info.flags & 0x400, 0)
        XCTAssertEqual(nodes.map(\.path), ["CAB-catalog", "CAB-assets"])
        XCTAssertEqual(nodes.map(\.flags), [4, 3])
        XCTAssertEqual(nodes[0].data, replacement)
        XCTAssertEqual(nodes[1].data, Data("assets-preserved".utf8))
    }

    func testWritableTemplateGuardRejectsExpandedBundleBeyondLimit() {
        let oversized = AoVUnityFSBundleInfo(
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2022.3.5f1",
            totalFileSize: 128,
            flags: 0x40,
            blockInfoCompression: "none",
            blockInfoLayout: .standard,
            blockInfoAtEnd: false,
            usesAoVEncryption: false,
            blockPaddingAtStart: false,
            blocks: [],
            nodes: [],
            totalUncompressedBytes: 512 * 1024 * 1024 + 1,
            totalCompressedBlockBytes: 0,
            availableBlockDataBytes: 0,
            blockDataOffset: 0
        )

        XCTAssertThrowsError(
            try AoVUnityFSContentRewriter.validateWritableTemplate(oversized)
        ) { error in
            guard let rewriteError = error as? AoVUnityFSContentRewriteError,
                  case .expandedArchiveTooLarge = rewriteError else {
                return XCTFail("Expected expanded-size guard, got \(error)")
            }
        }
    }

    func testAssetBundleObjectClosureFollowsTransitiveLocalPointersWithoutImportingRoot() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "MonoBehaviour", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<GameObject>", name: "m_GameObject", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 2, byteSize: 8, metaFlags: 0)
        ]
        let nullPointer = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: .object([
                "m_GameObject": .object([
                    "m_FileID": .signed(0),
                    "m_PathID": .signed(0)
                ])
            ]),
            littleEndian: true
        )
        let firstDependency = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: .object([
                "m_GameObject": .object([
                    "m_FileID": .signed(0),
                    "m_PathID": .signed(1003)
                ])
            ]),
            littleEndian: true
        )
        let source = makePointerSerializedFile(
            pathIDs: [1001, 1002, 1003],
            payloads: [nullPointer, firstDependency, nullPointer],
            typeTreeNodes: nodes
        )
        let assetPointer = AoVSerializedAssetPointer(fileID: 0, pathID: 1002)
        let legacyRecord = AoVSerializedAssetBundleRecord(
            pathID: 1001,
            name: "legacy-root",
            preloadReferences: [assetPointer],
            containerEntries: [
                AoVSerializedAssetBundleEntry(
                    key: "assets/new-skin.prefab",
                    preloadIndex: 0,
                    preloadSize: 1,
                    asset: assetPointer
                )
            ],
            mainAsset: AoVSerializedAssetPointer(fileID: 0, pathID: 0),
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 0,
            scriptCompatibilityEntries: [],
            classCompatibilityIDs: [],
            classCompatibilityEntries: [],
            runtimeCompatibility: nil,
            assetBundleName: "actor/legacy",
            dependencies: [],
            isStreamedSceneAssetBundle: false,
            trailingBytes: 0,
            trailingData: Data()
        )
        let sourceSummary = try AoVSerializedFileParser.inspect(source)
        let closure = try AoVUnityFSAssetBundleMergeExecutor.requiredLocalObjectClosure(
            legacyRecord: legacyRecord,
            selectedContainerKeys: ["assets/new-skin.prefab"],
            sourceData: source,
            sourceSummary: sourceSummary
        )

        XCTAssertEqual(closure, [1002, 1003])
        XCTAssertFalse(closure.contains(1001))
    }

    func testImportsLegacyAssetBundleObjectGraphAndRebuildsUnityFSNode() throws {
        let pointerTree = [
            AoVUnityTypeTreeNode(type: "MonoBehaviour", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<GameObject>", name: "m_GameObject", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 2, byteSize: 8, metaFlags: 0)
        ]
        let nullPointerPayload = try AoVUnityTypeTreeWriter.write(
            nodes: pointerTree,
            value: .object([
                "m_GameObject": .object([
                    "m_FileID": .signed(0),
                    "m_PathID": .signed(0)
                ])
            ]),
            littleEndian: true
        )
        let childPointerPayload = try AoVUnityTypeTreeWriter.write(
            nodes: pointerTree,
            value: .object([
                "m_GameObject": .object([
                    "m_FileID": .signed(0),
                    "m_PathID": .signed(1003)
                ])
            ]),
            littleEndian: true
        )
        let legacyAssetPointer = AoVSerializedAssetPointer(fileID: 0, pathID: 1002)
        let legacyRecord = AoVSerializedAssetBundleRecord(
            pathID: 1001,
            name: "actor-test",
            preloadReferences: [legacyAssetPointer],
            containerEntries: [
                AoVSerializedAssetBundleEntry(
                    key: "assets/actor/test.prefab",
                    preloadIndex: 0,
                    preloadSize: 1,
                    asset: legacyAssetPointer
                )
            ],
            mainAsset: AoVSerializedAssetPointer(fileID: 0, pathID: 0),
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 0,
            scriptCompatibilityEntries: [],
            classCompatibilityIDs: [],
            classCompatibilityEntries: [],
            runtimeCompatibility: 1,
            assetBundleName: "actor/test",
            dependencies: [],
            isStreamedSceneAssetBundle: false,
            trailingBytes: 0,
            trailingData: Data()
        )
        let currentRecord = AoVSerializedAssetBundleRecord(
            pathID: 5001,
            name: "actor-test",
            preloadReferences: [],
            containerEntries: [],
            mainAsset: AoVSerializedAssetPointer(fileID: 0, pathID: 0),
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 0,
            scriptCompatibilityEntries: [],
            classCompatibilityIDs: [],
            classCompatibilityEntries: [],
            runtimeCompatibility: 1,
            assetBundleName: "actor/test",
            dependencies: [],
            isStreamedSceneAssetBundle: false,
            trailingBytes: 0,
            trailingData: Data()
        )
        let legacySerializedFile = try makeAssetBundleSerializedFile(
            rootRecord: legacyRecord,
            pointerTree: pointerTree,
            assetObjects: [
                (1002, childPointerPayload),
                (1003, nullPointerPayload)
            ]
        )
        let currentSerializedFile = try makeAssetBundleSerializedFile(
            rootRecord: currentRecord,
            pointerTree: pointerTree,
            assetObjects: [(5002, nullPointerPayload)]
        )
        let destinationBundle = try AoVUnityFSRepacker.repack(
            [
                AoVUnityFSRepackNode(
                    path: "CAB-current",
                    data: currentSerializedFile,
                    flags: 0
                )
            ],
            formatVersion: 7,
            playerVersion: "5.0.0",
            engineVersion: "2022.3.5f1"
        ).data

        let report = try AoVModRemakeExporter.mergeAssetBundleResource(
            resourcePath: "1.64.1/assetbundle/test.assetbundle",
            baseAssetBundle: destinationBundle,
            serializedFileNodePath: "CAB-current",
            legacyInputs: [
                AoVAssetBundleLegacyObjectImportInput(
                    source: "legacy-test",
                    serializedFile: legacySerializedFile,
                    legacyObjectPathID: 1001,
                    destinationObjectPathID: 5001
                )
            ],
            availableCurrentBundleNames: ["actor/test"]
        )

        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(report.importedObjectCount, 2)
        let finalBundleNodes = try AoVUnityFSParser.extractNodes(report.payload)
        let finalSerializedFile = try XCTUnwrap(finalBundleNodes.first(where: { $0.path == "CAB-current" })?.data)
        let finalSummary = try AoVSerializedFileParser.inspect(finalSerializedFile)
        let finalRecord = try XCTUnwrap(finalSummary.assetBundleRecords.first(where: { $0.pathID == 5001 }))
        XCTAssertEqual(finalRecord.containerEntries.map(\.key), ["assets/actor/test.prefab"])
        XCTAssertEqual(finalRecord.containerEntries.first?.asset.fileID, 0)
        XCTAssertEqual(finalRecord.containerEntries.first?.asset.pathID, report.importedPathIDs[0])
        XCTAssertEqual(finalRecord.preloadReferences.first?.pathID, report.importedPathIDs[0])
        XCTAssertTrue(finalSummary.serializedObjects.contains(where: { $0.pathID == report.importedPathIDs[1] }))
    }

    func testChangedContainerDetectionFollowsNestedPPtrGraph() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "MonoBehaviour", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<GameObject>", name: "m_GameObject", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 2, byteSize: 8, metaFlags: 0)
        ]
        func payload(_ target: Int64) throws -> Data {
            try AoVUnityTypeTreeWriter.write(
                nodes: nodes,
                value: .object([
                    "m_GameObject": .object([
                        "m_FileID": .signed(0),
                        "m_PathID": .signed(target)
                    ])
                ]),
                littleEndian: true
            )
        }
        let source = makePointerSerializedFile(
            pathIDs: [1001, 1002, 1003],
            payloads: [try payload(1002), try payload(1003), try payload(0)],
            typeTreeNodes: nodes
        )
        let destination = makePointerSerializedFile(
            pathIDs: [1001, 1002, 1003],
            payloads: [try payload(1002), try payload(1003), try payload(1002)],
            typeTreeNodes: nodes
        )
        let pointer = AoVSerializedAssetPointer(fileID: 0, pathID: 1001)
        let legacy = AoVSerializedAssetBundleRecord(
            pathID: 9001,
            name: "legacy",
            preloadReferences: [pointer],
            containerEntries: [
                AoVSerializedAssetBundleEntry(
                    key: "assets/graph.prefab",
                    preloadIndex: 0,
                    preloadSize: 1,
                    asset: pointer
                )
            ],
            mainAsset: AoVSerializedAssetPointer(fileID: 0, pathID: 0),
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 0,
            scriptCompatibilityEntries: [],
            classCompatibilityIDs: [],
            classCompatibilityEntries: [],
            runtimeCompatibility: nil,
            assetBundleName: "actor/graph",
            dependencies: [],
            isStreamedSceneAssetBundle: false,
            trailingBytes: 0,
            trailingData: Data()
        )
        let current = AoVSerializedAssetBundleRecord(
            pathID: 9001,
            name: "current",
            preloadReferences: [pointer],
            containerEntries: legacy.containerEntries,
            mainAsset: AoVSerializedAssetPointer(fileID: 0, pathID: 0),
            mainAssetPreloadIndex: 0,
            mainAssetPreloadSize: 0,
            scriptCompatibilityEntries: [],
            classCompatibilityIDs: [],
            classCompatibilityEntries: [],
            runtimeCompatibility: nil,
            assetBundleName: "actor/graph",
            dependencies: [],
            isStreamedSceneAssetBundle: false,
            trailingBytes: 0,
            trailingData: Data()
        )

        let changed = try AoVUnityFSAssetBundleMergeExecutor.changedContainerKeys(
            legacyRecord: legacy,
            currentRecord: current,
            sourceData: source,
            destinationData: destination,
            sourceSummary: AoVSerializedFileParser.inspect(source),
            destinationSummary: AoVSerializedFileParser.inspect(destination)
        )
        XCTAssertEqual(changed, ["assets/graph.prefab"])
    }

    func testRejectsMissingNodeWithoutProducingOutput() throws {
        let original = try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: "CAB-catalog", data: Data([1]))],
            engineVersion: "2020.3.34f1"
        ).data

        XCTAssertThrowsError(try AoVUnityFSContentRewriter.replaceNode(
            in: original,
            nodePath: "CAB-missing",
            with: Data([2])
        ))
    }
    private func makeAssetBundleSerializedFile(
        rootRecord: AoVSerializedAssetBundleRecord,
        pointerTree: [AoVUnityTypeTreeNode],
        assetObjects: [(Int64, Data)]
    ) throws -> Data {
        let rootPayload = try AoVSerializedAssetBundleWriter.serialize(
            rootRecord,
            formatVersion: 22,
            unityVersion: "2022.3.5f1",
            littleEndian: true
        )
        let objectPayloads: [(pathID: Int64, typeID: Int32, data: Data)] =
            [(rootRecord.pathID, 0, rootPayload)] +
            assetObjects.map { (pathID: $0.0, typeID: 1, data: $0.1) }

        var byteStarts: [Int64] = []
        var dataSection = Data()
        for object in objectPayloads {
            while dataSection.count % 16 != 0 { dataSection.append(0) }
            byteStarts.append(Int64(dataSection.count))
            dataSection.append(object.data)
        }

        var metadata = Data()
        metadata.append(Data("2022.3.5f1\0".utf8))
        appendLittleEndianInt32(13, to: &metadata)
        metadata.append(1)
        appendLittleEndianInt32(2, to: &metadata)
        for (classID, nodes) in [(Int32(142), [AoVUnityTypeTreeNode]()), (Int32(28), pointerTree)] {
            appendLittleEndianInt32(classID, to: &metadata)
            metadata.append(0)
            appendLittleEndianInt16(-1, to: &metadata)
            metadata.append(Data(repeating: 0, count: 16))
            appendPointerTypeTree(nodes, to: &metadata)
            appendLittleEndianInt32(0, to: &metadata)
        }
        appendLittleEndianInt32(Int32(objectPayloads.count), to: &metadata)
        while metadata.count % 4 != 0 { metadata.append(0) }
        for (index, object) in objectPayloads.enumerated() {
            appendLittleEndianInt64(object.pathID, to: &metadata)
            appendLittleEndianInt64(byteStarts[index], to: &metadata)
            appendLittleEndianUInt32(UInt32(object.data.count), to: &metadata)
            appendLittleEndianInt32(object.typeID, to: &metadata)
        }
        appendLittleEndianInt32(0, to: &metadata)
        appendLittleEndianInt32(0, to: &metadata)
        appendLittleEndianInt32(0, to: &metadata)
        metadata.append(0)

        let dataOffset = ((48 + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + dataSection.count
        var file = Data()
        appendBigEndianUInt32(UInt32(metadata.count), to: &file)
        appendBigEndianUInt32(UInt32(fileSize), to: &file)
        appendBigEndianUInt32(22, to: &file)
        appendBigEndianUInt32(UInt32(dataOffset), to: &file)
        file.append(0)
        file.append(contentsOf: [0, 0, 0])
        appendBigEndianUInt32(UInt32(metadata.count), to: &file)
        appendBigEndianUInt64(UInt64(fileSize), to: &file)
        appendBigEndianUInt64(UInt64(dataOffset), to: &file)
        appendBigEndianUInt64(0, to: &file)
        file.append(metadata)
        while file.count < dataOffset { file.append(0) }
        file.append(dataSection)
        return file
    }

    private func makePointerSerializedFile(
        pathIDs: [Int64],
        payloads: [Data],
        typeTreeNodes: [AoVUnityTypeTreeNode]
    ) -> Data {
        precondition(pathIDs.count == payloads.count)
        var byteStarts: [Int64] = []
        var dataSection = Data()
        for payload in payloads {
            while dataSection.count % 16 != 0 { dataSection.append(0) }
            byteStarts.append(Int64(dataSection.count))
            dataSection.append(payload)
        }

        var metadata = Data()
        metadata.append(Data("2022.3.5f1\0".utf8))
        appendLittleEndianInt32(13, to: &metadata)
        metadata.append(1)
        appendLittleEndianInt32(1, to: &metadata)
        appendLittleEndianInt32(28, to: &metadata)
        metadata.append(0)
        appendLittleEndianInt16(-1, to: &metadata)
        metadata.append(Data(repeating: 0, count: 16))
        appendPointerTypeTree(typeTreeNodes, to: &metadata)
        appendLittleEndianInt32(0, to: &metadata)
        appendLittleEndianInt32(Int32(pathIDs.count), to: &metadata)
        while metadata.count % 4 != 0 { metadata.append(0) }
        for index in pathIDs.indices {
            appendLittleEndianInt64(pathIDs[index], to: &metadata)
            appendLittleEndianInt64(byteStarts[index], to: &metadata)
            appendLittleEndianUInt32(UInt32(payloads[index].count), to: &metadata)
            appendLittleEndianInt32(0, to: &metadata)
        }
        appendLittleEndianInt32(0, to: &metadata)
        appendLittleEndianInt32(0, to: &metadata)
        appendLittleEndianInt32(0, to: &metadata)
        metadata.append(0)

        let dataOffset = ((48 + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + dataSection.count
        var file = Data()
        appendBigEndianUInt32(UInt32(metadata.count), to: &file)
        appendBigEndianUInt32(UInt32(fileSize), to: &file)
        appendBigEndianUInt32(22, to: &file)
        appendBigEndianUInt32(UInt32(dataOffset), to: &file)
        file.append(0)
        file.append(contentsOf: [0, 0, 0])
        appendBigEndianUInt32(UInt32(metadata.count), to: &file)
        appendBigEndianUInt64(UInt64(fileSize), to: &file)
        appendBigEndianUInt64(UInt64(dataOffset), to: &file)
        appendBigEndianUInt64(0, to: &file)
        file.append(metadata)
        while file.count < dataOffset { file.append(0) }
        file.append(dataSection)
        return file
    }

    private func appendPointerTypeTree(_ nodes: [AoVUnityTypeTreeNode], to data: inout Data) {
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
        appendLittleEndianInt32(Int32(nodes.count), to: &data)
        appendLittleEndianInt32(Int32(strings.count), to: &data)
        for (index, node) in nodes.enumerated() {
            appendLittleEndianInt16(1, to: &data)
            data.append(node.level)
            data.append(0)
            appendLittleEndianUInt32(pairs[index].0, to: &data)
            appendLittleEndianUInt32(pairs[index].1, to: &data)
            appendLittleEndianInt32(node.byteSize, to: &data)
            appendLittleEndianInt32(Int32(index), to: &data)
            appendLittleEndianUInt32(node.metaFlags, to: &data)
            appendLittleEndianUInt64(0, to: &data)
        }
        data.append(strings)
    }

    private func appendLittleEndianInt16(_ value: Int16, to data: inout Data) {
        appendLittleEndianUInt16(UInt16(bitPattern: value), to: &data)
    }
    private func appendLittleEndianUInt16(_ value: UInt16, to data: inout Data) {
        data.append(UInt8(truncatingIfNeeded: value))
        data.append(UInt8(truncatingIfNeeded: value >> 8))
    }
    private func appendLittleEndianInt32(_ value: Int32, to data: inout Data) {
        appendLittleEndianUInt32(UInt32(bitPattern: value), to: &data)
    }
    private func appendLittleEndianUInt32(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 0, through: 24, by: 8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }
    private func appendLittleEndianInt64(_ value: Int64, to data: inout Data) {
        appendLittleEndianUInt64(UInt64(bitPattern: value), to: &data)
    }
    private func appendLittleEndianUInt64(_ value: UInt64, to data: inout Data) {
        for shift in stride(from: 0, through: 56, by: 8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }
    private func appendBigEndianUInt32(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 24, through: 0, by: -8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }
    private func appendBigEndianUInt64(_ value: UInt64, to data: inout Data) {
        for shift in stride(from: 56, through: 0, by: -8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }
}
