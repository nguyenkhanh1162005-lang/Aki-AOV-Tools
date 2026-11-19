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
    case noSafeStandaloneResources(String)
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
        case .noSafeStandaloneResources(let reason):
            return "Không có tài nguyên độc lập đủ điều kiện xuất an toàn: \(reason)"
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

    /// Automatic first-stage RMK export: copies only legacy-only standalone
    /// files whose payload is preserved byte-for-byte and whose source Packer
    /// and Verification records are unique. AssetBundles, Wwise banks, and any
    /// Packer-linked resource remain outside this path until their semantic
    /// merge/dependency adapters have been verified.
    static func exportLegacyOnlyStandaloneResources(
        baseRESURL: URL,
        legacyMODURLs: [URL]
    ) throws -> AoVModRemakeExportReport {
        guard !legacyMODURLs.isEmpty else {
            throw AoVModRemakeExporterError.noSafeStandaloneResources("Chưa chọn MOD cũ.")
        }
        let catalogInputs = try AoVModRemakeAnalyzer.loadRuntimeCatalogInputs(
            baseRESURL: baseRESURL,
            legacyMODURLs: legacyMODURLs
        )
        let sourceNames = legacyMODURLs.map { $0.lastPathComponent }
        guard Set(sourceNames).count == sourceNames.count else {
            throw AoVModRemakeExporterError.noSafeStandaloneResources(
                "Có nhiều file MOD trùng tên; hãy đổi tên để phân biệt nguồn metadata."
            )
        }

        let tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-AOV-STANDALONE-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempRoot) }

        func resolveResources(_ inputURL: URL) throws -> URL {
            let outer: Archive
            do {
                outer = try Archive(url: inputURL, accessMode: .read)
            } catch {
                throw AoVModRemakeExporterError.invalidBaseArchive(inputURL.lastPathComponent)
            }
            let nestedEntries = outer.filter {
                $0.type == .file &&
                ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("Resources.zip") == .orderedSame
            }
            guard nestedEntries.count <= 1 else {
                throw AoVModRemakeExporterError.ambiguousCatalog("Resources.zip")
            }
            guard let nested = nestedEntries.first else { return inputURL }
            guard nested.uncompressedSize <= 512 * 1024 * 1024 else {
                throw AoVModRemakeExporterError.archiveExpandedTooLarge("Resources.zip")
            }
            let destination = tempRoot.appendingPathComponent("resources-\(UUID().uuidString).zip")
            do {
                _ = try outer.extract(nested, to: destination, bufferSize: 64 * 1024)
                _ = try Archive(url: destination, accessMode: .read)
            } catch {
                try? FileManager.default.removeItem(at: destination)
                throw AoVModRemakeExporterError.invalidBaseArchive(
                    "\(inputURL.lastPathComponent)/Resources.zip"
                )
            }
            return destination
        }

        func readCatalog(_ archive: Archive, fileName: String) throws -> Data {
            let matches = archive.filter {
                $0.type == .file &&
                ($0.path as NSString).lastPathComponent.caseInsensitiveCompare(fileName) == .orderedSame
            }
            guard matches.count == 1, let entry = matches.first else {
                throw matches.isEmpty
                    ? AoVModRemakeExporterError.missingCatalog(fileName)
                    : AoVModRemakeExporterError.ambiguousCatalog(fileName)
            }
            return try readEntry(archive, entry, maxBytes: maximumSingleEntry)
        }

        func runtimePath(for archivePath: String, version: String) throws -> String? {
            let normalized = try normalizeArchivePath(archivePath)
            let parts = normalized.split(separator: "/").map(String.init)
            guard !parts.isEmpty else { return nil }
            if parts.count >= 3,
               parts[0].lowercased() == "resources",
               parts[1].caseInsensitiveCompare(version) == .orderedSame {
                return parts.dropFirst(2).joined(separator: "/")
            }
            if parts.count >= 2,
               parts[0].caseInsensitiveCompare(version) == .orderedSame {
                return parts.dropFirst().joined(separator: "/")
            }
            if parts[0].range(
                of: #"^\d+(?:\.\d+){1,3}$"#,
                options: .regularExpression
            ) != nil {
                return nil
            }
            return normalized
        }

        func makeRuntimeEntryMap(
            _ entries: [Entry],
            version: String
        ) throws -> [String: [Entry]] {
            var result: [String: [Entry]] = [:]
            for entry in entries {
                guard let path = try runtimePath(for: entry.path, version: version) else { continue }
                result[path, default: []].append(entry)
            }
            return result
        }

        func isStandalone(_ path: String, _ row: AoVRuntimeResourcePackInfo) -> Bool {
            let normalized = path.replacingOccurrences(of: "\\", with: "/")
            let lower = normalized.lowercased()
            let components = lower.split(separator: "/")
            let ext = URL(fileURLWithPath: normalized).pathExtension.lowercased()
            let excludedExtensions: Set<String> = [
                "assetbundle", "bnk", "wem", "zip", "pck", "obb"
            ]
            let dedicatedAdapterMarkers = [
                "assetref", "commonactions", "thanos/heroskin_",
                "reskinseniorlabelcfg", "battlebank", "lobbybank",
                "skillcombine", "ifix", "mses", "/sound/", "/audio/"
            ]
            return !components.contains("assetbundle") &&
                !excludedExtensions.contains(ext) &&
                !lower.contains(".pkg.") &&
                !dedicatedAdapterMarkers.contains(where: { lower.contains($0) }) &&
                !row.resourceInfos.contains(where: {
                    $0.extensionName.lowercased() == "assetbundle"
                })
        }

        let baseResourcesURL = try resolveResources(baseRESURL)
        let baseArchive: Archive
        do {
            baseArchive = try Archive(url: baseResourcesURL, accessMode: .read)
        } catch {
            throw AoVModRemakeExporterError.invalidBaseArchive(baseRESURL.lastPathComponent)
        }
        let baseEntries = try validatedEntries(baseArchive, label: "RES nền")
        let basePackerData = try readCatalog(baseArchive, fileName: packerFileName)
        let baseVerificationData = try readCatalog(baseArchive, fileName: verificationFileName)
        let basePackerTarget = try AoVUnityFSRuntimeCatalogMergeExecutor.locatePackerCatalog(
            in: basePackerData
        )
        let baseVerificationTarget = try AoVUnityFSRuntimeCatalogMergeExecutor.locateVerificationCatalog(
            in: baseVerificationData
        )
        guard case .packer(let basePacker) = basePackerTarget.catalog,
              case .verification(let baseVerification) = baseVerificationTarget.catalog,
              basePacker.version == baseVerification.version else {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "RES nền thiếu đúng cặp runtime Packer/Verification cùng version."
            )
        }

        let version = basePacker.version
        let basePackerPaths = Set(basePacker.allResourcePackInfo.map(\.pathInIFS))
        let baseRuntimeEntries = try makeRuntimeEntryMap(baseEntries, version: version)
        let lowerVersion = version.lowercased()
        let destinationPrefix: String
        if baseEntries.contains(where: {
            $0.path.lowercased().hasPrefix("resources/\(lowerVersion)/")
        }) {
            destinationPrefix = "Resources/\(version)/"
        } else if baseEntries.contains(where: {
            $0.path.lowercased().hasPrefix("\(lowerVersion)/")
        }) {
            destinationPrefix = "\(version)/"
        } else {
            destinationPrefix = ""
        }

        typealias Candidate = (
            source: String,
            path: String,
            packer: AoVRuntimeResourcePackInfo,
            verification: AoVRuntimeVerificationEntry,
            isZIP: Bool,
            data: Data
        )
        var candidatesByPath: [String: [Candidate]] = [:]
        var blockedSamples: [String] = []

        for sourceURL in legacyMODURLs {
            let sourceName = sourceURL.lastPathComponent
            let packerSources = catalogInputs.packer.filter { $0.source == sourceName }
            let verificationSources = catalogInputs.verification.filter { $0.source == sourceName }
            guard packerSources.count == 1, verificationSources.count == 1,
                  let sourcePacker = packerSources.first?.catalog,
                  let sourceVerification = verificationSources.first?.catalog,
                  sourcePacker.version == version,
                  sourceVerification.version == version else {
                throw AoVModRemakeExporterError.catalogMergeFailed(
                    "\(sourceName): không đọc được đúng một cặp Packer/Verification cùng version."
                )
            }

            let sourceResourcesURL = try resolveResources(sourceURL)
            let sourceArchive: Archive
            do {
                sourceArchive = try Archive(url: sourceResourcesURL, accessMode: .read)
            } catch {
                throw AoVModRemakeExporterError.invalidBaseArchive(sourceName)
            }
            let sourceEntries = try validatedEntries(sourceArchive, label: sourceName)
            let sourceRuntimeEntries = try makeRuntimeEntryMap(sourceEntries, version: version)
            let linkedIndices = Set(sourcePacker.allParentIndexToChild.flatMap {
                [$0.parentIdx, $0.childIdx]
            })
            let rowsByPath = Dictionary(
                grouping: sourcePacker.allResourcePackInfo,
                by: \.pathInIFS
            )

            for (index, row) in sourcePacker.allResourcePackInfo.enumerated() {
                let resourcePath: String
                do {
                    resourcePath = try normalizeArchivePath(row.pathInIFS)
                } catch {
                    continue
                }
                guard resourcePath == row.pathInIFS,
                      !basePackerPaths.contains(resourcePath),
                      baseRuntimeEntries[resourcePath] == nil,
                      rowsByPath[resourcePath]?.count == 1,
                      let rowIndex = Int32(exactly: index),
                      !linkedIndices.contains(rowIndex),
                      isStandalone(resourcePath, row) else {
                    continue
                }

                let zipRows = sourceVerification.allZipVerificationInfo.filter {
                    $0.pathInIFS == resourcePath
                }
                let databinRows = sourceVerification.allDatabinVerificationInfo.filter {
                    $0.pathInIFS == resourcePath
                }
                guard zipRows.count + databinRows.count == 1,
                      let matchingEntries = sourceRuntimeEntries[resourcePath],
                      matchingEntries.count == 1,
                      let resourceEntry = matchingEntries.first else {
                    blockedSamples.append("\(sourceName): \(resourcePath) (thiếu/trùng payload hoặc Verification)")
                    continue
                }
                guard resourceEntry.uncompressedSize <= maximumSingleEntry else {
                    blockedSamples.append("\(sourceName): \(resourcePath) (quá lớn)")
                    continue
                }

                let payload = try readEntry(
                    sourceArchive,
                    resourceEntry,
                    maxBytes: maximumSingleEntry
                )
                let isZIP = !zipRows.isEmpty
                let verificationRow = isZIP ? zipRows[0] : databinRows[0]
                candidatesByPath[resourcePath, default: []].append(
                    (
                        source: sourceName,
                        path: resourcePath,
                        packer: row,
                        verification: verificationRow,
                        isZIP: isZIP,
                        data: payload
                    )
                )
            }
        }

        var selected: [Candidate] = []
        for path in candidatesByPath.keys.sorted() {
            guard let candidates = candidatesByPath[path], let first = candidates.first else { continue }
            let identical = candidates.dropFirst().allSatisfy {
                $0.data == first.data &&
                $0.packer == first.packer &&
                $0.verification == first.verification &&
                $0.isZIP == first.isZIP
            }
            if identical {
                selected.append(first)
            } else {
                blockedSamples.append("\(path) (nhiều MOD có payload hoặc checksum xung đột)")
            }
        }
        guard !selected.isEmpty else {
            let samples = blockedSamples.prefix(5).joined(separator: "; ")
            let suffix = samples.isEmpty ? "" : " Mẫu bị loại: \(samples)."
            throw AoVModRemakeExporterError.noSafeStandaloneResources(
                "Không có file mới độc lập, đủ metadata và không có Packer dependency.\(suffix)"
            )
        }

        var totalBytes: UInt64 = 0
        var payloads: [AoVModRemakeResourcePayload] = []
        var finalPackerEntries: [String: AoVRuntimeResourcePackInfo] = [:]
        var finalZIPChecksums: [String: Int64] = [:]
        var finalDatabinChecksums: [String: Int64] = [:]
        var selectedBySource: [String: Set<String>] = [:]
        var selectedZIPBySource: [String: Set<String>] = [:]
        var selectedDatabinBySource: [String: Set<String>] = [:]

        for candidate in selected {
            let next = totalBytes.addingReportingOverflow(UInt64(candidate.data.count))
            guard !next.overflow, next.partialValue <= 256 * 1024 * 1024 else {
                throw AoVModRemakeExporterError.noSafeStandaloneResources(
                    "Giới hạn tự động hiện là 256 MiB mỗi lượt để tránh dùng quá nhiều RAM trên iPhone."
                )
            }
            totalBytes = next.partialValue
            payloads.append(
                AoVModRemakeResourcePayload(
                    archivePath: destinationPrefix + candidate.path,
                    ifsPath: candidate.path,
                    data: candidate.data
                )
            )
            finalPackerEntries[candidate.path] = candidate.packer
            selectedBySource[candidate.source, default: []].insert(candidate.path)
            if candidate.isZIP {
                finalZIPChecksums[candidate.path] = candidate.verification.crcXor
                selectedZIPBySource[candidate.source, default: []].insert(candidate.path)
            } else {
                finalDatabinChecksums[candidate.path] = candidate.verification.crcXor
                selectedDatabinBySource[candidate.source, default: []].insert(candidate.path)
            }
        }

        let filteredPacker: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)] =
            catalogInputs.packer.compactMap { item in
                let paths = selectedBySource[item.source] ?? []
                let rows = item.catalog.allResourcePackInfo.filter {
                    paths.contains($0.pathInIFS)
                }
                guard !rows.isEmpty else { return nil }
                return (
                    source: item.source,
                    catalog: AoVRuntimeResourcePackerCatalog(
                        name: item.catalog.name,
                        version: item.catalog.version,
                        svnRevision: item.catalog.svnRevision,
                        setName: item.catalog.setName,
                        allResourcePackInfo: rows,
                        allParentIndexToChild: [],
                        trailingData: item.catalog.trailingData
                    )
                )
            }
        let filteredVerification: [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)] =
            catalogInputs.verification.compactMap { item in
                let zipPaths = selectedZIPBySource[item.source] ?? []
                let databinPaths = selectedDatabinBySource[item.source] ?? []
                let zipRows = item.catalog.allZipVerificationInfo.filter {
                    zipPaths.contains($0.pathInIFS)
                }
                let databinRows = item.catalog.allDatabinVerificationInfo.filter {
                    databinPaths.contains($0.pathInIFS)
                }
                guard !zipRows.isEmpty || !databinRows.isEmpty else { return nil }
                return (
                    source: item.source,
                    catalog: AoVRuntimeResourceVerificationCatalog(
                        name: item.catalog.name,
                        version: item.catalog.version,
                        svnRevision: item.catalog.svnRevision,
                        allZipVerificationInfo: zipRows,
                        allDatabinVerificationInfo: databinRows,
                        trailingData: item.catalog.trailingData
                    )
                )
            }

        return try exportVerifiedRES(
            baseRESURL: baseRESURL,
            resourcePayloads: payloads,
            legacyPackerCatalogs: filteredPacker,
            verifiedFinalPackerEntries: finalPackerEntries,
            legacyVerificationCatalogs: filteredVerification,
            verifiedFinalDatabinChecksumValues: finalDatabinChecksums,
            verifiedFinalZIPChecksumValues: finalZIPChecksums
        )
    }

    /// Merges a matched class-142 AssetBundle record with its local object closure.
    /// The caller must preflight archive provenance, SerializedFile format and
    /// target platform. The importer then validates TypeTree compatibility,
    /// local/external PPtr targets, object closure and UnityFS round-trip.
    /// Cross-build analysis is permitted only in this candidate stage; payload
    /// bytes remain in memory until final Packer/Verification metadata is
    /// independently verified and passed to the strict RES exporter.
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

    /// Creates a structurally validated RES candidate from CAB object-merge results.
    /// It derives only the checksum formats already fingerprinted from this exact base RES:
    /// CRC64/WE for Verification ZIP, while preserving the source Packer AssetBundle metadata.
    /// The output is still a candidate and is never marked game-compatible.
    static func exportCandidateRESIncludingAssetBundleMerges(
        baseRESURL: URL,
        legacyMODURLs: [URL],
        assetBundleMergeResults: [AoVModRemakeAssetBundleMergeResult],
        checksumFingerprint: AoVRuntimeChecksumFingerprintReport
    ) throws -> AoVModRemakeExportReport {
        guard !assetBundleMergeResults.isEmpty else {
            throw AoVModRemakeExporterError.noResourcePayloads
        }
        guard checksumFingerprint.baseRESName == baseRESURL.lastPathComponent else {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "Checksum fingerprint không thuộc RES nền đang xuất."
            )
        }
        try validateBaseRESContainerCanBeRepackedAsCandidate(baseRESURL)
        try AoVModRemakeRESExportReadinessAnalyzer.validateFingerprintIdentity(
            checksumFingerprint,
            baseRESURL: baseRESURL
        )

        // The writer itself requires the current build's runtime schema. Older MOD
        // catalogs may be analyzed for migration, but cannot be written into this RES.
        let catalogInputs = try AoVModRemakeAnalyzer.loadRuntimeCatalogInputs(
            baseRESURL: baseRESURL,
            legacyMODURLs: legacyMODURLs
        )
        let readiness = try AoVModRemakeRESExportReadinessAnalyzer.buildProposals(
            baseRESName: baseRESURL.lastPathComponent,
            assetBundleMergeResults: assetBundleMergeResults,
            legacyPackerCatalogs: catalogInputs.basePacker + catalogInputs.packer,
            checksumFingerprint: checksumFingerprint
        )
        guard readiness.proposalCount == assetBundleMergeResults.count,
              readiness.candidateCount == readiness.proposalCount,
              readiness.blockedCount == 0 else {
            let blocked = readiness.proposals
                .filter { $0.status == .blocked }
                .map { "\($0.runtimePath): \($0.reasons.joined(separator: "; "))" }
                .prefix(4)
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "Không đủ bằng chứng để lập RES candidate: \(blocked.joined(separator: " | "))."
            )
        }

        var verifiedFinalPackerEntries: [String: AoVRuntimeResourcePackInfo] = [:]
        var verifiedFinalZIPChecksumValues: [String: Int64] = [:]
        for proposal in readiness.proposals {
            guard let packerEntry = proposal.proposedPackerEntry,
                  let verificationChecksum = proposal.proposedVerificationZIPChecksum,
                  packerEntry.pathInIFS == proposal.runtimePath else {
                throw AoVModRemakeExporterError.catalogMergeFailed(
                    "Thiếu Packer/Verification candidate cho \(proposal.runtimePath)."
                )
            }
            guard verifiedFinalPackerEntries.updateValue(
                packerEntry,
                forKey: proposal.runtimePath
            ) == nil,
            verifiedFinalZIPChecksumValues.updateValue(
                verificationChecksum,
                forKey: proposal.runtimePath
            ) == nil else {
                throw AoVModRemakeExporterError.catalogMergeFailed(
                    "Trùng runtime path trong tập RES candidate: \(proposal.runtimePath)."
                )
            }
        }

        let selectedPaths = Set(verifiedFinalPackerEntries.keys)
        let currentPackerPaths = Set(
            catalogInputs.basePacker.flatMap { $0.catalog.allResourcePackInfo.map(\.pathInIFS) }
        )
        let filteredPackerCatalogs = try filterPackerCatalogsForCandidateExport(
            catalogInputs.basePacker + catalogInputs.packer,
            currentPackerPaths: currentPackerPaths,
            selectedPackerPaths: selectedPaths
        )
        let filteredVerificationCatalogs = filterVerificationCatalogsForCandidateExport(
            catalogInputs.baseVerification + catalogInputs.verification,
            selectedZIPPaths: Set(verifiedFinalZIPChecksumValues.keys),
            selectedDatabinPaths: []
        )

        return try exportVerifiedRES(
            baseRESURL: baseRESURL,
            resourcePayloads: assetBundleMergeResults.map(\.resourcePayload),
            legacyPackerCatalogs: filteredPackerCatalogs,
            verifiedFinalPackerEntries: verifiedFinalPackerEntries,
            legacyVerificationCatalogs: filteredVerificationCatalogs,
            verifiedFinalDatabinChecksumValues: [:],
            verifiedFinalZIPChecksumValues: verifiedFinalZIPChecksumValues
        )
    }

    /// Refuses to label a loose resource ZIP as a game RES when its filelist.json
    /// references a separate IFS container. Updating that package requires a
    /// matching, validated IFS/.ifs.res writer; copying the extracted files alone
    /// would leave the game's actual IFS content unchanged.
    static func validateBaseRESContainerCanBeRepackedAsCandidate(_ baseRESURL: URL) throws {
        let archive: Archive
        do {
            archive = try Archive(url: baseRESURL, accessMode: .read)
        } catch {
            throw AoVModRemakeExporterError.invalidBaseArchive(baseRESURL.lastPathComponent)
        }
        let manifests = archive.filter {
            $0.type == .file &&
                ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("filelist.json") == .orderedSame
        }
        guard manifests.count <= 1 else {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "Gói RES có nhiều filelist.json; không thể xác định IFS container tương ứng."
            )
        }
        guard let manifest = manifests.first else {
            // Standalone ZIP-style RES inputs do not use the external IFS wrapper.
            return
        }

        let manifestData = try readEntry(archive, manifest, maxBytes: 4 * 1024 * 1024)
        let rootObject: Any
        do {
            rootObject = try JSONSerialization.jsonObject(with: manifestData)
        } catch {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "filelist.json không phải JSON hợp lệ; không thể xác minh IFS container."
            )
        }
        guard let root = rootObject as? [String: Any],
              let fileList = root["filelist"] as? [[String: Any]] else {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "filelist.json thiếu mảng filelist; không thể xác minh IFS container."
            )
        }

        let ifsRecords = fileList.compactMap { record -> (name: String, size: UInt64?)? in
            guard let name = record["filename"] as? String,
                  name.lowercased().hasSuffix(".ifs") else { return nil }
            let rawSize = record["filesize"] as? NSNumber
            return (name, rawSize?.uint64Value)
        }
        guard !ifsRecords.isEmpty else {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "filelist.json không có bản ghi IFS mà exporter hiện hỗ trợ."
            )
        }

        let manifestDirectory = (manifest.path as NSString).deletingLastPathComponent
        for record in ifsRecords {
            let name = record.name
            guard !name.isEmpty,
                  (name as NSString).lastPathComponent == name,
                  !name.contains("..") else {
                throw AoVModRemakeExporterError.invalidArchivePath(name)
            }
            let expectedPath = manifestDirectory.isEmpty
                ? name
                : manifestDirectory + "/" + name
            let ifsEntry = archive.first {
                $0.type == .file &&
                    $0.path.caseInsensitiveCompare(expectedPath) == .orderedSame
            }
            guard let ifsEntry else {
                let expectedSize = record.size.map { " (manifest khai báo \($0) byte)" } ?? ""
                throw AoVModRemakeExporterError.catalogMergeFailed(
                    "filelist.json tham chiếu IFS \(name)\(expectedSize) nhưng ZIP không chứa file IFS chính. Có .ifs.res/.cures riêng không đủ để tái dựng IFS; từ chối xuất ZIP rời như RES dùng trong game."
                )
            }
            let expectedSize = record.size
            if let expectedSize, expectedSize != ifsEntry.uncompressedSize {
                throw AoVModRemakeExporterError.catalogMergeFailed(
                    "IFS \(name) có dung lượng \(ifsEntry.uncompressedSize), khác manifest \(expectedSize) byte."
                )
            }
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "IFS \(name) có trong ZIP nhưng AKI chưa có bộ ghi IFS/.ifs.res đã xác minh để cập nhật đồng bộ. Chưa thể xuất RES dùng trong game."
            )
        }
    }

    /// Retains only the selected Packer rows and parent/child rows needed to keep
    /// links touching those candidates. Every retained dependency must already
    /// exist in the current RES or be included as a verified final AssetBundle.
    /// Rows and link indexes are rebuilt together, so unrelated MOD entries cannot
    /// pollute the global catalog merge plan.
    static func filterPackerCatalogsForCandidateExport(
        _ sourceCatalogs: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)],
        currentPackerPaths: Set<String>,
        selectedPackerPaths: Set<String>
    ) throws -> [(source: String, catalog: AoVRuntimeResourcePackerCatalog)] {
        let availablePaths = currentPackerPaths.union(selectedPackerPaths)
        var result: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)] = []

        for source in sourceCatalogs {
            let originalRows = source.catalog.allResourcePackInfo
            guard originalRows.contains(where: { selectedPackerPaths.contains($0.pathInIFS) }) else {
                continue
            }

            var keptIndices = Set<Int>()
            for (index, row) in originalRows.enumerated()
            where selectedPackerPaths.contains(row.pathInIFS) {
                keptIndices.insert(index)
            }

            var retainedLinks: [(Int, Int)] = []
            for link in source.catalog.allParentIndexToChild {
                guard let parentIndex = Int(exactly: link.parentIdx),
                      let childIndex = Int(exactly: link.childIdx),
                      originalRows.indices.contains(parentIndex),
                      originalRows.indices.contains(childIndex) else {
                    throw AoVModRemakeExporterError.catalogMergeFailed(
                        "\(source.source): Packer parent-child index ngoài phạm vi trong catalog có AssetBundle candidate."
                    )
                }
                let parentPath = originalRows[parentIndex].pathInIFS
                let childPath = originalRows[childIndex].pathInIFS
                let touchesCandidate = selectedPackerPaths.contains(parentPath) ||
                    selectedPackerPaths.contains(childPath)
                guard touchesCandidate else { continue }

                guard availablePaths.contains(parentPath), availablePaths.contains(childPath) else {
                    let missing = [
                        availablePaths.contains(parentPath) ? nil : parentPath,
                        availablePaths.contains(childPath) ? nil : childPath
                    ].compactMap { $0 }
                    throw AoVModRemakeExporterError.catalogMergeFailed(
                        "\(source.source): dependency Packer chưa có trong RES nền hoặc tập CAB đã ghép: \(missing.joined(separator: ", "))."
                    )
                }
                keptIndices.insert(parentIndex)
                keptIndices.insert(childIndex)
                retainedLinks.append((parentIndex, childIndex))
            }

            let orderedIndices = keptIndices.sorted()
            guard !orderedIndices.isEmpty else { continue }
            var newIndexByOldIndex: [Int: Int32] = [:]
            var filteredRows: [AoVRuntimeResourcePackInfo] = []
            filteredRows.reserveCapacity(orderedIndices.count)
            for (newIndex, oldIndex) in orderedIndices.enumerated() {
                guard let encoded = Int32(exactly: newIndex) else {
                    throw AoVModRemakeExporterError.catalogMergeFailed(
                        "\(source.source): số Packer entry sau lọc vượt Int32."
                    )
                }
                newIndexByOldIndex[oldIndex] = encoded
                filteredRows.append(originalRows[oldIndex])
            }

            var filteredLinks: [AoVPackInfoMapItem] = []
            var seenLinks = Set<AoVPackInfoMapItem>()
            for (oldParent, oldChild) in retainedLinks {
                guard let parent = newIndexByOldIndex[oldParent],
                      let child = newIndexByOldIndex[oldChild] else {
                    throw AoVModRemakeExporterError.catalogMergeFailed(
                        "\(source.source): không thể remap Packer dependency sau khi lọc."
                    )
                }
                let mapped = AoVPackInfoMapItem(parentIdx: parent, childIdx: child)
                if seenLinks.insert(mapped).inserted { filteredLinks.append(mapped) }
            }

            result.append((
                source: source.source,
                catalog: AoVRuntimeResourcePackerCatalog(
                    name: source.catalog.name,
                    version: source.catalog.version,
                    svnRevision: source.catalog.svnRevision,
                    setName: source.catalog.setName,
                    allResourcePackInfo: filteredRows,
                    allParentIndexToChild: filteredLinks,
                    trailingData: source.catalog.trailingData
                )
            ))
        }
        return result
    }

    static func filterVerificationCatalogsForCandidateExport(
        _ sourceCatalogs: [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)],
        selectedZIPPaths: Set<String>,
        selectedDatabinPaths: Set<String>
    ) -> [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)] {
        sourceCatalogs.compactMap { source in
            let zipRows = source.catalog.allZipVerificationInfo.filter {
                selectedZIPPaths.contains($0.pathInIFS)
            }
            let databinRows = source.catalog.allDatabinVerificationInfo.filter {
                selectedDatabinPaths.contains($0.pathInIFS)
            }
            guard !zipRows.isEmpty || !databinRows.isEmpty else { return nil }
            return (
                source: source.source,
                catalog: AoVRuntimeResourceVerificationCatalog(
                    name: source.catalog.name,
                    version: source.catalog.version,
                    svnRevision: source.catalog.svnRevision,
                    allZipVerificationInfo: zipRows,
                    allDatabinVerificationInfo: databinRows,
                    trailingData: source.catalog.trailingData
                )
            )
        }
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
        for (path, entry) in verifiedFinalPackerEntries {
            guard payloadIFSPathContains(path, in: payloadByIFSPath) else {
                throw AoVModRemakeExporterError.unusedFinalPackerEntry(path)
            }
            guard entry.pathInIFS == path else {
                throw AoVModRemakeExporterError.catalogMergeFailed(
                    "Packer final map dùng key \(path) nhưng entry trỏ tới \(entry.pathInIFS)."
                )
            }
        }
        for path in assetBundleIFSPaths where verifiedFinalPackerEntries[path] == nil {
            throw AoVModRemakeExporterError.missingFinalPackerEntry(path)
        }

        // The caller's verified checksum maps are the authoritative
        // classification. Do not guess ZIP vs Databin from a path prefix:
        // resource naming is not a checksum-format contract.
        let payloadIFSPaths = Set(payloadByIFSPath.keys)
        let databinIFSPaths = Set(verifiedFinalDatabinChecksumValues.keys)
        let zipIFSPaths = Set(verifiedFinalZIPChecksumValues.keys)
        let checksumTypeCollisions = databinIFSPaths.intersection(zipIFSPaths)
        guard checksumTypeCollisions.isEmpty else {
            let collisionSamples = Array(checksumTypeCollisions.sorted().prefix(4))
            let collisionMessage = "Một IFS path có cả Databin và ZIP checksum: \(collisionSamples)."
            throw AoVModRemakeExporterError.catalogMergeFailed(collisionMessage)
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
                "AssetBundle \(path) phải dùng ZIP Verification checksum."
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

    private static func payloadIFSPathContains(
        _ path: String,
        in payloads: [String: AoVModRemakeResourcePayload]
    ) -> Bool {
        payloads.values.contains { $0.ifsPath == path }
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
        // ZIPFoundation 0.9.x exposes the extracted entry path and sizes, but
        // not the original ZIP compression method. Re-encode by the same
        // safe path policy used for imported resources instead of guessing
        // from compressed/uncompressed sizes.
        storageMethod(forNewPath: entry.path)
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
                "Không mở lại được ZIP đầu ra: \(error.localizedDescription)"
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
                    "Resources.zip đầu ra không mở lại được: \(error.localizedDescription)"
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
                    "Không tìm thấy duy nhất tài nguyên đầu ra \(expectedPath)."
                )
            }
            let actual = try readEntry(resourceArchive, entry, maxBytes: maximumSingleEntry)
            guard actual == expectedData else {
                throw AoVModRemakeExporterError.integrityFailure(
                    "Payload \(expectedPath) thay đổi sau khi đóng gói."
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
