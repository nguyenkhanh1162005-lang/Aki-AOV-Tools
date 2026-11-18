import Foundation
import CommonCrypto
import SWCompression

enum AoVUnityFSBlockInfoLayout: String, Codable, Hashable, Sendable {
    case standard
    case arenaOfValor
}

struct AoVUnityFSBlock: Codable, Hashable, Sendable, Identifiable {
    let index: Int
    let uncompressedSize: UInt32
    let compressedSize: UInt32
    let flags: UInt16
    let reservedField: UInt16
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
    let blockInfoLayout: AoVUnityFSBlockInfoLayout
    let blockInfoAtEnd: Bool
    let usesAoVEncryption: Bool
    let blockPaddingAtStart: Bool
    let blocks: [AoVUnityFSBlock]
    let nodes: [AoVUnityFSNode]
    let totalUncompressedBytes: UInt64
    let totalCompressedBlockBytes: UInt64
    let availableBlockDataBytes: UInt64
    let blockDataOffset: UInt64
}

struct AoVUnityFSExtractedNode: Sendable {
    let path: String
    let data: Data
    let flags: UInt32

    init(path: String, data: Data, flags: UInt32 = 0) {
        self.path = path
        self.data = data
        self.flags = flags
    }
}

struct AoVUnityFSCatalogNodeSample: Codable, Sendable, Identifiable {
    let path: String
    let size: UInt64
    let prefixHex: String
    let stringHints: [String]
    let serializedFileSummary: String?
    /// Full bounded SerializedFile metadata, retained for catalog comparison.
    /// This is descriptive data only; it is never written back into a UnityFS bundle.
    let serializedFile: AoVSerializedFileSummary?
    var id: String { path }
}

struct AoVUnityFSScanResult: Codable, Sendable, Identifiable {
    let archiveName: String
    let path: String
    let formatVersion: UInt32
    let playerVersion: String
    let engineVersion: String
    let flags: UInt32
    let blockInfoCompression: String
    let blockInfoLayout: AoVUnityFSBlockInfoLayout
    let blockInfoAtEnd: Bool
    let usesAoVEncryption: Bool
    let blockCount: Int
    let nodeCount: Int
    let totalUncompressedBytes: UInt64
    let totalCompressedBlockBytes: UInt64
    let nodePathSamples: [String]
    let catalogExtractionStatus: String?
    let catalogNodeSamples: [AoVUnityFSCatalogNodeSample]

    var id: String { "\(archiveName)|\(path)" }

    init(
        archiveName: String,
        path: String,
        info: AoVUnityFSBundleInfo,
        catalogExtractionStatus: String? = nil,
        catalogNodeSamples: [AoVUnityFSCatalogNodeSample] = []
    ) {
        self.archiveName = archiveName
        self.path = path
        formatVersion = info.formatVersion
        playerVersion = info.playerVersion
        engineVersion = info.engineVersion
        flags = info.flags
        blockInfoCompression = info.blockInfoCompression
        blockInfoLayout = info.blockInfoLayout
        blockInfoAtEnd = info.blockInfoAtEnd
        usesAoVEncryption = info.usesAoVEncryption
        blockCount = info.blocks.count
        nodeCount = info.nodes.count
        totalUncompressedBytes = info.totalUncompressedBytes
        totalCompressedBlockBytes = info.totalCompressedBlockBytes
        nodePathSamples = Array(info.nodes.prefix(5).map(\.path))
        self.catalogExtractionStatus = catalogExtractionStatus
        self.catalogNodeSamples = catalogNodeSamples
    }
}

