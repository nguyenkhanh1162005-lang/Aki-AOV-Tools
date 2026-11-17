import Foundation
import CryptoKit

struct AoVUnityFSRebuildResult: Sendable {
    let data: Data
    let nodeCount: Int
    let blockCount: Int
    let uncompressedBytes: UInt64
    let dataMD5: String
}

enum AoVUnityFSRebuildError: LocalizedError {
    case unsupportedTemplate
    case invalidBlockSize
    case tooManyNodes
    case unsafeNodePath(String)
    case duplicateNodePath(String)
    case bundleTooLarge
    case blockInfoTooLarge
    case integerOverflow
    case roundTripMismatch(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedTemplate:
            return "UnityFS template chưa thuộc dải format 6–10 hoặc chưa đọc được."
        case .invalidBlockSize:
            return "Kích thước block phải từ 1 byte đến 64 MiB."
        case .tooManyNodes:
            return "UnityFS có quá nhiều node để tái đóng gói an toàn."
        case .unsafeNodePath(let path):
            return "UnityFS node có đường dẫn không an toàn: \(path)"
        case .duplicateNodePath(let path):
            return "UnityFS có node trùng đường dẫn: \(path)"
        case .bundleTooLarge:
            return "UnityFS sau khi tái đóng gói vượt giới hạn 512 MiB."
        case .blockInfoTooLarge:
            return "Block-info sau khi tạo vượt giới hạn 64 MiB."
        case .integerOverflow:
            return "Kích thước UnityFS vượt giới hạn biểu diễn số nguyên."
        case .roundTripMismatch(let reason):
            return "UnityFS rebuild không qua kiểm tra round-trip: \(reason)"
        }
    }
}

/// Repackages an existing UnityFS node set without changing node payloads.
/// It emits raw blocks and front block-info. This is a deterministic foundation
/// for later catalog editing, not a catalog merger by itself.
enum AoVUnityFSRebuilder {
    private static let maximumBundleBytes = 512 * 1024 * 1024
    private static let maximumBlockInfoBytes = 64 * 1024 * 1024
    private static let maximumNodes = 100_000
    private static let maximumBlockBytes = 64 * 1024 * 1024
    private static let outputFlags: UInt32 = 0x40

