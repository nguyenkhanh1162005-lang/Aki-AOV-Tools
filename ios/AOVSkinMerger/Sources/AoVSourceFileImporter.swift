import Foundation

struct AoVSourceImportReport: Sendable {
    let importedURLs: [URL]
    let errors: [String]
    let totalBytes: UInt64
}

enum AoVSourceFileImporterError: LocalizedError {
    case noFiles
    case tooManyFiles(limit: Int)
    case totalTooLarge
    case invalidFileType(String)
    case emptyFile(String)
    case notRegularFile(String)
    case copiedSizeMismatch(String)

    var errorDescription: String? {
        switch self {
        case .noFiles:
            return "Không có file ZIP nào được chọn."
        case .tooManyFiles(let limit):
            return "App nhận tối đa \(limit) ZIP trong một lần ghép. Hãy xóa bớt file rồi chọn lại."
        case .totalTooLarge:
            return "Tổng dung lượng ZIP vượt giới hạn 500 MiB của bản iOS thử nghiệm."
        case .invalidFileType(let name):
            return "\(name): chỉ nhận file có đuôi .zip."
        case .emptyFile(let name):
            return "\(name): file rỗng hoặc chưa tải hoàn tất từ Files/iCloud."
        case .notRegularFile(let name):
            return "\(name): mục được chọn không phải file thông thường."
        case .copiedSizeMismatch(let name):
            return "\(name): dung lượng file sau khi sao chép không khớp file gốc."
        }
    }
}

enum AoVSourceFileImporter {
    static let maximumFiles = 10
    static let maximumTotalBytes: UInt64 = 500 * 1024 * 1024

    static func copy(
        _ sourceURLs: [URL],
        into destinationFolder: URL,
        existingFileCount: Int = 0,
        existingBytes: UInt64 = 0
    ) throws -> AoVSourceImportReport {
        guard !sourceURLs.isEmpty else {
            throw AoVSourceFileImporterError.noFiles
        }
        guard existingFileCount >= 0,
              existingFileCount + sourceURLs.count <= maximumFiles else {
            throw AoVSourceFileImporterError.tooManyFiles(limit: maximumFiles)
        }
        guard existingBytes <= maximumTotalBytes else {
            throw AoVSourceFileImporterError.totalTooLarge
        }

        try FileManager.default.createDirectory(
            at: destinationFolder,
            withIntermediateDirectories: true
        )

        var imported: [URL] = []
        var failures: [String] = []
        var totalBytes = existingBytes

        for source in sourceURLs {
            do {
                let result = try copyOne(
                    source,
                    into: destinationFolder,
                    currentTotalBytes: totalBytes
                )
                imported.append(result.url)
                totalBytes = result.totalBytes
            } catch {
                failures.append("\(source.lastPathComponent): \(error.localizedDescription)")
            }
        }

        return AoVSourceImportReport(
            importedURLs: imported,
            errors: failures,
            totalBytes: totalBytes
        )
    }

    private static func copyOne(
        _ source: URL,
        into folder: URL,
        currentTotalBytes: UInt64
    ) throws -> (url: URL, totalBytes: UInt64) {
        guard source.pathExtension.lowercased() == "zip" else {
            throw AoVSourceFileImporterError.invalidFileType(source.lastPathComponent)
        }

        // Keep the security-scoped URL available until the coordinated copy ends.
        let accessing = source.startAccessingSecurityScopedResource()
        defer {
            if accessing {
                source.stopAccessingSecurityScopedResource()
            }
        }

        let coordinator = NSFileCoordinator(filePresenter: nil)
        var coordinationError: NSError?
        var copyError: Error?
        var copiedResult: (URL, UInt64)?

        coordinator.coordinate(
            readingItemAt: source,
            options: [],
            error: &coordinationError
        ) { coordinatedURL in
            do {
                let values = try coordinatedURL.resourceValues(
                    forKeys: [.isRegularFileKey, .fileSizeKey]
                )
                guard values.isRegularFile != false else {
                    throw AoVSourceFileImporterError.notRegularFile(source.lastPathComponent)
                }

                let sourceSize = UInt64(max(values.fileSize ?? 0, 0))
                guard sourceSize > 0 else {
                    throw AoVSourceFileImporterError.emptyFile(source.lastPathComponent)
                }

                let nextTotal = currentTotalBytes.addingReportingOverflow(sourceSize)
                guard !nextTotal.overflow, nextTotal.partialValue <= maximumTotalBytes else {
                    throw AoVSourceFileImporterError.totalTooLarge
                }

                let stagingURL = folder.appendingPathComponent(
                    ".\(UUID().uuidString).partial",
                    isDirectory: false
                )
                let finalURL = folder.appendingPathComponent(
                    "\(UUID().uuidString)-\(source.lastPathComponent)",
                    isDirectory: false
                )

                do {
                    try FileManager.default.copyItem(at: coordinatedURL, to: stagingURL)
                    let copiedValues = try stagingURL.resourceValues(forKeys: [.fileSizeKey])
                    let copiedSize = UInt64(max(copiedValues.fileSize ?? 0, 0))
                    guard copiedSize == sourceSize else {
                        throw AoVSourceFileImporterError.copiedSizeMismatch(source.lastPathComponent)
                    }
                    try FileManager.default.moveItem(at: stagingURL, to: finalURL)
                    copiedResult = (finalURL, nextTotal.partialValue)
                } catch {
                    try? FileManager.default.removeItem(at: stagingURL)
                    throw error
                }
            } catch {
                copyError = error
            }
        }

        if let coordinationError {
            throw coordinationError
        }
        if let copyError {
            throw copyError
        }
        guard let copiedResult else {
            throw AoVSourceFileImporterError.emptyFile(source.lastPathComponent)
        }
        return copiedResult
    }
}
