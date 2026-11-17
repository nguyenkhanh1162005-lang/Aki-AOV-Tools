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
        for (index, url) in legacyMODURLs.enumerated() {
            let scanURL = try resolveResourceArchive(url, tempRoot: tempRoot)
            legacyScanURLs.append(scanURL)
            _ = index
        }

        // Each side is scanned independently so the RES baseline and legacy MOD
        // collection each retain the scanner's 500 MiB input safety ceiling.
        guard let baseInventory = try ArchiveScanner.analyze([baseScanURL]).archives.first else {
            throw AoVModRemakeError.invalidArchive(baseRESURL.lastPathComponent)
        }
        let scannedLegacy = try ArchiveScanner.analyze(legacyScanURLs).archives
        let baseInventory = relabel(scannedBase, as: baseRESURL.lastPathComponent)
        let legacyInventories = scannedLegacy.enumerated().map { index, inventory in
            relabel(inventory, as: legacyMODURLs[index].lastPathComponent)
        }
        let baseVersion = try readVersionMarker(from: baseScanURL)
        var legacyVersions: [String: String] = [:]
        for index in legacyScanURLs.indices {
            if let version = try readVersionMarker(from: legacyScanURLs[index]) {
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
        warnings.append("Bản phân tích này không tạo ZIP remake. Cần bộ quy tắc catalog/dependency, checksum và UnityFS rebuild đã xác minh trước khi xuất.")
        
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
            warnings: warnings
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

private extension Collection {
    subscript(safe index: Index) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
