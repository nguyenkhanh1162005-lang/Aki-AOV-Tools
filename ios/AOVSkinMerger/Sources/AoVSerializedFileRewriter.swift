import Foundation

enum AoVSerializedFileRewriteError: LocalizedError {
    case unsupportedFormat(UInt32)
    case fileSizeMismatch
    case missingObject(Int64)
    case objectIsNotAssetBundle(Int64)
    case invalidObjectLayout(Int64)
    case unsupportedPathID(Int64)
    case objectTooLarge(Int64)
    case invalidReplacement(String)
    case overlappingObjects(Int64)
    case fileTooLarge
    case verificationFailed(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedFormat(let version):
            return "Chỉ hỗ trợ tái dựng SerializedFile format 9–30; format \(version) cần layout legacy riêng."
        case .fileSizeMismatch:
            return "Kích thước SerializedFile đầu vào không khớp header; không tái dựng để tránh mất phần dữ liệu dư."
        case .missingObject(let pathID):
            return "Không tìm thấy object pathID \(pathID) trong SerializedFile."
        case .objectIsNotAssetBundle(let pathID):
            return "Object pathID \(pathID) không phải AssetBundle class 142."
        case .invalidObjectLayout(let pathID):
            return "Object pathID \(pathID) có vùng dữ liệu chồng lấn hoặc nằm ngoài data section."
        case .unsupportedPathID(let pathID):
            return "pathID \(pathID) không biểu diễn được trong SerializedFile format này."
        case .objectTooLarge(let pathID):
            return "AssetBundle object pathID \(pathID) vượt giới hạn 64 MiB."
        case .invalidReplacement(let reason):
            return "AssetBundle replacement không hợp lệ: \(reason)"
        case .overlappingObjects(let pathID):
            return "Object pathID \(pathID) chồng lấn object kế tiếp."
        case .fileTooLarge:
            return "SerializedFile sau tái dựng vượt giới hạn 512 MiB."
        case .verificationFailed(let reason):
            return "SerializedFile tái dựng xong nhưng kiểm tra lại không đạt: \(reason)"
        }
    }
}

struct AoVSerializedFileRewriteReport: Sendable {
    let data: Data
    let replacedPathIDs: [Int64]
    let originalFileSize: UInt64
    let rewrittenFileSize: UInt64
    let originalObjectSizes: [Int64: UInt32]
    let rewrittenObjectSizes: [Int64: UInt32]
    let roundTripVerified: Bool
}

private struct AoVSerializedAssetBundleWriter {
    var data = Data()
    let littleEndian: Bool

    mutating func appendByte(_ value: UInt8) {
        data.append(value)
    }

    mutating func appendUInt16(_ value: UInt16) {
        if littleEndian {
            data.append(UInt8(value & 0xFF))
            data.append(UInt8((value >> 8) & 0xFF))
        } else {
            data.append(UInt8((value >> 8) & 0xFF))
            data.append(UInt8(value & 0xFF))
        }
    }

    mutating func appendUInt32(_ value: UInt32) {
        if littleEndian {
            for shift in stride(from: 0, through: 24, by: 8) {
                data.append(UInt8((value >> UInt32(shift)) & 0xFF))
            }
        } else {
            data.append(UInt8((value >> 24) & 0xFF))
            data.append(UInt8((value >> 16) & 0xFF))
            data.append(UInt8((value >> 8) & 0xFF))
            data.append(UInt8(value & 0xFF))
        }
    }

    mutating func appendInt32(_ value: Int32) {
        appendUInt32(UInt32(bitPattern: value))
    }

    mutating func appendUInt64(_ value: UInt64) {
        if littleEndian {
            for shift in stride(from: 0, through: 56, by: 8) {
                data.append(UInt8((value >> UInt64(shift)) & 0xFF))
            }
        } else {
            appendUInt32(UInt32((value >> 32) & 0xFFFF_FFFF))
            appendUInt32(UInt32(value & 0xFFFF_FFFF))
        }
    }

