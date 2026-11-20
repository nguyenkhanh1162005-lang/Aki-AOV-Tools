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

        // Match the app flow: acquire the file-provider lease before dispatching the copy.
        let securityScopeLease = AoVSecurityScopedAccess(urls: [source])
        let report = try AoVSourceFileImporter.copy(
            [source],
            into: appFolder,
            securityScopeLease: securityScopeLease
        )
        withExtendedLifetime(securityScopeLease) {}

        XCTAssertEqual(report.importedURLs.count, 1)
        XCTAssertTrue(report.errors.isEmpty)
        XCTAssertEqual(report.importedURLs.first?.lastPathComponent.hasSuffix("-Pack 10.ZIP"), true)
        XCTAssertEqual(try Data(contentsOf: report.importedURLs[0]), payload)
        XCTAssertEqual(report.totalBytes, UInt64(payload.count))
    }

    func testCopies3105ArchiveIntoAppFolderAndPreservesOriginalName() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }

        let source = root.appendingPathComponent("legacy-skin.3105")
        let appFolder = root.appendingPathComponent("Documents/SourceArchives", isDirectory: true)
        let payload = Data([0x50, 0x4B, 0x03, 0x04, 9, 8, 7, 6])
        try payload.write(to: source)

        let report = try AoVSourceFileImporter.copy([source], into: appFolder)

        XCTAssertEqual(report.importedURLs.count, 1)
        XCTAssertTrue(report.errors.isEmpty)
        XCTAssertTrue(report.importedURLs[0].lastPathComponent.hasSuffix("-legacy-skin.3105"))
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
        XCTAssertFalse(FileManager.default.fileExists(atPath: appFolder.path))
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
        XCTAssertTrue(report.errors[0].contains("chỉ nhận file .zip hoặc .3105"))
    }

    func testDoesNotRejectZipWhenExistingImportedBytesAreLarge() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }

        let source = root.appendingPathComponent("large-total.zip")
        let appFolder = root.appendingPathComponent("Documents/SourceArchives", isDirectory: true)
        try Data([0x50, 0x4B, 0x03, 0x04]).write(to: source)

        let report = try AoVSourceFileImporter.copy(
            [source],
            into: appFolder,
            existingBytes: UInt64.max - 1
        )

        XCTAssertEqual(report.importedURLs.count, 1)
        XCTAssertTrue(report.errors.isEmpty)
        XCTAssertEqual(report.totalBytes, UInt64.max)
        XCTAssertTrue(FileManager.default.fileExists(atPath: report.importedURLs[0].path))
    }
}
