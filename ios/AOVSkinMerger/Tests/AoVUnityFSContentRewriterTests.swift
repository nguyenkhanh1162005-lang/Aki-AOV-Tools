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
