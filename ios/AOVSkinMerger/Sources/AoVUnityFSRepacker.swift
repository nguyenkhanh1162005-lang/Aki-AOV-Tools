import Foundation
import CryptoKit

enum AoVUnityFSRepackError: LocalizedError {
    case noNodes
    case tooManyNodes(Int)
    case unsafePath(String)
    case duplicatePath(String)
    case invalidVersion(String)
    case payloadTooLarge
    case offsetOverflow(String)
    case blockInfoTooLarge
    case verificationFailed(String)

    var errorDescription: String? {
        switch self {
        case .noNodes:
            return "UnityFS cần ít nhất một node để đóng gói."
        case .tooManyNodes(let count):
            return "UnityFS có \(count) node, vượt giới hạn 100.000."
        case .unsafePath(let path):
            return "Đường dẫn node UnityFS không an toàn: \(path)"
        case .duplicatePath(let path):
            return "UnityFS có đường dẫn node trùng: \(path)"
        case .invalidVersion(let value):
            return "Phiên bản UnityFS không hợp lệ: \(value)"
        case .payloadTooLarge:
            return "Dữ liệu UnityFS vượt giới hạn 512 MiB."
        case .offsetOverflow(let path):
            return "Offset node bị tràn số: \(path)"
        case .blockInfoTooLarge:
            return "Block-info UnityFS vượt giới hạn 64 MiB."
        case .verificationFailed(let reason):
            return "UnityFS đóng gói xong nhưng round-trip không đạt: \(reason)"
        }
    }
}

struct AoVUnityFSRepackNode: Sendable {
    let path: String
    let data: Data
    let flags: UInt32

    init(path: String, data: Data, flags: UInt32 = 0) {
        self.path = path
        self.data = data
        self.flags = flags
    }
}

struct AoVUnityFSRepackReport: Sendable {
    let data: Data
    let nodeCount: Int
    let expandedBytes: UInt64
    let formatVersion: UInt32
    let playerVersion: String
    let engineVersion: String
    let blockInfoLayout: AoVUnityFSBlockInfoLayout
    let blockCount: Int
    let roundTripVerified: Bool
}

private struct AoVUnityFSBinaryWriter {
    var data = Data()

    mutating func appendUInt16BE(_ value: UInt16) {
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
    }

    mutating func appendUInt32BE(_ value: UInt32) {
        data.append(UInt8((value >> 24) & 0xFF))
        data.append(UInt8((value >> 16) & 0xFF))
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
    }

    mutating func appendUInt64BE(_ value: UInt64) {
        appendUInt32BE(UInt32((value >> 32) & 0xFFFF_FFFF))
        appendUInt32BE(UInt32(value & 0xFFFF_FFFF))
    }

    mutating func appendCString(_ value: String) {
        data.append(contentsOf: value.utf8)
        data.append(0)
    }

    mutating func align(_ boundary: Int) {
        guard boundary > 0 else { return }
        while data.count % boundary != 0 { data.append(0) }
    }

    mutating func replaceUInt32BE(at offset: Int, with value: UInt32) throws {
        guard offset >= 0, offset <= data.count, data.count - offset >= 4 else {
            throw AoVUnityFSRepackError.verificationFailed("Không ghi được trường UInt32 tại offset \(offset).")
        }
        data.replaceSubrange(offset..<(offset + 4), with: [
            UInt8((value >> 24) & 0xFF),
            UInt8((value >> 16) & 0xFF),
            UInt8((value >> 8) & 0xFF),
            UInt8(value & 0xFF)
        ])
    }

    mutating func replaceUInt64BE(at offset: Int, with value: UInt64) throws {
        guard offset >= 0, offset <= data.count, data.count - offset >= 8 else {
            throw AoVUnityFSRepackError.verificationFailed("Không ghi được trường UInt64 tại offset \(offset).")
        }
        var encoded = AoVUnityFSBinaryWriter()
        encoded.appendUInt64BE(value)
        data.replaceSubrange(offset..<(offset + 8), with: encoded.data)
    }
}

