import Foundation
import ZIPFoundation

struct AoVModRemakeResourcePayload: Sendable {
    /// Path as stored inside the RES ZIP / Resources.zip.
    let archivePath: String
    /// Runtime path used by PackerInfo / ResourceVerificationInfoSetXML.
    let ifsPath: String
    let data: Data
}

struct AoVModRemakeExportReport: Sendable {
    let outputURL: URL
    let baseRESName: String
    let replacedResources: Int
    let addedResources: Int
    let packerAddedPaths: [String]
    let packerUpdatedPaths: [String]
    let verificationAddedZIPPaths: [String]
    let verificationUpdatedZIPPaths: [String]
    let verificationAddedDatabinPaths: [String]
    let verificationUpdatedDatabinPaths: [String]
    let inputBytes: UInt64
    let outputBytes: UInt64
    let elapsedSeconds: TimeInterval
    let structurallyValidated: Bool
    let gameCompatibilityTested: Bool
}

enum AoVModRemakeExporterError: LocalizedError {
    case invalidBaseArchive(String)
    case invalidArchivePath(String)
    case duplicateArchivePath(String)
    case tooManyEntries(String)
    case archiveExpandedTooLarge(String)
    case resourceTooLarge(String)
    case noResourcePayloads
    case duplicateArchivePayload(String)
    case duplicateRuntimePath(String)
    case invalidRuntimePath(String)
    case catalogPathPayloadNotAllowed(String)
    case missingCatalog(String)
    case ambiguousCatalog(String)
    case missingFinalPackerEntry(String)
    case missingFinalChecksum(String)
    case unusedFinalPackerEntry(String)
    case unusedFinalChecksum(String)
    case catalogMergeFailed(String)
    case integrityFailure(String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseArchive(let name):
            return "RES nền \(name) không phải ZIP hợp lệ hoặc bị lỗi."
        case .invalidArchivePath(let path):
            return "RES có đường dẫn không an toàn: \(path)"
        case .duplicateArchivePath(let path):
            return "RES có đường dẫn sau chuẩn hóa bị trùng: \(path)"
        case .tooManyEntries(let name):
            return "\(name) có quá nhiều entry."
        case .archiveExpandedTooLarge(let name):
            return "\(name) vượt giới hạn dữ liệu giải nén."
        case .resourceTooLarge(let path):
            return "\(path) vượt giới hạn 512 MiB."
        case .noResourcePayloads:
            return "Chưa có payload tài nguyên cuối đã ghép để xuất vào RES."
        case .duplicateArchivePayload(let path):
            return "Có nhiều payload đầu ra cùng đường dẫn ZIP: \(path)"
        case .duplicateRuntimePath(let path):
            return "Có nhiều payload cùng runtime IFS path: \(path)"
        case .invalidRuntimePath(let path):
            return "Runtime IFS path không hợp lệ: \(path)"
        case .catalogPathPayloadNotAllowed(let path):
            return "\(path) là catalog runtime; exporter tự ghi Packer/Verification, không nhận payload thay thế trực tiếp."
        case .missingCatalog(let name):
            return "Không tìm thấy runtime catalog bắt buộc: \(name)"
        case .ambiguousCatalog(let name):
            return "RES có nhiều file cùng tên runtime catalog: \(name)"
        case .missingFinalPackerEntry(let path):
            return "Thiếu Packer entry đã xác minh cho AssetBundle cuối: \(path)"
        case .missingFinalChecksum(let path):
            return "Thiếu checksum cuối đã xác minh cho tài nguyên: \(path)"
        case .unusedFinalPackerEntry(let path):
            return "Packer entry cuối \(path) không có payload AssetBundle tương ứng trong batch."
        case .unusedFinalChecksum(let path):
            return "Checksum cuối \(path) không có payload tài nguyên tương ứng trong batch."
        case .catalogMergeFailed(let reason):
            return "Không thể cập nhật Packer/Verification runtime catalog: \(reason)"
        case .integrityFailure(let reason):
            return "RES sau khi xuất không vượt qua kiểm tra toàn vẹn: \(reason)"
        }
    }
}

/// Builds a new copy of the current RES from already-merged resource bytes.
/// This is the archive-level commit stage: it does not decide how skin payloads
/// are merged and it never invents Packer or Verification checksum values.
enum AoVModRemakeExporter {
    private static let maximumEntries = 20_000
    private static let maximumSingleEntry: UInt64 = 512 * 1024 * 1024
    private static let maximumExpandedBytes: UInt64 = 2 * 1024 * 1024 * 1024
    private static let maximumInputBytes: UInt64 = 1024 * 1024 * 1024
    private static let packerFileName = "resourcepackerinfosetall.assetbundle"
    private static let verificationFileName = "resourceverificationinfosetall.assetbundle"
    private static let outputFolderName = "AKI AOV SKIN Exports"