enum AoVUnityFSError: LocalizedError {
    case notUnityFS
    case truncated(String)
    case invalidHeader(String)
    case fileSizeMismatch(expected: UInt64, actual: Int)
    case unsupportedEncryption(UInt32)
    case ambiguousLegacyFlag
    case ambiguousBlockInfoLayout
    case decryptionFailed(String)
    case unsupportedInfoCompression(UInt32)
    case unsupportedBlockCompression(UInt32)
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
        case .ambiguousBlockInfoLayout:
            return "Bảng block UnityFS khớp nhiều layout khác nhau; không thể tự chọn kiểu giải mã an toàn."
        case .decryptionFailed(let reason):
            return "Không giải mã được lớp bảo vệ AOV: \(reason)"
        case .unsupportedInfoCompression(let method):
            return "Bảng metadata UnityFS dùng kiểu nén \(method), chưa có bộ giải nén an toàn."
        case .unsupportedBlockCompression(let method):
            return "Dữ liệu block UnityFS dùng kiểu nén \(method), chưa hỗ trợ (Aki hiện giải nén none/LZMA1/LZ4/LZ4HC).";
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

private struct AoVUnityFSParsedBlockTable: Equatable {
    let blocks: [AoVUnityFSBlock]
    let nodes: [AoVUnityFSNode]
    let totalUncompressedBytes: UInt64
    let totalCompressedBlockBytes: UInt64
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
    private static let maximumLZMADictionaryBytes = 128 * 1024 * 1024
    private static let maximumBlocks = 4_096
    private static let maximumNodes = 100_000
    // AOV's public UnityPy fork documents these UnityFS header AES and block-info
    // SM4 parameters. Header encryption is used only when the version-specific
    // archive flag says so; ordinary UnityFS headers are never sent through AES.
    private static let aovHeaderAESKey = Data([
        0xE3, 0x05, 0x62, 0x14, 0xD6, 0x0A, 0x20, 0x25,
        0x36, 0x96, 0x1B, 0x07, 0x74, 0xDC, 0x24, 0x02
    ])
    private static let aovHeaderAESIV = Data([
        0x1D, 0x6E, 0xEB, 0x4C, 0x86, 0xA9, 0x45, 0x44,
        0x45, 0x72, 0x12, 0x21, 0x2B, 0x43, 0x25, 0x2F
    ])
    private static let aovBlockSM4Key = Data([
        0x79, 0x7B, 0xCD, 0x5D, 0x7D, 0x7B, 0xB1, 0x11,
        0x43, 0xD0, 0x0D, 0x71, 0x3C, 0xDA, 0xA8, 0x08
    ])
    private static let aovBlockSM4IV = aovBlockSM4Key

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
        let rawHeaderFields = try header.readBytes(16, "UnityFS size header")
        let flags = try header.readUInt32BE("archive flags")

        let parsedEngine = AoVUnityEngineVersion(engineVersion)
        let engine = parsedEngine.flatMap { $0.major == 0 ? nil : $0 }
        let usesLegacyFlags = engine?.usesLegacyEncryptionFlag
        if flags & 0x1000 != 0 {
            throw AoVUnityFSError.unsupportedEncryption(flags)
        }
        let usesAoVEncryption: Bool
        if usesLegacyFlags == true {
            // On pre-gate Unity versions 0x200 is the legacy UnityCN
            // encryption flag, not the newer AoV header/block-info scheme.
            // Its payload format is not implemented here, so fail closed.
            guard flags & 0x200 == 0 else {
                throw AoVUnityFSError.unsupportedEncryption(flags)
            }
            usesAoVEncryption = false
        } else if usesLegacyFlags == false {
            usesAoVEncryption = flags & 0x400 != 0
        } else if flags & 0x600 != 0 {
            throw AoVUnityFSError.ambiguousLegacyFlag
        } else {
            usesAoVEncryption = false
        }

        let declaredFileSize: UInt64
        let compressedInfoSize: UInt32
        let uncompressedInfoSize: UInt32
        if usesAoVEncryption {
            let decrypted = try decryptAoVHeader(rawHeaderFields)
            let bytes = Array(decrypted)
            declaredFileSize = readLittleEndianUInt64(bytes, at: 0)
            compressedInfoSize = readLittleEndianUInt32(bytes, at: 8)
            uncompressedInfoSize = readLittleEndianUInt32(bytes, at: 12)
        } else {
            var sizeReader = AoVUnityFSReader(data: rawHeaderFields)
            declaredFileSize = try sizeReader.readUInt64BE("total file size")
            compressedInfoSize = try sizeReader.readUInt32BE("compressed block-info size")
            uncompressedInfoSize = try sizeReader.readUInt32BE("uncompressed block-info size")
        }

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

