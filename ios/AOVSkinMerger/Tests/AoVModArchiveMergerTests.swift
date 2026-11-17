import Foundation
import XCTest
import ZIPFoundation
@testable import AOVSkinMerger

final class AoVModArchiveMergerTests: XCTestCase {
    func testMergesAnUnknownNonConflictingSkinPayloadAndMeasuresRuntime() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Merger-Test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let baseURL = root.appendingPathComponent("base.zip")
        let overlayURL = root.appendingPathComponent("overlay.zip")
        try makeArchive(baseURL, entries: [
            ("version.txt", Data("1.64.1.12\n".utf8)),
            ("Data/base-resource.bin", Data("base-resource".utf8))
        ], root: root)
        try makeArchive(overlayURL, entries: [
            ("version.txt", Data("1.64.1.12\n".utf8)),
            ("Skins/Actor_999/NewVisual.meshbin", Data("opaque-skin-payload".utf8))
        ], root: root)

        let report = try AoVModArchiveMerger.merge(
            [overlayURL, baseURL],
            baseURL: baseURL
        )

        XCTAssertTrue(report.structurallyValidated)
        XCTAssertFalse(report.gameCompatibilityTested)
        XCTAssertEqual(report.sourceCount, 2)
        XCTAssertGreaterThan(report.inputBytes, 0)
        XCTAssertGreaterThan(report.outputBytes, 0)
        XCTAssertGreaterThanOrEqual(report.elapsedSeconds, 0)

        let output = try Archive(url: report.outputURL, accessMode: .read)
        let entry = try XCTUnwrap(output.first(where: {
            $0.type == .file && $0.path == "Skins/Actor_999/NewVisual.meshbin"
        }))
        var payload = Data()
        _ = try output.extract(entry, bufferSize: 64 * 1024) {
            payload.append($0)
        }
        XCTAssertEqual(payload, Data("opaque-skin-payload".utf8))
    }

    func testRejectsAnUnknownConflictingPayloadInsteadOfOverwritingIt() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Merger-Conflict-Test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let baseURL = root.appendingPathComponent("base.zip")
        let overlayURL = root.appendingPathComponent("overlay.zip")
        try makeArchive(baseURL, entries: [
            ("Data/collision.bin", Data("base-payload".utf8))
        ], root: root)
        try makeArchive(overlayURL, entries: [
            ("Data/collision.bin", Data("mod-payload!".utf8))
        ], root: root)

        XCTAssertThrowsError(
            try AoVModArchiveMerger.merge([baseURL, overlayURL], baseURL: baseURL)
        )
    }

    private func makeArchive(
        _ url: URL,
        entries: [(String, Data)],
        root: URL
    ) throws {
        let archive = try Archive(url: url, accessMode: .create)
        for (path, bytes) in entries {
            let source = root.appendingPathComponent(UUID().uuidString)
            try bytes.write(to: source, options: .atomic)
            try archive.addEntry(
                with: path,
                fileURL: source,
                compressionMethod: .deflate,
                bufferSize: 64 * 1024
            )
            try? FileManager.default.removeItem(at: source)
        }
    }
}