    static func exportVerifiedRES(
        baseRESURL: URL,
        resourcePayloads: [AoVModRemakeResourcePayload],
        legacyPackerCatalogs: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)],
        verifiedFinalPackerEntries: [String: AoVRuntimeResourcePackInfo],
        legacyVerificationCatalogs: [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)],
        verifiedFinalDatabinChecksumValues: [String: Int64],
        verifiedFinalZIPChecksumValues: [String: Int64]
    ) throws -> AoVModRemakeExportReport {
        let startedAt = ProcessInfo.processInfo.systemUptime
        guard !resourcePayloads.isEmpty else {
            throw AoVModRemakeExporterError.noResourcePayloads
        }

        let baseSize = (try? FileManager.default.attributesOfItem(atPath: baseRESURL.path)[.size] as? NSNumber)?
            .uint64Value ?? 0
        guard baseSize > 0, baseSize <= maximumInputBytes else {
            throw AoVModRemakeExporterError.invalidBaseArchive(baseRESURL.lastPathComponent)
        }

        let tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-AOV-RES-EXPORT-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempRoot) }

        let outerArchive: Archive
        do {
            outerArchive = try Archive(url: baseRESURL, accessMode: .read)
        } catch {
            throw AoVModRemakeExporterError.invalidBaseArchive(baseRESURL.lastPathComponent)
        }
        let outerEntries = try validatedEntries(outerArchive, label: baseRESURL.lastPathComponent)
        let nestedResourceEntries = outerEntries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("Resources.zip") == .orderedSame
        }
        guard nestedResourceEntries.count <= 1 else {
            throw AoVModRemakeExporterError.ambiguousCatalog("Resources.zip")
        }

        let innerURL: URL?
        let resourceArchive: Archive
        if let resourceEntry = nestedResourceEntries.first {
            let extractedURL = tempRoot.appendingPathComponent("RES-inner-\(UUID().uuidString).zip")
            do {
                _ = try outerArchive.extract(resourceEntry, to: extractedURL, bufferSize: 64 * 1024)
                resourceArchive = try Archive(url: extractedURL, accessMode: .read)
            } catch {
                throw AoVModRemakeExporterError.invalidBaseArchive("Resources.zip")
            }
            innerURL = extractedURL
        } else {
            resourceArchive = outerArchive
            innerURL = nil
        }

        let resourceEntries = try validatedEntries(resourceArchive, label: "Resources.zip / RES")
        var resourceEntryByPath: [String: Entry] = [:]
        for entry in resourceEntries {
            let normalized = try normalizeArchivePath(entry.path)
            resourceEntryByPath[normalized] = entry
        }

        var payloadByArchivePath: [String: AoVModRemakeResourcePayload] = [:]
        var payloadByIFSPath: [String: AoVModRemakeResourcePayload] = [:]
        var payloadArchivePathByNormalizedPath: [String: String] = [:]
        var totalPayloadBytes: UInt64 = 0
        for payload in resourcePayloads {
            let archivePath = try normalizeArchivePath(payload.archivePath)
            let ifsPath = normalizeRuntimePath(payload.ifsPath)
            guard !ifsPath.isEmpty, !ifsPath.hasPrefix("/"),
                  !ifsPath.split(separator: "/").contains(".."),
                  !ifsPath.split(separator: "/").contains(".") else {
                throw AoVModRemakeExporterError.invalidRuntimePath(payload.ifsPath)
            }
            guard UInt64(payload.data.count) <= maximumSingleEntry else {
                throw AoVModRemakeExporterError.resourceTooLarge(archivePath)
            }
            guard payloadByArchivePath[archivePath] == nil else {
                throw AoVModRemakeExporterError.duplicateArchivePayload(archivePath)
            }
            guard payloadByIFSPath[ifsPath] == nil else {
                throw AoVModRemakeExporterError.duplicateRuntimePath(ifsPath)
            }
            let lowerIFS = ifsPath.lowercased()
            if lowerIFS.hasSuffix(packerFileName) || lowerIFS.hasSuffix(verificationFileName) {
                throw AoVModRemakeExporterError.catalogPathPayloadNotAllowed(ifsPath)
            }
            let next = totalPayloadBytes.addingReportingOverflow(UInt64(payload.data.count))
            guard !next.overflow, next.partialValue <= maximumExpandedBytes else {
                throw AoVModRemakeExporterError.archiveExpandedTooLarge("các payload remake")
            }
            totalPayloadBytes = next.partialValue
            payloadByArchivePath[archivePath] = payload
            payloadByIFSPath[ifsPath] = payload
            payloadArchivePathByNormalizedPath[archivePath] = payload.archivePath
        }

        let assetBundleIFSPaths = Set(payloadByIFSPath.keys.filter {
            $0.lowercased().hasSuffix(".assetbundle")
        })
        for path in assetBundleIFSPaths where verifiedFinalPackerEntries[path] == nil {
            throw AoVModRemakeExporterError.missingFinalPackerEntry(path)
        }
        for path in verifiedFinalPackerEntries.keys where !assetBundleIFSPaths.contains(path) {
            throw AoVModRemakeExporterError.unusedFinalPackerEntry(path)
        }

        let databinIFSPaths = Set(payloadByIFSPath.keys.filter {
            $0.lowercased().hasPrefix("databin/")
        })
        let zipIFSPaths = Set(payloadByIFSPath.keys).subtracting(databinIFSPaths)
        for path in databinIFSPaths where verifiedFinalDatabinChecksumValues[path] == nil {
            throw AoVModRemakeExporterError.missingFinalChecksum(path)
        }
        for path in zipIFSPaths where verifiedFinalZIPChecksumValues[path] == nil {
            throw AoVModRemakeExporterError.missingFinalChecksum(path)
        }
        for path in verifiedFinalDatabinChecksumValues.keys where !databinIFSPaths.contains(path) {
            throw AoVModRemakeExporterError.unusedFinalChecksum(path)
        }
        for path in verifiedFinalZIPChecksumValues.keys where !zipIFSPaths.contains(path) {
            throw AoVModRemakeExporterError.unusedFinalChecksum(path)
        }

        let packerMatches = resourceEntries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare(packerFileName) == .orderedSame
        }
        let verificationMatches = resourceEntries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare(verificationFileName) == .orderedSame
        }
        guard packerMatches.count == 1 else {
            throw packerMatches.isEmpty
                ? AoVModRemakeExporterError.missingCatalog(packerFileName)
                : AoVModRemakeExporterError.ambiguousCatalog(packerFileName)
        }
        guard verificationMatches.count == 1 else {
            throw verificationMatches.isEmpty
                ? AoVModRemakeExporterError.missingCatalog(verificationFileName)
                : AoVModRemakeExporterError.ambiguousCatalog(verificationFileName)
        }

        let packerData = try readEntry(resourceArchive, packerMatches[0], maxBytes: maximumSingleEntry)
        let verificationData = try readEntry(resourceArchive, verificationMatches[0], maxBytes: maximumSingleEntry)
        let packerOutput: AoVUnityFSRuntimePackerMergeExecutionReport
        let verificationOutput: AoVUnityFSRuntimeVerificationMergeExecutionReport
        do {
            packerOutput = try AoVUnityFSRuntimeCatalogMergeExecutor.mergePacker(
                in: packerData,
                legacy: legacyPackerCatalogs,
                verifiedFinalEntries: verifiedFinalPackerEntries
            )
            verificationOutput = try AoVUnityFSRuntimeCatalogMergeExecutor.mergeVerification(
                in: verificationData,
                legacy: legacyVerificationCatalogs,
                verifiedFinalDatabinChecksumValues: verifiedFinalDatabinChecksumValues,
                verifiedFinalZIPChecksumValues: verifiedFinalZIPChecksumValues
            )
        } catch {
            throw AoVModRemakeExporterError.catalogMergeFailed(error.localizedDescription)
        }
        guard packerOutput.roundTripVerified, verificationOutput.roundTripVerified else {
            throw AoVModRemakeExporterError.catalogMergeFailed("Một hoặc cả hai runtime catalog không qua round-trip.")
        }

        var finalPayloads = payloadByArchivePath.mapValues(\.data)
        let packerArchivePath = try normalizeArchivePath(packerMatches[0].path)
        let verificationArchivePath = try normalizeArchivePath(verificationMatches[0].path)
        guard finalPayloads[packerArchivePath] == nil,
              finalPayloads[verificationArchivePath] == nil else {
            throw AoVModRemakeExporterError.catalogPathPayloadNotAllowed("Packer/Verification")
        }
        finalPayloads[packerArchivePath] = packerOutput.data
        finalPayloads[verificationArchivePath] = verificationOutput.data

        let stagedInnerURL = tempRoot.appendingPathComponent("RES-remade-inner.zip")
        if innerURL != nil {
            try writeResourcesArchive(
                baseArchive: resourceArchive,
                baseEntries: resourceEntries,
                replacements: finalPayloads,
                additionArchivePaths: payloadArchivePathByNormalizedPath,
                outputURL: stagedInnerURL,
                tempRoot: tempRoot
            )
        }

        let stagedOutputURL = tempRoot.appendingPathComponent("RES-remade.zip")
        if let innerURL, let resourceEntry = nestedResourceEntries.first {
            try writeOuterArchive(
                baseArchive: outerArchive,
                baseEntries: outerEntries,
                nestedResourceEntry: resourceEntry,
                innerArchiveURL: stagedInnerURL,
                replacements: finalPayloads,
                outputURL: stagedOutputURL,
                tempRoot: tempRoot
            )
        } else {
            try writeResourcesArchive(
                baseArchive: resourceArchive,
                baseEntries: resourceEntries,
                replacements: finalPayloads,
                additionArchivePaths: payloadArchivePathByNormalizedPath,
                outputURL: stagedOutputURL,
                tempRoot: tempRoot
            )
        }

        try verifyArchive(stagedOutputURL, label: "RES ZIP")
        let exportDirectory = try FileManager.default.url(
            for: .documentDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent(outputFolderName, isDirectory: true)
        try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
        let finalURL = exportDirectory.appendingPathComponent(
            "AKI_AOV_REMAKE_\(UUID().uuidString.prefix(8)).zip"
        )
        try FileManager.default.moveItem(at: stagedOutputURL, to: finalURL)

        let outputSize = (try? FileManager.default.attributesOfItem(atPath: finalURL.path)[.size] as? NSNumber)?
            .uint64Value ?? 0
        return AoVModRemakeExportReport(
            outputURL: finalURL,
            baseRESName: baseRESURL.lastPathComponent,
            replacedResources: payloadByArchivePath.keys.filter { resourceEntryByPath[$0] != nil }.count + 2,
            addedResources: payloadByArchivePath.keys.filter { resourceEntryByPath[$0] == nil }.count,
            packerAddedPaths: packerOutput.plan.addedPaths,
            packerUpdatedPaths: packerOutput.plan.updatedPaths,
            verificationAddedZIPPaths: verificationOutput.plan.addedZIPPaths,
            verificationUpdatedZIPPaths: verificationOutput.plan.updatedZIPPaths,
            verificationAddedDatabinPaths: verificationOutput.plan.addedDatabinPaths,
            verificationUpdatedDatabinPaths: verificationOutput.plan.updatedDatabinPaths,
            inputBytes: baseSize,
            outputBytes: outputSize,
            elapsedSeconds: max(0, ProcessInfo.processInfo.systemUptime - startedAt),
            structurallyValidated: true,
            gameCompatibilityTested: false
        )
    }

    private static func validatedEntries(_ archive: Archive, label: String) throws -> [Entry] {
        var result: [Entry] = []
        var seen = Set<String>()
        var expanded: UInt64 = 0
        for entry in archive {
            if entry.type == .directory { continue }
            guard entry.type == .file else {
                throw AoVModRemakeExporterError.invalidArchivePath(entry.path)
            }
            guard result.count < maximumEntries,
                  entry.uncompressedSize <= maximumSingleEntry else {
                throw AoVModRemakeExporterError.tooManyEntries(label)
            }
            let normalized = try normalizeArchivePath(entry.path)
            guard seen.insert(normalized).inserted else {
                throw AoVModRemakeExporterError.duplicateArchivePath(normalized)
            }
            let next = expanded.addingReportingOverflow(entry.uncompressedSize)
            guard !next.overflow, next.partialValue <= maximumExpandedBytes else {
                throw AoVModRemakeExporterError.archiveExpandedTooLarge(label)
            }
            expanded = next.partialValue
            result.append(entry)
        }
        return result
    }

    private static func normalizeArchivePath(_ raw: String) throws -> String {
        let path = raw.replacingOccurrences(of: "\\", with: "/")
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        guard !path.isEmpty, !path.hasPrefix("/"), !path.hasPrefix("~"),
              !parts.contains(".."), !parts.contains("."),
              !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw AoVModRemakeExporterError.invalidArchivePath(raw)
        }
        if parts.first == "Resources", parts.count >= 3 {
            return parts.dropFirst(2).joined(separator: "/")
        }
        if path.lowercased().hasSuffix(".assetbundle"),
           !path.lowercased().hasPrefix("assetbundle/") {
            return "assetbundle/" + path
        }
        return path
    }

    private static func normalizeRuntimePath(_ raw: String) -> String {
        raw.replacingOccurrences(of: "\\", with: "/")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func readEntry(_ archive: Archive, _ entry: Entry, maxBytes: UInt64) throws -> Data {
        guard entry.uncompressedSize <= maxBytes else {
            throw AoVModRemakeExporterError.resourceTooLarge(entry.path)
        }
        var data = Data()
        _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { data.append($0) })
        return data
    }

    private static func storageMethod(for entry: Entry) -> CompressionMethod {
        entry.isCompressed ? .deflate : .none
    }

    private static func storageMethod(forNewPath path: String) -> CompressionMethod {
        let ext = URL(fileURLWithPath: path).pathExtension.lowercased()
        let opaque: Set<String> = [
            "assetbundle", "bytes", "bnk", "wem", "zip", "png", "jpg",
            "jpeg", "webp", "mp3", "ogg", "mp4", "zst", "zstd", "lz4", "lzma"
        ]
        return opaque.contains(ext) ? .none : .deflate
    }

    private static func copyEntry(
        _ entry: Entry,
        from source: Archive,
        to destination: Archive,
        path: String
    ) throws {
        let scratch = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: scratch) }
        _ = try source.extract(entry, to: scratch, bufferSize: 64 * 1024)
        try destination.addEntry(
            with: path,
            fileURL: scratch,
            compressionMethod: storageMethod(for: entry),
            bufferSize: 64 * 1024
        )
    }

    private static func addData(
        _ data: Data,
        to destination: Archive,
        path: String,
        tempRoot: URL,
        compressionMethod: CompressionMethod
    ) throws {
        let scratch = tempRoot.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: scratch) }
        try data.write(to: scratch, options: .atomic)
        try destination.addEntry(
            with: path,
            fileURL: scratch,
            compressionMethod: compressionMethod,
            bufferSize: 64 * 1024
        )
    }

    private static func writeResourcesArchive(
        baseArchive: Archive,
        baseEntries: [Entry],
        replacements: [String: Data],
        additionArchivePaths: [String: String],
        outputURL: URL,
        tempRoot: URL
    ) throws {
        let output = try Archive(url: outputURL, accessMode: .create)
        var emitted = Set<String>()
        for entry in baseEntries {
            let path = try normalizeArchivePath(entry.path)
            if let payload = replacements[path] {
                try addData(
                    payload,
                    to: output,
                    path: entry.path,
                    tempRoot: tempRoot,
                    compressionMethod: storageMethod(for: entry)
                )
            } else {
                try copyEntry(entry, from: baseArchive, to: output, path: entry.path)
            }
            emitted.insert(path)
        }
        for (path, payload) in replacements where !emitted.contains(path) {
            let archivePath = additionArchivePaths[path] ?? path
            try addData(
                payload,
                to: output,
                path: archivePath,
                tempRoot: tempRoot,
                compressionMethod: storageMethod(forNewPath: archivePath)
            )
            emitted.insert(path)
        }
        try verifyArchive(outputURL, label: "Resources.zip")
    }

    private static func writeOuterArchive(
        baseArchive: Archive,
        baseEntries: [Entry],
        nestedResourceEntry: Entry,
        innerArchiveURL: URL,
        replacements: [String: Data],
        outputURL: URL,
        tempRoot: URL
    ) throws {
        let output = try Archive(url: outputURL, accessMode: .create)
        for entry in baseEntries {
            if entry.path == nestedResourceEntry.path {
                try output.addEntry(
                    with: entry.path,
                    fileURL: innerArchiveURL,
                    compressionMethod: storageMethod(for: entry),
                    bufferSize: 64 * 1024
                )
                continue
            }
            let path = try normalizeArchivePath(entry.path)
            if entry.path.replacingOccurrences(of: "\\", with: "/").hasPrefix("Resources/"),
               let payload = replacements[path] {
                try addData(
                    payload,
                    to: output,
                    path: entry.path,
                    tempRoot: tempRoot,
                    compressionMethod: storageMethod(for: entry)
                )
            } else {
                try copyEntry(entry, from: baseArchive, to: output, path: entry.path)
            }
        }
    }

    private static func verifyArchive(_ url: URL, label: String) throws {
        let archive: Archive
        do {
            archive = try Archive(url: url, accessMode: .read)
            for entry in archive where entry.type == .file {
                _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { _ in })
            }
        } catch {
            throw AoVModRemakeExporterError.integrityFailure("\(label): \(error.localizedDescription)")
        }
    }
}
