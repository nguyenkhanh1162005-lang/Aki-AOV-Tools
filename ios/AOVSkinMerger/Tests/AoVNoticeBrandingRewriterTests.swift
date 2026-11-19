import Foundation
import XCTest
import ZIPFoundation
@testable import AOVSkinMerger

final class AoVNoticeBrandingRewriterTests: XCTestCase {
    func testRewritesExactBrandingInsideNestedResourcesZIPAndAoVWrapper() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Notice-Nested-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let originalText = Data(
            "KIANA MOD AOV\nMod được tạo bởi youtuber KIANA MOD AOV\nWeb Mod\nYoutube\n".utf8
        )
        let encoded = try AoVBytesDecoder.encodeResource(
            originalText,
            resourceName: "Branding.lua.bytes",
            wrapperMode: "aes"
        )
        let innerURL = root.appendingPathComponent("Resources.zip")
        try makeArchive(innerURL, entries: [
            ("Scripts/Branding.lua.bytes", encoded),
            ("binary/opaque.assetbundle", Data("UnityFS\0opaque".utf8))
        ], root: root)
        let outerURL = root.appendingPathComponent("source.zip")
        try makeArchive(outerURL, entries: [
            ("Aki-AOV-21Skin-Merged/Resources.zip", try Data(contentsOf: innerURL))
        ], root: root)

        let sourceBytes = try Data(contentsOf: outerURL)
        let outputURL = root.appendingPathComponent("aki-branding.zip")
        let report = try AoVNoticeBrandingRewriter.rewriteArchive(
            inputURL: outerURL,
            outputURL: outputURL
        )
        XCTAssertTrue(report.structurallyValidated)
        XCTAssertFalse(report.gameTested)
        XCTAssertEqual(report.replacementCounts["KIANA MOD AOV"], 1)
        XCTAssertEqual(report.replacementCounts["Mod được tạo bởi youtuber KIANA MOD AOV"], 1)
        XCTAssertEqual(report.replacementCounts["Web Mod"], 1)
        XCTAssertEqual(report.replacementCounts["Youtube"], 1)
        XCTAssertEqual(report.rewrittenAoVWrappers, 1)
        XCTAssertEqual(try Data(contentsOf: outerURL), sourceBytes)

        let output = try Archive(url: outputURL, accessMode: .read)
        let nestedEntry = try XCTUnwrap(output.first(where: { $0.type == .file }))
        let nestedOutputURL = root.appendingPathComponent("nested-output.zip")
        _ = try output.extract(nestedEntry, to: nestedOutputURL, bufferSize: 64 * 1024)
        let nested = try Archive(url: nestedOutputURL, accessMode: .read)
        let brandingEntry = try XCTUnwrap(nested.first(where: {
            $0.type == .file && $0.path == "Scripts/Branding.lua.bytes"
        }))
        let opaqueEntry = try XCTUnwrap(nested.first(where: {
            $0.type == .file && $0.path == "binary/opaque.assetbundle"
        }))
        let encodedOutputURL = root.appendingPathComponent("branding-output.bytes")
        _ = try nested.extract(brandingEntry, to: encodedOutputURL, bufferSize: 64 * 1024)
        let decoded = try AoVBytesDecoder.decodeResource(
            Data(contentsOf: encodedOutputURL),
            resourceName: "Aki-AOV-21Skin-Merged/Resources.zip!/Scripts/Branding.lua.bytes"
        )
        let rewrittenText = try XCTUnwrap(String(data: decoded.data, encoding: .utf8))
        XCTAssertTrue(rewrittenText.contains("AKI MOD AOV"))
        XCTAssertTrue(rewrittenText.contains("Mod được gộp bởi AKI MOD AOV"))
        XCTAssertFalse(rewrittenText.contains("KIANA MOD AOV"))
        XCTAssertFalse(rewrittenText.contains("Web Mod"))
        XCTAssertFalse(rewrittenText.contains("Youtube"))

