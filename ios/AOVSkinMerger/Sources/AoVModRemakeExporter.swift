import Foundation
import ZIPFoundation

struct AoVModRemakeResourcePayload: Sendable {
    /// Path as stored inside the RES ZIP / Resources.zip.
    let archivePath: String
    /// Runtime path used by PackerInfo / ResourceVerificationInfoSetXML.
    let ifsPath: String
    let data: Data
}

struct AoVModRemakeAssetBundleMergeResult: Sendable {
    let resourcePath: String
    let resourcePayload: AoVModRemakeResourcePayload
    let importedObjectCount: Int
    let importedPathIDs: [Int64]
    let roundTripVerified: Bool
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
    case catalogVersionMismatch(String, String, String)
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
        case .catalogVersionMismatch(let kind, let sourceVersion, let baseVersion):
            return "\(kind) catalog nguồn có version \(sourceVersion), khác catalog RES nền \(baseVersion)."
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

    /// Convenience entry point that extracts real runtime catalogs from the
    /// selected legacy MOD archives and enforces exact build-marker equality.
    /// Payload bytes and final metadata must still come from verified adapters.
    /// Produces one merged AssetBundle resource payload for the RES export
    /// pipeline. These bytes remain a candidate until final Packer/Verification
    /// metadata is independently verified and passed to exportVerifiedRES.
    static func mergeAssetBundleResource(
        resourcePath: String,
        runtimePath: String? = nil,
        baseAssetBundle: Data,
        serializedFileNodePath: String,
        legacyInputs: [AoVAssetBundleLegacyObjectImportInput],
        availableCurrentBundleNames: Set<String>,
        verifiedExternalTargetFiles: [String: Data] = [:]
    ) throws -> AoVModRemakeAssetBundleMergeResult {
        guard resourcePath.lowercased().hasSuffix(".assetbundle") else {
            throw AoVModRemakeExporterError.invalidRuntimePath(resourcePath)
        }
        let report = try AoVUnityFSAssetBundleMergeExecutor.mergeImportingLegacyObjects(
            in: baseAssetBundle,
            serializedFileNodePath: serializedFileNodePath,
            legacyInputs: legacyInputs,
            availableCurrentBundleNames: availableCurrentBundleNames,
            verifiedExternalTargetFiles: verifiedExternalTargetFiles
        )
        guard report.roundTripVerified else {
            throw AoVModRemakeExporterError.integrityFailure(
                "AssetBundle \(resourcePath) không qua round-trip."
            )
        }
        return AoVModRemakeAssetBundleMergeResult(
            resourcePath: resourcePath,
            resourcePayload: AoVModRemakeResourcePayload(
                archivePath: resourcePath,
                ifsPath: runtimePath ?? resourcePath,
                data: report.data
            ),
            importedObjectCount: report.importedObjectCount,
            importedPathIDs: report.importedPathIDs,
            roundTripVerified: true
        )
    }

    /// Convenience entry point that feeds object-merged AssetBundles into the
    /// verified RES exporter. Checksum values are still mandatory and are never
    /// derived or guessed by this method.
    static func exportVerifiedRESIncludingAssetBundleMerges(
        baseRESURL: URL,
        legacyMODURLs: [URL],
        resourcePayloads: [AoVModRemakeResourcePayload],
        assetBundleMergeResults: [AoVModRemakeAssetBundleMergeResult],
        verifiedFinalPackerEntries: [String: AoVRuntimeResourcePackInfo],
        verifiedFinalDatabinChecksumValues: [String: Int64],
        verifiedFinalZIPChecksumValues: [String: Int64]
    ) throws -> AoVModRemakeExportReport {
        let mergedPayloads = resourcePayloads + assetBundleMergeResults.map(\.resourcePayload)
        return try exportVerifiedRES(
            baseRESURL: baseRESURL,
            legacyMODURLs: legacyMODURLs,
            resourcePayloads: mergedPayloads,
            verifiedFinalPackerEntries: verifiedFinalPackerEntries,
            verifiedFinalDatabinChecksumValues: verifiedFinalDatabinChecksumValues,
            verifiedFinalZIPChecksumValues: verifiedFinalZIPChecksumValues
        )
    }

