import Foundation
import CryptoKit
import ZIPFoundation

struct ArchivePathDigest: Codable, Hashable, Sendable, Identifiable {
    let archiveName: String
    let path: String
    let sha256: String
    let uncompressedBytes: UInt64
    var id: String { "\(archiveName)|\(path)|\(sha256)" }
}

struct ArchiveInventory: Codable, Identifiable, Sendable {
    let id: UUID
    let fileName: String
    let fileSize: UInt64
    let fileCount: Int
    let uncompressedBytes: UInt64
    let paths: [String]
    let pathDigests: [ArchivePathDigest]
    let unityFSBundles: [AoVUnityFSScanResult]
    let unityFSUnrecognizedCount: Int
    let unityFSTooLargeCount: Int
    let unityFSInspectionFailures: [String]
}

struct ArchiveOverlap: Codable, Identifiable, Sendable {
    let path: String
    let payloads: [ArchivePathDigest]
    let identicalPayload: Bool
    var sourceNames: [String] { Array(Set(payloads.map(\.archiveName))).sorted() }
    var id: String { path }
}

struct ArchiveAnalysis: Codable, Sendable {
    let archives: [ArchiveInventory]
    let overlappingPaths: [ArchiveOverlap]
    let totalInputBytes: UInt64
    let totalUncompressedBytes: UInt64
    let crcValidatedFiles: Int
    let identicalDuplicatePaths: Int
    let conflictingPaths: Int

    var unityFSBundles: [AoVUnityFSScanResult] {
        archives.flatMap(\.unityFSBundles)
    }
    var unityFSUnrecognizedCount: Int {
        archives.reduce(0) { $0 + $1.unityFSUnrecognizedCount }
    }
    var unityFSTooLargeCount: Int {
        archives.reduce(0) { $0 + $1.unityFSTooLargeCount }
    }
    var unityFSInspectionFailures: [String] {
        archives.flatMap(\.unityFSInspectionFailures)
    }
}

struct DecodedAESResource: Codable, Identifiable, Sendable {
    let archiveName: String
    let path: String
    let decodedBytes: UInt64
    let sha256: String
    let wrapperMode: String
    var id: String { "\(archiveName)|\(path)" }
}

struct AssetRefInspection: Codable, Identifiable, Sendable {
    let archiveName: String
    let path: String
    let actorID: String?
    let wrapperMode: String
    let decodedBytes: UInt64
    let sha256: String
    let hasBaseSubset: Bool
    let hasSkinSubset: Bool
    let referencePathCandidates: Int
    let sampleReferences: [String]
    var id: String { "\(archiveName)|\(path)" }
}

struct AESScanReport: Codable, Sendable {
    let decoded: [DecodedAESResource]
    let assetReferenceSets: [AssetRefInspection]
    let unrecognizedAssetRefCount: Int
    let otherBytesFiles: Int
    let tooLargeToInspect: Int
    let invalidAES: [String]
    var aesDecodedCount: Int { decoded.filter { $0.wrapperMode == "aes" }.count }
    var aesZstdDecodedCount: Int { decoded.filter { $0.wrapperMode == "aes+zstd" }.count }
    var zstdDecodedCount: Int { decoded.filter { $0.wrapperMode == "zstd" }.count }
}

enum ArchiveScanError: LocalizedError {
    case tooManyEntries(String)
    case entryTooLarge(String, String)
    case archiveTooLarge(String)
    case totalInputTooLarge
    case unsafePath(String, String)
    case symbolicLink(String, String)
    case duplicatePathInsideArchive(String, String)
    case crcFailure(String, String)

