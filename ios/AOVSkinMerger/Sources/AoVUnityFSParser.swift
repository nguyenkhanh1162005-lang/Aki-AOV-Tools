import Foundation

struct AoVUnityFSBlock: Codable, Hashable, Sendable, Identifiable {
    let index: Int
    let uncompressedSize: UInt32
    let compressedSize: UInt32
    let flags: UInt16
    let compression: String
    var id: Int { index }
}

struct AoVUnityFSNode: Codable, Hashable, Sendable, Identifiable {
    let offset: UInt64
    let size: UInt64
    let flags: UInt32
    let path: String
    var id: String { path }
}

struct AoVUnityFSBundleInfo: Codable, Sendable {
    let formatVersion: UInt32
    let playerVersion: String
    let engineVersion: String
    let totalFileSize: UInt64
    let flags: UInt32
    let blockInfoCompression: String
    let blockInfoAtEnd: Bool
    let blockPaddingAtStart: Bool
    let blocks: [AoVUnityFSBlock]
    let nodes: [AoVUnityFSNode]
    let totalUncompressedBytes: UInt64
    let totalCompressedBlockBytes: UInt64
    let availableBlockDataBytes: UInt64
}

struct AoVUnityFSScanResult: Codable, Sendable, Identifiable {
    let archiveName: String
    let path: String
    let formatVersion: UInt32
    let playerVersion: String
    let engineVersion: String
    let flags: UInt32
    let blockInfoCompression: String
    let blockInfoAtEnd: Bool
    let blockCount: Int
    let nodeCount: Int
    let totalUncompressedBytes: UInt64
    let totalCompressedBlockBytes: UInt64
    let nodePathSamples: [String]

    var id: String { "\(archiveName)|\(path)" }

    init(archiveName: String, path: String, info: AoVUnityFSBundleInfo) {
        self.archiveName = archiveName
        self.path = path
        formatVersion = info.formatVersion
        playerVersion = info.playerVersion
        engineVersion = info.engineVersion
        flags = info.flags
        blockInfoCompression = info.blockInfoCompression
        blockInfoAtEnd = info.blockInfoAtEnd
        blockCount = info.blocks.count
        nodeCount = info.nodes.count
        totalUncompressedBytes = info.totalUncompressedBytes
        totalCompressedBlockBytes = info.totalCompressedBlockBytes
        nodePathSamples = Array(info.nodes.prefix(5).map(\.path))
    }
}

enum AoVUnityFSError: LocalizedError {
    case notUnityFS
    case truncated(String)
    case invalidHeader(String)
    case fileSizeMismatch(expected: UInt64, actual: Int)
    case unsupportedEncryption(UInt32)
    case ambiguousLegacyFlag
    case unsupportedInfoCompression(UInt32)
    case invalidCompression(UInt32)
    case decompressionFailed(String)
    case invalidBlockTable(String)
    case invalidDirectory(String)
    case resourceLimit(String)

    var errorDescription: String? {
        switch self {
        case .notUnityFS:
            return "Không phải container UnityFS."
        case .truncated(let field):
            return "UnityFS bị cắt cụt tại trường \(field)."
        case .invalidHeader(let reason):
            return "Header UnityFS không hợp lệ: \(reason)"
        case .fileSizeMismatch(let expected, let actual):
            return "Kích thước UnityFS khai báo \(expected) byte nhưng thực tế \(actual) byte."
        case .unsupportedEncryption(let flags):
            return String(format: "UnityFS có cờ mã hóa chưa hỗ trợ (flags 0x%08X).", flags)
        case .ambiguousLegacyFlag:
            return "UnityFS không có phiên bản Unity rõ ràng và dùng cờ 0x200; chưa thể phân biệt padding với mã hóa."
        case .unsupportedInfoCompression(let method):
            return "Bảng metadata UnityFS dùng kiểu nén \(method), chưa có bộ giải nén an toàn."
        case .invalidCompression(let method):
            return "UnityFS có mã nén không hợp lệ: \(method)."
        case .decompressionFailed(let reason):
            return "Không giải nén được metadata UnityFS: \(reason)"
        case .invalidBlockTable(let reason):
            return "Block table UnityFS không hợp lệ: \(reason)"
        case .invalidDirectory(let reason):
            return "Directory table UnityFS không hợp lệ: \(reason)"
        case .resourceLimit(let reason):
            return "UnityFS vượt giới hạn kiểm tra: \(reason)"
        }
    }
}