        let opaqueURL = root.appendingPathComponent("opaque.assetbundle")
        _ = try nested.extract(opaqueEntry, to: opaqueURL, bufferSize: 64 * 1024)
        XCTAssertEqual(try Data(contentsOf: opaqueURL), Data("UnityFS\0opaque".utf8))
    }

    func testRewritesPlainTextAndPreservesOpaqueBinary() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Notice-Plain-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let original = Data("title = \"KIANA MOD AOV\"\nsite = \"Web Mod\"\n".utf8)
        let input = root.appendingPathComponent("source.zip")
        try makeArchive(input, entries: [
            ("Resources/startup.lua", original),
            ("Resources/unknown.bytes", Data([0, 1, 2, 255]))
        ], root: root)
        let sourceBytes = try Data(contentsOf: input)
        let output = root.appendingPathComponent("output.zip")

        let report = try AoVNoticeBrandingRewriter.rewriteArchive(inputURL: input, outputURL: output)
        XCTAssertGreaterThan(report.replacementTotal, 0)
        XCTAssertEqual(try Data(contentsOf: input), sourceBytes)

        let archive = try Archive(url: output, accessMode: .read)
        let textEntry = try XCTUnwrap(archive.first(where: { $0.path == "Resources/startup.lua" }))
        let opaqueEntry = try XCTUnwrap(archive.first(where: { $0.path == "Resources/unknown.bytes" }))
        let textURL = root.appendingPathComponent("startup.lua")
        let opaqueURL = root.appendingPathComponent("unknown.bytes")
        _ = try archive.extract(textEntry, to: textURL, bufferSize: 64 * 1024)
        _ = try archive.extract(opaqueEntry, to: opaqueURL, bufferSize: 64 * 1024)
        XCTAssertTrue(String(data: try Data(contentsOf: textURL), encoding: .utf8)?.contains("AKI MOD AOV") == true)
        XCTAssertEqual(try Data(contentsOf: opaqueURL), Data([0, 1, 2, 255]))
    }

    func testProtectsKillNotificationBillboardAndCommercialNoticeResources() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Notice-Protected-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let protected: [(String, Data)] = [
            ("Databin/Client/Huanhua/ResKillBillboardCfg.bytes", Data("KIANA MOD AOV".utf8)),
            ("Databin/Client/Huanhua/ResBillboardCfg.bytes", Data("KIANA MOD AOV".utf8)),
            ("assetbundle/commercializenotify/battle/commercializenotify_red_595.assetbundle", Data("KIANA MOD AOV".utf8))
        ]
        let input = root.appendingPathComponent("source.zip")
        try makeArchive(input, entries: [
            ("Resources/LoginNotice.lua", Data("title = \"KIANA MOD AOV\"".utf8))
        ] + protected, root: root)
        let output = root.appendingPathComponent("output.zip")

        let report = try AoVNoticeBrandingRewriter.rewriteArchive(inputURL: input, outputURL: output)
        XCTAssertEqual(report.protectedNoticeResources.count, 3)

        let archive = try Archive(url: output, accessMode: .read)
        for (path, payload) in protected {
            let entry = try XCTUnwrap(archive.first(where: { $0.path == path }))
            let extracted = root.appendingPathComponent(UUID().uuidString)
            _ = try archive.extract(entry, to: extracted, bufferSize: 64 * 1024)
            XCTAssertEqual(try Data(contentsOf: extracted), payload)
        }
    }

    func testNoMatchingTextDoesNotCreateOutput() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Notice-NoMatch-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let input = root.appendingPathComponent("source.zip")
        try makeArchive(input, entries: [
            ("Resources/startup.lua", Data("title = \"Hello\"".utf8))
        ], root: root)
        let output = root.appendingPathComponent("output.zip")
        XCTAssertThrowsError(try AoVNoticeBrandingRewriter.rewriteArchive(inputURL: input, outputURL: output)) {
            XCTAssertEqual($0 as? AoVNoticeBrandingRewriteError, .noMatchingText)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: output.path))
    }

    private func makeArchive(
        _ url: URL,
        entries: [(String, Data)],
        root: URL
    ) throws {
        let archive = try Archive(url: url, accessMode: .create)
        for (path, data) in entries {
            let source = root.appendingPathComponent(UUID().uuidString)
            try data.write(to: source)
            defer { try? FileManager.default.removeItem(at: source) }
            try archive.addEntry(
                with: path,
                fileURL: source,
                compressionMethod: .none,
                bufferSize: 64 * 1024
            )
        }
    }
}
