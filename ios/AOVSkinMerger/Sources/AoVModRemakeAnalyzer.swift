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
    let dependenciesAddedInCurrentCount: Int
    let dependenciesRemovedInCurrentCount: Int
    let findings: [AoVRemakeCatalogRecordFinding]
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
    let warnings: [String]

    var totalLegacyFiles: Int { legacy.reduce(0) { $0 + $1.fileCount } }
    var totalLegacyBytes: UInt64 { legacy.reduce(0) { $0 &+ $1.uncompressedBytes } }
    var identicalCount: Int { identicalPaths.count }
    var changedCount: Int { changedPaths.count }
    var legacyOnlyCount: Int { legacyOnlyPaths.count }

    /// Analysis is deliberately separate from the existing skin merger.
    /// No output is advertised until catalog remapping and UnityFS rebuilding exist.
    var rebuildAvailable: Bool { false }
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
        if packerCatalogComparison.currentRecordCount == 0 ||
            verificationCatalogComparison.currentRecordCount == 0 {
            warnings.append("Một hoặc cả hai catalog chưa có AssetBundle record class 142 đọc được; cần kiểm tra lại RES đầu vào hoặc layout SerializedFile.")
        }
        warnings.append("Catalog đã được phân tích sâu ở mức AssetBundle class 142, container key, preload range và dependency. Các pointer chỉ là ứng viên ánh xạ, chưa được ghi ngược vào file.")
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
            warnings: warnings
        )
    }

    private static func catalogRecords(
        in inventory: ArchiveInventory,
        kind: AoVRemakeCatalogKind
    ) -> [AoVRemakeCatalogRecordSnapshot] {
        inventory.unityFSBundles
            .filter { ($0.path as NSString).lastPathComponent.lowercased() == kind.fileName }
            .flatMap { bundle in
                bundle.catalogNodeSamples.flatMap { sample in
                    guard let serialized = sample.serializedFile else { return [] }
                    return serialized.assetBundleRecords.map { record in
                        AoVRemakeCatalogRecordSnapshot(
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
                            mainAssetFileID: record.mainAsset.fileID,
                            mainAssetPathID: record.mainAsset.pathID,
                            dependencies: record.dependencies,
                            containerEntries: record.containerEntries.map {
                                AoVRemakeCatalogEntrySnapshot(
                                    key: $0.key,
                                    preloadIndex: $0.preloadIndex,
                                    preloadSize: $0.preloadSize,
                                    assetFileID: $0.asset.fileID,
                                    assetPathID: $0.asset.pathID
                                )
                            }
                        )
                    }
                }
            }
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
