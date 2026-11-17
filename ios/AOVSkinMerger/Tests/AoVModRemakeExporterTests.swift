import Foundation
import XCTest
import ZIPFoundation
@testable import AOVSkinMerger

final class AoVModRemakeExporterTests: XCTestCase {
    func testExportsVerifiedAssetBundleAndDatabinIntoCleanRESAndUpdatesBothCatalogs() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-RES-Exporter-Test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let baseURL = root.appendingPathComponent("clean-res.zip")
        let currentPacker = makePackerCatalog()
        let currentVerification = makeVerificationCatalog()
        let packerBundle = try makeUnityFS(with: .packer(currentPacker))
        let verificationBundle = try makeUnityFS(with: .verification(currentVerification))

        try makeArchive(baseURL, entries: [
            ("version.txt", Data("1.64.1.12\n".utf8)),
            ("1.64.1/assetbundle/resourcepackerinfosetall.assetbundle", packerBundle),
            ("1.64.1/assetbundle/resourceverificationinfosetall.assetbundle", verificationBundle),
            ("1.64.1/assetbundle/current.assetbundle", Data("existing-bundle".utf8))
        ], root: root)

        let finalBundlePath = "assetbundle/creator731.assetbundle"
        let finalDatabinPath = "Databin/Client/Actor/creator731.bytes"
        let finalPackerEntry = AoVRuntimeResourcePackInfo(
            pathInIFS: finalBundlePath,
            pkgType: 0,
            crcXor: 987654,
            tag: "Hero",
            flags: 512,
            resourceInfos: [
                AoVRuntimeResourceItem(hashCode: 123456, extensionName: "assetbundle")
            ]
        )
        let finalBundleBytes = Data("creator-731-final-assetbundle".utf8)
        let finalDatabinBytes = Data("creator-731-final-databin".utf8)
        let payloads = [
            AoVModRemakeResourcePayload(
                archivePath: "1.64.1/assetbundle/creator731.assetbundle",
                ifsPath: finalBundlePath,
                data: finalBundleBytes
            ),
            AoVModRemakeResourcePayload(
                archivePath: "1.64.1/Databin/Client/Actor/creator731.bytes",
                ifsPath: finalDatabinPath,
                data: finalDatabinBytes
            )
        ]

        let report = try AoVModRemakeExporter.exportVerifiedRES(
            baseRESURL: baseURL,
            resourcePayloads: payloads,
            legacyPackerCatalogs: [],
            verifiedFinalPackerEntries: [finalBundlePath: finalPackerEntry],
            legacyVerificationCatalogs: [],
            verifiedFinalDatabinChecksumValues: [finalDatabinPath: 222333],
            verifiedFinalZIPChecksumValues: [finalBundlePath: 444555]
        )
        defer { try? FileManager.default.removeItem(at: report.outputURL) }

        XCTAssertTrue(report.structurallyValidated)
        XCTAssertFalse(report.gameCompatibilityTested)
        XCTAssertEqual(report.baseRESName, "clean-res.zip")
        XCTAssertEqual(report.replacedResources, 2)
        XCTAssertEqual(report.addedResources, 2)
        XCTAssertEqual(report.packerAddedPaths, [finalBundlePath])
        XCTAssertEqual(report.verificationAddedZIPPaths, [finalBundlePath])
        XCTAssertEqual(report.verificationAddedDatabinPaths, [finalDatabinPath])
        XCTAssertGreaterThan(report.outputBytes, 0)
        XCTAssertGreaterThanOrEqual(report.elapsedSeconds, 0)

        let output = try Archive(url: report.outputURL, accessMode: .read)
        let finalBundleEntry = try XCTUnwrap(output.first(where: {
            $0.type == .file && $0.path == "1.64.1/assetbundle/creator731.assetbundle"
        }))
        let finalDatabinEntry = try XCTUnwrap(output.first(where: {
            $0.type == .file && $0.path == "1.64.1/Databin/Client/Actor/creator731.bytes"
        }))
        var extractedBundle = Data()
        _ = try output.extract(finalBundleEntry, bufferSize: 64 * 1024) {
            extractedBundle.append($0)
        }
        var extractedDatabin = Data()
        _ = try output.extract(finalDatabinEntry, bufferSize: 64 * 1024) {
            extractedDatabin.append($0)
        }
        XCTAssertEqual(extractedBundle, finalBundleBytes)
        XCTAssertEqual(extractedDatabin, finalDatabinBytes)