    static func exportVerifiedRES(
        baseRESURL: URL,
        legacyMODURLs: [URL],
        resourcePayloads: [AoVModRemakeResourcePayload],
        verifiedFinalPackerEntries: [String: AoVRuntimeResourcePackInfo],
        verifiedFinalDatabinChecksumValues: [String: Int64],
        verifiedFinalZIPChecksumValues: [String: Int64]
    ) throws -> AoVModRemakeExportReport {
        let catalogInputs = try AoVModRemakeAnalyzer.loadRuntimeCatalogInputs(
            baseRESURL: baseRESURL,
            legacyMODURLs: legacyMODURLs
        )
        return try exportVerifiedRES(
            baseRESURL: baseRESURL,
            resourcePayloads: resourcePayloads,
            legacyPackerCatalogs: catalogInputs.packer,
            verifiedFinalPackerEntries: verifiedFinalPackerEntries,
            legacyVerificationCatalogs: catalogInputs.verification,
            verifiedFinalDatabinChecksumValues: verifiedFinalDatabinChecksumValues,
            verifiedFinalZIPChecksumValues: verifiedFinalZIPChecksumValues
        )
    }

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
            let runtimeComponents = ifsPath.split(separator: "/", omittingEmptySubsequences: false)
            guard payload.ifsPath == payload.ifsPath.trimmingCharacters(in: .whitespacesAndNewlines),
                  !ifsPath.isEmpty, !ifsPath.hasPrefix("/"),
                  !runtimeComponents.contains(""), !runtimeComponents.contains(".."),
                  !runtimeComponents.contains("."),
                  !ifsPath.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
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