private struct AoVUnityFSReader {
    let data: Data
    var offset: Int = 0

    mutating func readByte(_ field: String) throws -> UInt8 {
        guard offset >= 0, offset < data.count else {
            throw AoVUnityFSError.truncated(field)
        }
        let value = data[offset]
        offset += 1
        return value
    }

    mutating func readBytes(_ count: Int, _ field: String) throws -> Data {
        guard count >= 0, offset >= 0, count <= data.count - offset else {
            throw AoVUnityFSError.truncated(field)
        }
        let result = data.subdata(in: offset..<(offset + count))
        offset += count
        return result
    }

    mutating func readUInt16BE(_ field: String) throws -> UInt16 {
        let a = UInt16(try readByte(field))
        let b = UInt16(try readByte(field))
        return (a << 8) | b
    }

    mutating func readUInt32BE(_ field: String) throws -> UInt32 {
        let a = UInt32(try readByte(field))
        let b = UInt32(try readByte(field))
        let c = UInt32(try readByte(field))
        let d = UInt32(try readByte(field))
        return (a << 24) | (b << 16) | (c << 8) | d
    }

    mutating func readInt32BE(_ field: String) throws -> Int32 {
        Int32(bitPattern: try readUInt32BE(field))
    }

    mutating func readUInt64BE(_ field: String) throws -> UInt64 {
        let high = UInt64(try readUInt32BE(field))
        let low = UInt64(try readUInt32BE(field))
        return (high << 32) | low
    }

    mutating func readInt64BE(_ field: String) throws -> Int64 {
        Int64(bitPattern: try readUInt64BE(field))
    }

    mutating func readCString(_ field: String, maximumBytes: Int = 4_096) throws -> String {
        guard maximumBytes > 0, offset >= 0, offset < data.count else {
            throw AoVUnityFSError.truncated(field)
        }
        let start = offset
        let endLimit = min(data.count, start + maximumBytes + 1)
        while offset < endLimit {
            if data[offset] == 0 {
                let value = data.subdata(in: start..<offset)
                offset += 1
                guard let string = String(data: value, encoding: .utf8) else {
                    throw AoVUnityFSError.invalidHeader("\(field) không phải UTF-8.")
                }
                return string
            }
            offset += 1
        }
        throw AoVUnityFSError.truncated("\(field) (thiếu byte kết thúc hoặc quá dài)")
    }

    mutating func align(_ boundary: Int) throws {
        guard boundary > 0, boundary.nonzeroBitCount == 1 else {
            throw AoVUnityFSError.invalidHeader("boundary căn chỉnh không hợp lệ.")
        }
        let remainder = offset % boundary
        if remainder != 0 {
            let padding = boundary - remainder
            guard padding <= data.count - offset else {
                throw AoVUnityFSError.truncated("padding căn chỉnh")
            }
            offset += padding
        }
    }
}

private struct AoVUnityEngineVersion {
    let major: Int
    let minor: Int
    let patch: Int

    init?(_ value: String) {
        let components = value.split(separator: ".", omittingEmptySubsequences: false)
        guard components.count >= 3 else { return nil }
        var parsed: [Int] = []
        for component in components.prefix(3) {
            let digits = component.prefix(while: { $0.isNumber })
            guard !digits.isEmpty, let number = Int(digits) else { return nil }
            parsed.append(number)
        }
        major = parsed[0]
        minor = parsed[1]
        patch = parsed[2]
    }

