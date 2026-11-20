import Foundation
import CryptoKit

enum AoVNIFSArchiveInspectorError: Error, LocalizedError, Equatable {
    case truncated(String)
    case invalidMagic(String)
    case unsupportedHeaderSize(UInt32)
    case invalidLayout(String)
    case companionMismatch(String)
    case tableChecksumMismatch(String)

    var errorDescription: String? {
        switch self {
        case .truncated(let name): return "Dữ liệu NIFS/CURES bị thiếu: \(name)"
        case .invalidMagic(let name): return "Magic không hợp lệ: \(name)"
        case .unsupportedHeaderSize(let size): return "NIFS header size không hỗ trợ: \(size)"
        case .invalidLayout(let reason): return "Cấu trúc NIFS không hợp lệ: \(reason)"
        case .companionMismatch(let reason): return "IFS và CURES không khớp: \(reason)"
        }
    }
}

struct AoVNIFSArchiveHeader: Equatable, Sendable {
    let headerSize: UInt32
    let version: UInt16
    let sectorSize: UInt16
    let archiveSize: UInt64
    let betTablePosition: UInt64
    let hetTablePosition: UInt64
    let md5TablePosition: UInt64
    let bitmapPosition: UInt64
    let hetTableSize: UInt64
    let betTableSize: UInt64
    let md5TableSize: UInt64
    let bitmapSize: UInt64
    let md5PieceSize: UInt32
    let rawChunkSize: UInt32
    let betTableMD5: Data
    let hetTableMD5: Data

    static let magic: UInt32 = 0x7366_696E
    static let packedHeaderBytes = 92
    static let supportedHeaderBytes: UInt32 = 172
}

struct AoVNIFSCompanionInspection: Sendable {
    let archiveHeader: AoVNIFSArchiveHeader
    let ifsByteCount: Int
    let curesByteCount: Int
    let curesHeaderSize: UInt32
    let embeddedHeaderOffset: UInt64
    let embeddedHeaderSize: UInt64
    let companionMatches: Bool
}

/// Structural validation only. This does not decrypt file payloads or rebuild an IFS archive.
enum AoVNIFSArchiveInspector {
    private static let curesMagic: UInt32 = 0x0134_3D03
    private static let curesHeaderSizeOffset = 8
    private static let curesIFSByteCountOffset = 0x48
    private static let curesNIFSHeaderOffset = 0x70
    private static let curesNIFSHeaderSizeOffset = 0x78

    static func inspectIFS(_ data: Data) throws -> AoVNIFSArchiveHeader {
        guard data.count >= AoVNIFSArchiveHeader.packedHeaderBytes else {
            throw AoVNIFSArchiveInspectorError.truncated("IFS header")
        }
        guard try readUInt32(data, at: 0) == AoVNIFSArchiveHeader.magic else {
            throw AoVNIFSArchiveInspectorError.invalidMagic("NIFS")
        }

        let header = try parseHeaderFields(data)
        guard header.headerSize == AoVNIFSArchiveHeader.supportedHeaderBytes else {
            throw AoVNIFSArchiveInspectorError.unsupportedHeaderSize(header.headerSize)
        }
        guard UInt64(data.count) >= UInt64(header.headerSize) else {
            throw AoVNIFSArchiveInspectorError.truncated("NIFS extended header")
        }
        guard header.sectorSize > 0, header.md5PieceSize > 0, header.rawChunkSize > 0 else {
            throw AoVNIFSArchiveInspectorError.invalidLayout("sector/chunk size bằng 0")
        }
        guard header.archiveSize == header.md5TablePosition else {
            throw AoVNIFSArchiveInspectorError.invalidLayout("ArchiveSize không trùng MD5TablePos")
        }

        let hetEnd = try checkedEnd(header.hetTablePosition, header.hetTableSize, label: "HET")
        let betEnd = try checkedEnd(header.betTablePosition, header.betTableSize, label: "BET")
        let md5End = try checkedEnd(header.md5TablePosition, header.md5TableSize, label: "MD5")
        let bitmapEnd = try checkedEnd(header.bitmapPosition, header.bitmapSize, label: "bitmap")
        guard header.hetTablePosition >= UInt64(header.headerSize),
              hetEnd <= header.betTablePosition,
              betEnd <= header.md5TablePosition,
              md5End <= header.bitmapPosition,
              bitmapEnd <= UInt64(data.count) else {
            throw AoVNIFSArchiveInspectorError.invalidLayout("các bảng bị chồng lấn hoặc vượt kích thước file")
        }
        try validateTableSignature(data, at: header.hetTablePosition, expected: [0x48, 0x45, 0x54, 0x1A], name: "HET")
        try validateTableSignature(data, at: header.betTablePosition, expected: [0x42, 0x45, 0x54, 0x1A], name: "BET")
        try validateTableMD5(data, at: header.betTablePosition, size: header.betTableSize, expected: header.betTableMD5, name: "BET")
        try validateTableMD5(data, at: header.hetTablePosition, size: header.hetTableSize, expected: header.hetTableMD5, name: "HET")
        return header
    }

