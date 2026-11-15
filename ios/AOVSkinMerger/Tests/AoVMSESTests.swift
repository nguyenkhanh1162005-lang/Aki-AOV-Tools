import XCTest
import Foundation
import ZIPFoundation
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
            (7, "global base")
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
        XCTAssertEqual(report.retainedBaseConflictKeys, [59300])
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

    func testModArchivePipelineMergesActorOwnedMSESResource() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-MSES-ModMerge-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let resourcePath = "Databin/Client/Actor/heroSkin.bytes"
        let basePlain = try makeMSES(fieldCount: 631, rows: [(50000, "base actor")])
        let overlayPlain = try makeMSES(fieldCount: 631, rows: [(59300, "overlay actor")])
        let baseResource = try AoVBytesDecoder.encodeResource(basePlain, resourceName: resourcePath, wrapperMode: "aes")
        let overlayResource = try AoVBytesDecoder.encodeResource(overlayPlain, resourceName: resourcePath, wrapperMode: "aes")

        let commonPath = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
        let packerPath = "assetbundle/resourcepackerinfosetall.assetbundle"
        let verificationPath = "assetbundle/resourceverificationinfosetall.assetbundle"

        let baseResourcesURL = try makeArchive(root: root, name: "base-resources.zip", files: [
            "version.txt": Data("1.64.1.12".utf8),
            commonPath: Data("base-common-actions".utf8),
            packerPath: Data("base-packer-placeholder".utf8),
            verificationPath: Data("base-verification-placeholder".utf8),
            resourcePath: baseResource,
            "Actor_500_Actions.pkg.bytes": Data("actor-500-actions".utf8)
        ])
        let overlayResourcesURL = try makeArchive(root: root, name: "overlay-resources.zip", files: [
            "version.txt": Data("1.64.1.12".utf8),
            resourcePath: overlayResource,
            "Actor_593_Actions.pkg.bytes": Data("actor-593-actions".utf8)
        ])
        let baseOuter = try makeArchive(root: root, name: "base.zip", files: [
            "Resources.zip": Data(contentsOf: baseResourcesURL),
            "DanhSachSkin.txt": Data("Base Actor".utf8)
        ])
        let overlayOuter = try makeArchive(root: root, name: "overlay.zip", files: [
            "Resources.zip": Data(contentsOf: overlayResourcesURL),
            "DanhSachSkin.txt": Data("Overlay Actor".utf8)
        ])

        let report = try AoVModArchiveMerger.merge([baseOuter, overlayOuter])
        XCTAssertEqual(report.mergedMSESTables, [resourcePath])
        XCTAssertTrue(report.structurallyValidated)

        let outer = try Archive(url: report.outputURL, accessMode: .read)
        guard let resourcesEntry = outer.first(where: {
            ($0.path as NSString).lastPathComponent == "Resources.zip"
        }) else { return XCTFail("Missing merged Resources.zip") }
        let mergedResourcesURL = root.appendingPathComponent("merged-resources.zip")
        _ = try outer.extract(resourcesEntry, to: mergedResourcesURL)
        let resources = try Archive(url: mergedResourcesURL, accessMode: .read)
        guard let mergedEntry = resources.first(where: { $0.path == resourcePath }) else {
            return XCTFail("Missing merged heroSkin MSES resource")
        }
        var wrapped = Data()
        _ = try resources.extract(mergedEntry, consumer: { wrapped.append($0) })
        let decoded = try AoVBytesDecoder.decodeResource(wrapped, resourceName: resourcePath)
        let table = try AoVMSESParser.parse(decoded.data)
        XCTAssertEqual(table.rows.count, 2)
        XCTAssertEqual(rowMap(table)[50000], "base actor")
        XCTAssertEqual(rowMap(table)[59300], "overlay actor")
    }

    private func makeArchive(root: URL, name: String, files: [String: Data]) throws -> URL {
        let archiveURL = root.appendingPathComponent(name)
        let archive = try Archive(url: archiveURL, accessMode: .create)
        for (path, data) in files {
            let fileURL = root.appendingPathComponent(UUID().uuidString + ".tmp")
            try data.write(to: fileURL)
            defer { try? FileManager.default.removeItem(at: fileURL) }
            try archive.addEntry(with: path, fileURL: fileURL, compressionMethod: .deflate)
        }
        return archiveURL
    }


}
