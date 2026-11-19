import Foundation
import ZIPFoundation

struct AoVNoticeBrandingRewriteReport: Sendable {
    let outputURL: URL
    let inputBytes: UInt64
    let outputBytes: UInt64
    let replacementTotal: Int
    let replacementCounts: [String: Int]
    let modifiedResources: [String]
    let rewrittenAoVWrappers: Int
    let rewrittenNestedZIPs: Int
    let protectedNoticeResources: [String]
    let structurallyValidated: Bool
    let gameTested: Bool
}

enum AoVNoticeBrandingRewriteError: LocalizedError {
    case invalidInput
    case inputTooLarge
    case invalidArchive(String)
    case unsafePath(String)
    case duplicatePath(String)
    case tooManyEntries
    case expandedTooLarge
    case entryTooLarge(String)
    case unsupportedEntry(String)
    case nestedDepthExceeded
    case wrappedResourceDecodeFailed(String)
    case roundTripFailed(String)
    case noMatchingText
    case outputAlreadyExists

    var errorDescription: String? {
        switch self {
        case .invalidInput:
            return "Không tìm thấy ZIP kết quả để xử lý thông báo."
        case .inputTooLarge:
            return "ZIP kết quả phải lớn hơn 0 và không vượt 500 MiB."
        case .invalidArchive(let name):
            return "\(name): ZIP không hợp lệ hoặc CRC nguồn bị lỗi."
        case .unsafePath(let path):
            return "ZIP chứa đường dẫn không an toàn: \(path)"
        case .duplicatePath(let path):
            return "ZIP có đường dẫn trùng sau chuẩn hóa: \(path)"
        case .tooManyEntries:
            return "ZIP lồng có quá nhiều mục."
        case .expandedTooLarge:
            return "Tổng dữ liệu ZIP giải nén vượt giới hạn 1 GiB."
        case .entryTooLarge(let path):
            return "\(path): entry vượt giới hạn đọc 256 MiB."
        case .unsupportedEntry(let path):
            return "\(path): không hỗ trợ symbolic link hoặc entry đặc biệt."
        case .nestedDepthExceeded:
            return "ZIP lồng vượt giới hạn 3 tầng."
        case .wrappedResourceDecodeFailed(let path):
            return "\(path): wrapper AoV nhận diện được nhưng không giải mã được."
        case .roundTripFailed(let path):
            return "\(path): dữ liệu AoV sau khi mã hóa lại không khớp."
        case .noMatchingText:
            return "Không tìm thấy chuỗi thương hiệu cần đổi trong tài nguyên văn bản AoV đã giải mã được. AssetBundle, MSES, IFix, Wwise và dữ liệu nhị phân opaque không bị sửa."
        case .outputAlreadyExists:
            return "File đầu ra đã tồn tại. Hãy tạo tên đầu ra khác để không ghi đè file cũ."
        }
    }
}

/// Rewrites only exact AKI branding strings in editable UTF text and supported
/// AoV AES/Zstandard wrappers. Unity AssetBundles, MSES, IFix, Wwise and opaque
/// binary resources are never edited. Kill-notification and billboard resources
/// are explicitly protected even if they contain matching text.
enum AoVNoticeBrandingRewriter {
    private static let maximumInputBytes: UInt64 = 500 * 1024 * 1024
    private static let maximumEntries = 20_000
    private static let maximumExpandedBytes: UInt64 = 1024 * 1024 * 1024
    private static let maximumEntryBytes: UInt64 = 256 * 1024 * 1024
    private static let maximumNestedZIPDepth = 3
    private static let textExtensions: Set<String> = [
        "lua", "txt", "xml", "json", "json5", "ini", "cfg", "csv", "md", "yaml", "yml"
    ]
    private static let protectedPathMarkers = [
        "commercializenotify",
        "reskillbillboardcfg",
        "resbillboardcfg",
        "resbillboardskincfg",
        "killnotify",
        "kill_notification"
    ]
    private static let replacements: [(String, String)] = [
        ("Mod được tạo bởi youtuber KIANA MOD AOV", "Mod được gộp bởi AKI MOD AOV"),
        ("KIANA MOD AOV", "AKI MOD AOV"),
        ("Web Mod", ""),
        ("Youtube", "")
    ]