        // The caller's verified checksum maps are the authoritative
        // classification. Do not guess ZIP vs Databin from a path prefix:
        // resource naming is not a checksum-format contract.
        let payloadIFSPaths = Set(payloadByIFSPath.keys)
        let databinIFSPaths = Set(verifiedFinalDatabinChecksumValues.keys)
        let zipIFSPaths = Set(verifiedFinalZIPChecksumValues.keys)
        let checksumTypeCollisions = databinIFSPaths.intersection(zipIFSPaths)
        guard checksumTypeCollisions.isEmpty else {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "Một IFS path có cả Databin và ZIP checksum: \\(checksumTypeCollisions.sorted().prefix(4).joined(separator: ", "))."
            )
        }
        for path in payloadIFSPaths where
            !databinIFSPaths.contains(path) && !zipIFSPaths.contains(path) {
            throw AoVModRemakeExporterError.missingFinalChecksum(path)
        }
        for path in databinIFSPaths where !payloadIFSPaths.contains(path) {
            throw AoVModRemakeExporterError.unusedFinalChecksum(path)
        }
        for path in zipIFSPaths where !payloadIFSPaths.contains(path) {
            throw AoVModRemakeExporterError.unusedFinalChecksum(path)
        }
        for path in assetBundleIFSPaths where !zipIFSPaths.contains(path) {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "AssetBundle \\(path) phải dùng ZIP Verification checksum."
            )
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
        let packerTarget: AoVUnityFSRuntimeCatalogTarget
        let verificationTarget: AoVUnityFSRuntimeCatalogTarget
        do {
            packerTarget = try AoVUnityFSRuntimeCatalogMergeExecutor.locatePackerCatalog(in: packerData)
            verificationTarget = try AoVUnityFSRuntimeCatalogMergeExecutor.locateVerificationCatalog(
                in: verificationData
            )
        } catch {
            throw AoVModRemakeExporterError.catalogMergeFailed(error.localizedDescription)
        }
        guard case .packer(let currentPackerCatalog) = packerTarget.catalog,
              case .verification(let currentVerificationCatalog) = verificationTarget.catalog else {
            throw AoVModRemakeExporterError.catalogMergeFailed("Catalog RES không đúng loại.")
        }
        guard currentPackerCatalog.version == currentVerificationCatalog.version else {
            throw AoVModRemakeExporterError.catalogVersionMismatch(
                "Verification RES", currentVerificationCatalog.version, currentPackerCatalog.version
            )
        }
        for source in legacyPackerCatalogs where source.catalog.version != currentPackerCatalog.version {
            throw AoVModRemakeExporterError.catalogVersionMismatch(
                "Packer", source.catalog.version, currentPackerCatalog.version
            )
        }
        for source in legacyVerificationCatalogs where source.catalog.version != currentVerificationCatalog.version {
            throw AoVModRemakeExporterError.catalogVersionMismatch(
                "Verification", source.catalog.version, currentVerificationCatalog.version
            )
        }
        let packerOutput: AoVUnityFSRuntimePackerMergeExecutionReport
        let verificationOutput: AoVUnityFSRuntimeVerificationMergeExecutionReport
        do {
            packerOutput = try AoVUnityFSRuntimeCatalogMergeExecutor.mergePacker(
                in: packerData,
                serializedFileNodePath: packerTarget.nodePath,
                objectPathID: packerTarget.objectPathID,
                legacy: legacyPackerCatalogs,
                verifiedFinalEntries: verifiedFinalPackerEntries
            )
            verificationOutput = try AoVUnityFSRuntimeCatalogMergeExecutor.mergeVerification(
                in: verificationData,
                serializedFileNodePath: verificationTarget.nodePath,
                objectPathID: verificationTarget.objectPathID,
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
        if innerURL != nil, let resourceEntry = nestedResourceEntries.first {
            var outerAliases: [String: Data] = [:]
            for (path, data) in finalPayloads {
                let projectedPath = try outerLooseResourcePath(path)
                if let existing = outerAliases[projectedPath], existing != data {
                    throw AoVModRemakeExporterError.duplicateArchivePath(projectedPath)
                }
                outerAliases[projectedPath] = data
            }
            try writeOuterArchive(
                baseArchive: outerArchive,
                baseEntries: outerEntries,
                nestedResourceEntry: resourceEntry,
                innerArchiveURL: stagedInnerURL,
                replacements: finalPayloads,
                outerAliases: outerAliases,
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
        guard let expectedPackerCatalog = packerOutput.plan.candidateCatalog,
              let expectedVerificationCatalog = verificationOutput.plan.candidateCatalog else {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "Thiếu catalog candidate sau bước lập kế hoạch."
            )
        }
        try verifyFinalOutput(
            stagedOutputURL,
            hasNestedResourcesArchive: innerURL != nil,
            expectedPayloads: finalPayloads,
            expectedPackerCatalog: expectedPackerCatalog,
            expectedVerificationCatalog: expectedVerificationCatalog,
            tempRoot: tempRoot
        )
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
              !parts.contains(""), !parts.contains(".."), !parts.contains("."),
              !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw AoVModRemakeExporterError.invalidArchivePath(raw)
        }
        let versionPattern = #"^\d+(?:\.\d+){1,3}$"#
        if parts.count >= 2, parts[0] == "Resources" {
            if parts.count >= 3,
               parts[1].range(of: versionPattern, options: .regularExpression) != nil {
                return parts.dropFirst(2).joined(separator: "/")
            }
            return parts.dropFirst().joined(separator: "/")
        }
        return path
    }

    private static func outerLooseResourcePath(_ normalizedResourcePath: String) throws -> String {
        let normalized = try normalizeArchivePath(normalizedResourcePath)
        let components = normalized.split(separator: "/", omittingEmptySubsequences: false)
        guard let first = components.first else {
            throw AoVModRemakeExporterError.invalidArchivePath(normalizedResourcePath)
        }
        let versionPattern = #"^\d+(?:\.\d+){1,3}$"#
        if first.range(of: versionPattern, options: .regularExpression) != nil,
           components.count >= 2 {
            return components.dropFirst().joined(separator: "/")
        }
        return normalized
    }

    private static func normalizeRuntimePath(_ raw: String) -> String {
        raw.replacingOccurrences(of: "\\", with: "/")
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
        switch entry.compressionMethod {
        case .none:
            return .none
        default:
            // ZIPFoundation's writer supports the two storage methods used by
            // this exporter. Preserve stored entries and use DEFLATE for
            // compressed source entries instead of copying an unknown codec.
            return .deflate
        }
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
        outerAliases: [String: Data],
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
               let payload = outerAliases[path] ?? replacements[path] {
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

    private static func verifyFinalOutput(
        _ outputURL: URL,
        hasNestedResourcesArchive: Bool,
        expectedPayloads: [String: Data],
        expectedPackerCatalog: AoVRuntimeResourcePackerCatalog,
        expectedVerificationCatalog: AoVRuntimeResourceVerificationCatalog,
        tempRoot: URL
    ) throws {
        let outerArchive: Archive
        do {
            outerArchive = try Archive(url: outputURL, accessMode: .read)
        } catch {
            throw AoVModRemakeExporterError.integrityFailure(
                "Không mở lại được ZIP đầu ra: \\(error.localizedDescription)"
            )
        }
        let outerEntries = try validatedEntries(outerArchive, label: "ZIP đầu ra")
        let resourceArchive: Archive
        if hasNestedResourcesArchive {
            let matches = outerEntries.filter {
                ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("Resources.zip") == .orderedSame
            }
            guard matches.count == 1, let resourceEntry = matches.first else {
                throw AoVModRemakeExporterError.integrityFailure(
                    "ZIP đầu ra không giữ đúng một Resources.zip."
                )
            }
            let nestedURL = tempRoot.appendingPathComponent("RES-final-validation.zip")
            do {
                _ = try outerArchive.extract(resourceEntry, to: nestedURL, bufferSize: 64 * 1024)
                resourceArchive = try Archive(url: nestedURL, accessMode: .read)
            } catch {
                throw AoVModRemakeExporterError.integrityFailure(
                    "Resources.zip đầu ra không mở lại được: \\(error.localizedDescription)"
                )
            }
        } else {
            resourceArchive = outerArchive
        }

        let entries = try validatedEntries(resourceArchive, label: "Resources.zip đầu ra")
        for (expectedPath, expectedData) in expectedPayloads {
            let matches = entries.filter {
                (try? normalizeArchivePath($0.path)) == expectedPath
            }
            guard matches.count == 1, let entry = matches.first else {
                throw AoVModRemakeExporterError.integrityFailure(
                    "Không tìm thấy duy nhất tài nguyên đầu ra \\(expectedPath)."
                )
            }
            let actual = try readEntry(resourceArchive, entry, maxBytes: maximumSingleEntry)
            guard actual == expectedData else {
                throw AoVModRemakeExporterError.integrityFailure(
                    "Payload \\(expectedPath) thay đổi sau khi đóng gói."
                )
            }
        }

        let packerMatches = entries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare(packerFileName) == .orderedSame
        }
        let verificationMatches = entries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare(verificationFileName) == .orderedSame
        }
        guard packerMatches.count == 1, verificationMatches.count == 1,
              let packerEntry = packerMatches.first,
              let verificationEntry = verificationMatches.first else {
            throw AoVModRemakeExporterError.integrityFailure(
                "Packer/Verification catalog bị thiếu hoặc trùng sau khi đóng gói."
            )
        }

        let packerData = try readEntry(resourceArchive, packerEntry, maxBytes: maximumSingleEntry)
        let verificationData = try readEntry(resourceArchive, verificationEntry, maxBytes: maximumSingleEntry)
        let parsedPacker = try AoVUnityFSRuntimeCatalogMergeExecutor.locatePackerCatalog(in: packerData)
        let parsedVerification = try AoVUnityFSRuntimeCatalogMergeExecutor.locateVerificationCatalog(
            in: verificationData
        )
        guard case .packer(let actualPacker) = parsedPacker.catalog,
              actualPacker == expectedPackerCatalog else {
            throw AoVModRemakeExporterError.integrityFailure(
                "Packer runtime catalog không khớp candidate sau khi mở lại ZIP."
            )
        }
        guard case .verification(let actualVerification) = parsedVerification.catalog,
              actualVerification == expectedVerificationCatalog else {
            throw AoVModRemakeExporterError.integrityFailure(
                "Verification runtime catalog không khớp candidate sau khi mở lại ZIP."
            )
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
