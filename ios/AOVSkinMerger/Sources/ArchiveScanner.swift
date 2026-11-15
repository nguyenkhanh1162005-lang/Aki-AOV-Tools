import Foundation
import ZIPFoundation

struct ArchiveInventory: Identifiable, Sendable {
    let id: UUID
    let fileName: String
    let fileSize: UInt64
    let fileCount: Int
    let uncompressedBytes: UInt64
    let paths: [String]
}

struct ArchiveOverlap: Identifiable, Sendable {
    let path: String
    let sourceNames: [String]
    var id: String { path }
}

struct ArchiveAnalysis: Sendable {
    let archives: [ArchiveInventory]
    let overlappingPaths: [ArchiveOverlap]
    let totalInputBytes: UInt64
    let totalUncompressedBytes: UInt64
}

enum ArchiveScanError: LocalizedError {
    case tooManyEntries(String)
    case entryTooLarge(String, String)
    case archiveTooLarge(String)
    case unsafePath(String, String)
    case symbolicLink(String, String)

    var errorDescription: String? {
        switch self {
        case .tooManyEntries(let name):
            return "\(name): ZIP vượt giới hạn 20.000 mục."
        case .entryTooLarge(let name, let path):
            return "\(name): mục \(path) vượt giới hạn 256 MiB."
        case .archiveTooLarge(let name):
            return "\(name): dữ liệu giải nén khai báo vượt 1 GiB."
        case .unsafePath(let name, let path):
            return "\(name): đường dẫn không an toàn: \(path)"
        case .symbolicLink(let name, let path):
            return "\(name): symbolic link chưa được hỗ trợ: \(path)"
        }
    }
}

enum ArchiveScanner {
    private static let maximumEntries = 20_000
    private static let maximumEntryBytes: UInt64 = 256 * 1024 * 1024
    private static let maximumArchiveExpandedBytes: UInt64 = 1024 * 1024 * 1024

    static func analyze(_ urls: [URL]) throws -> ArchiveAnalysis {
        var inventories: [ArchiveInventory] = []
        var totalInput: UInt64 = 0
        var totalExpanded: UInt64 = 0

        for url in urls {
            let inventory = try scanOne(url)
            inventories.append(inventory)

            let inputSum = totalInput.addingReportingOverflow(inventory.fileSize)
            let expandedSum = totalExpanded.addingReportingOverflow(inventory.uncompressedBytes)
            guard !inputSum.overflow, !expandedSum.overflow else {
                throw ArchiveScanError.archiveTooLarge(url.lastPathComponent)
            }
            totalInput = inputSum.partialValue
            totalExpanded = expandedSum.partialValue
        }

        var owners: [String: Set<String>] = [:]
        for archive in inventories {
            for path in Set(archive.paths) {
                owners[path, default: []].insert(archive.fileName)
            }
        }
        let overlaps = owners
            .filter { $0.value.count > 1 }
            .map { ArchiveOverlap(path: $0.key, sourceNames: $0.value.sorted()) }
            .sorted { $0.path.localizedCaseInsensitiveCompare($1.path) == .orderedAscending }

        return ArchiveAnalysis(
            archives: inventories,
            overlappingPaths: overlaps,
            totalInputBytes: totalInput,
            totalUncompressedBytes: totalExpanded
        )
    }

    private static func scanOne(_ url: URL) throws -> ArchiveInventory {
        let archive = try Archive(url: url, accessMode: .read)
        var paths: [String] = []
        var expandedBytes: UInt64 = 0
        var entryCount = 0

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

            let entryBytes = entry.uncompressedSize
            guard entryBytes <= maximumEntryBytes else {
                throw ArchiveScanError.entryTooLarge(url.lastPathComponent, entry.path)
            }
            let sum = expandedBytes.addingReportingOverflow(entryBytes)
            guard !sum.overflow, sum.partialValue <= maximumArchiveExpandedBytes else {
                throw ArchiveScanError.archiveTooLarge(url.lastPathComponent)
            }
            expandedBytes = sum.partialValue
            paths.append(path)
        }

        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        let fileSize = (attributes[.size] as? NSNumber)?.uint64Value ?? 0
        return ArchiveInventory(
            id: UUID(),
            fileName: url.lastPathComponent,
            fileSize: fileSize,
            fileCount: entryCount,
            uncompressedBytes: expandedBytes,
            paths: paths.sorted()
        )
    }
}

func formatByteCount(_ bytes: UInt64) -> String {
    ByteCountFormatter.string(fromByteCount: Int64(clamping: bytes), countStyle: .file)
}