    mutating func appendInt64(_ value: Int64) {
        appendUInt64(UInt64(bitPattern: value))
    }

    mutating func appendAlignedString(_ value: String, field: String) throws {
        guard !value.contains("\0"), value.utf8.count <= 1_048_576,
              let count = Int32(exactly: value.utf8.count) else {
            throw AoVSerializedFileRewriteError.invalidReplacement("\(field) rỗng sai định dạng hoặc quá dài.")
        }
        appendInt32(count)
        data.append(contentsOf: value.utf8)
        align(4)
    }

    mutating func appendPointer(
        _ pointer: AoVSerializedAssetPointer,
        serializedVersion: UInt32,
        field: String
    ) throws {
        appendInt32(pointer.fileID)
        if serializedVersion < 14 {
            guard let pathID = Int32(exactly: pointer.pathID) else {
                throw AoVSerializedFileRewriteError.invalidReplacement("\(field) pathID vượt Int32 của SerializedFile format cũ.")
            }
            appendInt32(pathID)
        } else {
            appendInt64(pointer.pathID)
        }
    }

    mutating func align(_ boundary: Int) {
        guard boundary > 0 else { return }
        while data.count % boundary != 0 { data.append(0) }
    }
}

enum AoVSerializedFileRewriter {
    private static let maximumFileBytes = 512 * 1024 * 1024
    private static let maximumObjectBytes = 64 * 1024 * 1024

    /// Re-serializes one Unity AssetBundle (class 142) object and rebuilds the
    /// SerializedFile data section, updating every affected object offset/size.
    /// Unknown trailing fields inside the original AssetBundle object are preserved
    /// byte-for-byte and appended after the known fields.
    static func replaceAssetBundleRecord(
        in source: Data,
        pathID: Int64,
        with replacement: AoVSerializedAssetBundleRecord
    ) throws -> AoVSerializedFileRewriteReport {
        let summary = try AoVSerializedFileParser.inspect(source)
        guard summary.formatVersion >= 9 else {
            throw AoVSerializedFileRewriteError.unsupportedFormat(summary.formatVersion)
        }
        guard summary.fileSize == UInt64(source.count) else {
            throw AoVSerializedFileRewriteError.fileSizeMismatch
        }
        guard replacement.pathID == pathID else {
            throw AoVSerializedFileRewriteError.invalidReplacement("pathID replacement không trùng target.")
        }
        guard let object = summary.assetBundleObjects.first(where: { $0.pathID == pathID }) else {
            if summary.serializedObjects.contains(where: { $0.pathID == pathID }) {
                throw AoVSerializedFileRewriteError.objectIsNotAssetBundle(pathID)
            }
            throw AoVSerializedFileRewriteError.missingObject(pathID)
        }
        guard let originalRecord = summary.assetBundleRecords.first(where: { $0.pathID == pathID }) else {
            throw AoVSerializedFileRewriteError.invalidObjectLayout(pathID)
        }
        guard let start = Int(exactly: object.byteOffset),
              let size = Int(exactly: object.byteSize),
              start <= source.count, size <= source.count - start,
              size <= maximumObjectBytes else {
            throw AoVSerializedFileRewriteError.objectTooLarge(pathID)
        }

        let originalPayload = source.subdata(in: start..<(start + size))
        let trailingCount = originalRecord.trailingBytes
        guard trailingCount >= 0, trailingCount <= originalPayload.count else {
            throw AoVSerializedFileRewriteError.invalidObjectLayout(pathID)
        }
        let trailing = trailingCount == 0
            ? Data()
            : originalPayload.suffix(trailingCount)
        var replacementPayload = try serializeAssetBundleRecord(
            replacement,
            serializedVersion: summary.formatVersion,
            unityVersion: summary.unityVersion,
            littleEndian: summary.littleEndian
        )
        replacementPayload.append(trailing)
        guard replacementPayload.count <= maximumObjectBytes else {
            throw AoVSerializedFileRewriteError.objectTooLarge(pathID)
        }

        let report = try rewrite(
            source,
            replacingObjects: [pathID: replacementPayload]
        )
        let outputSummary = try AoVSerializedFileParser.inspect(report.data)
        guard let outputRecord = outputSummary.assetBundleRecords.first(where: { $0.pathID == pathID }),
              outputRecord == replacement,
              outputRecord.trailingBytes == originalRecord.trailingBytes else {
            throw AoVSerializedFileRewriteError.verificationFailed("AssetBundle record sau serialize không khớp bản thay thế.")
        }
        return report
    }