        let outputPacker = try readEntry(
            output,
            path: "1.64.1/assetbundle/resourcepackerinfosetall.assetbundle"
        )
        let outputVerification = try readEntry(
            output,
            path: "1.64.1/assetbundle/resourceverificationinfosetall.assetbundle"
        )
        let packerInfo = try AoVUnityFSParser.inspect(outputPacker)
        let verificationInfo = try AoVUnityFSParser.inspect(outputVerification)
        XCTAssertTrue(packerInfo.usesAoVEncryption)
        XCTAssertTrue(verificationInfo.usesAoVEncryption)

        let packerNodes = try AoVUnityFSParser.extractNodes(outputPacker)
        let verificationNodes = try AoVUnityFSParser.extractNodes(outputVerification)
        let packerNode = try XCTUnwrap(packerNodes.first)
        let verificationNode = try XCTUnwrap(verificationNodes.first)
        let parsedPacker = try AoVSerializedFileParser.inspect(packerNode.data)
        let parsedVerification = try AoVSerializedFileParser.inspect(verificationNode.data)

        XCTAssertTrue(parsedPacker.resourcePackerCatalog?.allResourcePackInfo.contains {
            $0.pathInIFS == finalBundlePath && $0 == finalPackerEntry
        } == true)
        XCTAssertEqual(
            parsedVerification.resourceVerificationCatalog?.allZipVerificationInfo.first(where: {
                $0.pathInIFS == finalBundlePath
            })?.crcXor,
            444555
        )
        XCTAssertEqual(
            parsedVerification.resourceVerificationCatalog?.allDatabinVerificationInfo.first(where: {
                $0.pathInIFS == finalDatabinPath
            })?.crcXor,
            222333
        )
    }

    func testExportsNestedResourcesZIPWhilePreservingOuterWrapperFiles() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Nested-RES-Exporter-Test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root, includingPropertiesForKeys: nil) }

        let innerURL = root.appendingPathComponent("Resources.zip")
        let outerURL = root.appendingPathComponent("clean-res-wrapper.zip")
        try makeArchive(innerURL, entries: [
            ("1.64.1/assetbundle/resourcepackerinfosetall.assetbundle",
             try makeUnityFS(with: .packer(makePackerCatalog()))),
            ("1.64.1/assetbundle/resourceverificationinfosetall.assetbundle",
             try makeUnityFS(with: .verification(makeVerificationCatalog()))),
            ("1.64.1/assetbundle/current.assetbundle", Data("current".utf8))
        ], root: root)

        let outer = try Archive(url: outerURL, accessMode: .create)
        try outer.addEntry(
            with: "Resources.zip",
            fileURL: innerURL,
            compressionMethod: .none,
            bufferSize: 64 * 1024
        )
        let skinListURL = root.appendingPathComponent("DanhSachSkin.txt")
        let skinList = Data("Danh sách skin nền\n".utf8)
        try skinList.write(to: skinListURL)
        try outer.addEntry(
            with: "DanhSachSkin.txt",
            fileURL: skinListURL,
            compressionMethod: .deflate,
            bufferSize: 64 * 1024
        )

        let runtimePath = "assetbundle/nested-creator.assetbundle"
        let finalEntry = AoVRuntimeResourcePackInfo(
            pathInIFS: runtimePath,
            pkgType: 0,
            crcXor: 10101,
            tag: "Hero",
            flags: 512,
            resourceInfos: [
                AoVRuntimeResourceItem(hashCode: 20202, extensionName: "assetbundle")
            ]
        )
        let payload = AoVModRemakeResourcePayload(
            archivePath: "1.64.1/assetbundle/nested-creator.assetbundle",
            ifsPath: runtimePath,
            data: Data("nested-final-bundle".utf8)
        )

        let report = try AoVModRemakeExporter.exportVerifiedRES(
            baseRESURL: outerURL,
            resourcePayloads: [payload],
            legacyPackerCatalogs: [],
            verifiedFinalPackerEntries: [runtimePath: finalEntry],
            legacyVerificationCatalogs: [],
            verifiedFinalDatabinChecksumValues: [:],
            verifiedFinalZIPChecksumValues: [runtimePath: 30303]
        )
        defer { try? FileManager.default.removeItem(at: report.outputURL) }

        let outputOuter = try Archive(url: report.outputURL, accessMode: .read)
        let outputSkinList = try readEntry(outputOuter, path: "DanhSachSkin.txt")
        XCTAssertEqual(outputSkinList, skinList)
        let outputResourcesEntry = try XCTUnwrap(outputOuter.first(where: {
            $0.type == .file && $0.path == "Resources.zip"
        }))
        let extractedInner = root.appendingPathComponent("output-Resources.zip")
        _ = try outputOuter.extract(outputResourcesEntry, to: extractedInner, bufferSize: 64 * 1024)
        let outputInner = try Archive(url: extractedInner, accessMode: .read)
        XCTAssertEqual(
            try readEntry(outputInner, path: "1.64.1/assetbundle/nested-creator.assetbundle"),
            Data("nested-final-bundle".utf8)
        )
        XCTAssertNotNil(outputInner.first(where: {
            $0.type == .file && $0.path == "1.64.1/assetbundle/resourcepackerinfosetall.assetbundle"
        }))
        XCTAssertNotNil(outputInner.first(where: {
            $0.type == .file && $0.path == "1.64.1/assetbundle/resourceverificationinfosetall.assetbundle"
        }))
    }

    func testLoadsLegacyRuntimeCatalogsFromVersionMatchedArchives() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Catalog-Loader-Test-\\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let baseURL = root.appendingPathComponent("clean-res.zip")
        let legacyURL = root.appendingPathComponent("legacy-mod.zip")
        let version = Data("1.64.1.12\\n".utf8)
        let packer = try makeUnityFS(with: .packer(makePackerCatalog()))
        let verification = try makeUnityFS(with: .verification(makeVerificationCatalog()))
        let entries = [
            ("version.txt", version),
            ("1.64.1/assetbundle/resourcepackerinfosetall.assetbundle", packer),
            ("1.64.1/assetbundle/resourceverificationinfosetall.assetbundle", verification)
        ]
        try makeArchive(baseURL, entries: entries, root: root)
        try makeArchive(legacyURL, entries: entries, root: root)

        let inputs = try AoVModRemakeAnalyzer.loadRuntimeCatalogInputs(
            baseRESURL: baseURL,
            legacyMODURLs: [legacyURL]
        )
        XCTAssertEqual(inputs.packer.count, 1)
        XCTAssertEqual(inputs.verification.count, 1)
        XCTAssertEqual(inputs.packer[0].source, "legacy-mod.zip")
        XCTAssertEqual(inputs.packer[0].catalog.name, "ResourcePackerInfoSetXML")
        XCTAssertEqual(inputs.verification[0].catalog.name, "ResourceVerificationInfoSetXML")
    }

    func testLegacyCatalogLoaderRejectsDifferentBuildVersion() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-Catalog-Version-Guard-Test-\\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let baseURL = root.appendingPathComponent("clean-res.zip")
        let legacyURL = root.appendingPathComponent("old-build-mod.zip")
        try makeArchive(baseURL, entries: [
            ("version.txt", Data("1.64.1.12\\n".utf8))
        ], root: root)
        try makeArchive(legacyURL, entries: [
            ("version.txt", Data("1.64.1.11\\n".utf8))
        ], root: root)

        XCTAssertThrowsError(
            try AoVModRemakeAnalyzer.loadRuntimeCatalogInputs(
                baseRESURL: baseURL,
                legacyMODURLs: [legacyURL]
            )
        ) { error in
            guard let remakeError = error as? AoVModRemakeError,
                  case .versionMismatch("old-build-mod.zip", "1.64.1.11", "1.64.1.12") = remakeError else {
                return XCTFail("Expected a strict build-version mismatch, got \\(error)")
            }
        }
    }

    func testExporterRejectsCatalogVersionMismatchBeforeWriting() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-RES-Catalog-Version-Test-\\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let baseURL = root.appendingPathComponent("clean-res.zip")
        try makeArchive(baseURL, entries: [
            ("1.64.1/assetbundle/resourcepackerinfosetall.assetbundle",
             try makeUnityFS(with: .packer(makePackerCatalog()))),
            ("1.64.1/assetbundle/resourceverificationinfosetall.assetbundle",
             try makeUnityFS(with: .verification(makeVerificationCatalog())))
        ], root: root)

        XCTAssertThrowsError(
            try AoVModRemakeExporter.exportVerifiedRES(
                baseRESURL: baseURL,
                resourcePayloads: [
                    AoVModRemakeResourcePayload(
                        archivePath: "1.64.1/Ages/Legacy.pkg.bytes",
                        ifsPath: "Ages/Legacy.pkg.bytes",
                        data: Data("legacy-package".utf8)
                    )
                ],
                legacyPackerCatalogs: [
                    ("older-build.zip", makePackerCatalog(version: "1.64.1.11"))
                ],
                verifiedFinalPackerEntries: [:],
                legacyVerificationCatalogs: [],
                verifiedFinalDatabinChecksumValues: [:],
                verifiedFinalZIPChecksumValues: ["Ages/Legacy.pkg.bytes": 777]
            )
        ) { error in
            guard let exporterError = error as? AoVModRemakeExporterError,
                  case .catalogVersionMismatch("Packer", "1.64.1.11", "1.64.1.12") = exporterError else {
                return XCTFail("Expected catalog version mismatch, got \\(error)")
            }
        }
    }

    func testExporterRefusesNewDatabinWithoutVerifiedChecksum() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-RES-Exporter-Guard-Test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let baseURL = root.appendingPathComponent("clean-res.zip")
        try makeArchive(baseURL, entries: [
            ("version.txt", Data("1.64.1.12\n".utf8))
        ], root: root)

        XCTAssertThrowsError(
            try AoVModRemakeExporter.exportVerifiedRES(
                baseRESURL: baseURL,
                resourcePayloads: [
                    AoVModRemakeResourcePayload(
                        archivePath: "1.64.1/Databin/Client/Actor/unverified.bytes",
                        ifsPath: "Databin/Client/Actor/unverified.bytes",
                        data: Data("unverified".utf8)
                    )
                ],
                legacyPackerCatalogs: [],
                verifiedFinalPackerEntries: [:],
                legacyVerificationCatalogs: [],
                verifiedFinalDatabinChecksumValues: [:],
                verifiedFinalZIPChecksumValues: [:]
            )
        ) { error in
            guard let exporterError = error as? AoVModRemakeExporterError,
                  case .missingFinalChecksum("Databin/Client/Actor/unverified.bytes") = exporterError else {
                return XCTFail("Expected missing final Databin checksum, got \(error)")
            }
        }
    }

    private func makePackerCatalog(version: String = "1.64.1.12") -> AoVRuntimeResourcePackerCatalog {
        AoVRuntimeResourcePackerCatalog(
            name: "ResourcePackerInfoSetXML",
            version: version,
            svnRevision: "1793129",
            setName: "ResourcePackerInfoSetXML",
            allResourcePackInfo: [
                AoVRuntimeResourcePackInfo(
                    pathInIFS: "assetbundle/current.assetbundle",
                    pkgType: 0,
                    crcXor: 100,
                    tag: "Hero",
                    flags: 512,
                    resourceInfos: [
                        AoVRuntimeResourceItem(hashCode: 111, extensionName: "assetbundle")
                    ]
                )
            ],
            allParentIndexToChild: [
                AoVPackInfoMapItem(parentIdx: 0, childIdx: 0)
            ],
            trailingData: Data([0xAA, 0xBB])
        )
    }

    private func makeVerificationCatalog() -> AoVRuntimeResourceVerificationCatalog {
        AoVRuntimeResourceVerificationCatalog(
            name: "ResourceVerificationInfoSetXML",
            version: "1.64.1.12",
            svnRevision: "1793129",
            allZipVerificationInfo: [],
            allDatabinVerificationInfo: [],
            trailingData: Data([0xCC, 0xDD])
        )
    }

    private func makeUnityFS(with catalog: AoVRuntimeResourceCatalog) throws -> Data {
        let serialized = makeSerializedFile(with: catalog)
        return try AoVUnityFSRepacker.repack(
            [AoVUnityFSRepackNode(path: "CAB-runtime-catalog", data: serialized, flags: 7)],
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2022.3.5f1",
            blockInfoLayout: .arenaOfValor,
            aovEncryption: true
        ).data
    }

    private func makeSerializedFile(with catalog: AoVRuntimeResourceCatalog) -> Data {
        let name: String
        switch catalog {
        case .packer(let value): name = value.name
        case .verification(let value): name = value.name
        }
        let objectPayload = makeMonoBehaviour(name: name, catalog: catalog)
        var metadata = Data()
        appendCString("2022.3.5f1", to: &metadata)
        appendInt32(9, to: &metadata)
        metadata.append(0)
        appendInt32(1, to: &metadata)
        appendInt32(114, to: &metadata)
        metadata.append(0)
        appendInt16(-1, to: &metadata)
        metadata.append(Data(repeating: 0, count: 32))
        appendInt32(0, to: &metadata)
        appendInt32(1, to: &metadata)
        align4(&metadata)
        appendInt64(4321, to: &metadata)
        appendInt64(0, to: &metadata)
        appendUInt32(UInt32(objectPayload.count), to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        appendInt32(0, to: &metadata)
        appendCString("", to: &metadata)

        let dataOffset = ((48 + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + objectPayload.count
        var file = Data()
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt32(fileSize), to: &file)
        appendBE(UInt32(22), to: &file)
        appendBE(UInt32(dataOffset), to: &file)
        file.append(0)
        file.append(contentsOf: [0, 0, 0])
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt64(fileSize), to: &file)
        appendBE(UInt64(dataOffset), to: &file)
        appendBE(UInt64(0), to: &file)
        file.append(metadata)
        while file.count < dataOffset { file.append(0) }
        file.append(objectPayload)
        return file
    }

    private func makeMonoBehaviour(name: String, catalog: AoVRuntimeResourceCatalog) -> Data {
        var data = Data(repeating: 0, count: 12)
        data.append(1)
        align4(&data)
        data.append(Data(repeating: 0, count: 12))
        appendAlignedString(name, to: &data)
        switch catalog {
        case .packer(let value):
            appendAlignedString(value.version, to: &data)
            appendAlignedString(value.svnRevision, to: &data)
            appendAlignedString(value.setName, to: &data)
            appendInt32(Int32(value.allResourcePackInfo.count), to: &data)
            for item in value.allResourcePackInfo {
                appendAlignedString(item.pathInIFS, to: &data)
                appendInt32(item.pkgType, to: &data)
                appendInt64(item.crcXor, to: &data)
                appendAlignedString(item.tag, to: &data)
                appendUInt16(item.flags, to: &data)
                align4(&data)
                appendInt32(Int32(item.resourceInfos.count), to: &data)
                for resource in item.resourceInfos {
                    appendInt64(resource.hashCode, to: &data)
                    appendAlignedString(resource.extensionName, to: &data)
                }
            }
            appendInt32(Int32(value.allParentIndexToChild.count), to: &data)
            for link in value.allParentIndexToChild {
                appendInt32(link.parentIdx, to: &data)
                appendInt32(link.childIdx, to: &data)
            }
            data.append(value.trailingData)
        case .verification(let value):
            appendAlignedString(value.version, to: &data)
            appendAlignedString(value.svnRevision, to: &data)
            appendInt32(Int32(value.allZipVerificationInfo.count), to: &data)
            for item in value.allZipVerificationInfo {
                appendAlignedString(item.pathInIFS, to: &data)
                appendInt64(item.crcXor, to: &data)
            }
            appendInt32(Int32(value.allDatabinVerificationInfo.count), to: &data)
            for item in value.allDatabinVerificationInfo {
                appendAlignedString(item.pathInIFS, to: &data)
                appendInt64(item.crcXor, to: &data)
            }
            data.append(value.trailingData)
        }
        return data
    }

    private func makeArchive(_ url: URL, entries: [(String, Data)], root: URL) throws {
        let archive = try Archive(url: url, accessMode: .create)
        for (path, data) in entries {
            let scratch = root.appendingPathComponent(UUID().uuidString)
            try data.write(to: scratch, options: .atomic)
            try archive.addEntry(
                with: path,
                fileURL: scratch,
                compressionMethod: .none,
                bufferSize: 64 * 1024
            )
            try? FileManager.default.removeItem(at: scratch)
        }
    }

    private func readEntry(_ archive: Archive, path: String) throws -> Data {
        let entry = try XCTUnwrap(archive.first(where: { $0.type == .file && $0.path == path }))
        var data = Data()
        _ = try archive.extract(entry, bufferSize: 64 * 1024) { data.append($0) }
        return data
    }

    private func appendCString(_ value: String, to data: inout Data) {
        data.append(Data((value + "\0").utf8))
    }

    private func appendAlignedString(_ value: String, to data: inout Data) {
        appendInt32(Int32(value.utf8.count), to: &data)
        data.append(Data(value.utf8))
        align4(&data)
    }

    private func appendInt16(_ value: Int16, to data: inout Data) {
        let bits = UInt16(bitPattern: value)
        data.append(UInt8(truncatingIfNeeded: bits))
        data.append(UInt8(truncatingIfNeeded: bits >> 8))
    }

    private func appendUInt16(_ value: UInt16, to data: inout Data) {
        data.append(UInt8(truncatingIfNeeded: value))
        data.append(UInt8(truncatingIfNeeded: value >> 8))
    }

    private func appendInt32(_ value: Int32, to data: inout Data) {
        appendUInt32(UInt32(bitPattern: value), to: &data)
    }

    private func appendUInt32(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 0, through: 24, by: 8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendInt64(_ value: Int64, to data: inout Data) {
        let bits = UInt64(bitPattern: value)
        for shift in stride(from: 0, through: 56, by: 8) {
            data.append(UInt8(truncatingIfNeeded: bits >> shift))
        }
    }

    private func appendBE(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 24, through: 0, by: -8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendBE(_ value: UInt64, to data: inout Data) {
        for shift in stride(from: 56, through: 0, by: -8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func align4(_ data: inout Data) {
        while data.count % 4 != 0 { data.append(0) }
    }
}