        let headerNeedsAlignment = formatVersion >= 7 || (engine?.needsHeaderAlignment ?? false)
        if headerNeedsAlignment {
            try header.align(16)
        }
        let headerEnd = header.offset
        let blockInfoAtEnd = flags & 0x80 != 0
        let blockPaddingAtStart = (usesLegacyFlags == false) && (flags & 0x200 != 0)
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
        var compressedInfo = data.subdata(in: infoOffset..<(infoOffset + compressedLength))
        if usesAoVEncryption {
            compressedInfo = try cryptAoVBlockInfo(compressedInfo, decrypt: true)
        }
        let infoCompressionCode = flags & 0x3F
        let infoCompression = try decompressBlockInfo(
            compressedInfo,
            expectedSize: uncompressedLength,
            method: infoCompressionCode
        )

        let standardTable = try? parseBlockDirectory(infoCompression, layout: .standard)
        let aovTable = try? parseBlockDirectory(infoCompression, layout: .arenaOfValor)
        let selectedTable: AoVUnityFSParsedBlockTable
        let selectedLayout: AoVUnityFSBlockInfoLayout
        switch (standardTable, aovTable) {
        case (let standard?, nil):
            selectedTable = standard
            selectedLayout = .standard
        case (nil, let aov?):
            selectedTable = aov
            selectedLayout = .arenaOfValor
        case (let standard?, let aov?):
            guard standard == aov else {
                throw AoVUnityFSError.ambiguousBlockInfoLayout
            }
            selectedTable = standard
            selectedLayout = .standard
        case (nil, nil):
            throw AoVUnityFSError.invalidBlockTable("Không khớp UnityFS chuẩn hoặc layout block-info AOV.")
        }

        guard dataStart >= headerEnd, dataEnd >= dataStart else {
            throw AoVUnityFSError.invalidHeader("vùng dữ liệu block bị đảo hoặc chồng lấn.")
        }
        let availableBlockData = UInt64(dataEnd - dataStart)
        guard selectedTable.totalCompressedBlockBytes <= availableBlockData else {
            throw AoVUnityFSError.invalidBlockTable(
                "các block khai báo \(selectedTable.totalCompressedBlockBytes) byte nén, chỉ có \(availableBlockData) byte trong bundle."
            )
        }

        return AoVUnityFSBundleInfo(
            formatVersion: formatVersion,
            playerVersion: playerVersion,
            engineVersion: engineVersion,
            totalFileSize: declaredFileSize,
            flags: flags,
            blockInfoCompression: compressionName(infoCompressionCode),
            blockInfoLayout: selectedLayout,
            blockInfoAtEnd: blockInfoAtEnd,
            usesAoVEncryption: usesAoVEncryption,
            blockPaddingAtStart: blockPaddingAtStart,
            blocks: selectedTable.blocks,
            nodes: selectedTable.nodes,
            totalUncompressedBytes: selectedTable.totalUncompressedBytes,
            totalCompressedBlockBytes: selectedTable.totalCompressedBlockBytes,
            availableBlockDataBytes: availableBlockData,
            blockDataOffset: UInt64(dataStart)
        )
    }

