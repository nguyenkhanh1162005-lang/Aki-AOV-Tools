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

        let info = try AoVUnityFSParser.inspect(report.data)
        XCTAssertEqual(info.nodes.map(\.path), originalNodes.map(\.path))
        let nodes = try AoVUnityFSParser.extractNodes(report.data)
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

        let info = try AoVUnityFSParser.inspect(report.data)
        let extracted = try AoVUnityFSParser.extractNodes(report.data)

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

        let info = try AoVUnityFSParser.inspect(report.data)
        let extracted = try AoVUnityFSParser.extractNodes(report.data)

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

        let info = try AoVUnityFSParser.inspect(report.data)
        let nodes = try AoVUnityFSParser.extractNodes(report.data)
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
                return XCTFail("Expected expanded-size guard, got \\(error)")
            }
        }
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
}