    /// Rebuilds the SerializedFile data section while keeping its metadata length
    /// unchanged. Gaps/padding between objects are copied verbatim. Only formats
    /// 9–30 are accepted because legacy v5–v8 store metadata at EOF.
    static func rewrite(
        _ source: Data,
        replacingObjects replacements: [Int64: Data]
    ) throws -> AoVSerializedFileRewriteReport {
        guard !replacements.isEmpty else {
            throw AoVSerializedFileRewriteError.invalidReplacement("Danh sách object thay thế đang rỗng.")
        }
        let summary = try AoVSerializedFileParser.inspect(source)
        guard summary.formatVersion >= 9 else {
            throw AoVSerializedFileRewriteError.unsupportedFormat(summary.formatVersion)
        }
        guard summary.fileSize == UInt64(source.count) else {
            throw AoVSerializedFileRewriteError.fileSizeMismatch
        }
        guard let dataOffset = Int(exactly: summary.dataOffset),
              dataOffset <= source.count else {
            throw AoVSerializedFileRewriteError.invalidObjectLayout(0)
        }

        let objects = summary.serializedObjects.sorted { $0.byteOffset < $1.byteOffset }
        let objectsByID = Dictionary(uniqueKeysWithValues: objects.map { ($0.pathID, $0) })
        for (pathID, payload) in replacements {
            guard objectsByID[pathID] != nil else {
                throw AoVSerializedFileRewriteError.missingObject(pathID)
            }
            guard payload.count <= maximumObjectBytes,
                  UInt32(exactly: payload.count) != nil else {
                throw AoVSerializedFileRewriteError.objectTooLarge(pathID)
            }
        }

        var rebuiltData = Data()
        var cursor: UInt64 = 0
        var rewrittenOffsets: [Int64: UInt64] = [:]
        var rewrittenSizes: [Int64: UInt32] = [:]

        for object in objects {
            guard object.byteOffset >= summary.dataOffset else {
                throw AoVSerializedFileRewriteError.invalidObjectLayout(object.pathID)
            }
            let relativeStart = object.byteOffset - summary.dataOffset
            let originalEnd = relativeStart.addingReportingOverflow(UInt64(object.byteSize))
            guard !originalEnd.overflow, originalEnd.partialValue <= summary.fileSize - summary.dataOffset else {
                throw AoVSerializedFileRewriteError.invalidObjectLayout(object.pathID)
            }
            guard relativeStart >= cursor else {
                throw AoVSerializedFileRewriteError.overlappingObjects(object.pathID)
            }

            let gapLength = relativeStart - cursor
            guard let gapStart = Int(exactly: summary.dataOffset + cursor),
                  let gapCount = Int(exactly: gapLength),
                  gapStart <= source.count, gapCount <= source.count - gapStart else {
                throw AoVSerializedFileRewriteError.invalidObjectLayout(object.pathID)
            }
            if gapCount > 0 {
                rebuiltData.append(source.subdata(in: gapStart..<(gapStart + gapCount)))
            }

            let originalStart = Int(object.byteOffset)
            let originalSize = Int(object.byteSize)
            guard originalStart <= source.count,
                  originalSize <= source.count - originalStart else {
                throw AoVSerializedFileRewriteError.invalidObjectLayout(object.pathID)
            }
            let payload = replacements[object.pathID] ??
                source.subdata(in: originalStart..<(originalStart + originalSize))
            guard let newSize = UInt32(exactly: payload.count) else {
                throw AoVSerializedFileRewriteError.objectTooLarge(object.pathID)
            }
            let newRelativeOffset = UInt64(rebuiltData.count)
            rewrittenOffsets[object.pathID] = newRelativeOffset
            rewrittenSizes[object.pathID] = newSize
            rebuiltData.append(payload)
            cursor = originalEnd.partialValue
        }

        let dataSectionBytes = summary.fileSize - summary.dataOffset
        guard cursor <= dataSectionBytes else {
            throw AoVSerializedFileRewriteError.invalidObjectLayout(0)
        }
        let trailingLength = dataSectionBytes - cursor
        guard let trailingStart = Int(exactly: summary.dataOffset + cursor),
              let trailingCount = Int(exactly: trailingLength),
              trailingStart <= source.count, trailingCount <= source.count - trailingStart else {
            throw AoVSerializedFileRewriteError.invalidObjectLayout(0)
        }
        if trailingCount > 0 {
            rebuiltData.append(source.subdata(in: trailingStart..<(trailingStart + trailingCount)))
        }

        let newFileSize = UInt64(dataOffset).addingReportingOverflow(UInt64(rebuiltData.count))
        guard !newFileSize.overflow, newFileSize.partialValue <= UInt64(maximumFileBytes),
              let newFileSizeInt = Int(exactly: newFileSize.partialValue) else {
            throw AoVSerializedFileRewriteError.fileTooLarge
        }

        var output = source.prefix(dataOffset)
        var result = Data(output)
        result.append(rebuiltData)
        guard result.count == newFileSizeInt else {
            throw AoVSerializedFileRewriteError.verificationFailed("Kích thước file sau nối data section không khớp.")
        }

        // Header size is big-endian for SerializedFile formats 9–21. Unity 2020+
        // extended headers use the target endianness for their 64-bit fields.
        if summary.formatVersion >= 22 {
            try writeUInt64(
                newFileSize.partialValue,
                into: &result,
                at: summary.fileSizeFieldOffset,
                littleEndian: summary.littleEndian
            )
        } else {
            guard let fileSize32 = UInt32(exactly: newFileSize.partialValue) else {
                throw AoVSerializedFileRewriteError.fileTooLarge
            }
            try writeUInt32(fileSize32, into: &result, at: summary.fileSizeFieldOffset, littleEndian: false)
        }

        for object in objects {
            guard let newOffset = rewrittenOffsets[object.pathID],
                  let newSize = rewrittenSizes[object.pathID] else { continue }
            if summary.formatVersion >= 22 {
                try writeUInt64(
                    newOffset,
                    into: &result,
                    at: object.byteStartFieldOffset,
                    littleEndian: summary.littleEndian
                )
            } else {
                guard let offset32 = UInt32(exactly: newOffset) else {
                    throw AoVSerializedFileRewriteError.invalidObjectLayout(object.pathID)
                }
                try writeUInt32(
                    offset32,
                    into: &result,
                    at: object.byteStartFieldOffset,
                    littleEndian: summary.littleEndian
                )
            }
            try writeUInt32(
                newSize,
                into: &result,
                at: object.byteSizeFieldOffset,
                littleEndian: summary.littleEndian
            )
        }

        let verified = try AoVSerializedFileParser.inspect(result)
        guard verified.fileSize == UInt64(result.count),
              verified.serializedObjects.count == summary.serializedObjects.count else {
            throw AoVSerializedFileRewriteError.verificationFailed("Object table sau tái dựng không khớp.")
        }
        for object in objects {
            guard let verifiedObject = verified.serializedObjects.first(where: { $0.pathID == object.pathID }),
                  verifiedObject.byteSize == rewrittenSizes[object.pathID],
                  verifiedObject.byteOffset == summary.dataOffset + (rewrittenOffsets[object.pathID] ?? 0) else {
                throw AoVSerializedFileRewriteError.verificationFailed("Offset/size object pathID \(object.pathID) không khớp.")
            }
        }
        for (pathID, replacement) in replacements {
            guard let rewrittenObject = verified.serializedObjects.first(where: { $0.pathID == pathID }),
                  let start = Int(exactly: rewrittenObject.byteOffset),
                  start <= result.count, replacement.count <= result.count - start,
                  result.subdata(in: start..<(start + replacement.count)) == replacement else {
                throw AoVSerializedFileRewriteError.verificationFailed("Payload object pathID \(pathID) không khớp.")
            }
        }

        return AoVSerializedFileRewriteReport(
            data: result,
            replacedPathIDs: replacements.keys.sorted(),
            originalFileSize: summary.fileSize,
            rewrittenFileSize: UInt64(result.count),
            originalObjectSizes: Dictionary(uniqueKeysWithValues: objects.map { ($0.pathID, $0.byteSize) }),
            rewrittenObjectSizes: rewrittenSizes,
            roundTripVerified: true
        )
    }