    private struct RewriteState {
        var entryCount = 0
        var expandedBytes: UInt64 = 0
        var replacementTotal = 0
        var replacementCounts: [String: Int] = [:]
        var modifiedResources: [String] = []
        var rewrittenAoVWrappers = 0
        var rewrittenNestedZIPs = 0
        var protectedNoticeResources: [String] = []
    }

    static func rewriteArchive(inputURL: URL, outputURL: URL) throws -> AoVNoticeBrandingRewriteReport {
        guard FileManager.default.fileExists(atPath: inputURL.path) else {
            throw AoVNoticeBrandingRewriteError.invalidInput
        }
        let inputSize = (try? FileManager.default.attributesOfItem(atPath: inputURL.path)[.size] as? NSNumber)?
            .uint64Value ?? 0
        guard inputSize > 0, inputSize <= maximumInputBytes else {
            throw AoVNoticeBrandingRewriteError.inputTooLarge
        }
        guard !FileManager.default.fileExists(atPath: outputURL.path) else {
            throw AoVNoticeBrandingRewriteError.outputAlreadyExists
        }

        let parent = outputURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        let tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-NOTICE-REWRITE-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempRoot) }

        let stagedOutput = tempRoot.appendingPathComponent("rewritten.zip")
        var state = RewriteState()
        try rewriteZIP(
            inputURL: inputURL,
            outputURL: stagedOutput,
            archiveLabel: inputURL.lastPathComponent,
            depth: 0,
            tempRoot: tempRoot,
            state: &state
        )
        guard state.replacementTotal > 0 else {
            throw AoVNoticeBrandingRewriteError.noMatchingText
        }

        let outputSize = (try? FileManager.default.attributesOfItem(atPath: stagedOutput.path)[.size] as? NSNumber)?
            .uint64Value ?? 0
        guard outputSize > 0 else {
            throw AoVNoticeBrandingRewriteError.invalidArchive(outputURL.lastPathComponent)
        }
        try FileManager.default.moveItem(at: stagedOutput, to: outputURL)

        return AoVNoticeBrandingRewriteReport(
            outputURL: outputURL,
            inputBytes: inputSize,
            outputBytes: outputSize,
            replacementTotal: state.replacementTotal,
            replacementCounts: state.replacementCounts,
            modifiedResources: state.modifiedResources,
            rewrittenAoVWrappers: state.rewrittenAoVWrappers,
            rewrittenNestedZIPs: state.rewrittenNestedZIPs,
            protectedNoticeResources: state.protectedNoticeResources,
            structurallyValidated: true,
            gameTested: false
        )
    }

    private static func rewriteZIP(
        inputURL: URL,
        outputURL: URL,
        archiveLabel: String,
        depth: Int,
        tempRoot: URL,
        state: inout RewriteState
    ) throws {
        guard depth <= maximumNestedZIPDepth else {
            throw AoVNoticeBrandingRewriteError.nestedDepthExceeded
        }
        let source: Archive
        do {
            source = try Archive(url: inputURL, accessMode: .read)
        } catch {
            throw AoVNoticeBrandingRewriteError.invalidArchive(archiveLabel)
        }

        let destination: Archive
        do {
            destination = try Archive(url: outputURL, accessMode: .create)
        } catch {
            throw AoVNoticeBrandingRewriteError.invalidArchive(archiveLabel)
        }

        var seen = Set<String>()
        var expectedPaths: [String] = []
        for entry in source {
            state.entryCount += 1
            guard state.entryCount <= maximumEntries else {
                throw AoVNoticeBrandingRewriteError.tooManyEntries
            }
            let canonical = try safePath(entry.path)
            guard seen.insert(canonical).inserted else {
                throw AoVNoticeBrandingRewriteError.duplicatePath(canonical)
            }
            guard entry.type == .file else {
                if entry.type == .directory { continue }
                throw AoVNoticeBrandingRewriteError.unsupportedEntry(canonical)
            }
            guard entry.uncompressedSize <= maximumEntryBytes else {
                throw AoVNoticeBrandingRewriteError.entryTooLarge(canonical)
            }
            let sum = state.expandedBytes.addingReportingOverflow(entry.uncompressedSize)
            guard !sum.overflow, sum.partialValue <= maximumExpandedBytes else {
                throw AoVNoticeBrandingRewriteError.expandedTooLarge
            }
            state.expandedBytes = sum.partialValue
            expectedPaths.append(entry.path)

            let rawURL = tempRoot.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: rawURL) }
            do {
                _ = try source.extract(entry, to: rawURL, bufferSize: 64 * 1024)
            } catch {
                throw AoVNoticeBrandingRewriteError.invalidArchive("\(archiveLabel)!/\(entry.path)")
            }

            let logicalPath = "\(archiveLabel)!/\(entry.path)"
            let rewrittenURL = try processFile(
                rawURL,
                memberName: entry.path,
                logicalPath: logicalPath,
                depth: depth,
                tempRoot: tempRoot,
                state: &state
            )
            defer {
                if let rewrittenURL, rewrittenURL != rawURL {
                    try? FileManager.default.removeItem(at: rewrittenURL)
                }
            }
            let payloadURL = rewrittenURL ?? rawURL
            do {
                try destination.addEntry(
                    with: entry.path,
                    fileURL: payloadURL,
                    compressionMethod: storageMethod(for: entry.path),
                    bufferSize: 64 * 1024
                )
            } catch {
                throw AoVNoticeBrandingRewriteError.invalidArchive(
                    "Không ghi được \(archiveLabel)!/\(entry.path): \(error.localizedDescription)"
                )
            }
        }

        try verifyZIP(outputURL, expectedPaths: expectedPaths, label: archiveLabel, tempRoot: tempRoot)
    }

    /// Returns a replacement file URL when content changed; nil means preserve
    /// the extracted source file exactly.
    private static func processFile(
        _ fileURL: URL,
        memberName: String,
        logicalPath: String,
        depth: Int,
        tempRoot: URL,
        state: inout RewriteState
    ) throws -> URL? {
        if isProtectedPath(logicalPath) {
            if state.protectedNoticeResources.count < 100 {
                state.protectedNoticeResources.append(logicalPath)
            }
            return nil
        }

        let prefix = try readPrefix(fileURL, count: 8)
        let suffix = URL(fileURLWithPath: memberName).pathExtension.lowercased()

        if AoVBytesDecoder.isAoVWrappedResource(Data(prefix)) {
            let raw = try readData(fileURL, limit: 32 * 1024 * 1024 + 1024 * 1024)
            let decoded: AoVDecodedResource
            do {
                decoded = try AoVBytesDecoder.decodeResource(raw, resourceName: logicalPath)
            } catch {
                throw AoVNoticeBrandingRewriteError.wrappedResourceDecodeFailed(logicalPath)
            }

            var rewrittenPayload = decoded.data
            var changed = false
            if looksLikeZIP(decoded.data) {
                let nestedInput = tempRoot.appendingPathComponent(UUID().uuidString + ".zip")
                let nestedOutput = tempRoot.appendingPathComponent(UUID().uuidString + ".zip")
                try decoded.data.write(to: nestedInput, options: .atomic)
                defer {
                    try? FileManager.default.removeItem(at: nestedInput)
                    try? FileManager.default.removeItem(at: nestedOutput)
                }
                let before = state.replacementTotal
                try rewriteZIP(
                    inputURL: nestedInput,
                    outputURL: nestedOutput,
                    archiveLabel: logicalPath,
                    depth: depth + 1,
                    tempRoot: tempRoot,
                    state: &state
                )
                if state.replacementTotal > before {
                    rewrittenPayload = try readData(nestedOutput, limit: maximumEntryBytes)
                    changed = true
                    state.rewrittenNestedZIPs += 1
                }
            } else if let textResult = rewriteText(decoded.data, resourcePath: logicalPath) {
                rewrittenPayload = textResult.data
                record(textResult, path: logicalPath, state: &state)
                changed = true
            }

            guard changed else { return nil }
            let encoded: Data
            do {
                encoded = try AoVBytesDecoder.encodeResource(
                    rewrittenPayload,
                    resourceName: logicalPath,
                    wrapperMode: decoded.wrapperMode
                )
                let verified = try AoVBytesDecoder.decodeResource(encoded, resourceName: logicalPath)
                guard verified.data == rewrittenPayload,
                      verified.wrapperMode == decoded.wrapperMode else {
                    throw AoVNoticeBrandingRewriteError.roundTripFailed(logicalPath)
                }
            } catch let error as AoVNoticeBrandingRewriteError {
                throw error
            } catch {
                throw AoVNoticeBrandingRewriteError.roundTripFailed(logicalPath)
            }
            let output = tempRoot.appendingPathComponent(UUID().uuidString)
            try encoded.write(to: output, options: .atomic)
            state.rewrittenAoVWrappers += 1
            return output
        }

        if looksLikeZIP(Data(prefix)) {
            let nestedOutput = tempRoot.appendingPathComponent(UUID().uuidString + ".zip")
            defer { try? FileManager.default.removeItem(at: nestedOutput) }
            let before = state.replacementTotal
            do {
                try rewriteZIP(
                    inputURL: fileURL,
                    outputURL: nestedOutput,
                    archiveLabel: logicalPath,
                    depth: depth + 1,
                    tempRoot: tempRoot,
                    state: &state
                )
            } catch {
                if suffix == "zip" { throw error }
                return nil
            }
            if state.replacementTotal > before {
                let copied = tempRoot.appendingPathComponent(UUID().uuidString + ".zip")
                try FileManager.default.copyItem(at: nestedOutput, to: copied)
                state.rewrittenNestedZIPs += 1
                return copied
            }
            return nil
        }

        guard textExtensions.contains(suffix) else { return nil }
        let data = try readData(fileURL, limit: maximumEntryBytes)
        guard let result = rewriteText(data, resourcePath: logicalPath) else { return nil }
        record(result, path: logicalPath, state: &state)
        let output = tempRoot.appendingPathComponent(UUID().uuidString)
        try result.data.write(to: output, options: .atomic)
        return output
    }

    private struct TextRewriteResult {
        let data: Data
        let replacements: [String: Int]
        let encodingName: String
    }

    private static func rewriteText(_ data: Data, resourcePath: String) -> TextRewriteResult? {
        let candidates: [(String.Encoding, String)] = [
            (.utf8, "utf-8"),
            (.utf16LittleEndian, "utf-16-le"),
            (.utf16BigEndian, "utf-16-be")
        ]
        var decoded: String?
        var selectedEncoding: String.Encoding?
        var encodingName = ""
        for (encoding, name) in candidates {
            guard let text = String(data: data, encoding: encoding),
                  !text.isEmpty,
                  !text.unicodeScalars.contains("\0") else { continue }
            let scalars = Array(text.unicodeScalars)
            let printable = scalars.filter {
                !CharacterSet.controlCharacters.contains($0) || $0 == "\r" || $0 == "\n" || $0 == "\t"
            }.count
            guard Double(printable) / Double(max(1, scalars.count)) >= 0.94 else { continue }
            decoded = text
            selectedEncoding = encoding
            encodingName = name
            break
        }
        guard var updated = decoded, let selectedEncoding else { return nil }

        var counts: [String: Int] = [:]
        for (old, new) in replacements {
            let count = updated.components(separatedBy: old).count - 1
            guard count > 0 else { continue }
            updated = updated.replacingOccurrences(of: old, with: new)
            counts[old] = count
        }
        guard !counts.isEmpty,
              let output = updated.data(using: selectedEncoding),
              output.count <= Int(maximumEntryBytes),
              output != data else { return nil }
        return TextRewriteResult(data: output, replacements: counts, encodingName: encodingName)
    }

    private static func record(_ result: TextRewriteResult, path: String, state: inout RewriteState) {
        let count = result.replacements.values.reduce(0, +)
        state.replacementTotal += count
        for (key, value) in result.replacements {
            state.replacementCounts[key, default: 0] += value
        }
        if state.modifiedResources.count < 200 {
            state.modifiedResources.append("\(path) [\(result.encodingName)]")
        }
    }

    private static func verifyZIP(
        _ url: URL,
        expectedPaths: [String],
        label: String,
        tempRoot: URL
    ) throws {
        let archive: Archive
        do {
            archive = try Archive(url: url, accessMode: .read)
        } catch {
            throw AoVNoticeBrandingRewriteError.invalidArchive(label)
        }
        let actualEntries = archive.filter { $0.type == .file }
        guard actualEntries.map(\.path) == expectedPaths else {
            throw AoVNoticeBrandingRewriteError.invalidArchive(
                "\(label): danh sách đường dẫn thay đổi sau khi ghi ZIP."
            )
        }
        for entry in actualEntries {
            let scratch = tempRoot.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: scratch) }
            do {
                _ = try archive.extract(entry, to: scratch, bufferSize: 64 * 1024)
            } catch {
                throw AoVNoticeBrandingRewriteError.invalidArchive(
                    "\(label): CRC đầu ra sai tại \(entry.path)."
                )
            }
        }
    }

    private static func safePath(_ raw: String) throws -> String {
        let normalized = raw.replacingOccurrences(of: "\\", with: "/")
        let candidate = normalized.hasSuffix("/") ? String(normalized.dropLast()) : normalized
        let components = candidate.split(separator: "/", omittingEmptySubsequences: false)
        guard !candidate.isEmpty,
              !candidate.hasPrefix("/"),
              !candidate.hasPrefix("~"),
              !components.contains(""),
              !components.contains("."),
              !components.contains(".."),
              !candidate.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw AoVNoticeBrandingRewriteError.unsafePath(raw)
        }
        return candidate
    }

    private static func isProtectedPath(_ path: String) -> Bool {
        let normalized = path.replacingOccurrences(of: "\\", with: "/").lowercased()
        return protectedPathMarkers.contains(where: normalized.contains)
    }

    private static func looksLikeZIP(_ data: Data) -> Bool {
        data.starts(with: [0x50, 0x4B, 0x03, 0x04]) ||
            data.starts(with: [0x50, 0x4B, 0x05, 0x06]) ||
            data.starts(with: [0x50, 0x4B, 0x07, 0x08])
    }

    private static func readPrefix(_ url: URL, count: Int) throws -> [UInt8] {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        return Array((try handle.read(upToCount: count)) ?? Data())
    }

    private static func readData(_ url: URL, limit: UInt64) throws -> Data {
        let values = try url.resourceValues(forKeys: [.fileSizeKey])
        let size = UInt64(max(values.fileSize ?? 0, 0))
        guard size <= limit else {
            throw AoVNoticeBrandingRewriteError.entryTooLarge(url.lastPathComponent)
        }
        return try Data(contentsOf: url, options: .mappedIfSafe)
    }

    private static func storageMethod(for path: String) -> CompressionMethod {
        let ext = URL(fileURLWithPath: path).pathExtension.lowercased()
        let opaque: Set<String> = [
            "assetbundle", "bytes", "bnk", "wem", "zip", "pck", "obb",
            "png", "jpg", "jpeg", "webp", "mp3", "ogg", "mp4", "zst", "zstd", "lz4", "lzma"
        ]
        return opaque.contains(ext) ? .none : .deflate
    }
}