    var errorDescription: String? {
        switch self {
        case .tooManyEntries(let name):
            return "\(name): ZIP vượt giới hạn 20.000 mục."
        case .entryTooLarge(let name, let path):
            return "\(name): mục \(path) vượt giới hạn 256 MiB."
        case .archiveTooLarge(let name):
            return "\(name): dữ liệu giải nén khai báo vượt giới hạn 1 GiB."
        case .totalInputTooLarge:
            return "Tổng dung lượng ZIP đầu vào vượt giới hạn 500 MiB của bản iOS thử nghiệm."
        case .unsafePath(let name, let path):
            return "\(name): đường dẫn không an toàn: \(path)"
        case .symbolicLink(let name, let path):
            return "\(name): symbolic link chưa được hỗ trợ: \(path)"
        case .duplicatePathInsideArchive(let name, let path):
            return "\(name): có nhiều mục cùng đường dẫn \(path); cần kiểm tra riêng."
        case .crcFailure(let name, let path):
            return "\(name): không đọc/kiểm tra CRC được mục \(path)."
        }
    }
}

enum ArchiveScanner {
    private static let maximumEntries = 20_000
    private static let maximumEntryBytes: UInt64 = 256 * 1024 * 1024
    private static let maximumArchiveExpandedBytes: UInt64 = 1024 * 1024 * 1024
    private static let maximumTotalInputBytes: UInt64 = 500 * 1024 * 1024
    private static let maximumAESInspectInputBytes: UInt64 = 32 * 1024 * 1024
    private static let maximumUnityFSInspectEntryBytes: UInt64 = 64 * 1024 * 1024
    private static let maximumUnityFSInspectTotalBytes: UInt64 = 256 * 1024 * 1024
    private static let maximumCatalogExpandedBytes: UInt64 = 128 * 1024 * 1024

    static func analyze(_ urls: [URL]) throws -> ArchiveAnalysis {
        var inventories: [ArchiveInventory] = []
        var totalInput: UInt64 = 0
        var totalExpanded: UInt64 = 0

        for url in urls {
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            let size = (attributes[.size] as? NSNumber)?.uint64Value ?? 0
            let inputSum = totalInput.addingReportingOverflow(size)
            guard !inputSum.overflow, inputSum.partialValue <= maximumTotalInputBytes else {
                throw ArchiveScanError.totalInputTooLarge
            }
            totalInput = inputSum.partialValue

            let inventory = try scanOne(url)
            inventories.append(inventory)
            let expandedSum = totalExpanded.addingReportingOverflow(inventory.uncompressedBytes)
            guard !expandedSum.overflow, expandedSum.partialValue <= 2 * maximumArchiveExpandedBytes else {
                throw ArchiveScanError.archiveTooLarge(url.lastPathComponent)
            }
            totalExpanded = expandedSum.partialValue
        }

        var owners: [String: [ArchivePathDigest]] = [:]
        for inventory in inventories {
            for record in inventory.pathDigests {
                owners[record.path, default: []].append(record)
            }
        }
        let overlaps = owners
            .filter { $0.value.count > 1 }
            .map { path, payloads in
                ArchiveOverlap(
                    path: path,
                    payloads: payloads.sorted {
                        if $0.archiveName != $1.archiveName { return $0.archiveName < $1.archiveName }
                        return $0.sha256 < $1.sha256
                    },
                    identicalPayload: Set(payloads.map(\.sha256)).count == 1
                )
            }
            .sorted { $0.path.localizedCaseInsensitiveCompare($1.path) == .orderedAscending }

        return ArchiveAnalysis(
            archives: inventories,
            overlappingPaths: overlaps,
            totalInputBytes: totalInput,
            totalUncompressedBytes: totalExpanded,
            crcValidatedFiles: inventories.reduce(0) { $0 + $1.fileCount },
            identicalDuplicatePaths: overlaps.filter(\.identicalPayload).count,
            conflictingPaths: overlaps.filter { !$0.identicalPayload }.count
        )
    }