    private static func serializeAssetBundleRecord(
        _ record: AoVSerializedAssetBundleRecord,
        serializedVersion: UInt32,
        unityVersion: String,
        littleEndian: Bool
    ) throws -> Data {
        let versionParts = unityVersion.split(separator: ".").prefix(3).map { part -> Int in
            let digits = part.prefix(while: { $0.isNumber })
            return Int(digits) ?? -1
        }
        guard versionParts.count >= 3, versionParts.allSatisfy({ $0 >= 0 }) else {
            throw AoVSerializedFileRewriteError.invalidReplacement("Không phân tích được Unity version \(unityVersion).")
        }
        let major = versionParts[0]
        let minor = versionParts[1]

        var writer = AoVSerializedAssetBundleWriter(littleEndian: littleEndian)
        try writer.appendAlignedString(record.name, field: "m_Name")
        guard let preloadCount = Int32(exactly: record.preloadReferences.count),
              let containerCount = Int32(exactly: record.containerEntries.count),
              let classCount = Int32(exactly: record.classCompatibilityEntries.count),
              let dependencyCount = Int32(exactly: record.dependencies.count) else {
            throw AoVSerializedFileRewriteError.invalidReplacement("Vector count vượt Int32.")
        }

        writer.appendInt32(preloadCount)
        for (index, pointer) in record.preloadReferences.enumerated() {
            try writer.appendPointer(pointer, serializedVersion: serializedVersion, field: "m_PreloadTable[\(index)]")
        }

        writer.appendInt32(containerCount)
        for (index, entry) in record.containerEntries.enumerated() {
            guard entry.preloadIndex >= 0, entry.preloadSize >= 0 else {
                throw AoVSerializedFileRewriteError.invalidReplacement("m_Container[\(index)] có preload range âm.")
            }
            let end = Int64(entry.preloadIndex).addingReportingOverflow(Int64(entry.preloadSize))
            guard !end.overflow, end.partialValue <= Int64(record.preloadReferences.count) else {
                throw AoVSerializedFileRewriteError.invalidReplacement("m_Container[\(index)] preload range vượt m_PreloadTable.")
            }
            try writer.appendAlignedString(entry.key, field: "m_Container[\(index)] key")
            writer.appendInt32(entry.preloadIndex)
            writer.appendInt32(entry.preloadSize)
            try writer.appendPointer(entry.asset, serializedVersion: serializedVersion, field: "m_Container[\(index)] asset")
        }

        guard record.mainAssetPreloadIndex >= 0, record.mainAssetPreloadSize >= 0 else {
            throw AoVSerializedFileRewriteError.invalidReplacement("m_MainAsset preload range âm.")
        }
        let mainEnd = Int64(record.mainAssetPreloadIndex).addingReportingOverflow(Int64(record.mainAssetPreloadSize))
        guard !mainEnd.overflow, mainEnd.partialValue <= Int64(record.preloadReferences.count) else {
            throw AoVSerializedFileRewriteError.invalidReplacement("m_MainAsset preload range vượt m_PreloadTable.")
        }
        writer.appendInt32(record.mainAssetPreloadIndex)
        writer.appendInt32(record.mainAssetPreloadSize)
        try writer.appendPointer(record.mainAsset, serializedVersion: serializedVersion, field: "m_MainAsset")

        if major == 5 && minor == 4 {
            guard let scriptCount = Int32(exactly: record.scriptCompatibilityEntries.count) else {
                throw AoVSerializedFileRewriteError.invalidReplacement("m_ScriptCompatibility count vượt Int32.")
            }
            writer.appendInt32(scriptCount)
            for item in record.scriptCompatibilityEntries {
                writer.appendInt32(item.first)
                writer.appendInt32(item.second)
            }
        } else if !record.scriptCompatibilityEntries.isEmpty {
            throw AoVSerializedFileRewriteError.invalidReplacement("m_ScriptCompatibility chỉ hỗ trợ layout Unity 5.4.")
        }

        writer.appendInt32(classCount)
        for (index, entry) in record.classCompatibilityEntries.enumerated() {
            writer.appendInt32(entry.classID)
            writer.appendInt32(entry.info.preloadIndex)
            writer.appendInt32(entry.info.preloadSize)
            try writer.appendPointer(
                entry.info.asset,
                serializedVersion: serializedVersion,
                field: "m_ClassCompatibility[\(index)]"
            )
        }

        if major > 4 || (major == 4 && minor >= 2) {
            guard let runtimeCompatibility = record.runtimeCompatibility else {
                throw AoVSerializedFileRewriteError.invalidReplacement("Thiếu m_RuntimeCompatibility.")
            }
            writer.appendUInt32(runtimeCompatibility)
        }

        if major >= 5 {
            try writer.appendAlignedString(record.assetBundleName, field: "m_AssetBundleName")
            writer.appendInt32(dependencyCount)
            for (index, dependency) in record.dependencies.enumerated() {
                try writer.appendAlignedString(dependency, field: "m_Dependencies[\(index)]")
            }
            writer.appendByte(record.isStreamedSceneAssetBundle ? 1 : 0)
        } else if !record.assetBundleName.isEmpty || !record.dependencies.isEmpty || record.isStreamedSceneAssetBundle {
            throw AoVSerializedFileRewriteError.invalidReplacement("AssetBundle fields của Unity 5+ không được ghi vào layout cũ hơn.")
        }

        return writer.data
    }

