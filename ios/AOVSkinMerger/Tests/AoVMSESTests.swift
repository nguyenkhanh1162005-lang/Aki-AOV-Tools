import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVMSESTests: XCTestCase {
    private func makeMSES(fieldCount: UInt32, rows: [(UInt32, String)]) throws -> Data {
        var header = Data(repeating: 0, count: AoVMSESParser.headerSize)
        header.replaceSubrange(0..<4, with: Data("MSES".utf8))
        func write(_ value: UInt32, at offset: Int) {
            header[offset] = UInt8(value & 0xFF)
            header[offset + 1] = UInt8((value >> 8) & 0xFF)
            header[offset + 2] = UInt8((value >> 16) & 0xFF)
            header[offset + 3] = UInt8((value >> 24) & 0xFF)
        }
        write(7, at: 4)
        write(fieldCount, at: 8)
        write(UInt32(rows.count), at: 12)
        let shell = AoVMSESTable(
            version: 7,
            fieldCount: fieldCount,
            rows: [],
            header: header,
            checksumValid: true
        )
        let payload = rows.map { key, value -> Data in
            var row = Data([
                UInt8(key & 0xFF),
                UInt8((key >> 8) & 0xFF),
                UInt8((key >> 16) & 0xFF),
                UInt8((key >> 24) & 0xFF)
            ])
            row.append(Data(value.utf8))
            return row
        }
        return try AoVMSESParser.build(shell, rows: payload)
    }

    private func rowMap(_ table: AoVMSESTable) -> [UInt32: String] {
        Dictionary(uniqueKeysWithValues: table.rows.map { row in
            let key = UInt32(row[0]) |
                (UInt32(row[1]) << 8) |
                (UInt32(row[2]) << 16) |
                (UInt32(row[3]) << 24)
            return (key, String(data: row.dropFirst(4), encoding: .utf8) ?? "")
        })
    }

    func testMSESHeaderChecksumAndBuildRoundTrip() throws {
        let original = try makeMSES(fieldCount: 4, rows: [(1, "one"), (2, "two")])
        let table = try AoVMSESParser.parse(original)
        XCTAssertEqual(table.version, 7)
        XCTAssertEqual(table.fieldCount, 4)
        XCTAssertEqual(table.rows.count, 2)
        XCTAssertTrue(table.checksumValid)
        XCTAssertEqual(try AoVMSESParser.build(table, rows: table.rows), original)
    }

    func testActorOwnedRowsAppendOverlayAndResolveSharedConflictsByOwner() throws {
        let path = "Databin/Client/Actor/heroSkin.bytes"
        let basePlain = try makeMSES(fieldCount: 631, rows: [
            (59300, "base actor 593"),
            (59500, "base actor 595 stale"),
            (7, "global base")
        ])
        let overlayPlain = try makeMSES(fieldCount: 631, rows: [
            (59300, "overlay must not replace actor 593"),
            (59500, "overlay actor 595"),
            (59501, "new actor 595"),
            (7, "global overlay must not replace base")
        ])
        let base = try AoVBytesDecoder.encodeResource(basePlain, resourceName: path, wrapperMode: "aes")
        let overlay = try AoVBytesDecoder.encodeResource(overlayPlain, resourceName: path, wrapperMode: "aes")

        let (result, report) = try AoVMSESParser.mergeResource(
            baseResource: base,
            overlayResource: overlay,
            resourcePath: path,
            baseActorIDs: ["593"],
            overlayActorIDs: ["595"]
        )
        let decoded = try AoVBytesDecoder.decodeResource(result, resourceName: path)
        let table = try AoVMSESParser.parse(decoded.data)
        let rows = rowMap(table)
        XCTAssertEqual(rows[59300], "base actor 593")
        XCTAssertEqual(rows[59500], "overlay actor 595")
        XCTAssertEqual(rows[59501], "new actor 595")
        XCTAssertEqual(rows[7], "global base")
        XCTAssertEqual(report.adapter, "actor_owned_mses_rows")
        XCTAssertEqual(report.appendedKeys, [59501])
        XCTAssertEqual(report.overlayOwnedConflictKeys, [59500])
        XCTAssertEqual(report.retainedBaseConflictKeys, [59300, 7])
        XCTAssertTrue(report.verified)
    }

    func testActorOwnedMSESFailsWhenOverlayAddsUnownedGlobalKey() throws {
        let path = "Databin/Client/Actor/heroSkin.bytes"
        let basePlain = try makeMSES(fieldCount: 631, rows: [(59300, "base")])
        let overlayPlain = try makeMSES(fieldCount: 631, rows: [
            (59500, "overlay"),
            (42, "unowned new global row")
        ])
        let base = try AoVBytesDecoder.encodeResource(basePlain, resourceName: path, wrapperMode: "aes")
        let overlay = try AoVBytesDecoder.encodeResource(overlayPlain, resourceName: path, wrapperMode: "aes")
        XCTAssertThrowsError(try AoVMSESParser.mergeResource(
            baseResource: base,
            overlayResource: overlay,
            resourcePath: path,
            baseActorIDs: ["593"],
            overlayActorIDs: ["595"]
        ))
    }

    func testHuanhuaTableUsesExactAllowlistedSkinKey() throws {
        let path = "Databin/Client/Huanhua/ResBillboardCfg.bytes"
        let basePlain = try makeMSES(fieldCount: 32, rows: [(0x252, "base")])
        let overlayPlain = try makeMSES(fieldCount: 67, rows: [
            (0x252, "base"),
            (0x253, "verified billboard skin")
        ])
        let base = try AoVBytesDecoder.encodeResource(basePlain, resourceName: path, wrapperMode: "zstd")
        let overlay = try AoVBytesDecoder.encodeResource(overlayPlain, resourceName: path, wrapperMode: "zstd")
        let (result, report) = try AoVMSESParser.mergeResource(
            baseResource: base,
            overlayResource: overlay,
            resourcePath: path,
            baseActorIDs: [],
            overlayActorIDs: []
        )
        let decoded = try AoVBytesDecoder.decodeResource(result, resourceName: path)
        let rows = rowMap(try AoVMSESParser.parse(decoded.data))
        XCTAssertEqual(rows[0x253], "verified billboard skin")
        XCTAssertEqual(report.adapter, "verified_huanhua_key_0x253")
        XCTAssertTrue(report.verified)
    }

    func testRejectsDifferentVersionAndBadChecksum() throws {
        let original = try makeMSES(fieldCount: 4, rows: [(1, "one")])
        var damaged = original
        damaged[damaged.count - 1] ^= 0x01
        XCTAssertThrowsError(try AoVMSESParser.parse(damaged))
    }
}
