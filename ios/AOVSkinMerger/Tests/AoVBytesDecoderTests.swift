import XCTest
import Foundation
import ZIPFoundation
@testable import AOVSkinMerger

final class AoVBytesDecoderTests: XCTestCase {
    func testDecryptsKnownRealAoVAESFixture() throws {
        let hex = "224a6700cd0200008791646bd02a34c49fa5c166879dec16dc4de77e247574d27d9e6319c432b8faad1861c5a9f0d7bf09fba0bff12227640b76ac4990bbaac0a1b3b63b951728e06fe8a65d3ddc365932672f8b06f14ecd4fadc930e53653edede161b3506d26c85c27fbc232422703660a7c48df48261e0d1ceee130d3d2af26e3672621c96e197ba4c873f613c7c9fe2bcfab9e272beeac45097685002abf229754f9effb6122b8c94e40c2fc4b4e45870ec9827bea068ff97fc593ce6510b2af71d966a29ab0059632d5f1f72d7123d387576268ec859a39ae9d045af2588ae4489e5c15b68ccf4e0dde023ea30c2bdc457870286f8f1f327e7ccf0dd1a7728211f2128281f8817a4cd6c78a18b3e51dadf966567770ad0d029d625316ca695c832c47752d89fcb09e7798e1c336084288f85deefdc01b58798c042b44e8359ac66697f057746f4f1ece5eb91521a5892fb26a7fa199e2878cf624682c1bf39fe53751e0a2d3af3a8d889d59dff0c709ab0a34bd6ae9e52abccf093f60b55ac25b917d1ddac542268206e977c08be18a0353f7956a26c9f74d914e5538d5e00ecdd86de4dc12e4c3039d47543a8f005dae07880c24eef8cde91229bbde31b41f12202e08627310581dfc38c80793720f0e5b109d7b2573351f30fb95b15fff0efa1c6420b578d12e781c8277615a957cb52afa16454d0816d34a56bc04d6e00788c262a4a9acf0767b2363d21dbf39c4cdd157c479c22c4207bc89a904744bcba0fc5c4b9b210b56be3186fbab58044f0932a1fabc214461ad26f9b99e684ffde2518707936061e49712689779e0f95da15969b75ffbf27e6c02cadfd717ea1bac1dbfd6be59823c08b64993df57aef0b3240fe5b9a154a567e9559c048b67e0cc1a0b71e26294aa9552ee846c22efeff0ac613f9c5c607d05a8327d3d7468eacceb510d6dcf998601c2d358175440d9959a87abb3d065b0dbf442afba593a1cd4e722f2b0bb47d64f1e7823ff568d364fbc904efdfc"
        let bytes = stride(from: 0, to: hex.count, by: 2).map { offset -> UInt8 in
            let start = hex.index(hex.startIndex, offsetBy: offset)
            let end = hex.index(start, offsetBy: 2)
            return UInt8(hex[start..<end], radix: 16)!
        }
        let decoded = try AoVBytesDecoder.decodeAESOnly(
            Data(bytes),
            resourceName: "ResSkinSeniorLabelCfg.bytes"
        )
        XCTAssertEqual(decoded.count, 717)
    }

    func testAESZstdIsExplicitlyRejected() {
        var data = Data([0x22, 0x4A, 0x67, 0xEF, 0x01, 0, 0, 0])
        data.append(Data(repeating: 0, count: 16))
        XCTAssertThrowsError(
            try AoVBytesDecoder.decodeAESOnly(data, resourceName: "Independent_lua.bytes")
        ) { error in
            XCTAssertEqual(error as? AoVBytesDecodeError, .aesZstdUnsupported)
        }
    }

    func testSamePathPayloadsAreClassifiedBySHA256() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AOVSkinMergerTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let sameA = try makeArchive(root: root, name: "same-a.zip", path: "Resources/asset.bytes", payload: Data("same".utf8))
        let sameB = try makeArchive(root: root, name: "same-b.zip", path: "Resources/asset.bytes", payload: Data("same".utf8))
        let sameReport = try ArchiveScanner.analyze([sameA, sameB])
        XCTAssertEqual(sameReport.identicalDuplicatePaths, 1)
        XCTAssertEqual(sameReport.conflictingPaths, 0)
        XCTAssertEqual(sameReport.crcValidatedFiles, 2)

        let conflictB = try makeArchive(root: root, name: "different.zip", path: "Resources/asset.bytes", payload: Data("changed".utf8))
        let conflictReport = try ArchiveScanner.analyze([sameA, conflictB])
        XCTAssertEqual(conflictReport.identicalDuplicatePaths, 0)
        XCTAssertEqual(conflictReport.conflictingPaths, 1)
    }

    private func makeArchive(root: URL, name: String, path: String, payload: Data) throws -> URL {
        let source = root.appendingPathComponent(UUID().uuidString + ".bin")
        try payload.write(to: source)
        let archiveURL = root.appendingPathComponent(name)
        let archive = try Archive(url: archiveURL, accessMode: .create)
        try archive.addEntry(with: path, fileURL: source, compressionMethod: .deflate)
        return archiveURL
    }
}
