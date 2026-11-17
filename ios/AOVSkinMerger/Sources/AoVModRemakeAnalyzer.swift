import Foundation
import ZIPFoundation

enum AoVModRemakeError: LocalizedError {
    case noLegacyMods
    case tooManyLegacyMods
    case sameArchiveSelected
    case invalidArchive(String)
    case multipleResourceArchives(String)
    case resourceArchiveTooLarge(String)
    case versionMarkerTooLarge(String)

    var errorDescription: String? {
        switch self {
        case .noLegacyMods:
            return "Hãy chọn ít nhất một MOD cũ để đối chiếu với RES mới."
        case .tooManyLegacyMods:
            return "Chế độ RMK nhận tối đa 10 MOD cũ mỗi lượt."
        case .sameArchiveSelected:
            return "RES mới không được đồng thời chọn làm MOD cũ."
        case .invalidArchive(let name):
            return "\(name): không mở được ZIP."
        case .multipleResourceArchives(let name):
            return "\(name): wrapper chứa nhiều Resources.zip, không thể tự đoán gói cần dùng."
        case .resourceArchiveTooLarge(let name):
            return "\(name): Resources.zip vượt giới hạn giải nén 512 MiB."
        case .versionMarkerTooLarge(let path):
            return "\(path): version.txt vượt giới hạn đọc an toàn."
        }
    }
}

enum AoVRemakePathKind: String, Codable, Sendable {
    case identical
    case changed
    case legacyOnly
}

struct AoVRemakePathFinding: Codable, Identifiable, Sendable {
    let path: String
    let kind: AoVRemakePathKind
    let legacySources: [String]
    let baseSHA256: String?
    let legacySHA256: [String]
    let baseBytes: UInt64?
    let legacyBytes: UInt64

    var id: String { path }
}

enum AoVRemakeCatalogKind: String, Codable, Equatable, Hashable, Sendable, CaseIterable {
    case packer
    case verification

    var fileName: String {
        switch self {
        case .packer: return "resourcepackerinfosetall.assetbundle"
        case .verification: return "resourceverificationinfosetall.assetbundle"
        }
    }

    var displayName: String {
        switch self {
        case .packer: return "Packer"
        case .verification: return "Verification"
        }
    }
}

enum AoVRemakeCatalogRecordStatus: String, Codable, Equatable, Hashable, Sendable {
    case mappedCandidate
    case missingFromCurrent
    case ambiguousCurrentMatch
}

struct AoVRemakeCatalogEntrySnapshot: Codable, Sendable, Identifiable {
    let key: String
    let preloadIndex: Int32
    let preloadSize: Int32
    let assetFileID: Int32
    let assetPathID: Int64
    let preloadRangeValid: Bool

    var id: String { key }
}

struct AoVRemakeCatalogRecordSnapshot: Codable, Sendable, Identifiable {
    let sourceArchive: String
    let catalogPath: String
    let nodePath: String
    let serializedFormatVersion: UInt32
    let unityVersion: String
    let targetPlatform: Int32
    let objectPathID: Int64
    let name: String
    let assetBundleName: String
    let preloadCount: Int
    let invalidPreloadRangeCount: Int
    let invalidPreloadRangeSamples: [String]
    let classCompatibilityIDs: [Int32]
    let resolvedLocalPointerCount: Int
    let externalPointerCount: Int
    let nullPointerCount: Int
    let unresolvedLocalPointerCount: Int
    let invalidExternalPointerCount: Int
    let pointerAuditSamples: [String]
    let mainAssetFileID: Int32
    let mainAssetPathID: Int64
    let dependencies: [String]
    let containerEntries: [AoVRemakeCatalogEntrySnapshot]

    var matchingName: String {
        let bundle = assetBundleName.trimmingCharacters(in: .whitespacesAndNewlines)
        return (bundle.isEmpty ? name : bundle).lowercased()
    }

    var id: String {
        "\(sourceArchive)|\(catalogPath)|\(nodePath)|\(objectPathID)"
    }
}

struct AoVRemakeCatalogRecordFinding: Codable, Sendable, Identifiable {
    let sourceArchive: String
    let kind: AoVRemakeCatalogKind
    let assetBundleName: String
    let status: AoVRemakeCatalogRecordStatus
    let legacyContainerEntryCount: Int
    let currentContainerEntryCount: Int
    let mappedContainerKeyCount: Int
    let legacyOnlyContainerKeyCount: Int
    let currentOnlyContainerKeyCount: Int
    let ambiguousContainerKeyCount: Int
    let changedAssetPointerCount: Int
    let changedPreloadRangeCount: Int
    let invalidLegacyPreloadRangeCount: Int
    let invalidCurrentPreloadRangeCount: Int
    let mainAssetPointerChanged: Bool
    let classCompatibilityChanged: Bool
    let unresolvedLegacyLocalPointerCount: Int
    let unresolvedCurrentLocalPointerCount: Int
    let invalidLegacyExternalPointerCount: Int
    let invalidCurrentExternalPointerCount: Int
    let legacyPointerAuditSamples: [String]
    let currentPointerAuditSamples: [String]
    let dependenciesAddedInCurrent: [String]
    let dependenciesRemovedInCurrent: [String]
    let legacyOnlyKeySamples: [String]
    let currentOnlyKeySamples: [String]
    let changedPointerKeySamples: [String]

    var id: String { "\(sourceArchive)|\(kind.rawValue)|\(assetBundleName)" }
}

struct AoVRemakeCatalogKindReport: Codable, Sendable {
    let kind: AoVRemakeCatalogKind
    let currentRecordCount: Int
    let legacyRecordCount: Int
    let mappedRecordCount: Int
    let missingCurrentRecordCount: Int
    let ambiguousRecordCount: Int
    let currentOnlyRecordCount: Int
    let mappedContainerKeyCount: Int
    let legacyOnlyContainerKeyCount: Int
    let currentOnlyContainerKeyCount: Int
    let ambiguousContainerKeyCount: Int
    let changedAssetPointerCount: Int
    let changedPreloadRangeCount: Int
    let invalidLegacyPreloadRangeCount: Int
    let invalidCurrentPreloadRangeCount: Int
    let changedMainAssetPointerCount: Int
    let changedClassCompatibilityCount: Int
    let unresolvedLegacyLocalPointerCount: Int
    let unresolvedCurrentLocalPointerCount: Int
    let invalidLegacyExternalPointerCount: Int
    let invalidCurrentExternalPointerCount: Int
    let dependenciesAddedInCurrentCount: Int
    let dependenciesRemovedInCurrentCount: Int
    let findings: [AoVRemakeCatalogRecordFinding]
}

