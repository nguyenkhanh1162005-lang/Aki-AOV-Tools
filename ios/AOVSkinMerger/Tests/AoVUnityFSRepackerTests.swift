import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVUnityFSRepackerTests: XCTestCase {
    func testRepackRoundTripsMultipleNodesAndFlags() throws {
        let nodes = [
            AoVUnityFSRepackNode(path: "CAB-main", data: Data("main serialized content".utf8), flags: 4),
            AoVUnityFSRepackNode(path: "assets/skin.bytes", data: Data([0, 1, 2, 3, 4]), flags: 0),
            AoVUnityFSRepackNode(path: "assets/empty", data: Data(), flags: 2)
        ]

        let report = try AoVUnityFSRepacker.repack(
            nodes,
            formatVersion: 7,
            playerVersion: "5.0.0",
            engineVersion: "2021.3.2f1"
        )

        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(report.nodeCount, 3)
        XCTAssertEqual(report.blockCount, 1)

        let info = try AoVUnityFSParser.inspect(report.data)
        XCTAssertEqual(info.formatVersion, 7)
        XCTAssertEqual(info.engineVersion, "2021.3.2f1")
        XCTAssertFalse(info.blockInfoAtEnd)
        XCTAssertEqual(info.blockInfoCompression, "none")
        XCTAssertEqual(info.nodes.map(\.path), nodes.map(\.path))
        XCTAssertEqual(info.nodes.map(\.flags), nodes.map(\.flags))

        let extracted = try AoVUnityFSParser.extractNodes(report.data)
        let map = Dictionary(uniqueKeysWithValues: extracted.map { ($0.path, $0.data) })
        for node in nodes {
            XCTAssertEqual(map[node.path], node.data)
        }
    }

    func testRepackRoundTripsUnity2019AlignedFormatSixHeader() throws {
        let node = AoVUnityFSRepackNode(path: "CAB-catalog", data: Data(repeating: 0xA5, count: 33))
        let report = try AoVUnityFSRepacker.repack(
            [node],
            formatVersion: 6,
            engineVersion: "2019.4.20f1"
        )

        let info = try AoVUnityFSParser.inspect(report.data)
        XCTAssertEqual(info.nodes.first?.path, "CAB-catalog")
        XCTAssertEqual(info.totalUncompressedBytes, 33)
    }

    func testRejectsDuplicateNodePaths() {
        let nodes = [
            AoVUnityFSRepackNode(path: "CAB-main", data: Data([1])),
            AoVUnityFSRepackNode(path: "CAB-main", data: Data([2]))
        ]
        XCTAssertThrowsError(try AoVUnityFSRepacker.repack(nodes, engineVersion: "2020.3.34f1"))
    }

    func testRejectsPathTraversalAndAbsolutePaths() {
        XCTAssertThrowsError(try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: "../outside", data: Data([1]))],
            engineVersion: "2020.3.34f1"
        ))
        XCTAssertThrowsError(try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: "/absolute", data: Data([1]))],
            engineVersion: "2020.3.34f1"
        ))
    }

    func testRejectsEmptyArchivePayload() {
        XCTAssertThrowsError(try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: "empty", data: Data())],
            engineVersion: "2020.3.34f1"
        ))
    }
}
