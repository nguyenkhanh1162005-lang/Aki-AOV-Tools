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

    func testRejectsLegacyEncryptionFlagInsteadOfTreatingItAsPadding() {
        let bundle = makeBundle(
            engineVersion: "2019.4.20f1",
            flags: 0x240
        )
        XCTAssertThrowsError(try AoVUnityFSParser.inspect(bundle)) { error in
            guard let parserError = error as? AoVUnityFSError,
                  case .unsupportedEncryption(_) = parserError else {
                return XCTFail("Expected unsupported encryption, got \(error)")
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

    func testRejectsCompressedMetadataModesNotYetImplemented() {
        let bundle = makeBundle(flags: 0x41)
        XCTAssertThrowsError(try AoVUnityFSParser.inspect(bundle)) { error in
            guard let parserError = error as? AoVUnityFSError,
                  case .unsupportedInfoCompression(1) = parserError else {
                return XCTFail("Expected unsupported LZMA metadata, got \(error)")
            }
        }
    }

    func testRejectsDeclaredFileSizeMismatch() {
        var bundle = makeBundle()
        let sizeOffset = Data("UnityFS\0".utf8).count +
            4 + Data("5.0.0\0".utf8).count + Data("2020.3.34f1\0".utf8).count
        bundle[sizeOffset + 7] ^= 0x01
        XCTAssertThrowsError(try AoVUnityFSParser.inspect(bundle))
    }

    private func makeBundle(
        formatVersion: UInt32 = 6,
        engineVersion: String = "2020.3.34f1",
        flags: UInt32 = 0x40,
        nodeSize: UInt64 = 4
    ) -> Data {
        var blockInfo = Data(repeating: 0, count: 16)
        appendBE(UInt32(1), to: &blockInfo) // block count
        appendBE(UInt32(4), to: &blockInfo) // uncompressed block bytes
        appendBE(UInt32(4), to: &blockInfo) // compressed block bytes
        appendBE(UInt16(0), to: &blockInfo) // no block compression
        appendBE(UInt32(1), to: &blockInfo) // node count
        appendBE(UInt64(0), to: &blockInfo)
        appendBE(nodeSize, to: &blockInfo)
        appendBE(UInt32(0), to: &blockInfo)
        blockInfo.append(Data("CAB-test\0".utf8))

        let dataBlock = Data([0x11, 0x22, 0x33, 0x44])
        var bundle = Data("UnityFS\0".utf8)
        appendBE(formatVersion, to: &bundle)
        bundle.append(Data("5.0.0\0".utf8))
        bundle.append(Data((engineVersion + "\0").utf8))

        let sizeOffset = bundle.count
        appendBE(UInt64(0), to: &bundle)
        appendBE(UInt32(blockInfo.count), to: &bundle)
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
            bundle.append(dataBlock)
            bundle.append(blockInfo)
        } else {
            bundle.append(blockInfo)
            if flags & 0x200 != 0 { align16(&bundle) }
            bundle.append(dataBlock)
        }

        let totalSize = UInt64(bundle.count)
        var encodedSize = Data()
        appendBE(totalSize, to: &encodedSize)
        bundle.replaceSubrange(sizeOffset..<(sizeOffset + 8), with: encodedSize)
        return bundle
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