    var needsHeaderAlignment: Bool {
        major == 2019 && minor == 4 && patch >= 15
    }

    // UnityPy's version gate distinguishes the older UnityCN encryption bit
    // from the later BlockInfoNeedPaddingAtStart bit.
    var usesLegacyEncryptionFlag: Bool {
        if major < 2020 { return true }
        if major == 2020 {
            return minor < 3 || (minor == 3 && patch < 34)
        }
        if major == 2021 { return minor < 3 || (minor == 3 && patch < 2) }
        if major == 2022 { return minor < 1 || (minor == 1 && patch < 1) }
        return false
    }
}

enum AoVUnityFSParser {
    private static let maximumBundleBytes = 512 * 1024 * 1024
    private static let maximumBlockInfoBytes = 64 * 1024 * 1024
    private static let maximumBlocks = 4_096
    private static let maximumNodes = 100_000

    static func inspect(_ data: Data) throws -> AoVUnityFSBundleInfo {
        guard data.count >= 32 else {
            throw AoVUnityFSError.truncated("header")
        }
        guard data.count <= maximumBundleBytes else {
            throw AoVUnityFSError.resourceLimit("bundle lớn hơn 512 MiB.")
        }

        var header = AoVUnityFSReader(data: data)
        let signature = try header.readCString("signature", maximumBytes: 16)
        guard signature == "UnityFS" else { throw AoVUnityFSError.notUnityFS }

        let formatVersion = try header.readUInt32BE("format version")
        guard (6...10).contains(formatVersion) else {
            throw AoVUnityFSError.invalidHeader("format version \(formatVersion) ngoài dải hỗ trợ 6–10.")
        }
        let playerVersion = try header.readCString("player version")
        let engineVersion = try header.readCString("engine version")
        let declaredFileSize = try header.readUInt64BE("total file size")
        let compressedInfoSize = try header.readUInt32BE("compressed block-info size")
        let uncompressedInfoSize = try header.readUInt32BE("uncompressed block-info size")
        let flags = try header.readUInt32BE("archive flags")

        guard declaredFileSize == UInt64(data.count) else {
            throw AoVUnityFSError.fileSizeMismatch(expected: declaredFileSize, actual: data.count)
        }
        guard compressedInfoSize > 0, uncompressedInfoSize > 0,
              compressedInfoSize <= maximumBlockInfoBytes,
              uncompressedInfoSize <= maximumBlockInfoBytes else {
            throw AoVUnityFSError.resourceLimit("kích thước block-info bằng 0 hoặc vượt 64 MiB.")
        }
        guard flags & 0x40 != 0 else {
            throw AoVUnityFSError.invalidHeader("thiếu cờ directory info kết hợp (0x40).")
        }

        let engine = AoVUnityEngineVersion(engineVersion)
        let headerNeedsAlignment = formatVersion >= 7 || (engine?.needsHeaderAlignment ?? false)
        if headerNeedsAlignment {
            try header.align(16)
        }
        let headerEnd = header.offset

        let usesLegacyFlags = engine?.usesLegacyEncryptionFlag
        if flags & 0x400 != 0 || flags & 0x1000 != 0 {
            throw AoVUnityFSError.unsupportedEncryption(flags)
        }
        if flags & 0x200 != 0 {
            if usesLegacyFlags == true {
                throw AoVUnityFSError.unsupportedEncryption(flags)
            }
            if usesLegacyFlags == nil {
                throw AoVUnityFSError.ambiguousLegacyFlag
            }
        }

        let blockInfoAtEnd = flags & 0x80 != 0
        let blockPaddingAtStart = flags & 0x200 != 0
        let compressedLength = Int(compressedInfoSize)
        let uncompressedLength = Int(uncompressedInfoSize)

        let infoOffset: Int
        let dataStart: Int
        let dataEnd: Int
        if blockInfoAtEnd {
            guard compressedLength <= data.count - headerEnd else {
                throw AoVUnityFSError.truncated("block-info ở cuối file")
            }
            infoOffset = data.count - compressedLength
            guard infoOffset >= headerEnd else {
                throw AoVUnityFSError.invalidHeader("block-info chồng lên header.")
            }
            var dataReader = AoVUnityFSReader(data: data, offset: headerEnd)
            if blockPaddingAtStart { try dataReader.align(16) }
            dataStart = dataReader.offset
            dataEnd = infoOffset
        } else {
            infoOffset = headerEnd
            guard compressedLength <= data.count - infoOffset else {
                throw AoVUnityFSError.truncated("block-info ngay sau header")
            }
            var dataReader = AoVUnityFSReader(data: data, offset: infoOffset + compressedLength)
            if blockPaddingAtStart { try dataReader.align(16) }
            dataStart = dataReader.offset
            dataEnd = data.count
        }

        guard infoOffset >= 0, compressedLength <= data.count - infoOffset else {
            throw AoVUnityFSError.truncated("block-info payload")
        }
        let compressedInfo = data.subdata(in: infoOffset..<(infoOffset + compressedLength))
        let infoCompressionCode = flags & 0x3F
        let infoCompression = try decompressBlockInfo(
            compressedInfo,
            expectedSize: uncompressedLength,
            method: infoCompressionCode
        )

        var reader = AoVUnityFSReader(data: infoCompression)
        _ = try reader.readBytes(16, "uncompressed data hash")
        let rawBlockCount = try reader.readInt32BE("block count")
        guard rawBlockCount >= 0, rawBlockCount <= maximumBlocks else {
            throw AoVUnityFSError.resourceLimit("số block \(rawBlockCount) không hợp lệ hoặc vượt \(maximumBlocks).")
        }

        var blocks: [AoVUnityFSBlock] = []
        blocks.reserveCapacity(Int(rawBlockCount))
        var totalUncompressed: UInt64 = 0
        var totalCompressed: UInt64 = 0
        for index in 0..<Int(rawBlockCount) {
            let uncompressed = try reader.readUInt32BE("block \(index) uncompressed size")
            let compressed = try reader.readUInt32BE("block \(index) compressed size")
            let blockFlags = try reader.readUInt16BE("block \(index) flags")
            let method = UInt32(blockFlags & 0x3F)
            guard method <= 4 else {
                throw AoVUnityFSError.invalidCompression(method)
            }
            let sumUncompressed = totalUncompressed.addingReportingOverflow(UInt64(uncompressed))
            let sumCompressed = totalCompressed.addingReportingOverflow(UInt64(compressed))
            guard !sumUncompressed.overflow, !sumCompressed.overflow else {
                throw AoVUnityFSError.resourceLimit("tổng kích thước block bị tràn số.")
            }
            totalUncompressed = sumUncompressed.partialValue
            totalCompressed = sumCompressed.partialValue
            blocks.append(
                AoVUnityFSBlock(
                    index: index,
                    uncompressedSize: uncompressed,
                    compressedSize: compressed,
                    flags: blockFlags,
                    compression: compressionName(method)
                )
            )
        }

        let rawNodeCount = try reader.readInt32BE("node count")
        guard rawNodeCount >= 0, rawNodeCount <= maximumNodes else {
            throw AoVUnityFSError.resourceLimit("số node \(rawNodeCount) không hợp lệ hoặc vượt \(maximumNodes).")
        }

        var nodes: [AoVUnityFSNode] = []
        nodes.reserveCapacity(Int(rawNodeCount))
        var seenPaths = Set<String>()
        for index in 0..<Int(rawNodeCount) {
            let rawOffset = try reader.readInt64BE("node \(index) offset")
            let rawSize = try reader.readInt64BE("node \(index) size")
            let nodeFlags = try reader.readUInt32BE("node \(index) flags")
            let path = try reader.readCString("node \(index) path")
            guard rawOffset >= 0, rawSize >= 0 else {
                throw AoVUnityFSError.invalidDirectory("node \(index) có offset/size âm.")
            }
            let nodeOffset = UInt64(rawOffset)
            let nodeSize = UInt64(rawSize)
            let nodeEnd = nodeOffset.addingReportingOverflow(nodeSize)
            guard !nodeEnd.overflow, nodeEnd.partialValue <= totalUncompressed else {
                throw AoVUnityFSError.invalidDirectory("node \(path) nằm ngoài vùng dữ liệu giải nén.")
            }
            guard isSafeRelativePath(path) else {
                throw AoVUnityFSError.invalidDirectory("đường dẫn node không an toàn: \(path)")
            }
            guard seenPaths.insert(path).inserted else {
                throw AoVUnityFSError.invalidDirectory("đường dẫn node bị lặp: \(path)")
            }
            nodes.append(AoVUnityFSNode(
                offset: nodeOffset,
                size: nodeSize,
                flags: nodeFlags,
                path: path
            ))
        }

        guard dataStart >= headerEnd, dataEnd >= dataStart else {
            throw AoVUnityFSError.invalidHeader("vùng dữ liệu block bị đảo hoặc chồng lấn.")
        }
        let availableBlockData = UInt64(dataEnd - dataStart)
        guard totalCompressed <= availableBlockData else {
            throw AoVUnityFSError.invalidBlockTable(
                "các block khai báo \(totalCompressed) byte nén, chỉ có \(availableBlockData) byte trong bundle."
            )
        }

        return AoVUnityFSBundleInfo(
            formatVersion: formatVersion,
            playerVersion: playerVersion,
            engineVersion: engineVersion,
            totalFileSize: declaredFileSize,
            flags: flags,
            blockInfoCompression: compressionName(infoCompressionCode),
            blockInfoAtEnd: blockInfoAtEnd,
            blockPaddingAtStart: blockPaddingAtStart,
            blocks: blocks,
            nodes: nodes,
            totalUncompressedBytes: totalUncompressed,
            totalCompressedBlockBytes: totalCompressed,
            availableBlockDataBytes: availableBlockData
        )
    }