    static func rebuild(
        template: Data,
        nodes: [AoVUnityFSExtractedNode],
        blockSize: Int = 4 * 1024 * 1024,
        verifyRoundTrip: Bool = true
    ) throws -> AoVUnityFSRebuildResult {
        let templateInfo: AoVUnityFSBundleInfo
        do {
            templateInfo = try AoVUnityFSParser.inspect(template)
        } catch {
            throw AoVUnityFSRebuildError.unsupportedTemplate
        }
        guard (6...10).contains(templateInfo.formatVersion) else {
            throw AoVUnityFSRebuildError.unsupportedTemplate
        }
        guard blockSize > 0, blockSize <= maximumBlockBytes else {
            throw AoVUnityFSRebuildError.invalidBlockSize
        }
        guard nodes.count <= maximumNodes else {
            throw AoVUnityFSRebuildError.tooManyNodes
        }

        var seenPaths = Set<String>()
        var payload = Data()
        var nodeRecords: [(offset: UInt64, size: UInt64, flags: UInt32, path: String)] = []
        nodeRecords.reserveCapacity(nodes.count)

        for node in nodes {
            guard isSafeRelativePath(node.path) else {
                throw AoVUnityFSRebuildError.unsafeNodePath(node.path)
            }
            guard seenPaths.insert(node.path).inserted else {
                throw AoVUnityFSRebuildError.duplicateNodePath(node.path)
            }
            guard let offset = UInt64(exactly: payload.count),
                  let size = UInt64(exactly: node.data.count) else {
                throw AoVUnityFSRebuildError.integerOverflow
            }
            let nextCount = payload.count.addingReportingOverflow(node.data.count)
            guard !nextCount.overflow,
                  nextCount.partialValue <= maximumBundleBytes else {
                throw AoVUnityFSRebuildError.bundleTooLarge
            }
            nodeRecords.append((offset, size, node.flags, node.path))
            payload.append(node.data)
        }

        let blocks = makeBlocks(for: payload, blockSize: blockSize)
        var blockInfo = Data(Insecure.MD5.hash(data: payload))
        appendBE(UInt32(blocks.count), to: &blockInfo)
        for block in blocks {
            guard let uncompressedSize = UInt32(exactly: block.count) else {
                throw AoVUnityFSRebuildError.integerOverflow
            }
            appendBE(uncompressedSize, to: &blockInfo)
            appendBE(uncompressedSize, to: &blockInfo)
            appendBE(UInt16(0), to: &blockInfo)
        }
        appendBE(UInt32(nodeRecords.count), to: &blockInfo)
        for node in nodeRecords {
            guard let offset = Int64(exactly: node.offset),
                  let size = Int64(exactly: node.size) else {
                throw AoVUnityFSRebuildError.integerOverflow
            }
            appendBE(offset, to: &blockInfo)
            appendBE(size, to: &blockInfo)
            appendBE(node.flags, to: &blockInfo)
            blockInfo.append(Data((node.path + "\0").utf8))
        }
        guard blockInfo.count <= maximumBlockInfoBytes,
              let blockInfoSize = UInt32(exactly: blockInfo.count) else {
            throw AoVUnityFSRebuildError.blockInfoTooLarge
        }

        var header = Data("UnityFS\0".utf8)
        appendBE(templateInfo.formatVersion, to: &header)
        header.append(Data((templateInfo.playerVersion + "\0").utf8))
        header.append(Data((templateInfo.engineVersion + "\0").utf8))
        let fileSizeOffset = header.count
        appendBE(UInt64(0), to: &header)
        appendBE(blockInfoSize, to: &header)
        appendBE(blockInfoSize, to: &header)
        appendBE(outputFlags, to: &header)
        if needsHeaderAlignment(formatVersion: templateInfo.formatVersion, engineVersion: templateInfo.engineVersion) {
            align16(&header)
        }

        let sizeWithoutPatch = UInt64(header.count).addingReportingOverflow(UInt64(blockInfo.count))
        guard !sizeWithoutPatch.overflow else { throw AoVUnityFSRebuildError.integerOverflow }
        let totalSize = sizeWithoutPatch.partialValue.addingReportingOverflow(UInt64(payload.count))
        guard !totalSize.overflow,
              totalSize.partialValue <= maximumBundleBytes,
              let totalSizeInt = Int(exactly: totalSize.partialValue) else {
            throw AoVUnityFSRebuildError.bundleTooLarge
        }

        var encodedFileSize = Data()
        appendBE(totalSize.partialValue, to: &encodedFileSize)
        header.replaceSubrange(fileSizeOffset..<(fileSizeOffset + 8), with: encodedFileSize)

        var output = Data()
        output.reserveCapacity(totalSizeInt)
        output.append(header)
        output.append(blockInfo)
        output.append(payload)
        guard output.count == totalSizeInt else {
            throw AoVUnityFSRebuildError.roundTripMismatch("kích thước file không khớp header.")
        }

        let md5 = Data(Insecure.MD5.hash(data: payload))
        let md5Hex = md5.map { String(format: "%02x", $0) }.joined()

        if verifyRoundTrip {
            let rebuiltInfo: AoVUnityFSBundleInfo
            do {
                rebuiltInfo = try AoVUnityFSParser.inspect(output)
            } catch {
                throw AoVUnityFSRebuildError.roundTripMismatch(error.localizedDescription)
            }
            guard rebuiltInfo.formatVersion == templateInfo.formatVersion,
                  rebuiltInfo.playerVersion == templateInfo.playerVersion,
                  rebuiltInfo.engineVersion == templateInfo.engineVersion,
                  rebuiltInfo.blockInfoCompression == "none",
                  !rebuiltInfo.blockInfoAtEnd,
                  !rebuiltInfo.blockPaddingAtStart,
                  rebuiltInfo.nodes.count == nodes.count,
                  rebuiltInfo.totalUncompressedBytes == UInt64(payload.count) else {
                throw AoVUnityFSRebuildError.roundTripMismatch("header hoặc directory table khác dữ liệu dự kiến.")
            }

            let extracted: [AoVUnityFSExtractedNode]
            do {
                extracted = try AoVUnityFSParser.extractNodes(
                    output,
                    maximumExpandedBytes: UInt64(maximumBundleBytes),
                    maximumNodeCount: maximumNodes
                )
            } catch {
                throw AoVUnityFSRebuildError.roundTripMismatch(error.localizedDescription)
            }
            guard extracted.count == nodes.count else {
                throw AoVUnityFSRebuildError.roundTripMismatch("số node sau giải nén thay đổi.")
            }
            for (expected, actual) in zip(nodes, extracted) {
                guard expected.path == actual.path,
                      expected.flags == actual.flags,
                      expected.data == actual.data else {
                    throw AoVUnityFSRebuildError.roundTripMismatch("node \(expected.path) không giữ nguyên payload/flags.")
                }
            }
        }

        return AoVUnityFSRebuildResult(
            data: output,
            nodeCount: nodes.count,
            blockCount: blocks.count,
            uncompressedBytes: UInt64(payload.count),
            dataMD5: md5Hex
        )
    }

    private static func makeBlocks(for data: Data, blockSize: Int) -> [Data] {
        guard !data.isEmpty else { return [] }
        var result: [Data] = []
        result.reserveCapacity((data.count + blockSize - 1) / blockSize)
        var offset = 0
        while offset < data.count {
            let count = min(blockSize, data.count - offset)
            result.append(data.subdata(in: offset..<(offset + count)))
            offset += count
        }
        return result
    }

    private static func needsHeaderAlignment(formatVersion: UInt32, engineVersion: String) -> Bool {
        if formatVersion >= 7 { return true }
        let parts = engineVersion.split(separator: ".")
        guard parts.count >= 3, parts[0] == "2019", parts[1] == "4" else { return false }
        let patchDigits = parts[2].prefix(while: { $0.isNumber })
        return (Int(patchDigits) ?? 0) >= 15
    }

    private static func isSafeRelativePath(_ path: String) -> Bool {
        guard !path.isEmpty, !path.hasPrefix("/"), !path.hasPrefix("~") else { return false }
        let normalized = path.replacingOccurrences(of: "\\", with: "/")
        let components = normalized.split(separator: "/", omittingEmptySubsequences: false)
        guard !components.isEmpty,
              !components.contains(where: { $0 == "." || $0 == ".." }) else { return false }
        return !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
    }

    private static func align16(_ data: inout Data) {
        while data.count % 16 != 0 { data.append(0) }
    }

    private static func appendBE(_ value: UInt16, to data: inout Data) {
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
    }

    private static func appendBE(_ value: UInt32, to data: inout Data) {
        data.append(UInt8((value >> 24) & 0xFF))
        data.append(UInt8((value >> 16) & 0xFF))
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
    }

    private static func appendBE(_ value: UInt64, to data: inout Data) {
        appendBE(UInt32((value >> 32) & 0xFFFF_FFFF), to: &data)
        appendBE(UInt32(value & 0xFFFF_FFFF), to: &data)
    }

    private static func appendBE(_ value: Int64, to data: inout Data) {
        appendBE(UInt64(bitPattern: value), to: &data)
    }
}