enum AoVUnityFSRepacker {
    private static let maximumBundleBytes = 512 * 1024 * 1024
    private static let maximumBlockInfoBytes = 64 * 1024 * 1024
    private static let maximumNodes = 100_000
    private static let archiveFlags: UInt32 = 0x40 // front block-info, uncompressed info, directory info combined

    /// Rebuilds a UnityFS archive with one uncompressed data block.
    /// This is a structural container writer; callers must still preserve/rebuild
    /// the serialized content and all object/dependency references themselves.
    static func repack(
        _ nodes: [AoVUnityFSRepackNode],
        formatVersion: UInt32 = 6,
        playerVersion: String = "5.0.0",
        engineVersion: String,
        blockInfoLayout: AoVUnityFSBlockInfoLayout = .standard
    ) throws -> AoVUnityFSRepackReport {
        guard !nodes.isEmpty else { throw AoVUnityFSRepackError.noNodes }
        guard nodes.count <= maximumNodes else { throw AoVUnityFSRepackError.tooManyNodes(nodes.count) }
        guard (6...10).contains(formatVersion),
              isValidVersionString(playerVersion),
              isValidVersionString(engineVersion) else {
            throw AoVUnityFSRepackError.invalidVersion("\(playerVersion) / \(engineVersion)")
        }

        var seenPaths = Set<String>()
        var expanded = Data()
        var directory: [(path: String, offset: UInt64, size: UInt64, flags: UInt32)] = []
        directory.reserveCapacity(nodes.count)

        for node in nodes {
            guard isSafeRelativePath(node.path) else {
                throw AoVUnityFSRepackError.unsafePath(node.path)
            }
            guard seenPaths.insert(node.path).inserted else {
                throw AoVUnityFSRepackError.duplicatePath(node.path)
            }
            let start = UInt64(expanded.count)
            let next = start.addingReportingOverflow(UInt64(node.data.count))
            guard !next.overflow, next.partialValue <= UInt64(maximumBundleBytes) else {
                throw AoVUnityFSRepackError.payloadTooLarge
            }
            directory.append((node.path, start, UInt64(node.data.count), node.flags))
            expanded.append(node.data)
        }

        guard !expanded.isEmpty, expanded.count <= maximumBundleBytes else {
            throw AoVUnityFSRepackError.payloadTooLarge
        }
        guard let blockExpandedSize = UInt32(exactly: expanded.count) else {
            throw AoVUnityFSRepackError.payloadTooLarge
        }

        var blockInfo = AoVUnityFSBinaryWriter()
        blockInfo.data.append(Data(Insecure.MD5.hash(data: expanded)))
        blockInfo.appendUInt32BE(1) // block count
        switch blockInfoLayout {
        case .standard:
            blockInfo.appendUInt32BE(blockExpandedSize) // uncompressed block
            blockInfo.appendUInt32BE(blockExpandedSize) // compressed block
            blockInfo.appendUInt16BE(0) // block compression = none
        case .arenaOfValor:
            blockInfo.appendUInt16BE(0) // block flags = uncompressed
            blockInfo.appendUInt16BE(0) // AOV reserved field
            blockInfo.appendUInt32BE(blockExpandedSize) // compressed block
            blockInfo.appendUInt32BE(blockExpandedSize) // uncompressed block
        }
        blockInfo.appendUInt32BE(UInt32(directory.count))

        for item in directory {
            switch blockInfoLayout {
            case .standard:
                blockInfo.appendUInt64BE(item.offset)
                blockInfo.appendUInt64BE(item.size)
            case .arenaOfValor:
                blockInfo.appendUInt64BE(item.size)
                blockInfo.appendUInt64BE(item.offset)
            }
            blockInfo.appendUInt32BE(item.flags)
            blockInfo.appendCString(item.path)
        }
        guard blockInfo.data.count <= maximumBlockInfoBytes,
              let infoSize = UInt32(exactly: blockInfo.data.count) else {
            throw AoVUnityFSRepackError.blockInfoTooLarge
        }

        var writer = AoVUnityFSBinaryWriter()
        writer.appendCString("UnityFS")
        writer.appendUInt32BE(formatVersion)
        writer.appendCString(playerVersion)
        writer.appendCString(engineVersion)

        let fileSizeOffset = writer.data.count
        writer.appendUInt64BE(0)
        let compressedInfoSizeOffset = writer.data.count
        writer.appendUInt32BE(infoSize)
        let uncompressedInfoSizeOffset = writer.data.count
        writer.appendUInt32BE(infoSize)
        writer.appendUInt32BE(archiveFlags)

        if formatVersion >= 7 || needsHeaderAlignment(engineVersion) {
            writer.align(16)
        }

        writer.data.append(blockInfo.data)
        writer.data.append(expanded)

        let totalSize = UInt64(writer.data.count)
        guard totalSize <= UInt64(maximumBundleBytes),
              let totalSizeInt = Int(exactly: totalSize) else {
            throw AoVUnityFSRepackError.payloadTooLarge
        }
        _ = totalSizeInt
        try writer.replaceUInt64BE(at: fileSizeOffset, with: totalSize)
        try writer.replaceUInt32BE(at: compressedInfoSizeOffset, with: infoSize)
        try writer.replaceUInt32BE(at: uncompressedInfoSizeOffset, with: infoSize)

        let output = writer.data
        do {
            let inspected = try AoVUnityFSParser.inspect(output)
            guard inspected.formatVersion == formatVersion,
                  inspected.playerVersion == playerVersion,
                  inspected.engineVersion == engineVersion,
                  inspected.blockInfoLayout == blockInfoLayout,
                  inspected.nodes.count == nodes.count,
                  inspected.blocks.count == 1,
                  inspected.totalUncompressedBytes == UInt64(expanded.count) else {
                throw AoVUnityFSRepackError.verificationFailed("Header, block table hoặc directory table không khớp.")
            }

            let extracted = try AoVUnityFSParser.extractNodes(
                output,
                maximumExpandedBytes: UInt64(maximumBundleBytes),
                maximumNodeCount: maximumNodes
            )
            guard extracted.count == nodes.count else {
                throw AoVUnityFSRepackError.verificationFailed("Số node round-trip khác đầu vào.")
            }
            let extractedByPath = Dictionary(uniqueKeysWithValues: extracted.map { ($0.path, $0.data) })
            for node in nodes {
                guard extractedByPath[node.path] == node.data else {
                    throw AoVUnityFSRepackError.verificationFailed("Payload node \(node.path) không khớp sau khi giải nén.")
                }
            }
            return AoVUnityFSRepackReport(
                data: output,
                nodeCount: nodes.count,
                expandedBytes: UInt64(expanded.count),
                formatVersion: formatVersion,
                playerVersion: playerVersion,
                engineVersion: engineVersion,
                blockInfoLayout: blockInfoLayout,
                blockCount: inspected.blocks.count,
                roundTripVerified: true
            )
        } catch let error as AoVUnityFSRepackError {
            throw error
        } catch {
            throw AoVUnityFSRepackError.verificationFailed(error.localizedDescription)
        }
    }

    private static func isValidVersionString(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 256 && !value.contains("\0")
    }

    private static func needsHeaderAlignment(_ engineVersion: String) -> Bool {
        let parts = engineVersion.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count >= 3,
              parts[0] == "2019",
              parts[1] == "4" else { return false }
        let patchDigits = parts[2].prefix(while: { $0.isNumber })
        return (Int(patchDigits) ?? 0) >= 15
    }

    private static func isSafeRelativePath(_ path: String) -> Bool {
        guard !path.isEmpty,
              !path.hasPrefix("/"),
              !path.contains("\\"),
              !path.contains("\0"),
              path.utf8.count <= 4_096 else { return false }
        let components = path.split(separator: "/", omittingEmptySubsequences: false)
        return !components.isEmpty && components.allSatisfy { component in
            !component.isEmpty && component != "." && component != ".."
        }
    }
}