    static func inspectCompanion(ifsData: Data, curesData: Data) throws -> AoVNIFSCompanionInspection {
        let archiveHeader = try inspectIFS(ifsData)
        guard curesData.count >= curesNIFSHeaderSizeOffset + 8 else {
            throw AoVNIFSArchiveInspectorError.truncated("CURES header")
        }
        guard try readUInt32(curesData, at: 0) == curesMagic else {
            throw AoVNIFSArchiveInspectorError.invalidMagic("CURES")
        }
        let curesHeaderSize = try readUInt32(curesData, at: curesHeaderSizeOffset)
        guard curesHeaderSize == 296 else {
            throw AoVNIFSArchiveInspectorError.invalidLayout("CURES header size không bằng 296")
        }
        let referencedIFSBytes = try readUInt64(curesData, at: curesIFSByteCountOffset)
        let embeddedOffset = try readUInt64(curesData, at: curesNIFSHeaderOffset)
        let embeddedSize = try readUInt64(curesData, at: curesNIFSHeaderSizeOffset)
        guard referencedIFSBytes == UInt64(ifsData.count) else {
            throw AoVNIFSArchiveInspectorError.companionMismatch("kích thước IFS được tham chiếu không trùng file thực tế")
        }
        guard embeddedSize == UInt64(archiveHeader.headerSize),
              embeddedOffset <= UInt64(curesData.count),
              embeddedSize <= UInt64(curesData.count) - embeddedOffset else {
            throw AoVNIFSArchiveInspectorError.companionMismatch("vị trí/kích thước NIFS header nhúng không hợp lệ")
        }
        let start = Int(embeddedOffset)
        let end = start + Int(embeddedSize)
        let embeddedHeader = try parseHeaderFields(Data(curesData[start..<end]))
        guard embeddedHeader == archiveHeader else {
            throw AoVNIFSArchiveInspectorError.companionMismatch("NIFS header trong CURES khác IFS")
        }
        return AoVNIFSCompanionInspection(
            archiveHeader: archiveHeader,
            ifsByteCount: ifsData.count,
            curesByteCount: curesData.count,
            curesHeaderSize: curesHeaderSize,
            embeddedHeaderOffset: embeddedOffset,
            embeddedHeaderSize: embeddedSize,
            companionMatches: true
        )
    }

    private static func parseHeaderFields(_ data: Data) throws -> AoVNIFSArchiveHeader {
        guard data.count >= Int(AoVNIFSArchiveHeader.supportedHeaderBytes),
              try readUInt32(data, at: 0) == AoVNIFSArchiveHeader.magic else {
            throw AoVNIFSArchiveInspectorError.invalidMagic("NIFS header")
        }
        return AoVNIFSArchiveHeader(
            headerSize: try readUInt32(data, at: 4),
            version: try readUInt16(data, at: 8),
            sectorSize: try readUInt16(data, at: 10),
            archiveSize: try readUInt64(data, at: 12),
            betTablePosition: try readUInt64(data, at: 20),
            hetTablePosition: try readUInt64(data, at: 28),
            md5TablePosition: try readUInt64(data, at: 36),
            bitmapPosition: try readUInt64(data, at: 44),
            hetTableSize: try readUInt64(data, at: 52),
            betTableSize: try readUInt64(data, at: 60),
            md5TableSize: try readUInt64(data, at: 68),
            bitmapSize: try readUInt64(data, at: 76),
            md5PieceSize: try readUInt32(data, at: 84),
            rawChunkSize: try readUInt32(data, at: 88),
            betTableMD5: data.subdata(in: 124..<140),
            hetTableMD5: data.subdata(in: 140..<156)
        )
    }

    private static func validateTableSignature(_ data: Data, at offset: UInt64, expected: [UInt8], name: String) throws {
        guard offset <= UInt64(Int.max), Int(offset) <= data.count,
              expected.count <= data.count - Int(offset) else {
            throw AoVNIFSArchiveInspectorError.truncated("\(name) table header")
        }
        let actual = expected.indices.map { data[data.index(data.startIndex, offsetBy: Int(offset) + $0)] }
        guard actual == expected else {
            throw AoVNIFSArchiveInspectorError.invalidMagic("\(name) table")
        }
    }

    private static func validateTableMD5(_ data: Data, at offset: UInt64, size: UInt64, expected: Data, name: String) throws {
        guard offset <= UInt64(Int.max), size <= UInt64(Int.max),
              Int(offset) <= data.count, Int(size) <= data.count - Int(offset) else {
            throw AoVNIFSArchiveInspectorError.truncated("\\(name) table checksum range")
        }
        let range = Int(offset)..<(Int(offset) + Int(size))
        let actual = Data(Insecure.MD5.hash(data: data.subdata(in: range)))
        guard actual == expected else {
            throw AoVNIFSArchiveInspectorError.tableChecksumMismatch(name)
        }
    }

    private static func checkedEnd(_ start: UInt64, _ size: UInt64, label: String) throws -> UInt64 {
        let (end, overflow) = start.addingReportingOverflow(size)
        guard !overflow else { throw AoVNIFSArchiveInspectorError.invalidLayout("tràn số ở bảng \(label)") }
        return end
    }

    private static func readUInt16(_ data: Data, at offset: Int) throws -> UInt16 {
        let a = UInt16(try readByte(data, at: offset))
        let b = UInt16(try readByte(data, at: offset + 1))
        return a | (b << 8)
    }

    private static func readUInt32(_ data: Data, at offset: Int) throws -> UInt32 {
        var value: UInt32 = 0
        for index in 0..<4 { value |= UInt32(try readByte(data, at: offset + index)) << (index * 8) }
        return value
    }

    private static func readUInt64(_ data: Data, at offset: Int) throws -> UInt64 {
        var value: UInt64 = 0
        for index in 0..<8 { value |= UInt64(try readByte(data, at: offset + index)) << (index * 8) }
        return value
    }

    private static func readByte(_ data: Data, at offset: Int) throws -> UInt8 {
        guard offset >= 0, offset < data.count else {
            throw AoVNIFSArchiveInspectorError.truncated("byte tại offset \(offset)")
        }
        return data[data.index(data.startIndex, offsetBy: offset)]
    }
}