    private static func writeUInt32(
        _ value: UInt32,
        into data: inout Data,
        at offset: Int,
        littleEndian: Bool
    ) throws {
        guard offset >= 0, offset <= data.count, data.count - offset >= 4 else {
            throw AoVSerializedFileRewriteError.verificationFailed("UInt32 field offset không hợp lệ.")
        }
        let bytes: [UInt8]
        if littleEndian {
            bytes = [
                UInt8(value & 0xFF),
                UInt8((value >> 8) & 0xFF),
                UInt8((value >> 16) & 0xFF),
                UInt8((value >> 24) & 0xFF)
            ]
        } else {
            bytes = [
                UInt8((value >> 24) & 0xFF),
                UInt8((value >> 16) & 0xFF),
                UInt8((value >> 8) & 0xFF),
                UInt8(value & 0xFF)
            ]
        }
        data.replaceSubrange(offset..<(offset + 4), with: bytes)
    }

    private static func writeUInt64(
        _ value: UInt64,
        into data: inout Data,
        at offset: Int,
        littleEndian: Bool
    ) throws {
        guard offset >= 0, offset <= data.count, data.count - offset >= 8 else {
            throw AoVSerializedFileRewriteError.verificationFailed("UInt64 field offset không hợp lệ.")
        }
        var bytes: [UInt8] = []
        bytes.reserveCapacity(8)
        if littleEndian {
            for shift in stride(from: 0, through: 56, by: 8) {
                bytes.append(UInt8((value >> UInt64(shift)) & 0xFF))
            }
        } else {
            for shift in stride(from: 32, through: 0, by: -8) {
                bytes.append(UInt8((value >> UInt64(shift)) & 0xFF))
            }
        }
        data.replaceSubrange(offset..<(offset + 8), with: bytes)
    }
}