    static func inspectAESResources(_ urls: [URL]) throws -> AESScanReport {
        var decoded: [DecodedAESResource] = []
        var assetReferenceSets: [AssetRefInspection] = []
        var unrecognizedAssetRefs = 0
        var otherBytes = 0
        var tooLarge = 0
        var invalidAES: [String] = []

        for url in urls {
            let archive = try Archive(url: url, accessMode: .read)
            try scanAESFiles(
                in: archive,
                sourceName: url.lastPathComponent,
                pathPrefix: "",
                decoded: &decoded,
                assetReferenceSets: &assetReferenceSets,
                unrecognizedAssetRefs: &unrecognizedAssetRefs,
                otherBytes: &otherBytes,
                tooLarge: &tooLarge,
                invalidAES: &invalidAES
            )

            // MOD wrappers commonly keep game resources in a nested Resources.zip.
            // Extract only that ZIP to a temporary file, then scan it entry-by-entry.
            for entry in archive where entry.type == .file &&
                (entry.path as NSString).lastPathComponent.lowercased() == "resources.zip" {
                guard entry.uncompressedSize <= maximumArchiveExpandedBytes else {
                    tooLarge += 1
                    continue
                }
                let nestedURL = FileManager.default.temporaryDirectory
                    .appendingPathComponent("aov-resources-\(UUID().uuidString).zip")
                defer { try? FileManager.default.removeItem(at: nestedURL) }
                do {
                    _ = try archive.extract(entry, to: nestedURL, bufferSize: 64 * 1024)
                    let nestedArchive = try Archive(url: nestedURL, accessMode: .read)
                    try scanAESFiles(
                        in: nestedArchive,
                        sourceName: url.lastPathComponent,
                        pathPrefix: "\(entry.path)!/",
                        decoded: &decoded,
                        assetReferenceSets: &assetReferenceSets,
                        unrecognizedAssetRefs: &unrecognizedAssetRefs,
                        otherBytes: &otherBytes,
                        tooLarge: &tooLarge,
                        invalidAES: &invalidAES
                    )
                } catch {
                    throw ArchiveScanError.crcFailure(url.lastPathComponent, entry.path)
                }
            }
        }

        return AESScanReport(
            decoded: decoded,
            assetReferenceSets: assetReferenceSets,
            unrecognizedAssetRefCount: unrecognizedAssetRefs,
            otherBytesFiles: otherBytes,
            tooLargeToInspect: tooLarge,
            invalidAES: invalidAES
        )
    }