enum AoVRemakeCatalogPlanStatus: String, Codable, Equatable, Sendable {
    case candidate
    case requiresExternalTargetAudit
    case noChanges
    case missingCurrentBundle
    case ambiguousCurrentBundle
    case blocked
}

struct AoVRemakeCatalogPlanFinding: Codable, Sendable, Identifiable {
    let kind: AoVRemakeCatalogKind
    let bundleName: String
    let currentCatalogPath: String?
    let currentNodePath: String?
    let currentObjectPathID: Int64?
    let sourceArchives: [String]
    let status: AoVRemakeCatalogPlanStatus
    let addedContainerKeys: [String]
    let alreadyPresentContainerKeys: [String]
    let currentVersionRetainedPointerConflictKeys: [String]
    let addedPreloadReferenceCount: Int
    let addedDependencies: [String]
    let remappedExternalPointerCount: Int
    let externalTargetAuditSamples: [String]
    let reasons: [String]

    var id: String {
        "\(kind.rawValue)|\(bundleName)|\(currentCatalogPath ?? "missing")|\(sourceArchives.joined(separator: ","))"
    }
}

private struct AoVRemakeCatalogRecordContext: Sendable, Identifiable {
    let sourceArchive: String
    let catalogPath: String
    let nodePath: String
    let serializedFile: AoVSerializedFileSummary
    let record: AoVSerializedAssetBundleRecord

    var bundleName: String {
        let value = record.assetBundleName.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? record.name : value
    }

    var matchingName: String { bundleName.lowercased() }
    var id: String { "\(sourceArchive)|\(catalogPath)|\(nodePath)|\(record.pathID)" }
}

private struct AoVRemakeCatalogPlanAccumulator {
    let target: AoVRemakeCatalogRecordContext
    var sourceArchives = Set<String>()
    var addedKeys = Set<String>()
    var alreadyPresentKeys = Set<String>()
    var currentVersionRetainedPointerConflicts = Set<String>()
    var addedPreloadReferences = 0
    var addedDependencies = Set<String>()
    var remappedExternalPointers = 0
    var externalAuditSamples = Set<String>()
    var reasons = Set<String>()
    var failed = false
    var hasChanges = false
}

struct AoVModRemakeReport: Codable, Sendable {
    let base: ArchiveInventory
    let legacy: [ArchiveInventory]
    let baseVersion: String?
    let legacyVersions: [String: String]
    let identicalPaths: [AoVRemakePathFinding]
    let changedPaths: [AoVRemakePathFinding]
    let legacyOnlyPaths: [AoVRemakePathFinding]
    let baseOnlyPathCount: Int
    let baseHasPackerCatalog: Bool
    let baseHasVerificationCatalog: Bool
    let legacyHasPackerCatalog: [String]
    let legacyHasVerificationCatalog: [String]
    let packerCatalogComparison: AoVRemakeCatalogKindReport
    let verificationCatalogComparison: AoVRemakeCatalogKindReport
    let catalogMergePlanFindings: [AoVRemakeCatalogPlanFinding]
    let warnings: [String]

    var totalLegacyFiles: Int { legacy.reduce(0) { $0 + $1.fileCount } }
    var totalLegacyBytes: UInt64 { legacy.reduce(0) { $0 &+ $1.uncompressedBytes } }
    var identicalCount: Int { identicalPaths.count }
    var changedCount: Int { changedPaths.count }
    var legacyOnlyCount: Int { legacyOnlyPaths.count }
    var catalogPlanCandidateCount: Int {
        catalogMergePlanFindings.filter { $0.status == .candidate || $0.status == .requiresExternalTargetAudit }.count
    }
    var catalogPlanBlockedCount: Int {
        catalogMergePlanFindings.filter {
            $0.status == .blocked || $0.status == .missingCurrentBundle || $0.status == .ambiguousCurrentBundle
        }.count
    }
    var catalogPlanExternalAuditCount: Int {
        catalogMergePlanFindings.filter { $0.status == .requiresExternalTargetAudit }.count
    }

    /// Analysis is deliberately separate from the existing skin merger.
    /// No output is advertised until catalog remapping and UnityFS rebuilding exist.
    var rebuildAvailable: Bool { false }
}

private struct AoVRemakeCatalogPointerAudit {
    let resolvedLocal: Int
    let external: Int
    let null: Int
    let unresolvedLocal: Int
    let invalidExternal: Int
    let samples: [String]
}

enum AoVModRemakeAnalyzer {
    private static let maximumLegacyMods = 10
    private static let maximumNestedResourcesBytes: UInt64 = 512 * 1024 * 1024
    private static let maximumVersionMarkerBytes: UInt64 = 64 * 1024
    private static let packerFileName = "resourcepackerinfosetall.assetbundle"
    private static let verificationFileName = "resourceverificationinfosetall.assetbundle"
    private static let maximumCatalogFindings = 500