    private static func decompressBlockInfo(
        _ compressed: Data,
        expectedSize: Int,
        method: UInt32
    ) throws -> Data {
        switch method {
        case 0:
            guard compressed.count == expectedSize else {
                throw AoVUnityFSError.decompressionFailed(
                    "metadata không nén có kích thước \(compressed.count), cần \(expectedSize)."
                )
            }
            return compressed
        case 2, 3:
            do {
                return try AoVLZ4Block.decompress(
                    compressed,
                    expectedSize: expectedSize,
                    maximumSize: maximumBlockInfoBytes
                )
            } catch {
                throw AoVUnityFSError.decompressionFailed(error.localizedDescription)
            }
        case 1, 4:
            throw AoVUnityFSError.unsupportedInfoCompression(method)
        default:
            throw AoVUnityFSError.invalidCompression(method)
        }
    }

    private static func compressionName(_ method: UInt32) -> String {
        switch method {
        case 0: return "none"
        case 1: return "LZMA"
        case 2: return "LZ4"
        case 3: return "LZ4HC"
        case 4: return "LZHAM"
        default: return "unknown (\(method))"
        }
    }

    private static func isSafeRelativePath(_ path: String) -> Bool {
        guard !path.isEmpty, !path.hasPrefix("/"), !path.hasPrefix("~") else { return false }
        let normalized = path.replacingOccurrences(of: "\\", with: "/")
        let components = normalized.split(separator: "/", omittingEmptySubsequences: false)
        guard !components.isEmpty,
              !components.contains(where: { $0 == "." || $0 == ".." }) else { return false }
        return !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
    }
}