    private static func parseBlockDirectory(
        _ data: Data,
        layout: AoVUnityFSBlockInfoLayout
    ) throws -> AoVUnityFSParsedBlockTable {
        var reader = AoVUnityFSReader(data: data)
        _ = try reader.readBytes(16, "uncompressed data hash")
        let rawBlockCount = try reader.readInt32BE("block count")
        guard rawBlockCount >= 0, rawBlockCount <= maximumBlocks else {
            throw AoVUnityFSError.resourceLimit("số block \(rawBlockCount) không hợp lệ hoặc vượt giới hạn.")
        }

        var blocks: [AoVUnityFSBlock] = []
        blocks.reserveCapacity(Int(rawBlockCount))
        var totalUncompressed: UInt64 = 0
        var totalCompressed: UInt64 = 0
        for index in 0..<Int(rawBlockCount) {
            let uncompressed: UInt32
            let compressed: UInt32
            let blockFlags: UInt16
            let reserved: UInt16
            switch layout {
            case .standard:
                uncompressed = try reader.readUInt32BE("block \(index) uncompressed size")
                compressed = try reader.readUInt32BE("block \(index) compressed size")
                blockFlags = try reader.readUInt16BE("block \(index) flags")
                reserved = 0
            case .arenaOfValor:
                blockFlags = try reader.readUInt16BE("AOV block \(index) flags")
                reserved = try reader.readUInt16BE("AOV block \(index) reserved field")
                guard reserved == 0 else {
                    throw AoVUnityFSError.invalidBlockTable(
                        "AOV block \(index) có reserved field khác 0."
                    )
                }
                compressed = try reader.readUInt32BE("AOV block \(index) compressed size")
                uncompressed = try reader.readUInt32BE("AOV block \(index) uncompressed size")
            }
            let method = UInt32(blockFlags & 0x3F)
            guard method <= 4 else { throw AoVUnityFSError.invalidCompression(method) }
            let nextUncompressed = totalUncompressed.addingReportingOverflow(UInt64(uncompressed))
            let nextCompressed = totalCompressed.addingReportingOverflow(UInt64(compressed))
            guard !nextUncompressed.overflow, !nextCompressed.overflow else {
                throw AoVUnityFSError.resourceLimit("tổng kích thước block bị tràn số.")
            }
            totalUncompressed = nextUncompressed.partialValue
            totalCompressed = nextCompressed.partialValue
            blocks.append(
                AoVUnityFSBlock(
                    index: index,
                    uncompressedSize: uncompressed,
                    compressedSize: compressed,
                    flags: blockFlags,
                    reservedField: reserved,
                    compression: compressionName(method)
                )
            )
        }

        let rawNodeCount = try reader.readInt32BE("node count")
        guard rawNodeCount >= 0, rawNodeCount <= maximumNodes else {
            throw AoVUnityFSError.resourceLimit("số node \(rawNodeCount) không hợp lệ hoặc vượt giới hạn.")
        }
        var nodes: [AoVUnityFSNode] = []
        nodes.reserveCapacity(Int(rawNodeCount))
        var seenPaths = Set<String>()

        for index in 0..<Int(rawNodeCount) {
            let rawOffset: Int64
            let rawSize: Int64
            switch layout {
            case .standard:
                rawOffset = try reader.readInt64BE("node \(index) offset")
                rawSize = try reader.readInt64BE("node \(index) size")
            case .arenaOfValor:
                rawSize = try reader.readInt64BE("AOV node \(index) size")
                rawOffset = try reader.readInt64BE("AOV node \(index) offset")
            }
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

        return AoVUnityFSParsedBlockTable(
            blocks: blocks,
            nodes: nodes,
            totalUncompressedBytes: totalUncompressed,
            totalCompressedBlockBytes: totalCompressed
        )
    }

    /// Extract UnityFS directory nodes for read-only inspection. This never rebuilds
    /// or modifies a bundle and enforces a caller-supplied expanded-size ceiling.
    static func extractNodes(
        _ data: Data,
        matching paths: Set<String>? = nil,
        maximumExpandedBytes: UInt64 = 256 * 1024 * 1024,
        maximumNodeCount: Int = 512
    ) throws -> [AoVUnityFSExtractedNode] {
        let info = try inspect(data)
        guard maximumExpandedBytes > 0,
              maximumExpandedBytes <= UInt64(maximumBundleBytes) else {
            throw AoVUnityFSError.resourceLimit("giới hạn giải nén phải nằm trong khoảng 1 byte–512 MiB.")
        }
        guard info.totalUncompressedBytes <= maximumExpandedBytes else {
            throw AoVUnityFSError.resourceLimit(
                "dữ liệu sau giải nén là \(info.totalUncompressedBytes) byte, vượt giới hạn \(maximumExpandedBytes) byte."
            )
        }
        guard maximumNodeCount > 0, info.nodes.count <= maximumNodeCount else {
            throw AoVUnityFSError.resourceLimit(
                "bundle có \(info.nodes.count) node, vượt giới hạn trích xuất \(maximumNodeCount)."
            )
        }
        guard let startOffset = Int(exactly: info.blockDataOffset),
              startOffset <= data.count else {
            throw AoVUnityFSError.invalidBlockTable("offset dữ liệu block không biểu diễn được.")
        }

        let expectedExpandedSize = Int(info.totalUncompressedBytes)
        var expanded = Data()
        expanded.reserveCapacity(expectedExpandedSize)
        var cursor = startOffset

        for block in info.blocks {
            let compressedCount = Int(block.compressedSize)
            let expectedBlockSize = Int(block.uncompressedSize)
            guard cursor >= 0, cursor <= data.count,
                  compressedCount <= data.count - cursor else {
                throw AoVUnityFSError.truncated("dữ liệu block \(block.index)")
            }

            let compressed = data.subdata(in: cursor..<(cursor + compressedCount))
            let method = UInt32(block.flags & 0x3F)
            let decoded: Data
            switch method {
            case 0:
                guard compressed.count == expectedBlockSize else {
                    throw AoVUnityFSError.decompressionFailed(
                        "block \(block.index) không nén có \(compressed.count) byte, cần \(expectedBlockSize)."
                    )
                }
                decoded = compressed
            case 1:
                decoded = try decompressLZMA(
                    compressed,
                    expectedSize: expectedBlockSize,
                    maximumOutputSize: Int(maximumExpandedBytes),
                    context: "block \(block.index)"
                )
            case 2, 3:
                do {
                    decoded = try AoVLZ4Block.decompress(
                        compressed,
                        expectedSize: expectedBlockSize,
                        maximumSize: Int(maximumExpandedBytes)
                    )
                } catch {
                    throw AoVUnityFSError.decompressionFailed(
                        "block \(block.index): \(error.localizedDescription)"
                    )
                }
            case 4:
                throw AoVUnityFSError.unsupportedBlockCompression(method)
            default:
                throw AoVUnityFSError.invalidCompression(method)
            }

            let nextCount = expanded.count.addingReportingOverflow(decoded.count)
            guard !nextCount.overflow,
                  UInt64(nextCount.partialValue) <= maximumExpandedBytes else {
                throw AoVUnityFSError.resourceLimit("dữ liệu block giải nén vượt giới hạn.")
            }
            expanded.append(decoded)
            cursor += compressedCount
        }

        guard expanded.count == expectedExpandedSize else {
            throw AoVUnityFSError.invalidBlockTable(
                "tổng block giải nén là \(expanded.count) byte, directory khai báo \(expectedExpandedSize) byte."
            )
        }

        var extracted: [AoVUnityFSExtractedNode] = []
        var extractedBytes: UInt64 = 0
        for node in info.nodes {
            if let paths, !paths.contains(node.path) { continue }
            guard let offset = Int(exactly: node.offset),
                  let size = Int(exactly: node.size),
                  offset <= expanded.count,
                  size <= expanded.count - offset else {
                throw AoVUnityFSError.invalidDirectory("node \(node.path) không nằm trong dữ liệu đã giải nén.")
            }
            let nextBytes = extractedBytes.addingReportingOverflow(UInt64(size))
            guard !nextBytes.overflow, nextBytes.partialValue <= maximumExpandedBytes else {
                throw AoVUnityFSError.resourceLimit("tổng node được trích xuất vượt giới hạn.")
            }
            extractedBytes = nextBytes.partialValue
            extracted.append(
                AoVUnityFSExtractedNode(
                    path: node.path,
                    data: expanded.subdata(in: offset..<(offset + size)),
                    flags: node.flags
                )
            )
        }
        return extracted
    }

    /// Printable strings are diagnostic hints only, not parsed catalog fields.
    static func asciiStringHints(
        _ data: Data,
        maximumBytes: Int = 256 * 1024,
        maximumCount: Int = 6
    ) -> [String] {
        guard maximumBytes > 0, maximumCount > 0 else { return [] }
        let limit = min(data.count, maximumBytes)
        var hints: [String] = []
        var seen = Set<String>()
        var start: Int?

        func appendHint(_ lower: Int, _ upper: Int) {
            guard upper - lower >= 6,
                  let value = String(data: data.subdata(in: lower..<upper), encoding: .ascii),
                  seen.insert(value).inserted,
                  hints.count < maximumCount else { return }
            hints.append(value)
        }

        for index in 0..<limit {
            let byte = data[index]
            if (0x20...0x7E).contains(byte) {
                if start == nil { start = index }
            } else if let lower = start {
                appendHint(lower, index)
                start = nil
                if hints.count >= maximumCount { break }
            }
        }
        if hints.count < maximumCount, let lower = start {
            appendHint(lower, limit)
        }
        return hints
    }

    /// Encrypts the 16-byte little-endian UnityFS AoV header fields and applies
    /// the same per-word byte reversal expected by the AoV loader.
    static func encryptAoVHeader(_ plaintext: Data) throws -> Data {
        guard plaintext.count == 16 else {
            throw AoVUnityFSError.decryptionFailed("Header AES phải có đúng 16 byte.")
        }
        var encrypted = Data(count: 16)
        let outputCapacity = encrypted.count
        var outputLength: size_t = 0
        let status = plaintext.withUnsafeBytes { inputBytes in
            Self.aovHeaderAESKey.withUnsafeBytes { keyBytes in
                Self.aovHeaderAESIV.withUnsafeBytes { ivBytes in
                    encrypted.withUnsafeMutableBytes { outputBytes in
                        CCCrypt(
                            CCOperation(kCCEncrypt),
                            CCAlgorithm(kCCAlgorithmAES),
                            CCOptions(0),
                            keyBytes.baseAddress,
                            Self.aovHeaderAESKey.count,
                            ivBytes.baseAddress,
                            inputBytes.baseAddress,
                            plaintext.count,
                            outputBytes.baseAddress,
                            outputCapacity,
                            &outputLength
                        )
                    }
                }
            }
        }
        guard status == kCCSuccess, outputLength == 16 else {
            throw AoVUnityFSError.decryptionFailed("AES-CBC header encrypt trả về trạng thái \(status).")
        }
        let bytes = Array(encrypted)
        var reordered = Data()
        reordered.append(contentsOf: bytes[0..<8].reversed())
        reordered.append(contentsOf: bytes[8..<12].reversed())
        reordered.append(contentsOf: bytes[12..<16].reversed())
        return reordered
    }

    static func cryptAoVBlockInfo(_ data: Data, decrypt: Bool) throws -> Data {
        try AoVSM4.cryptCBC(
            data,
            key: Self.aovBlockSM4Key,
            iv: Self.aovBlockSM4IV,
            decrypt: decrypt
        )
    }

    static func aovEncryptionFlag(for engineVersion: String) throws -> UInt32 {
        guard let engine = AoVUnityEngineVersion(engineVersion) else {
            throw AoVUnityFSError.invalidHeader(
                "Không xác định được Unity version để chọn cờ mã hóa AOV."
            )
        }
        guard !engine.usesLegacyEncryptionFlag else {
            throw AoVUnityFSError.unsupportedEncryption(0x200)
        }
        return 0x400
    }

    private static func decryptAoVHeader(_ encrypted: Data) throws -> Data {
        guard encrypted.count == 16 else {
            throw AoVUnityFSError.decryptionFailed("Header AES phải có đúng 16 byte.")
        }
        let bytes = Array(encrypted)
        var reordered = Data()
        reordered.append(contentsOf: bytes[0..<8].reversed())
        reordered.append(contentsOf: bytes[8..<12].reversed())
        reordered.append(contentsOf: bytes[12..<16].reversed())

        var output = Data(count: 16)
        let outputCapacity = output.count
        var outputLength: size_t = 0
        let status = reordered.withUnsafeBytes { inputBytes in
            Self.aovHeaderAESKey.withUnsafeBytes { keyBytes in
                Self.aovHeaderAESIV.withUnsafeBytes { ivBytes in
                    output.withUnsafeMutableBytes { outputBytes in
                        CCCrypt(
                            CCOperation(kCCDecrypt),
                            CCAlgorithm(kCCAlgorithmAES),
                            CCOptions(0),
                            keyBytes.baseAddress,
                            Self.aovHeaderAESKey.count,
                            ivBytes.baseAddress,
                            inputBytes.baseAddress,
                            reordered.count,
                            outputBytes.baseAddress,
                            outputCapacity,
                            &outputLength
                        )
                    }
                }
            }
        }
        guard status == kCCSuccess, outputLength == 16 else {
            throw AoVUnityFSError.decryptionFailed("AES-CBC header trả về trạng thái \(status).")
        }
        return output
    }

    private static func readLittleEndianUInt32(_ bytes: [UInt8], at offset: Int) -> UInt32 {
        UInt32(bytes[offset]) |
            (UInt32(bytes[offset + 1]) << 8) |
            (UInt32(bytes[offset + 2]) << 16) |
            (UInt32(bytes[offset + 3]) << 24)
    }

    private static func readLittleEndianUInt64(_ bytes: [UInt8], at offset: Int) -> UInt64 {
        var value: UInt64 = 0
        for index in 0..<8 {
            value |= UInt64(bytes[offset + index]) << UInt64(index * 8)
        }
        return value
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
        case 1:
            return try decompressLZMA(
                compressed,
                expectedSize: expectedSize,
                maximumOutputSize: maximumBlockInfoBytes,
                context: "block-info"
            )
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
        case 4:
            throw AoVUnityFSError.unsupportedInfoCompression(method)
        default:
            throw AoVUnityFSError.invalidCompression(method)
        }
    }