    static func analyze(baseRESURL: URL, legacyMODURLs: [URL]) throws -> AoVModRemakeReport {
        guard !legacyMODURLs.isEmpty else { throw AoVModRemakeError.noLegacyMods }
        guard legacyMODURLs.count <= maximumLegacyMods else { throw AoVModRemakeError.tooManyLegacyMods }

        let baseIdentity = baseRESURL.standardizedFileURL
        guard !legacyMODURLs.contains(where: { $0.standardizedFileURL == baseIdentity }) else {
            throw AoVModRemakeError.sameArchiveSelected
        }

        let tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-AOV-REMAKE-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempRoot) }

        let baseScanURL = try resolveResourceArchive(baseRESURL, tempRoot: tempRoot)
        var legacyScanURLs: [URL] = []
        for url in legacyMODURLs {
            let scanURL = try resolveResourceArchive(url, tempRoot: tempRoot)
            legacyScanURLs.append(scanURL)
        }

        // Each side is scanned independently so the RES baseline and legacy MOD
        // collection each retain the scanner's 500 MiB input safety ceiling.
        guard let scannedBase = try ArchiveScanner.analyze([baseScanURL]).archives.first else {
            throw AoVModRemakeError.invalidArchive(baseRESURL.lastPathComponent)
        }
        let scannedLegacy = try ArchiveScanner.analyze(legacyScanURLs).archives
        let baseInventory = relabel(scannedBase, as: baseRESURL.lastPathComponent)
        let legacyInventories = scannedLegacy.enumerated().map { index, inventory in
            relabel(inventory, as: legacyMODURLs[index].lastPathComponent)
        }
        let baseVersion = try readVersionMarker(from: baseRESURL) ?? readVersionMarker(from: baseScanURL)
        var legacyVersions: [String: String] = [:]
        for index in legacyScanURLs.indices {
            if let version = try readVersionMarker(from: legacyMODURLs[index]) ?? readVersionMarker(from: legacyScanURLs[index]) {
                legacyVersions[legacyMODURLs[index].lastPathComponent] = version
            }
        }

        let baseByPath = Dictionary(uniqueKeysWithValues: baseInventory.pathDigests.map { ($0.path, $0) })
        var legacyByPath: [String: [ArchivePathDigest]] = [:]
        for inventory in legacyInventories {
            for record in inventory.pathDigests {
                legacyByPath[record.path, default: []].append(record)
            }
        }

        var identical: [AoVRemakePathFinding] = []
        var changed: [AoVRemakePathFinding] = []
        var legacyOnly: [AoVRemakePathFinding] = []

        for path in legacyByPath.keys.sorted() {
            guard let legacyRecords = legacyByPath[path]?.sorted(by: { $0.archiveName < $1.archiveName }) else {
                continue
            }
            let legacySources = Array(Set(legacyRecords.map(\.archiveName))).sorted()
            let legacyDigests = Array(Set(legacyRecords.map(\.sha256))).sorted()
            let totalLegacyBytes = legacyRecords.reduce(UInt64(0)) { $0 &+ $1.uncompressedBytes }
            if let baseRecord = baseByPath[path] {
                let allSame = legacyRecords.allSatisfy { $0.sha256 == baseRecord.sha256 }
                let finding = AoVRemakePathFinding(
                    path: path,
                    kind: allSame ? .identical : .changed,
                    legacySources: legacySources,
                    baseSHA256: baseRecord.sha256,
                    legacySHA256: legacyDigests,
                    baseBytes: baseRecord.uncompressedBytes,
                    legacyBytes: totalLegacyBytes
                )
                if allSame { identical.append(finding) } else { changed.append(finding) }
            } else {
                legacyOnly.append(
                    AoVRemakePathFinding(
                        path: path,
                        kind: .legacyOnly,
                        legacySources: legacySources,
                        baseSHA256: nil,
                        legacySHA256: legacyDigests,
                        baseBytes: nil,
                        legacyBytes: totalLegacyBytes
                    )
                )
            }
        }

        let legacyPathSet = Set(legacyByPath.keys)
        let baseOnlyCount = baseByPath.keys.reduce(into: 0) { count, path in
            if !legacyPathSet.contains(path) { count += 1 }
        }
        let baseNames = Set(baseInventory.paths.map { ($0 as NSString).lastPathComponent.lowercased() })
        let baseHasPacker = baseNames.contains(packerFileName)
        let baseHasVerification = baseNames.contains(verificationFileName)
        let legacyPacker = legacyInventories.filter { inventory in
            inventory.paths.contains { ($0 as NSString).lastPathComponent.lowercased() == packerFileName }
        }.map(\.fileName)
        let legacyVerification = legacyInventories.filter { inventory in
            inventory.paths.contains { ($0 as NSString).lastPathComponent.lowercased() == verificationFileName }
        }.map(\.fileName)

        var warnings: [String] = []
        if baseVersion == nil {
            warnings.append("Không tìm thấy version.txt trong RES nền; chưa xác định được build của RES mới.")
        }
        let missingLegacyVersions = legacyInventories.filter { legacyVersions[$0.fileName] == nil }.map(\.fileName)
        if !missingLegacyVersions.isEmpty {
            warnings.append("Một số MOD cũ không có version.txt: \(missingLegacyVersions.joined(separator: ", ")).")
        }
        let mismatchedVersions = legacyVersions.filter { _, value in
            guard let baseVersion else { return false }
            return value != baseVersion
        }
        if !mismatchedVersions.isEmpty {
            warnings.append("Có MOD khác build RES nền. Các tài nguyên khác hash cần remap theo schema, không được ghi đè trực tiếp.")
        }
        if !baseHasPacker || !baseHasVerification {
            warnings.append("RES nền thiếu Packer Catalog hoặc Verification Catalog. Chưa đủ metadata để tái dựng bundle.")
        }
        if changed.isEmpty && legacyOnly.isEmpty {
            warnings.append("Không thấy khác biệt payload theo đường dẫn giữa bộ RES và MOD cũ; cần kiểm tra đúng file đầu vào.")
        } else {
            warnings.append("Các path khác payload hoặc chỉ có ở MOD cũ mới là ứng viên cần phân tích; chưa thể tự kết luận tương thích.")
        }
        let packerCatalogComparison = compareCatalogRecords(
            current: catalogRecords(in: baseInventory, kind: .packer),
            legacy: legacyInventories.flatMap { catalogRecords(in: $0, kind: .packer) },
            kind: .packer
        )
        let verificationCatalogComparison = compareCatalogRecords(
            current: catalogRecords(in: baseInventory, kind: .verification),
            legacy: legacyInventories.flatMap { catalogRecords(in: $0, kind: .verification) },
            kind: .verification
        )
        let currentPackerContexts = catalogRecordContexts(in: baseInventory, kind: .packer)
        let currentVerificationContexts = catalogRecordContexts(in: baseInventory, kind: .verification)
        let legacyPackerContexts = legacyInventories.flatMap { catalogRecordContexts(in: $0, kind: .packer) }
        let legacyVerificationContexts = legacyInventories.flatMap { catalogRecordContexts(in: $0, kind: .verification) }

        let availableCurrentBundleNames = Set(currentPackerContexts.flatMap { context in
            [context.bundleName, context.record.name, context.record.assetBundleName]
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
                .filter { !$0.isEmpty }
        })
        let packerPlanFindings = buildCatalogMergePlans(
            current: currentPackerContexts,
            legacy: legacyPackerContexts,
            kind: .packer,
            availableCurrentBundleNames: availableCurrentBundleNames
        )
        let verificationPlanFindings = buildCatalogMergePlans(
            current: currentVerificationContexts,
            legacy: legacyVerificationContexts,
            kind: .verification,
            availableCurrentBundleNames: availableCurrentBundleNames
        )
        let catalogMergePlanFindings = (packerPlanFindings + verificationPlanFindings)
            .sorted {
                if $0.kind != $1.kind { return $0.kind.rawValue < $1.kind.rawValue }
                if $0.bundleName != $1.bundleName { return $0.bundleName < $1.bundleName }
                return $0.sourceArchives.joined() < $1.sourceArchives.joined()
            }
        if packerCatalogComparison.currentRecordCount == 0 ||
            verificationCatalogComparison.currentRecordCount == 0 {
            warnings.append("Một hoặc cả hai catalog chưa có AssetBundle record class 142 đọc được; cần kiểm tra lại RES đầu vào hoặc layout SerializedFile.")
        }
        let unresolvedLocalPointers =
            packerCatalogComparison.unresolvedLegacyLocalPointerCount +
            packerCatalogComparison.unresolvedCurrentLocalPointerCount +
            verificationCatalogComparison.unresolvedLegacyLocalPointerCount +
            verificationCatalogComparison.unresolvedCurrentLocalPointerCount
        let invalidExternalPointers =
            packerCatalogComparison.invalidLegacyExternalPointerCount +
            packerCatalogComparison.invalidCurrentExternalPointerCount +
            verificationCatalogComparison.invalidLegacyExternalPointerCount +
            verificationCatalogComparison.invalidCurrentExternalPointerCount
        if unresolvedLocalPointers > 0 || invalidExternalPointers > 0 {
            warnings.append("Có pointer chưa phân giải được: local object không tìm thấy = \(unresolvedLocalPointers), external fileID không hợp lệ = \(invalidExternalPointers). Không được tự động remap các record này.")
        }
        if !catalogMergePlanFindings.isEmpty {
            warnings.append("Đã lập kế hoạch ghép thử cho \(catalogMergePlanFindings.filter { $0.status == .candidate || $0.status == .requiresExternalTargetAudit }.count) AssetBundle; \(catalogMergePlanFindings.filter { $0.status == .blocked || $0.status == .missingCurrentBundle || $0.status == .ambiguousCurrentBundle }.count) trường hợp bị chặn hoặc chưa khớp.")
        }
        if catalogMergePlanFindings.contains(where: { $0.status == .requiresExternalTargetAudit }) {
            warnings.append("Một số kế hoạch dùng external PPtr đã remap theo GUID/path, nhưng vẫn cần xác minh pathID trong CAB đích trước khi được phép ghi.")
        }
        warnings.append("Catalog đã được phân tích sâu ở mức AssetBundle class 142, container key, preload range, pointer và dependency. Các pointer chỉ là ứng viên ánh xạ, chưa được ghi ngược vào file.")
        warnings.append("Bản phân tích này không tạo ZIP remake. Cần xác minh remap pointer, checksum và UnityFS rebuild trên bộ RES/MOD thật trước khi xuất.")
        
        return AoVModRemakeReport(
            base: baseInventory,
            legacy: legacyInventories,
            baseVersion: baseVersion,
            legacyVersions: legacyVersions,
            identicalPaths: identical,
            changedPaths: changed,
            legacyOnlyPaths: legacyOnly,
            baseOnlyPathCount: baseOnlyCount,
            baseHasPackerCatalog: baseHasPacker,
            baseHasVerificationCatalog: baseHasVerification,
            legacyHasPackerCatalog: legacyPacker,
            legacyHasVerificationCatalog: legacyVerification,
            packerCatalogComparison: packerCatalogComparison,
            verificationCatalogComparison: verificationCatalogComparison,
            catalogMergePlanFindings: catalogMergePlanFindings,
            warnings: warnings
        )
    }

    private static func catalogRecordContexts(
        in inventory: ArchiveInventory,
        kind: AoVRemakeCatalogKind
    ) -> [AoVRemakeCatalogRecordContext] {
        inventory.unityFSBundles
            .filter { ($0.path as NSString).lastPathComponent.lowercased() == kind.fileName }
            .flatMap { bundle -> [AoVRemakeCatalogRecordContext] in
                bundle.catalogNodeSamples.flatMap { sample -> [AoVRemakeCatalogRecordContext] in
                    guard let serialized = sample.serializedFile else {
                        return [AoVRemakeCatalogRecordContext]()
                    }
                    return serialized.assetBundleRecords.map { record -> AoVRemakeCatalogRecordContext in
                        AoVRemakeCatalogRecordContext(
                            sourceArchive: inventory.fileName,
                            catalogPath: bundle.path,
                            nodePath: sample.path,
                            serializedFile: serialized,
                            record: record
                        )
                    }
                }
            }
    }

    private static func buildCatalogMergePlans(
        current: [AoVRemakeCatalogRecordContext],
        legacy: [AoVRemakeCatalogRecordContext],
        kind: AoVRemakeCatalogKind,
        availableCurrentBundleNames: Set<String>
    ) -> [AoVRemakeCatalogPlanFinding] {
        let currentByName = Dictionary(grouping: current, by: \.matchingName)
        var accumulators: [String: AoVRemakeCatalogPlanAccumulator] = [:]
        var unmatchedFindings: [AoVRemakeCatalogPlanFinding] = []
        var workingRecords: [String: AoVSerializedAssetBundleRecord] = [:]

        let orderedLegacy = legacy.sorted {
            if $0.matchingName != $1.matchingName { return $0.matchingName < $1.matchingName }
            if $0.sourceArchive != $1.sourceArchive { return $0.sourceArchive < $1.sourceArchive }
            return $0.nodePath < $1.nodePath
        }

        for oldContext in orderedLegacy {
            let candidates = currentByName[oldContext.matchingName] ?? []
            guard candidates.count == 1, let target = candidates.first else {
                let status: AoVRemakeCatalogPlanStatus = candidates.isEmpty
                    ? .missingCurrentBundle : .ambiguousCurrentBundle
                unmatchedFindings.append(
                    AoVRemakeCatalogPlanFinding(
                        kind: kind,
                        bundleName: oldContext.bundleName,
                        currentCatalogPath: nil,
                        currentNodePath: nil,
                        currentObjectPathID: nil,
                        sourceArchives: [oldContext.sourceArchive],
                        status: status,
                        addedContainerKeys: [],
                        alreadyPresentContainerKeys: [],
                        currentVersionRetainedPointerConflictKeys: [],
                        addedPreloadReferenceCount: 0,
                        addedDependencies: [],
                        remappedExternalPointerCount: 0,
                        externalTargetAuditSamples: [],
                        reasons: [candidates.isEmpty
                            ? "Không có AssetBundle cùng tên trong RES mới."
                            : "Có nhiều AssetBundle cùng tên trong RES mới, không thể chọn duy nhất."]
                    )
                )
                continue
            }

            var accumulator = accumulators[target.id] ?? AoVRemakeCatalogPlanAccumulator(target: target)
            accumulator.sourceArchives.insert(oldContext.sourceArchive)
            let currentRecord = workingRecords[target.id] ?? target.record
            do {
                let plan = try AoVAssetBundleCatalogMerger.plan(
                    current: currentRecord,
                    legacy: oldContext.record,
                    currentSerializedFile: target.serializedFile,
                    legacySerializedFile: oldContext.serializedFile,
                    availableCurrentBundleNames: availableCurrentBundleNames
                )
                workingRecords[target.id] = plan.mergedRecord
                accumulator.addedKeys.formUnion(plan.addedContainerKeys)
                accumulator.alreadyPresentKeys.formUnion(plan.alreadyPresentContainerKeys)
                accumulator.currentVersionRetainedPointerConflicts.formUnion(plan.currentVersionRetainedPointerConflictKeys)
                accumulator.addedPreloadReferences += plan.addedPreloadReferenceCount
                accumulator.addedDependencies.formUnion(plan.addedDependencies)
                accumulator.remappedExternalPointers += plan.remappedExternalPointerCount
                accumulator.externalAuditSamples.formUnion(plan.externalTargetAuditSamples)
                if !plan.addedContainerKeys.isEmpty || !plan.addedDependencies.isEmpty {
                    accumulator.hasChanges = true
                }
            } catch {
                accumulator.failed = true
                accumulator.reasons.insert("\(oldContext.sourceArchive): \(error.localizedDescription)")
            }
            accumulators[target.id] = accumulator
        }

        let plannedFindings = accumulators.values.map { accumulator -> AoVRemakeCatalogPlanFinding in
            let status: AoVRemakeCatalogPlanStatus
            if accumulator.failed {
                status = .blocked
            } else if accumulator.remappedExternalPointers > 0 {
                status = .requiresExternalTargetAudit
            } else if !accumulator.hasChanges {
                status = .noChanges
            } else {
                status = .candidate
            }
            return AoVRemakeCatalogPlanFinding(
                kind: kind,
                bundleName: accumulator.target.bundleName,
                currentCatalogPath: accumulator.target.catalogPath,
                currentNodePath: accumulator.target.nodePath,
                currentObjectPathID: accumulator.target.record.pathID,
                sourceArchives: accumulator.sourceArchives.sorted(),
                status: status,
                addedContainerKeys: accumulator.addedKeys.sorted(),
                alreadyPresentContainerKeys: accumulator.alreadyPresentKeys.sorted(),
                currentVersionRetainedPointerConflictKeys: accumulator.currentVersionRetainedPointerConflicts.sorted(),
                addedPreloadReferenceCount: accumulator.addedPreloadReferences,
                addedDependencies: accumulator.addedDependencies.sorted(),
                remappedExternalPointerCount: accumulator.remappedExternalPointers,
                externalTargetAuditSamples: accumulator.externalAuditSamples.sorted(),
                reasons: accumulator.reasons.sorted()
            )
        }
        return unmatchedFindings + plannedFindings
    }

    private static func catalogRecords(
        in inventory: ArchiveInventory,
        kind: AoVRemakeCatalogKind
    ) -> [AoVRemakeCatalogRecordSnapshot] {
        inventory.unityFSBundles
            .filter { ($0.path as NSString).lastPathComponent.lowercased() == kind.fileName }
            .flatMap { bundle -> [AoVRemakeCatalogRecordSnapshot] in
                bundle.catalogNodeSamples.flatMap { sample -> [AoVRemakeCatalogRecordSnapshot] in
                    guard let serialized = sample.serializedFile else {
                        return [AoVRemakeCatalogRecordSnapshot]()
                    }
                    return serialized.assetBundleRecords.map { record -> AoVRemakeCatalogRecordSnapshot in
                        let pointerAudit = auditPointers(record, in: serialized)
                        return AoVRemakeCatalogRecordSnapshot(
                            sourceArchive: inventory.fileName,
                            catalogPath: bundle.path,
                            nodePath: sample.path,
                            serializedFormatVersion: serialized.formatVersion,
                            unityVersion: serialized.unityVersion,
                            targetPlatform: serialized.targetPlatform,
                            objectPathID: record.pathID,
                            name: record.name,
                            assetBundleName: record.assetBundleName,
                            preloadCount: record.preloadReferences.count,
                            invalidPreloadRangeCount: record.containerEntries.filter { entry in
                                let start = Int64(entry.preloadIndex)
                                let count = Int64(entry.preloadSize)
                                let end = start.addingReportingOverflow(count)
                                return entry.preloadIndex < 0 || entry.preloadSize < 0 ||
                                    end.overflow || end.partialValue > Int64(record.preloadReferences.count)
                            }.count,
                            invalidPreloadRangeSamples: Array(record.containerEntries.filter { entry in
                                let start = Int64(entry.preloadIndex)
                                let count = Int64(entry.preloadSize)
                                let end = start.addingReportingOverflow(count)
                                return entry.preloadIndex < 0 || entry.preloadSize < 0 ||
                                    end.overflow || end.partialValue > Int64(record.preloadReferences.count)
                            }.prefix(8).map { $0.key }),
                            classCompatibilityIDs: record.classCompatibilityIDs,
                            resolvedLocalPointerCount: pointerAudit.resolvedLocal,
                            externalPointerCount: pointerAudit.external,
                            nullPointerCount: pointerAudit.null,
                            unresolvedLocalPointerCount: pointerAudit.unresolvedLocal,
                            invalidExternalPointerCount: pointerAudit.invalidExternal,
                            pointerAuditSamples: pointerAudit.samples,
                            mainAssetFileID: record.mainAsset.fileID,
                            mainAssetPathID: record.mainAsset.pathID,
                            dependencies: record.dependencies,
                            containerEntries: record.containerEntries.map {
                                AoVRemakeCatalogEntrySnapshot(
                                    key: $0.key,
                                    preloadIndex: $0.preloadIndex,
                                    preloadSize: $0.preloadSize,
                                    assetFileID: $0.asset.fileID,
                                    assetPathID: $0.asset.pathID,
                                    preloadRangeValid: $0.preloadIndex >= 0 &&
                                        $0.preloadSize >= 0 &&
                                        !Int64($0.preloadIndex).addingReportingOverflow(Int64($0.preloadSize)).overflow &&
                                        Int64($0.preloadIndex).addingReportingOverflow(Int64($0.preloadSize)).partialValue <= Int64(record.preloadReferences.count)
                                )
                            }
                        )
                    }
                }
            }
    }

    private static func auditPointers(
        _ record: AoVSerializedAssetBundleRecord,
        in serialized: AoVSerializedFileSummary
    ) -> AoVRemakeCatalogPointerAudit {
        let pointers: [(String, AoVSerializedAssetPointer)] =
            record.preloadReferences.enumerated().map { ("preload[\($0.offset)]", $0.element) } +
            record.containerEntries.map { ("container[\($0.key)]", $0.asset) } +
            [("mainAsset", record.mainAsset)] +
            record.classCompatibilityEntries.map { ("classCompat[\($0.classID)]", $0.info.asset) }

        let localIDs = serialized.serializedObjectPathIDs
        let externalCount = serialized.externalReferences.count
        var resolvedLocal = 0
        var external = 0
        var null = 0
        var unresolvedLocal = 0
        var invalidExternal = 0
        var samples: [String] = []

        for (label, pointer) in pointers {
            if pointer.pathID == 0 {
                null += 1
            } else if pointer.fileID == 0 {
                if localIDs.contains(pointer.pathID) {
                    resolvedLocal += 1
                } else {
                    unresolvedLocal += 1
                    if samples.count < 12 {
                        samples.append("\(label) -> local pathID \(pointer.pathID) không có trong object table")
                    }
                }
            } else if pointer.fileID > 0 && Int(pointer.fileID) <= externalCount {
                external += 1
            } else {
                invalidExternal += 1
                if samples.count < 12 {
                    samples.append("\(label) -> fileID \(pointer.fileID) ngoài \(externalCount) external reference")
                }
            }
        }

        return AoVRemakeCatalogPointerAudit(
            resolvedLocal: resolvedLocal,
            external: external,
            null: null,
            unresolvedLocal: unresolvedLocal,
            invalidExternal: invalidExternal,
            samples: samples
        )
    }

    /// Compare logical AssetBundle names and container keys. Pointers are reported
    /// as mapping candidates only: path IDs are scoped to their own SerializedFile
    /// and must never be copied from an old catalog into a current catalog blindly.
    static func compareCatalogRecords(
        current: [AoVRemakeCatalogRecordSnapshot],
        legacy: [AoVRemakeCatalogRecordSnapshot],
        kind: AoVRemakeCatalogKind
    ) -> AoVRemakeCatalogKindReport {
        let currentByName = Dictionary(grouping: current, by: \.matchingName)
        var matchedRecordCount = 0
        var missingRecordCount = 0
        var ambiguousRecordCount = 0
        var mappedKeys = 0
        var legacyOnlyKeys = 0
        var currentOnlyKeys = 0
        var ambiguousKeys = 0
        var changedPointers = 0
        var changedPreloadRanges = 0
        var invalidLegacyPreloadRanges = 0
        var invalidCurrentPreloadRanges = 0
        var changedMainAssetPointers = 0
        var changedClassCompatibility = 0
        var unresolvedLegacyLocalPointers = 0
        var unresolvedCurrentLocalPointers = 0
        var invalidLegacyExternalPointers = 0
        var invalidCurrentExternalPointers = 0
        var dependenciesAdded = 0
        var dependenciesRemoved = 0
        var findings: [AoVRemakeCatalogRecordFinding] = []
        var seenLegacyNames = Set<String>()

        for oldRecord in legacy {
            let identity = oldRecord.matchingName
            seenLegacyNames.insert(identity)
            let candidates = currentByName[identity] ?? []
            let status: AoVRemakeCatalogRecordStatus
            if candidates.isEmpty {
                status = .missingFromCurrent
                missingRecordCount += 1
                invalidLegacyPreloadRanges += oldRecord.invalidPreloadRangeCount
                unresolvedLegacyLocalPointers += oldRecord.unresolvedLocalPointerCount
                invalidLegacyExternalPointers += oldRecord.invalidExternalPointerCount
                findings.append(
                    AoVRemakeCatalogRecordFinding(
                        sourceArchive: oldRecord.sourceArchive,
                        kind: kind,
                        assetBundleName: oldRecord.assetBundleName.isEmpty ? oldRecord.name : oldRecord.assetBundleName,
                        status: status,
                        legacyContainerEntryCount: oldRecord.containerEntries.count,
                        currentContainerEntryCount: 0,
                        mappedContainerKeyCount: 0,
                        legacyOnlyContainerKeyCount: oldRecord.containerEntries.count,
                        currentOnlyContainerKeyCount: 0,
                        ambiguousContainerKeyCount: 0,
                        changedAssetPointerCount: 0,
                        changedPreloadRangeCount: 0,
                        invalidLegacyPreloadRangeCount: oldRecord.invalidPreloadRangeCount,
                        invalidCurrentPreloadRangeCount: 0,
                        mainAssetPointerChanged: false,
                        classCompatibilityChanged: false,
                        unresolvedLegacyLocalPointerCount: oldRecord.unresolvedLocalPointerCount,
                        unresolvedCurrentLocalPointerCount: 0,
                        invalidLegacyExternalPointerCount: oldRecord.invalidExternalPointerCount,
                        invalidCurrentExternalPointerCount: 0,
                        legacyPointerAuditSamples: oldRecord.pointerAuditSamples,
                        currentPointerAuditSamples: [],
                        dependenciesAddedInCurrent: [],
                        dependenciesRemovedInCurrent: [],
                        legacyOnlyKeySamples: Array(oldRecord.containerEntries.prefix(8).map(\.key)),
                        currentOnlyKeySamples: [],
                        changedPointerKeySamples: []
                    )
                )
                legacyOnlyKeys += oldRecord.containerEntries.count
                continue
            }
            guard candidates.count == 1, let newRecord = candidates.first else {
                status = .ambiguousCurrentMatch
                ambiguousRecordCount += 1
                invalidLegacyPreloadRanges += oldRecord.invalidPreloadRangeCount
                invalidCurrentPreloadRanges += candidates.reduce(0) { $0 + $1.invalidPreloadRangeCount }
                unresolvedLegacyLocalPointers += oldRecord.unresolvedLocalPointerCount
                unresolvedCurrentLocalPointers += candidates.reduce(0) { $0 + $1.unresolvedLocalPointerCount }
                invalidLegacyExternalPointers += oldRecord.invalidExternalPointerCount
                invalidCurrentExternalPointers += candidates.reduce(0) { $0 + $1.invalidExternalPointerCount }
                findings.append(
                    AoVRemakeCatalogRecordFinding(
                        sourceArchive: oldRecord.sourceArchive,
                        kind: kind,
                        assetBundleName: oldRecord.assetBundleName.isEmpty ? oldRecord.name : oldRecord.assetBundleName,
                        status: status,
                        legacyContainerEntryCount: oldRecord.containerEntries.count,
                        currentContainerEntryCount: candidates.reduce(0) { $0 + $1.containerEntries.count },
                        mappedContainerKeyCount: 0,
                        legacyOnlyContainerKeyCount: 0,
                        currentOnlyContainerKeyCount: 0,
                        ambiguousContainerKeyCount: 0,
                        changedAssetPointerCount: 0,
                        changedPreloadRangeCount: 0,
                        invalidLegacyPreloadRangeCount: oldRecord.invalidPreloadRangeCount,
                        invalidCurrentPreloadRangeCount: candidates.reduce(0) { $0 + $1.invalidPreloadRangeCount },
                        mainAssetPointerChanged: false,
                        classCompatibilityChanged: false,
                        unresolvedLegacyLocalPointerCount: oldRecord.unresolvedLocalPointerCount,
                        unresolvedCurrentLocalPointerCount: candidates.reduce(0) { $0 + $1.unresolvedLocalPointerCount },
                        invalidLegacyExternalPointerCount: oldRecord.invalidExternalPointerCount,
                        invalidCurrentExternalPointerCount: candidates.reduce(0) { $0 + $1.invalidExternalPointerCount },
                        legacyPointerAuditSamples: oldRecord.pointerAuditSamples,
                        currentPointerAuditSamples: Array(candidates.flatMap(\.pointerAuditSamples).prefix(12)),
                        dependenciesAddedInCurrent: [],
                        dependenciesRemovedInCurrent: [],
                        legacyOnlyKeySamples: [],
                        currentOnlyKeySamples: [],
                        changedPointerKeySamples: []
                    )
                )
                continue
            }

            status = .mappedCandidate
            matchedRecordCount += 1
            let oldByKey = Dictionary(grouping: oldRecord.containerEntries, by: \.key)
            let newByKey = Dictionary(grouping: newRecord.containerEntries, by: \.key)
            let allKeys = Set(oldByKey.keys).union(newByKey.keys).sorted()
            var recordMappedKeys = 0
            var recordLegacyOnly = 0
            var recordCurrentOnly = 0
            var recordAmbiguous = 0
            var recordChangedPointers = 0
            var recordChangedRanges = 0
            let recordInvalidLegacyRanges = oldRecord.invalidPreloadRangeCount
            let recordInvalidCurrentRanges = newRecord.invalidPreloadRangeCount
            let recordMainAssetPointerChanged = oldRecord.mainAssetFileID != newRecord.mainAssetFileID ||
                oldRecord.mainAssetPathID != newRecord.mainAssetPathID
            let recordClassCompatibilityChanged = oldRecord.classCompatibilityIDs != newRecord.classCompatibilityIDs
            let recordUnresolvedLegacyLocalPointers = oldRecord.unresolvedLocalPointerCount
            let recordUnresolvedCurrentLocalPointers = newRecord.unresolvedLocalPointerCount
            let recordInvalidLegacyExternalPointers = oldRecord.invalidExternalPointerCount
            let recordInvalidCurrentExternalPointers = newRecord.invalidExternalPointerCount
            var legacyOnlySamples: [String] = []
            var currentOnlySamples: [String] = []
            var pointerChangedSamples: [String] = []

            for key in allKeys {
                let oldEntries = oldByKey[key] ?? []
                let newEntries = newByKey[key] ?? []
                if oldEntries.count == 1 && newEntries.count == 1,
                   let oldEntry = oldEntries.first, let newEntry = newEntries.first {
                    recordMappedKeys += 1
                    if oldEntry.assetFileID != newEntry.assetFileID ||
                        oldEntry.assetPathID != newEntry.assetPathID {
                        recordChangedPointers += 1
                        if pointerChangedSamples.count < 8 { pointerChangedSamples.append(key) }
                    }
                    if oldEntry.preloadIndex != newEntry.preloadIndex ||
                        oldEntry.preloadSize != newEntry.preloadSize {
                        recordChangedRanges += 1
                    }
                } else if oldEntries.isEmpty && newEntries.count == 1 {
                    recordCurrentOnly += 1
                    if currentOnlySamples.count < 8 { currentOnlySamples.append(key) }
                } else if newEntries.isEmpty && oldEntries.count == 1 {
                    recordLegacyOnly += 1
                    if legacyOnlySamples.count < 8 { legacyOnlySamples.append(key) }
                } else {
                    recordAmbiguous += 1
                }
            }

            let oldDependencies = Set(oldRecord.dependencies)
            let newDependencies = Set(newRecord.dependencies)
            let added = newDependencies.subtracting(oldDependencies).sorted()
            let removed = oldDependencies.subtracting(newDependencies).sorted()
            dependenciesAdded += added.count
            dependenciesRemoved += removed.count
            invalidLegacyPreloadRanges += recordInvalidLegacyRanges
            invalidCurrentPreloadRanges += recordInvalidCurrentRanges
            if recordMainAssetPointerChanged { changedMainAssetPointers += 1 }
            if recordClassCompatibilityChanged { changedClassCompatibility += 1 }
            unresolvedLegacyLocalPointers += recordUnresolvedLegacyLocalPointers
            unresolvedCurrentLocalPointers += recordUnresolvedCurrentLocalPointers
            invalidLegacyExternalPointers += recordInvalidLegacyExternalPointers
            invalidCurrentExternalPointers += recordInvalidCurrentExternalPointers
            mappedKeys += recordMappedKeys
            legacyOnlyKeys += recordLegacyOnly
            currentOnlyKeys += recordCurrentOnly
            ambiguousKeys += recordAmbiguous
            changedPointers += recordChangedPointers
            changedPreloadRanges += recordChangedRanges

            findings.append(
                AoVRemakeCatalogRecordFinding(
                    sourceArchive: oldRecord.sourceArchive,
                    kind: kind,
                    assetBundleName: oldRecord.assetBundleName.isEmpty ? oldRecord.name : oldRecord.assetBundleName,
                    status: status,
                    legacyContainerEntryCount: oldRecord.containerEntries.count,
                    currentContainerEntryCount: newRecord.containerEntries.count,
                    mappedContainerKeyCount: recordMappedKeys,
                    legacyOnlyContainerKeyCount: recordLegacyOnly,
                    currentOnlyContainerKeyCount: recordCurrentOnly,
                    ambiguousContainerKeyCount: recordAmbiguous,
                    changedAssetPointerCount: recordChangedPointers,
                    changedPreloadRangeCount: recordChangedRanges,
                    invalidLegacyPreloadRangeCount: recordInvalidLegacyRanges,
                    invalidCurrentPreloadRangeCount: recordInvalidCurrentRanges,
                    mainAssetPointerChanged: recordMainAssetPointerChanged,
                    classCompatibilityChanged: recordClassCompatibilityChanged,
                    unresolvedLegacyLocalPointerCount: recordUnresolvedLegacyLocalPointers,
                    unresolvedCurrentLocalPointerCount: recordUnresolvedCurrentLocalPointers,
                    invalidLegacyExternalPointerCount: recordInvalidLegacyExternalPointers,
                    invalidCurrentExternalPointerCount: recordInvalidCurrentExternalPointers,
                    legacyPointerAuditSamples: oldRecord.pointerAuditSamples,
                    currentPointerAuditSamples: newRecord.pointerAuditSamples,
                    dependenciesAddedInCurrent: Array(added.prefix(12)),
                    dependenciesRemovedInCurrent: Array(removed.prefix(12)),
                    legacyOnlyKeySamples: legacyOnlySamples,
                    currentOnlyKeySamples: currentOnlySamples,
                    changedPointerKeySamples: pointerChangedSamples
                )
            )
        }

        let currentOnlyRecordCount = current.filter { !seenLegacyNames.contains($0.matchingName) }.count
        findings.sort {
            if $0.status.rawValue != $1.status.rawValue {
                return $0.status.rawValue < $1.status.rawValue
            }
            if $0.sourceArchive != $1.sourceArchive {
                return $0.sourceArchive < $1.sourceArchive
            }
            return $0.assetBundleName < $1.assetBundleName
        }

        return AoVRemakeCatalogKindReport(
            kind: kind,
            currentRecordCount: current.count,
            legacyRecordCount: legacy.count,
            mappedRecordCount: matchedRecordCount,
            missingCurrentRecordCount: missingRecordCount,
            ambiguousRecordCount: ambiguousRecordCount,
            currentOnlyRecordCount: currentOnlyRecordCount,
            mappedContainerKeyCount: mappedKeys,
            legacyOnlyContainerKeyCount: legacyOnlyKeys,
            currentOnlyContainerKeyCount: currentOnlyKeys,
            ambiguousContainerKeyCount: ambiguousKeys,
            changedAssetPointerCount: changedPointers,
            changedPreloadRangeCount: changedPreloadRanges,
            invalidLegacyPreloadRangeCount: invalidLegacyPreloadRanges,
            invalidCurrentPreloadRangeCount: invalidCurrentPreloadRanges,
            changedMainAssetPointerCount: changedMainAssetPointers,
            changedClassCompatibilityCount: changedClassCompatibility,
            unresolvedLegacyLocalPointerCount: unresolvedLegacyLocalPointers,
            unresolvedCurrentLocalPointerCount: unresolvedCurrentLocalPointers,
            invalidLegacyExternalPointerCount: invalidLegacyExternalPointers,
            invalidCurrentExternalPointerCount: invalidCurrentExternalPointers,
            dependenciesAddedInCurrentCount: dependenciesAdded,
            dependenciesRemovedInCurrentCount: dependenciesRemoved,
            findings: Array(findings.prefix(maximumCatalogFindings))
        )
    }

    private static func relabel(_ inventory: ArchiveInventory, as name: String) -> ArchiveInventory {
        ArchiveInventory(
            id: inventory.id,
            fileName: name,
            fileSize: inventory.fileSize,
            fileCount: inventory.fileCount,
            uncompressedBytes: inventory.uncompressedBytes,
            paths: inventory.paths,
            pathDigests: inventory.pathDigests.map {
                ArchivePathDigest(
                    archiveName: name,
                    path: $0.path,
                    sha256: $0.sha256,
                    uncompressedBytes: $0.uncompressedBytes
                )
            },
            unityFSBundles: inventory.unityFSBundles,
            unityFSUnrecognizedCount: inventory.unityFSUnrecognizedCount,
            unityFSTooLargeCount: inventory.unityFSTooLargeCount,
            unityFSInspectionFailures: inventory.unityFSInspectionFailures
        )
    }

    private static func resolveResourceArchive(_ inputURL: URL, tempRoot: URL) throws -> URL {
        let archive: Archive
        do {
            archive = try Archive(url: inputURL, accessMode: .read)
        } catch {
            throw AoVModRemakeError.invalidArchive(inputURL.lastPathComponent)
        }
        let resources = archive.filter {
            $0.type == .file &&
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("Resources.zip") == .orderedSame
        }
        guard resources.count <= 1 else {
            throw AoVModRemakeError.multipleResourceArchives(inputURL.lastPathComponent)
        }
        guard let nested = resources.first else { return inputURL }
        guard nested.uncompressedSize <= maximumNestedResourcesBytes else {
            throw AoVModRemakeError.resourceArchiveTooLarge(inputURL.lastPathComponent)
        }
        let destination = tempRoot.appendingPathComponent("resources-\(UUID().uuidString).zip")
        do {
            _ = try archive.extract(nested, to: destination, bufferSize: 64 * 1024)
            _ = try Archive(url: destination, accessMode: .read)
        } catch {
            try? FileManager.default.removeItem(at: destination)
            throw AoVModRemakeError.invalidArchive("\(inputURL.lastPathComponent)/Resources.zip")
        }
        return destination
    }

    private static func readVersionMarker(from url: URL) throws -> String? {
        let archive: Archive
        do {
            archive = try Archive(url: url, accessMode: .read)
        } catch {
            throw AoVModRemakeError.invalidArchive(url.lastPathComponent)
        }
        let candidates = archive.filter {
            $0.type == .file &&
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("version.txt") == .orderedSame
        }.sorted { $0.path < $1.path }
        guard let entry = candidates.first else { return nil }
        guard entry.uncompressedSize <= maximumVersionMarkerBytes else {
            throw AoVModRemakeError.versionMarkerTooLarge(entry.path)
        }
        var data = Data()
        _ = try archive.extract(entry, bufferSize: 16 * 1024, consumer: { data.append($0) })
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        let line = text
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .first { !$0.isEmpty }
        guard let line, line.count <= 128 else { return nil }
        return line
    }
}