    private static func scanAESFiles(
        in archive: Archive,
        sourceName: String,
        pathPrefix: String,
        decoded: inout [DecodedAESResource],
        assetReferenceSets: inout [AssetRefInspection],
        unrecognizedAssetRefs: inout Int,
        otherBytes: inout Int,
        tooLarge: inout Int,
        invalidAES: inout [String]
    ) throws {
        for entry in archive where entry.type == .file && entry.path.lowercased().hasSuffix(".bytes") {
            let displayPath = pathPrefix + entry.path
            guard entry.uncompressedSize <= maximumAESInspectInputBytes else {
                tooLarge += 1
                continue
            }

            var resourceData = Data()
            do {
                _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { chunk in
                    resourceData.append(chunk)
                })
            } catch {
                throw ArchiveScanError.crcFailure(sourceName, displayPath)
            }

            let isAssetRef = isAssetReferenceSetPath(displayPath)
            if !AoVBytesDecoder.isAoVWrappedResource(resourceData) {
                if isAssetRef {
                    if let report = inspectAssetReferenceSet(
                        resourceData,
                        archiveName: sourceName,
                        path: displayPath,
                        wrapperMode: "raw"
                    ) {
                        assetReferenceSets.append(report)
                    } else {
                        unrecognizedAssetRefs += 1
                    }
                } else {
                    otherBytes += 1
                }
                continue
            }

            do {
                let decodedResource = try AoVBytesDecoder.decodeResource(resourceData, resourceName: entry.path)
                let payload = decodedResource.data
                let digest = SHA256.hash(data: payload)
                decoded.append(
                    DecodedAESResource(
                        archiveName: sourceName,
                        path: displayPath,
                        decodedBytes: UInt64(payload.count),
                        sha256: digest.map { String(format: "%02x", $0) }.joined(),
                        wrapperMode: decodedResource.wrapperMode
                    )
                )
                if isAssetRef {
                    if let report = inspectAssetReferenceSet(
                        payload,
                        archiveName: sourceName,
                        path: displayPath,
                        wrapperMode: decodedResource.wrapperMode
                    ) {
                        assetReferenceSets.append(report)
                    } else {
                        unrecognizedAssetRefs += 1
                    }
                }
            } catch {
                if isAssetRef { unrecognizedAssetRefs += 1 }
                if invalidAES.count < 30 {
                    invalidAES.append("\(sourceName): \(displayPath) — \(error.localizedDescription)")
                }
            }
        }
    }

    private static func isAssetReferenceSetPath(_ path: String) -> Bool {
        let normalized = path.replacingOccurrences(of: "\\", with: "/")
        let parts = normalized.split(separator: "/").map { $0.lowercased() }
        guard parts.contains("assetrefs"), parts.contains("hero") else { return false }
        let filename = (normalized as NSString).lastPathComponent
        let pattern = #"^\d+_AssetRef\.bytes$"#
        return filename.range(of: pattern, options: [.regularExpression, .caseInsensitive]) != nil
    }

    private static func inspectAssetReferenceSet(
        _ payload: Data,
        archiveName: String,
        path: String,
        wrapperMode: String
    ) -> AssetRefInspection? {
        let rootSignature = Array("ROOTC".utf8)
        let typeSignature = Array("TypeAssetRefAnalyser.AssetReferenceSet".utf8)
        let baseSubsetSignature = Array("baseSubset".utf8)
        let skinSubsetSignature = Array("skinSubset".utf8)

        func find(_ needle: [UInt8], lower: Int, upper: Int) -> Int? {
            guard lower >= 0, upper <= payload.count, upper - lower >= needle.count else {
                return nil
            }
            for offset in lower...(upper - needle.count) {
                var matched = true
                for index in needle.indices where payload[offset + index] != needle[index] {
                    matched = false
                    break
                }
                if matched { return offset }
            }
            return nil
        }

        guard let root = find(rootSignature, lower: 0, upper: min(64, payload.count)),
              let type = find(typeSignature, lower: root, upper: min(payload.count, root + 1024)),
              find(baseSubsetSignature, lower: type, upper: min(payload.count, type + 2048)) != nil else {
            return nil
        }

        var candidates = Set<String>()
        var index = 0
        while index < payload.count && candidates.count < 500 {
            let byte = payload[index]
            if byte == 86 || byte == 118 {
                let start = index
                var end = index
                while end < payload.count && end - start <= 240 {
                    let value = payload[end]
                    let isLetter = (65...90).contains(value) || (97...122).contains(value)
                    let isDigit = (48...57).contains(value)
                    if isLetter || isDigit || value == 95 || value == 45 || value == 46 || value == 47 {
                        end += 1
                    } else {
                        break
                    }
                }
                if end - start >= 7 {
                    let slice = payload[start..<end]
                    if slice.contains(47), let candidate = String(bytes: slice, encoding: .ascii) {
                        candidates.insert(candidate)
                    }
                    index = end
                    continue
                }
            }
            index += 1
        }

        let filename = (path.replacingOccurrences(of: "\\", with: "/") as NSString).lastPathComponent
        let actor = filename.range(of: #"^\d+(?=_AssetRef\.bytes$)"#,
                                   options: [.regularExpression, .caseInsensitive])
            .map { String(filename[$0]) }
        let digest = SHA256.hash(data: payload).map { String(format: "%02x", $0) }.joined()
        return AssetRefInspection(
            archiveName: archiveName,
            path: path,
            actorID: actor,
            wrapperMode: wrapperMode,
            decodedBytes: UInt64(payload.count),
            sha256: digest,
            hasBaseSubset: true,
            hasSkinSubset: find(skinSubsetSignature, lower: 0, upper: payload.count) != nil,
            referencePathCandidates: candidates.count,
            sampleReferences: candidates.sorted().prefix(5).map { $0 }
        )
    }

    private static func scanOne(_ url: URL) throws -> ArchiveInventory {
        let archive = try Archive(url: url, accessMode: .read)
        var pathDigests: [ArchivePathDigest] = []
        var paths: [String] = []
        var seenPaths = Set<String>()
        var expandedBytes: UInt64 = 0
        var entryCount = 0
        var unityFSInspectBytes: UInt64 = 0
        var unityFSBundles: [AoVUnityFSScanResult] = []
        var unityFSUnrecognizedCount = 0
        var unityFSTooLargeCount = 0
        var unityFSInspectionFailures: [String] = []

        for entry in archive {
            if entry.type == .directory { continue }
            if entry.type == .symlink {
                throw ArchiveScanError.symbolicLink(url.lastPathComponent, entry.path)
            }

            entryCount += 1
            guard entryCount <= maximumEntries else {
                throw ArchiveScanError.tooManyEntries(url.lastPathComponent)
            }

            let path = entry.path.replacingOccurrences(of: "\\", with: "/")
            let parts = path.split(separator: "/", omittingEmptySubsequences: false)
            let unsafe = path.hasPrefix("/") || path.hasPrefix("~") ||
                parts.contains("..") || parts.contains(".") ||
                path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
            guard !unsafe else {
                throw ArchiveScanError.unsafePath(url.lastPathComponent, entry.path)
            }

            guard seenPaths.insert(path).inserted else {
                throw ArchiveScanError.duplicatePathInsideArchive(url.lastPathComponent, path)
            }

            let entryBytes = entry.uncompressedSize
            guard entryBytes <= maximumEntryBytes else {
                throw ArchiveScanError.entryTooLarge(url.lastPathComponent, entry.path)
            }
            let sum = expandedBytes.addingReportingOverflow(entryBytes)
            guard !sum.overflow, sum.partialValue <= maximumArchiveExpandedBytes else {
                throw ArchiveScanError.archiveTooLarge(url.lastPathComponent)
            }
            expandedBytes = sum.partialValue

            let isAssetBundle = path.lowercased().hasSuffix(".assetbundle")
            let nextUnityFSBudget = unityFSInspectBytes.addingReportingOverflow(entryBytes)
            let shouldInspectUnityFS = isAssetBundle &&
                entryBytes <= maximumUnityFSInspectEntryBytes &&
                !nextUnityFSBudget.overflow &&
                nextUnityFSBudget.partialValue <= maximumUnityFSInspectTotalBytes
            if isAssetBundle && !shouldInspectUnityFS {
                unityFSTooLargeCount += 1
            }

            var bundlePayload = Data()
            if shouldInspectUnityFS {
                bundlePayload.reserveCapacity(Int(entryBytes))
                unityFSInspectBytes = nextUnityFSBudget.partialValue
            }

            var hasher = SHA256()
            do {
                _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { chunk in
                    hasher.update(data: chunk)
                    if shouldInspectUnityFS { bundlePayload.append(chunk) }
                })
            } catch {
                throw ArchiveScanError.crcFailure(url.lastPathComponent, entry.path)
            }
            let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()

            if shouldInspectUnityFS {
                let unitySignature = Data("UnityFS\0".utf8)
                if bundlePayload.count >= unitySignature.count &&
                    bundlePayload.prefix(unitySignature.count) == unitySignature {
                    do {
                        let info = try AoVUnityFSParser.inspect(bundlePayload)
                        var catalogStatus: String?
                        var catalogSamples: [AoVUnityFSCatalogNodeSample] = []

                        // Inspect bounded SerializedFile nodes in every UnityFS
                        // AssetBundle, not just the two runtime catalog bundles.
                        // This provides generic CAB discovery without writing data.
                        do {
                            let extracted = try AoVUnityFSParser.extractNodes(
                                bundlePayload,
                                maximumExpandedBytes: maximumCatalogExpandedBytes,
                                maximumNodeCount: 256
                            )
                            catalogSamples = extracted.map { node in
                                    let serializedSummary: String?
                                    let serializedFile: AoVSerializedFileSummary?
                                    do {
                                        let serialized = try AoVSerializedFileParser.inspect(node.data)
                                        serializedFile = serialized
                                        let bundleObjects = serialized.assetBundleObjects
                                            .map { "pathID \($0.pathID) · offset \($0.byteOffset) · \($0.byteSize) byte" }
                                            .joined(separator: "; ")
                                        let catalogRecords = serialized.assetBundleRecords.map { record in
                                            let dependencies = record.dependencies.prefix(8).joined(separator: ", ")
                                            let containerSamples = record.containerEntries.prefix(5).map {
                                                "\($0.key) -> \($0.asset.fileID):\($0.asset.pathID)"
                                            }.joined(separator: ", ")
                                            let mainAsset = "\(record.mainAsset.fileID):\(record.mainAsset.pathID)"
                                            return "name=\(record.name) · bundle=\(record.assetBundleName) · preload=\(record.preloadReferences.count) · container=\(record.containerEntries.count) · main=\(mainAsset) · classCompat=\(record.classCompatibilityEntries.count) · scriptCompat=\(record.scriptCompatibilityEntries.count)" +
                                                (dependencies.isEmpty ? "" : " · deps=[\(dependencies)]") +
                                                (containerSamples.isEmpty ? "" : " · entries=[\(containerSamples)]")
                                        }.joined(separator: " || ")
                                        let packerSummary = serialized.resourcePackerCatalog.map {
                                            " · AoV Packer \($0.version): \($0.allResourcePackInfo.count) bundle · \($0.allParentIndexToChild.count) parent-child map"
                                        } ?? ""
                                        let verificationSummary = serialized.resourceVerificationCatalog.map {
                                            " · AoV Verification \($0.version): \($0.allZipVerificationInfo.count) ZIP · \($0.allDatabinVerificationInfo.count) Databin"
                                        } ?? ""
                                        serializedSummary = "SerializedFile v\(serialized.formatVersion) · Unity \(serialized.unityVersion) · platform \(serialized.targetPlatform) · \(serialized.objectCount) object · AssetBundle class 142: \(serialized.assetBundleObjectCount)" +
                                            (bundleObjects.isEmpty ? "" : " · \(bundleObjects)") +
                                            (catalogRecords.isEmpty ? "" : " · \(catalogRecords)") +
                                            packerSummary +
                                            verificationSummary
                                    } catch {
                                        serializedFile = nil
                                        serializedSummary = "Không nhận diện SerializedFile: \(error.localizedDescription)"
                                    }
                                    return AoVUnityFSCatalogNodeSample(
                                        path: node.path,
                                        size: UInt64(node.data.count),
                                        prefixHex: node.data.prefix(16)
                                            .map { String(format: "%02X", $0) }
                                            .joined(separator: " "),
                                        stringHints: AoVUnityFSParser.asciiStringHints(node.data),
                                        serializedFileSummary: serializedSummary,
                                        serializedFile: serializedFile
                                    )
                                }
                            catalogStatus = "Đã trích xuất read-only \(extracted.count) node để audit SerializedFile/CAB."
                        } catch {
                            catalogStatus = "Không trích xuất được SerializedFile/CAB node: \(error.localizedDescription)"
                        }

                        unityFSBundles.append(
                            AoVUnityFSScanResult(
                                archiveName: url.lastPathComponent,
                                path: path,
                                info: info,
                                catalogExtractionStatus: catalogStatus,
                                catalogNodeSamples: catalogSamples
                            )
                        )
                    } catch {
                        if unityFSInspectionFailures.count < 30 {
                            unityFSInspectionFailures.append(
                                "\(path): \(error.localizedDescription)"
                            )
                        }
                    }
                } else {
                    unityFSUnrecognizedCount += 1
                }
            }
            paths.append(path)
            pathDigests.append(
                ArchivePathDigest(
                    archiveName: url.lastPathComponent,
                    path: path,
                    sha256: digest,
                    uncompressedBytes: entryBytes
                )
            )
        }

        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        let fileSize = (attributes[.size] as? NSNumber)?.uint64Value ?? 0
        return ArchiveInventory(
            id: UUID(),
            fileName: url.lastPathComponent,
            fileSize: fileSize,
            fileCount: entryCount,
            uncompressedBytes: expandedBytes,
            paths: paths.sorted(),
            pathDigests: pathDigests,
            unityFSBundles: unityFSBundles,
            unityFSUnrecognizedCount: unityFSUnrecognizedCount,
            unityFSTooLargeCount: unityFSTooLargeCount,
            unityFSInspectionFailures: unityFSInspectionFailures
        )
    }
}

func formatByteCount(_ bytes: UInt64) -> String {
    ByteCountFormatter.string(fromByteCount: Int64(clamping: bytes), countStyle: .file)
}