    /// UnityFS LZMA entries use a five-byte property prefix followed by a raw
    /// LZMA1 stream. Apple's Compression framework exposes LZMA2/XZ, not this
    /// UnityFS variant, so decode it with the bounded SWCompression LZMA1 API.
    static func decompressLZMA(
        _ compressed: Data,
        expectedSize: Int,
        maximumOutputSize: Int,
        context: String = "LZMA"
    ) throws -> Data {
        guard expectedSize >= 0, maximumOutputSize > 0,
              expectedSize <= maximumOutputSize else {
            throw AoVUnityFSError.resourceLimit(
                "\(context): kích thước giải nén yêu cầu vượt giới hạn \(maximumOutputSize) byte."
            )
        }
        guard compressed.count >= 5 else {
            throw AoVUnityFSError.truncated("\(context): LZMA properties")
        }

        let propertyByte = Int(compressed[compressed.startIndex])
        guard propertyByte < 9 * 5 * 5 else {
            throw AoVUnityFSError.decompressionFailed(
                "\(context): byte thuộc tính LZMA không hợp lệ."
            )
        }

        let lc = propertyByte % 9
        let quotient = propertyByte / 9
        let lp = quotient % 5
        let pb = quotient / 5
        let dictionarySizeValue = UInt32(compressed[compressed.startIndex + 1]) |
            (UInt32(compressed[compressed.startIndex + 2]) << 8) |
            (UInt32(compressed[compressed.startIndex + 3]) << 16) |
            (UInt32(compressed[compressed.startIndex + 4]) << 24)

        guard dictionarySizeValue >= 4_096,
              dictionarySizeValue <= UInt32(maximumLZMADictionaryBytes),
              let dictionarySize = Int(exactly: dictionarySizeValue) else {
            throw AoVUnityFSError.resourceLimit(
                "\(context): dictionary LZMA \(dictionarySizeValue) byte nằm ngoài giới hạn an toàn."
            )
        }

        let rawPayload = Data(compressed.dropFirst(5))
        do {
            let properties = LZMAProperties(
                lc: lc,
                lp: lp,
                pb: pb,
                dictionarySize: dictionarySize
            )
            let decoded = try LZMA.decompress(
                data: rawPayload,
                properties: properties,
                uncompressedSize: expectedSize
            )
            guard decoded.count == expectedSize else {
                throw AoVUnityFSError.decompressionFailed(
                    "\(context): LZMA trả về \(decoded.count) byte, cần \(expectedSize)."
                )
            }
            return decoded
        } catch let error as AoVUnityFSError {
            throw error
        } catch {
            throw AoVUnityFSError.decompressionFailed(
                "\(context): LZMA1 thất bại: \(error.localizedDescription)"
            )
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
