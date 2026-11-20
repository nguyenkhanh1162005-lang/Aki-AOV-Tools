import Foundation
import XCTest
import CryptoKit
@testable import AOVSkinMerger

final class AoVNIFSArchiveInspectorTests: XCTestCase {
    func testInspectsMatchingIFSAndCURESCompanion() throws {
        let ifs = makeIFS()
        let cures = makeCURES(for: ifs)

        let result = try AoVNIFSArchiveInspector.inspectCompanion(ifsData: ifs, curesData: cures)
        XCTAssertTrue(result.companionMatches)
        XCTAssertEqual(result.ifsByteCount, ifs.count)
        XCTAssertEqual(result.embeddedHeaderOffset, 446)
        XCTAssertEqual(result.archiveHeader.archiveSize, 204)
        XCTAssertEqual(result.archiveHeader.hetTablePosition, 172)
        XCTAssertEqual(result.archiveHeader.betTablePosition, 188)
    }

    func testRejectsCURESWithWrongReferencedIFSSize() {
        let ifs = makeIFS()
        var cures = makeCURES(for: ifs)
        cures.writeUInt64LE(UInt64(ifs.count + 1), at: 0x48)
        XCTAssertThrowsError(try AoVNIFSArchiveInspector.inspectCompanion(ifsData: ifs, curesData: cures))
    }

    func testRejectsCorruptedHETTableDigest() {
        var ifs = makeIFS()
        ifs[184] ^= 0xFF
        XCTAssertThrowsError(try AoVNIFSArchiveInspector.inspectIFS(ifs))
    }

    func testRejectsCorruptedBETTableDigest() {
        var ifs = makeIFS()
        ifs[200] ^= 0xFF
        XCTAssertThrowsError(try AoVNIFSArchiveInspector.inspectIFS(ifs))
    }

    func testRejectsOverlappingNIFSTables() {
        var ifs = makeIFS()
        ifs.writeUInt64LE(180, at: 28)
        XCTAssertThrowsError(try AoVNIFSArchiveInspector.inspectIFS(ifs))
    }

    private func makeCURES(for ifs: Data) -> Data {
        var cures = Data(repeating: 0, count: 618)
        cures.writeUInt32LE(0x0134_3D03, at: 0)
        cures.writeUInt32LE(296, at: 8)
        cures.writeUInt64LE(UInt64(ifs.count), at: 0x48)
        cures.writeUInt64LE(446, at: 0x70)
        cures.writeUInt64LE(172, at: 0x78)
        cures.replaceSubrange(446..<618, with: ifs.prefix(172))
        return cures
    }

    private func makeIFS() -> Data {
        var data = Data(repeating: 0, count: 212)
        data.writeUInt32LE(0x7366_696E, at: 0)
        data.writeUInt32LE(172, at: 4)
        data.writeUInt16LE(0, at: 8)
        data.writeUInt16LE(5, at: 10)
        data.writeUInt64LE(204, at: 12)
        data.writeUInt64LE(188, at: 20)
        data.writeUInt64LE(172, at: 28)
        data.writeUInt64LE(204, at: 36)
        data.writeUInt64LE(208, at: 44)
        data.writeUInt64LE(16, at: 52)
        data.writeUInt64LE(16, at: 60)
        data.writeUInt64LE(4, at: 68)
        data.writeUInt64LE(4, at: 76)
        data.writeUInt32LE(16_384, at: 84)
        data.writeUInt32LE(16_384, at: 88)
        data.replaceSubrange(172..<176, with: [0x48, 0x45, 0x54, 0x1A])
        data.writeUInt32LE(1, at: 176)
        data.writeUInt32LE(4, at: 180)
        data.replaceSubrange(188..<192, with: [0x42, 0x45, 0x54, 0x1A])
        data.writeUInt32LE(1, at: 192)
        data.writeUInt32LE(4, at: 196)
        data.replaceSubrange(124..<140, with: Data(Insecure.MD5.hash(data: data.subdata(in: 188..<204))))
        data.replaceSubrange(140..<156, with: Data(Insecure.MD5.hash(data: data.subdata(in: 172..<188))))
        return data
    }
}

private extension Data {
    mutating func writeUInt16LE(_ value: UInt16, at offset: Int) {
        writeUInt32LE(UInt32(value), at: offset, byteCount: 2)
    }

    mutating func writeUInt32LE(_ value: UInt32, at offset: Int) {
        writeUInt32LE(value, at: offset, byteCount: 4)
    }

    mutating func writeUInt64LE(_ value: UInt64, at offset: Int) {
        for index in 0..<8 { self[offset + index] = UInt8(truncatingIfNeeded: value >> (index * 8)) }
    }

    private mutating func writeUInt32LE(_ value: UInt32, at offset: Int, byteCount: Int) {
        for index in 0..<byteCount { self[offset + index] = UInt8(truncatingIfNeeded: value >> (index * 8)) }
    }
}
