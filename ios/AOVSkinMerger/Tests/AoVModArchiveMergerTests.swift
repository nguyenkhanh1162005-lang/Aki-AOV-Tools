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
            ("Skins/Actor_999/NewVisual.bytes", Data("opaque-skin-payload".utf8))
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
        print("AKI_MERGE_BENCHMARK elapsed=\(report.elapsedSeconds)s input=\(report.inputBytes)B output=\(report.outputBytes)B")

        let output = try Archive(url: report.outputURL, accessMode: .read)
        let entry = try XCTUnwrap(output.first(where: {
            $0.type == .file && $0.path == "Skins/Actor_999/NewVisual.bytes"
        }))
        var payload = Data()
        _ = try output.extract(entry, bufferSize: 64 * 1024) {
            payload.append($0)
        }
        XCTAssertEqual(payload, Data("opaque-skin-payload".utf8))
        XCTAssertEqual(UInt64(entry.compressedSize), entry.uncompressedSize,
                       "Opaque .bytes payload should be stored without wasteful DEFLATE compression.")
    }

    func testThirtyTwoMiBOpaquePayloadMergeStaysUnderTenSeconds() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Merger-Benchmark-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let baseURL = root.appendingPathComponent("base.zip")
        let overlayURL = root.appendingPathComponent("overlay.zip")
        try makeArchive(baseURL, entries: [
            ("version.txt", Data("1.64.1.12\n".utf8))
        ], root: root, compressionMethod: .none)

        var state: UInt32 = 0xA0C0_2026
        var bytes = [UInt8]()
        bytes.reserveCapacity(32 * 1024 * 1024)
        for _ in 0..<(32 * 1024 * 1024) {
            state = state &* 1_664_525 &+ 1_013_904_223
            bytes.append(UInt8(truncatingIfNeeded: state >> 24))
        }
        try makeArchive(overlayURL, entries: [
            ("Skins/Actor_999/large-opaque.bytes", Data(bytes))
        ], root: root, compressionMethod: .none)

        let report = try AoVModArchiveMerger.merge(
            [baseURL, overlayURL],
            baseURL: baseURL
        )

        print("AKI_MERGE_BENCHMARK_32M elapsed=\(report.elapsedSeconds)s input=\(report.inputBytes)B output=\(report.outputBytes)B")
        XCTAssertLessThan(report.elapsedSeconds, 10.0, "32 MiB opaque-resource benchmark exceeded the 10-second target.")
        XCTAssertGreaterThanOrEqual(report.outputBytes, 32 * 1024 * 1024)

        let output = try Archive(url: report.outputURL, accessMode: .read)
        let entry = try XCTUnwrap(output.first(where: {
            $0.type == .file && $0.path == "Skins/Actor_999/large-opaque.bytes"
        }))
        XCTAssertEqual(entry.uncompressedSize, 32 * 1024 * 1024)
        XCTAssertEqual(UInt64(entry.compressedSize), entry.uncompressedSize)
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
        root: URL,
        compressionMethod: CompressionMethod = .deflate
    ) throws {
        let archive = try Archive(url: url, accessMode: .create)
        for (path, bytes) in entries {
            let source = root.appendingPathComponent(UUID().uuidString)
            try bytes.write(to: source, options: .atomic)
            try archive.addEntry(
                with: path,
                fileURL: source,
                compressionMethod: compressionMethod,
                bufferSize: 64 * 1024
            )
            try? FileManager.default.removeItem(at: source)
        }
    }
}
