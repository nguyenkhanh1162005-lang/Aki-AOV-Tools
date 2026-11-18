import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVUnityFSParserTests: XCTestCase {
    func testInspectsUnityFSBundleWithFrontBlockInfo() throws {
        let bundle = makeBundle()
        let info = try AoVUnityFSParser.inspect(bundle)

        XCTAssertEqual(info.formatVersion, 6)
        XCTAssertEqual(info.engineVersion, "2020.3.34f1")
        XCTAssertEqual(info.blockInfoCompression, "none")
        XCTAssertFalse(info.blockInfoAtEnd)
        XCTAssertEqual(info.blocks.count, 1)
        XCTAssertEqual(info.nodes.count, 1)
        XCTAssertEqual(info.nodes.first?.path, "CAB-test")
        XCTAssertEqual(info.nodes.first?.size, 4)
        XCTAssertEqual(info.totalUncompressedBytes, 4)
        XCTAssertEqual(info.totalCompressedBlockBytes, 4)
    }

    func testInspectsUnityFSBundleWithEndBlockInfoAndAlignedHeader() throws {
        let bundle = makeBundle(
            formatVersion: 7,
            engineVersion: "2021.3.2f1",
            flags: 0xC0
        )
        let info = try AoVUnityFSParser.inspect(bundle)

        XCTAssertTrue(info.blockInfoAtEnd)
        XCTAssertEqual(info.formatVersion, 7)
        XCTAssertEqual(info.nodes.map(\.path), ["CAB-test"])
    }

    func testInspectsLZ4CompressedBlockInfo() throws {
        let bundle = makeBundle(flags: 0x42)
        let info = try AoVUnityFSParser.inspect(bundle)
        XCTAssertEqual(info.blockInfoCompression, "LZ4")
        XCTAssertEqual(info.nodes.first?.path, "CAB-test")
    }

    func testHonorsBlockInfoPaddingFlagForNewUnityRevisions() throws {
        let bundle = makeBundle(
            engineVersion: "2022.3.1f1",
            flags: 0x240
        )
        let info = try AoVUnityFSParser.inspect(bundle)

        XCTAssertTrue(info.blockPaddingAtStart)
        XCTAssertEqual(info.availableBlockDataBytes, 4)
    }

    func testAlignsVersionSixHeaderForUnity2019_4_15AndLater() throws {
        let bundle = makeBundle(engineVersion: "2019.4.20f1")
        let info = try AoVUnityFSParser.inspect(bundle)
        XCTAssertEqual(info.nodes.first?.path, "CAB-test")
    }

    func testRejectsUnencryptedPayloadMarkedWithLegacyEncryptionFlag() {
        // This synthetic fixture has a plain header but sets the legacy
        // UnityCN encryption bit. The parser must try the encrypted layout,
        // not silently treat 0x200 as a block-padding flag.
        let bundle = makeBundle(
            engineVersion: "2019.4.20f1",
            flags: 0x240
        )
        XCTAssertThrowsError(try AoVUnityFSParser.inspect(bundle)) { error in
            guard let parserError = error as? AoVUnityFSError,
                  case .fileSizeMismatch(_, _) = parserError else {
                return XCTFail("Expected malformed encrypted header rejection, got \(error)")
            }
        }
    }

    func testRejectsAmbiguousPaddingEncryptionFlagForUnknownRevision() {
        let bundle = makeBundle(engineVersion: "0.0.0", flags: 0x240)
        XCTAssertThrowsError(try AoVUnityFSParser.inspect(bundle)) { error in
            guard let parserError = error as? AoVUnityFSError,
                  case .ambiguousLegacyFlag = parserError else {
                return XCTFail("Expected ambiguous flag rejection, got \(error)")
            }
        }
    }

    func testRejectsNodeRangesOutsideUncompressedBlocks() {
        let bundle = makeBundle(nodeSize: 5)
        XCTAssertThrowsError(try AoVUnityFSParser.inspect(bundle))
    }

    func testInspectsAndExtractsUnityFSWithLZMABlockInfo() throws {
        let encoded = "VW5pdHlGUwAAAAAGNS4wLjAAMjAyMC4zLjM0ZjEAAAAAAAAAAGwAAAAlAAAAPwAAAEFdABAAAAAAZtpnmFVjFk2iV6wNtW3YSNbBKUyv8zen///yJcAAbGVnYWN5LWFvdi1ibG9jay1sem1h"
        let bundle = try XCTUnwrap(Data(base64Encoded: encoded))
        let info = try AoVUnityFSParser.inspect(bundle)

        XCTAssertEqual(info.blockInfoCompression, "LZMA")
        XCTAssertEqual(info.blockInfoLayout, .standard)
        XCTAssertEqual(info.nodes.map(\.path), ["CAB-lzma"])

        let extracted = try AoVUnityFSParser.extractNodes(bundle)
        XCTAssertEqual(extracted.first?.data, Data("legacy-aov-block-lzma".utf8))
    }

    func testRejectsDeclaredFileSizeMismatch() {
        var bundle = makeBundle()
        let sizeOffset = Data("UnityFS\0".utf8).count +
            4 + Data("5.0.0\0".utf8).count + Data("2020.3.34f1\0".utf8).count
        bundle[sizeOffset + 7] ^= 0x01
        XCTAssertThrowsError(try AoVUnityFSParser.inspect(bundle))
    }


    func testExtractsNodeFromUncompressedBlock() throws {
        let payload = Data([0x11, 0x22, 0x33, 0x44])
        let extracted = try AoVUnityFSParser.extractNodes(makeBundle(blockPayload: payload))

        XCTAssertEqual(extracted.count, 1)
        XCTAssertEqual(extracted.first?.path, "CAB-test")
        XCTAssertEqual(extracted.first?.data, payload)
    }

    func testExtractsNodeFromLZ4CompressedBlock() throws {
        let payload = Data("hello".utf8)
        let bundle = makeBundle(blockCompression: 2, blockPayload: payload)
        let extracted = try AoVUnityFSParser.extractNodes(bundle)

        XCTAssertEqual(extracted.first?.data, payload)
    }

    func testExtractsNodeWhenBlockInfoIsAtEnd() throws {
        let payload = Data([0xAA, 0xBB, 0xCC, 0xDD])
        let bundle = makeBundle(
            formatVersion: 7,
            engineVersion: "2021.3.2f1",
            flags: 0xC0,
            blockPayload: payload
        )
        let extracted = try AoVUnityFSParser.extractNodes(bundle)

        XCTAssertEqual(extracted.first?.data, payload)
    }

    func testExtractsNodeFromLZMACompressedDataBlock() throws {
        let encoded = "VW5pdHlGUwAAAAAGNS4wLjAAMjAyMC4zLjM0ZjEAAAAAAAAAAJYAAAA/AAAAPwAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAABUAAAAlAAEAAAABAAAAAAAAAAAAAAAAAAAAFQAAAABDQUItbHptYQBdABAAAAA2GUkpepqG5vS+QhEmWilv9ykni/SwzS+V///HpQAA"
        let bundle = try XCTUnwrap(Data(base64Encoded: encoded))

        let extracted = try AoVUnityFSParser.extractNodes(bundle)
        XCTAssertEqual(extracted.first?.path, "CAB-lzma")
        XCTAssertEqual(extracted.first?.data, Data("legacy-aov-block-lzma".utf8))
    }

    func testRejectsUnsupportedLZHAMBlockData() {
        let bundle = makeBundle(blockCompression: 4)
        XCTAssertThrowsError(try AoVUnityFSParser.extractNodes(bundle)) { error in
            guard let parserError = error as? AoVUnityFSError,
                  case .unsupportedBlockCompression(4) = parserError else {
                return XCTFail("Expected unsupported LZHAM block, got \(error)")
            }
        }
    }

    func testRejectsLZMADictionaryAboveSafetyLimit() {
        let properties = Data([93, 0, 0, 0, 0x10]) // 256 MiB dictionary
        XCTAssertThrowsError(
            try AoVUnityFSParser.decompressLZMA(
                properties,
                expectedSize: 4,
                maximumOutputSize: 64
            )
        ) { error in
            guard let parserError = error as? AoVUnityFSError,
                  case .resourceLimit(_) = parserError else {
                return XCTFail("Expected dictionary resource-limit rejection, got \(error)")
            }
        }
    }

    func testExtractionEnforcesExpandedSizeLimit() {
        let bundle = makeBundle(blockPayload: Data([1, 2, 3, 4]))
        XCTAssertThrowsError(
            try AoVUnityFSParser.extractNodes(bundle, maximumExpandedBytes: 3)
        )
    }


    func testExtractsNodeAcrossMultipleUnityFSBlocks() throws {
        let first = Data([0x10, 0x20, 0x30, 0x40])
        let second = Data([0x50, 0x60, 0x70, 0x80])
        let bundle = makeMultiBlockBundle([first, second])
        let extracted = try AoVUnityFSParser.extractNodes(bundle)

        XCTAssertEqual(extracted.count, 1)
        XCTAssertEqual(extracted.first?.path, "CAB-multi")
        var expected = first
        expected.append(second)
        XCTAssertEqual(extracted.first?.data, expected)
    }

    func testExtractsLZ4HCBlockWithSameDecoder() throws {
        let payload = Data("unity-lz4hc".utf8)
        let bundle = makeBundle(blockCompression: 3, blockPayload: payload)
        let extracted = try AoVUnityFSParser.extractNodes(bundle)

        XCTAssertEqual(extracted.first?.data, payload)
    }

    func testRebuilderRoundTripsMultipleNodesAndRawBlocks() throws {
        let template = makeBundle()
        let nodes = [
            AoVUnityFSExtractedNode(
                path: "CAB-test",
                data: Data("new payload".utf8),
                flags: 7
            ),
            AoVUnityFSExtractedNode(
                path: "CAB-second",
                data: Data([0x80, 0x81]),
                flags: 9
            )
        ]

        let rebuilt = try AoVUnityFSRebuilder.rebuild(
            template: template,
            nodes: nodes,
            blockSize: 4
        )
        let info = try AoVUnityFSParser.inspect(rebuilt.data)
        let extracted = try AoVUnityFSParser.extractNodes(
            rebuilt.data,
            maximumExpandedBytes: 1024
        )

        XCTAssertEqual(rebuilt.nodeCount, 2)
        XCTAssertEqual(rebuilt.blockCount, 4)
        XCTAssertEqual(rebuilt.uncompressedBytes, 13)
        XCTAssertEqual(rebuilt.dataMD5.count, 32)
        XCTAssertEqual(info.blockInfoCompression, "none")
        XCTAssertFalse(info.blockInfoAtEnd)
        XCTAssertFalse(info.blockPaddingAtStart)
        XCTAssertEqual(info.flags, 0x40)
        XCTAssertEqual(info.nodes.map(\.path), ["CAB-test", "CAB-second"])
        XCTAssertEqual(info.nodes.map(\.flags), [7, 9])
        XCTAssertEqual(extracted.map(\.path), nodes.map(\.path))
        XCTAssertEqual(extracted.map(\.data), nodes.map(\.data))
        XCTAssertEqual(extracted.map(\.flags), nodes.map(\.flags))
    }

    func testRebuilderPreservesUnity2019HeaderAlignment() throws {
        let template = makeBundle(
            formatVersion: 6,
            engineVersion: "2019.4.20f1"
        )
        let sourceNodes = try AoVUnityFSParser.extractNodes(template)
        let rebuilt = try AoVUnityFSRebuilder.rebuild(
            template: template,
            nodes: sourceNodes
        )
        let info = try AoVUnityFSParser.inspect(rebuilt.data)
        let extracted = try AoVUnityFSParser.extractNodes(rebuilt.data)

        XCTAssertEqual(info.formatVersion, 6)
        XCTAssertEqual(info.engineVersion, "2019.4.20f1")
        XCTAssertEqual(extracted.first?.data, sourceNodes.first?.data)
    }

    func testRebuilderSupportsEmptyNodePayload() throws {
        let template = makeBundle()
        let nodes = [
            AoVUnityFSExtractedNode(path: "CAB-empty", data: Data(), flags: 3)
        ]

        let rebuilt = try AoVUnityFSRebuilder.rebuild(template: template, nodes: nodes)
        let info = try AoVUnityFSParser.inspect(rebuilt.data)
        let extracted = try AoVUnityFSParser.extractNodes(rebuilt.data)

        XCTAssertEqual(rebuilt.blockCount, 0)
        XCTAssertEqual(info.nodes.count, 1)
        XCTAssertEqual(info.totalUncompressedBytes, 0)
        XCTAssertEqual(extracted.first?.path, "CAB-empty")
        XCTAssertEqual(extracted.first?.data, Data())
        XCTAssertEqual(extracted.first?.flags, 3)
    }

    func testLegacyRebuilderRejectsAOVEncryptedTemplateInsteadOfDroppingEncryption() throws {
        let encrypted = try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: "CAB-encrypted", data: Data("protected".utf8))],
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2022.3.5f1",
            blockInfoLayout: .arenaOfValor,
            aovEncryption: true
        ).data
        let nodes = try AoVUnityFSParser.extractNodes(encrypted)

        XCTAssertThrowsError(
            try AoVUnityFSRebuilder.rebuild(template: encrypted, nodes: nodes)
        ) { error in
            guard let rebuildError = error as? AoVUnityFSRebuildError,
                  case .unsupportedTemplate = rebuildError else {
                return XCTFail("Expected encrypted-template rejection, got \(error)")
            }
        }
    }

    func testRebuilderRejectsDuplicateNodePaths() {
        let nodes = [
            AoVUnityFSExtractedNode(path: "CAB-same", data: Data([1])),
            AoVUnityFSExtractedNode(path: "CAB-same", data: Data([2]))
        ]

        XCTAssertThrowsError(try AoVUnityFSRebuilder.rebuild(template: makeBundle(), nodes: nodes)) { error in
            guard let rebuildError = error as? AoVUnityFSRebuildError,
                  case .duplicateNodePath("CAB-same") = rebuildError else {
                return XCTFail("Expected duplicate path rejection, got \(error)")
            }
        }
    }

    func testRebuilderRejectsUnsafeNodePaths() {
        let nodes = [AoVUnityFSExtractedNode(path: "../escape", data: Data([1]))]
        XCTAssertThrowsError(try AoVUnityFSRebuilder.rebuild(template: makeBundle(), nodes: nodes)) { error in
            guard let rebuildError = error as? AoVUnityFSRebuildError,
                  case .unsafeNodePath("../escape") = rebuildError else {
                return XCTFail("Expected unsafe path rejection, got \(error)")
            }
        }
    }

    private func makeBundle(
        formatVersion: UInt32 = 6,
        engineVersion: String = "2020.3.34f1",
        flags: UInt32 = 0x40,
        nodeSize: UInt64? = nil,
        blockCompression: UInt16 = 0,
        blockPayload: Data = Data([0x11, 0x22, 0x33, 0x44])
    ) -> Data {
        let storedDataBlock: Data
        if blockCompression == 2 || blockCompression == 3 {
            storedDataBlock = makeLZ4LiteralBlock(blockPayload)
        } else {
            storedDataBlock = blockPayload
        }

        var blockInfo = Data(repeating: 0, count: 16)
        appendBE(UInt32(1), to: &blockInfo) // block count
        appendBE(UInt32(blockPayload.count), to: &blockInfo)
        appendBE(UInt32(storedDataBlock.count), to: &blockInfo)
        appendBE(blockCompression, to: &blockInfo)
        appendBE(UInt32(1), to: &blockInfo) // node count
        appendBE(UInt64(0), to: &blockInfo)
        appendBE(nodeSize ?? UInt64(blockPayload.count), to: &blockInfo)
        appendBE(UInt32(0), to: &blockInfo)
        blockInfo.append(Data("CAB-test\0".utf8))

        let infoCompression = flags & 0x3F
        var storedBlockInfo = blockInfo
        if infoCompression == 2 || infoCompression == 3 {
            var lz4 = Data([0xF0])
            var remaining = blockInfo.count - 15
            while remaining >= 255 {
                lz4.append(255)
                remaining -= 255
            }
            lz4.append(UInt8(remaining))
            lz4.append(blockInfo)
            storedBlockInfo = lz4
        }

        var bundle = Data("UnityFS\0".utf8)
        appendBE(formatVersion, to: &bundle)
        bundle.append(Data("5.0.0\0".utf8))
        bundle.append(Data((engineVersion + "\0").utf8))

        let sizeOffset = bundle.count
        appendBE(UInt64(0), to: &bundle)
        appendBE(UInt32(storedBlockInfo.count), to: &bundle)
        appendBE(UInt32(blockInfo.count), to: &bundle)
        appendBE(flags, to: &bundle)
        let versionParts = engineVersion.split(separator: ".")
        let is2019Aligned = versionParts.count >= 3 &&
            versionParts[0] == "2019" &&
            versionParts[1] == "4" &&
            (Int(versionParts[2].prefix(while: { $0.isNumber })) ?? 0) >= 15
        if formatVersion >= 7 || is2019Aligned {
            align16(&bundle)
        }

        if flags & 0x80 != 0 {
            if flags & 0x200 != 0 { align16(&bundle) }
            bundle.append(storedDataBlock)
            bundle.append(storedBlockInfo)
        } else {
            bundle.append(storedBlockInfo)
            if flags & 0x200 != 0 { align16(&bundle) }
            bundle.append(storedDataBlock)
        }

        let totalSize = UInt64(bundle.count)
        var encodedSize = Data()
        appendBE(totalSize, to: &encodedSize)
        bundle.replaceSubrange(sizeOffset..<(sizeOffset + 8), with: encodedSize)
        return bundle
    }


    private func makeMultiBlockBundle(_ blocks: [Data]) -> Data {
        var blockInfo = Data(repeating: 0, count: 16)
        appendBE(UInt32(blocks.count), to: &blockInfo)
        for block in blocks {
            appendBE(UInt32(block.count), to: &blockInfo)
            appendBE(UInt32(block.count), to: &blockInfo)
            appendBE(UInt16(0), to: &blockInfo)
        }
        appendBE(UInt32(1), to: &blockInfo)
        appendBE(UInt64(0), to: &blockInfo)
        appendBE(UInt64(blocks.reduce(0) { $0 + $1.count }), to: &blockInfo)
        appendBE(UInt32(0), to: &blockInfo)
        blockInfo.append(Data("CAB-multi\0".utf8))

        var bundle = Data("UnityFS\0".utf8)
        appendBE(UInt32(6), to: &bundle)
        bundle.append(Data("5.0.0\0".utf8))
        bundle.append(Data("2020.3.34f1\0".utf8))
        let sizeOffset = bundle.count
        appendBE(UInt64(0), to: &bundle)
        appendBE(UInt32(blockInfo.count), to: &bundle)
        appendBE(UInt32(blockInfo.count), to: &bundle)
        appendBE(UInt32(0x40), to: &bundle)
        bundle.append(blockInfo)
        for block in blocks { bundle.append(block) }

        var encodedSize = Data()
        appendBE(UInt64(bundle.count), to: &encodedSize)
        bundle.replaceSubrange(sizeOffset..<(sizeOffset + 8), with: encodedSize)
        return bundle
    }

    private func makeLZ4LiteralBlock(_ data: Data) -> Data {
        var result = Data([UInt8(min(data.count, 15) << 4)])
        if data.count >= 15 {
            var remaining = data.count - 15
            while remaining >= 255 {
                result.append(255)
                remaining -= 255
            }
            result.append(UInt8(remaining))
        }
        result.append(data)
        return result
    }

    private func align16(_ data: inout Data) {
        while data.count % 16 != 0 { data.append(0) }
    }

    private func appendBE(_ value: UInt16, to data: inout Data) {
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
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
