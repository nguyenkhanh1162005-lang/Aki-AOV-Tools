import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVSourceFileImporterTests: XCTestCase {
    private func temporaryDirectory() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AOVSourceImportTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    func testCopiesSelectedZipIntoAppFolderAndPreservesOriginalName() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }

        let sourceFolder = root.appendingPathComponent("provider", isDirectory: true)
        let appFolder = root.appendingPathComponent("Documents/SourceArchives", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceFolder, withIntermediateDirectories: true)
        let source = sourceFolder.appendingPathComponent("Pack 10.ZIP")
        let payload = Data([0x50, 0x4B, 0x03, 0x04, 1, 2, 3, 4])
        try payload.write(to: source)

        let report = try AoVSourceFileImporter.copy([source], into: appFolder)

        XCTAssertEqual(report.importedURLs.count, 1)
        XCTAssertTrue(report.errors.isEmpty)
        XCTAssertEqual(report.importedURLs.first?.lastPathComponent.hasSuffix("-Pack 10.ZIP"), true)
        XCTAssertEqual(try Data(contentsOf: report.importedURLs[0]), payload)
        XCTAssertEqual(report.totalBytes, UInt64(payload.count))
    }

    func testRejectsMoreThanTenFilesBeforeCopying() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }

        let sourceFolder = root.appendingPathComponent("provider", isDirectory: true)
        let appFolder = root.appendingPathComponent("Documents/SourceArchives", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceFolder, withIntermediateDirectories: true)
        let urls = try (0..<11).map { index -> URL in
            let url = sourceFolder.appendingPathComponent("Pack\(index).zip")
            try Data([0x50, 0x4B, 0x03, 0x04]).write(to: url)
            return url
        }

        XCTAssertThrowsError(try AoVSourceFileImporter.copy(urls, into: appFolder))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: appFolder.path).count, 0)
    }

    func testReportsNonZipSelectionWithoutCopyingIt() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }

        let source = root.appendingPathComponent("not-a-zip.txt")
        let appFolder = root.appendingPathComponent("Documents/SourceArchives", isDirectory: true)
        try Data("not a zip".utf8).write(to: source)

        let report = try AoVSourceFileImporter.copy([source], into: appFolder)

        XCTAssertTrue(report.importedURLs.isEmpty)
        XCTAssertEqual(report.errors.count, 1)
        XCTAssertTrue(report.errors[0].contains("chỉ nhận file"))
    }

    func testEnforcesCombinedSizeLimitWithoutCopyingOversizedSelection() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }

        let source = root.appendingPathComponent("small.zip")
        let appFolder = root.appendingPathComponent("Documents/SourceArchives", isDirectory: true)
        try Data([0x50, 0x4B, 0x03, 0x04]).write(to: source)

        let report = try AoVSourceFileImporter.copy(
            [source],
            into: appFolder,
            existingBytes: AoVSourceFileImporter.maximumTotalBytes - 2
        )

        XCTAssertTrue(report.importedURLs.isEmpty)
        XCTAssertEqual(report.errors.count, 1)
        XCTAssertTrue(report.errors[0].contains("500 MiB"))
    }
}
