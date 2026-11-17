import Foundation

enum AoVSerializedAssetBundleWriteError: LocalizedError {
    case unsupportedFileVersion(UInt32)
    case invalidObjectPathID(Int64)
    case objectNotAssetBundle(Int64)
    case duplicateObjectPathID(Int64)
    case duplicateContainerKey(String)
    case invalidPreloadRange(String)
    case unresolvedPointer(String)
    case invalidExternalFileID(String)
    case malformedRecord(String)
    case objectTooLarge
    case fileTooLarge
    case unsupportedIntegerWidth
    case validationFailed(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedFileVersion(let version):
            return "Chưa hỗ trợ ghi SerializedFile format version \(version)."
        case .invalidObjectPathID(let pathID):
            return "Không tìm thấy duy nhất AssetBundle object pathID \(pathID)."
        case .objectNotAssetBundle(let pathID):
            return "Object pathID \(pathID) không thuộc class 142."
        case .duplicateObjectPathID(let pathID):
            return "SerializedFile có object pathID \(pathID) bị trùng."
        case .duplicateContainerKey(let key):
            return "AssetBundle có container key trùng: \(key)."
        case .invalidPreloadRange(let field):
            return "Preload range không hợp lệ ở \(field)."
        case .unresolvedPointer(let field):
            return "PPtr nội bộ chưa phân giải được ở \(field)."
        case .invalidExternalFileID(let field):
            return "PPtr external fileID không hợp lệ ở \(field)."
        case .malformedRecord(let reason):
            return "AssetBundle record không hợp lệ: \(reason)"
        case .objectTooLarge:
            return "AssetBundle object mới vượt giới hạn 64 MiB."
        case .fileTooLarge:
            return "SerializedFile sau khi ghi vượt giới hạn 512 MiB."
        case .unsupportedIntegerWidth:
            return "Offset hoặc kích thước không vừa kiểu số của SerializedFile."
        case .validationFailed(let reason):
            return "SerializedFile rewrite không qua kiểm tra: \(reason)"
        }
    }
}

private struct AoVSerializedDataWriter {
    var data = Data()
    let littleEndian: Bool

    mutating func appendByte(_ value: UInt8) {
        data.append(value)
    }

    mutating func appendInt32(_ value: Int32) {
        appendUInt32(UInt32(bitPattern: value))
    }

    mutating func appendUInt32(_ value: UInt32) {
        if littleEndian {
            data.append(UInt8(value & 0xFF))
            data.append(UInt8((value >> 8) & 0xFF))
            data.append(UInt8((value >> 16) & 0xFF))
            data.append(UInt8((value >> 24) & 0xFF))
        } else {
            data.append(UInt8((value >> 24) & 0xFF))
            data.append(UInt8((value >> 16) & 0xFF))
            data.append(UInt8((value >> 8) & 0xFF))
            data.append(UInt8(value & 0xFF))
        }
    }

    mutating func appendInt64(_ value: Int64) {
        let bits = UInt64(bitPattern: value)
        if littleEndian {
            for shift in stride(from: 0, through: 56, by: 8) {
                data.append(UInt8((bits >> UInt64(shift)) & 0xFF))
            }
        } else {
            appendUInt32(UInt32((bits >> 32) & 0xFFFF_FFFF))
            appendUInt32(UInt32(bits & 0xFFFF_FFFF))
        }
    }

    mutating func appendCount(_ value: Int, field: String) throws {
        guard let count = Int32(exactly: value), count >= 0 else {
            throw AoVSerializedAssetBundleWriteError.malformedRecord("\(field) vượt giới hạn Int32.")
        }
        appendInt32(count)
    }

    mutating func appendAlignedString(_ value: String, field: String) throws {
        let bytes = Data(value.utf8)
        guard bytes.count <= 1_048_576,
              let count = Int32(exactly: bytes.count) else {
            throw AoVSerializedAssetBundleWriteError.malformedRecord("\(field) quá dài.")
        }
        appendInt32(count)
        data.append(bytes)
        while data.count % 4 != 0 { data.append(0) }
    }

    mutating func appendPointer(
        _ pointer: AoVSerializedAssetPointer,
        serializedVersion: UInt32,
        field: String
    ) throws {
        appendInt32(pointer.fileID)
        if serializedVersion < 14 {
            guard let pathID = Int32(exactly: pointer.pathID) else {
                throw AoVSerializedAssetBundleWriteError.unsupportedIntegerWidth
            }
            appendInt32(pathID)
        } else {
            appendInt64(pointer.pathID)
        }
        _ = field
    }
}

enum AoVSerializedAssetBundleWriter {
    private static let maximumObjectBytes = 64 * 1024 * 1024
    private static let maximumFileBytes = 512 * 1024 * 1024

    static func serialize(
        _ record: AoVSerializedAssetBundleRecord,
        formatVersion: UInt32,
        unityVersion: String,
        littleEndian: Bool
    ) throws -> Data {
        guard (9...30).contains(formatVersion) else {
            throw AoVSerializedAssetBundleWriteError.unsupportedFileVersion(formatVersion)
        }
        try validateRecordShape(record, unityVersion: unityVersion)

        var writer = AoVSerializedDataWriter(littleEndian: littleEndian)
        try writer.appendAlignedString(record.name, field: "m_Name")

        try writer.appendCount(record.preloadReferences.count, field: "m_PreloadTable")
        for (index, pointer) in record.preloadReferences.enumerated() {
            try writer.appendPointer(
                pointer,
                serializedVersion: formatVersion,
                field: "m_PreloadTable[\(index)]"
            )
        }

        try writer.appendCount(record.containerEntries.count, field: "m_Container")
        for entry in record.containerEntries {
            try writer.appendAlignedString(entry.key, field: "m_Container key")
            writer.appendInt32(entry.preloadIndex)
            writer.appendInt32(entry.preloadSize)
            try writer.appendPointer(
                entry.asset,
                serializedVersion: formatVersion,
                field: "m_Container[\(entry.key)]"
            )
        }

        writer.appendInt32(record.mainAssetPreloadIndex)
        writer.appendInt32(record.mainAssetPreloadSize)
        try writer.appendPointer(
            record.mainAsset,
            serializedVersion: formatVersion,
            field: "m_MainAsset"
        )

        let version = try parseUnityVersion(unityVersion)
        if version.major == 5 && version.minor == 4 {
            try writer.appendCount(record.scriptCompatibilityEntries.count, field: "m_ScriptCompatibility")
            for entry in record.scriptCompatibilityEntries {
                writer.appendInt32(entry.first)
                writer.appendInt32(entry.second)
            }
        }

        try writer.appendCount(record.classCompatibilityEntries.count, field: "m_ClassCompatibility")
        for entry in record.classCompatibilityEntries {
            writer.appendInt32(entry.classID)
            writer.appendInt32(entry.info.preloadIndex)
            writer.appendInt32(entry.info.preloadSize)
            try writer.appendPointer(
                entry.info.asset,
                serializedVersion: formatVersion,
                field: "m_ClassCompatibility[\(entry.classID)]"
            )
        }

        if version.major > 4 || (version.major == 4 && version.minor >= 2) {
            guard let runtimeCompatibility = record.runtimeCompatibility else {
                throw AoVSerializedAssetBundleWriteError.malformedRecord("thiếu m_RuntimeCompatibility.")
            }
            writer.appendUInt32(runtimeCompatibility)
        }

        if version.major >= 5 {
            try writer.appendAlignedString(record.assetBundleName, field: "m_AssetBundleName")
            try writer.appendCount(record.dependencies.count, field: "m_Dependencies")
            for (index, dependency) in record.dependencies.enumerated() {
                try writer.appendAlignedString(dependency, field: "m_Dependencies[\(index)]")
            }
            writer.appendByte(record.isStreamedSceneAssetBundle ? 1 : 0)
        }

        writer.data.append(record.trailingData)
        guard writer.data.count <= maximumObjectBytes else {
            throw AoVSerializedAssetBundleWriteError.objectTooLarge
        }
        return writer.data
    }

    static func rewrite(
        serializedFile data: Data,
        assetBundleRecord record: AoVSerializedAssetBundleRecord
    ) throws -> Data {
        let summary: AoVSerializedFileSummary
        do {
            summary = try AoVSerializedFileParser.inspect(data)
        } catch {
            throw AoVSerializedAssetBundleWriteError.validationFailed(error.localizedDescription)
        }

        guard (9...30).contains(summary.formatVersion) else {
            throw AoVSerializedAssetBundleWriteError.unsupportedFileVersion(summary.formatVersion)
        }
        guard summary.fileSize == UInt64(data.count) else {
            throw AoVSerializedAssetBundleWriteError.validationFailed("fileSize không trùng kích thước dữ liệu đầu vào.")
        }
        guard record.trailingBytes == record.trailingData.count else {
            throw AoVSerializedAssetBundleWriteError.malformedRecord("trailingBytes không khớp trailingData.")
        }

        let matching = summary.serializedObjects.filter { $0.pathID == record.pathID }
        guard matching.count == 1, let target = matching.first else {
            if matching.count > 1 {
                throw AoVSerializedAssetBundleWriteError.duplicateObjectPathID(record.pathID)
            }
            throw AoVSerializedAssetBundleWriteError.invalidObjectPathID(record.pathID)
        }
        guard target.classID == 142 else {
            throw AoVSerializedAssetBundleWriteError.objectNotAssetBundle(record.pathID)
        }

        try validateReferences(record, summary: summary)

        let replacement = try serialize(
            record,
            formatVersion: summary.formatVersion,
            unityVersion: summary.unityVersion,
            littleEndian: summary.littleEndian
        )
        guard replacement.count <= maximumObjectBytes,
              let newSize = UInt32(exactly: replacement.count) else {
            throw AoVSerializedAssetBundleWriteError.objectTooLarge
        }

        let alignedAbsoluteStart = aligned16(data.count)
        guard let absoluteStart = UInt64(exactly: alignedAbsoluteStart),
              absoluteStart >= summary.dataOffset else {
            throw AoVSerializedAssetBundleWriteError.unsupportedIntegerWidth
        }
        let relativeStart = absoluteStart - summary.dataOffset
        let newFileSize = alignedAbsoluteStart.addingReportingOverflow(replacement.count)
        guard !newFileSize.overflow,
              newFileSize.partialValue <= maximumFileBytes,
              let newFileSize64 = UInt64(exactly: newFileSize.partialValue) else {
            throw AoVSerializedAssetBundleWriteError.fileTooLarge
        }

        var output = data
        if alignedAbsoluteStart > output.count {
            output.append(Data(repeating: 0, count: alignedAbsoluteStart - output.count))
        }
        output.append(replacement)

        if summary.formatVersion >= 22 {
            guard let relative = Int64(exactly: relativeStart),
                  let fileSize = Int64(exactly: newFileSize64) else {
                throw AoVSerializedAssetBundleWriteError.unsupportedIntegerWidth
            }
            patchInt64(
                &output,
                at: target.byteStartFieldOffset,
                value: relative,
                littleEndian: summary.littleEndian
            )
            patchInt64(
                &output,
                at: summary.fileSizeFieldOffset,
                value: fileSize,
                littleEndian: false
            )
        } else {
            guard let relative = UInt32(exactly: relativeStart),
                  let fileSize = UInt32(exactly: newFileSize64) else {
                throw AoVSerializedAssetBundleWriteError.unsupportedIntegerWidth
            }
            patchUInt32(
                &output,
                at: target.byteStartFieldOffset,
                value: relative,
                littleEndian: summary.littleEndian
            )
            patchUInt32(
                &output,
                at: summary.fileSizeFieldOffset,
                value: fileSize,
                littleEndian: false
            )
        }
        patchUInt32(
            &output,
            at: target.byteSizeFieldOffset,
            value: newSize,
            littleEndian: summary.littleEndian
        )

        let verified: AoVSerializedFileSummary
        do {
            verified = try AoVSerializedFileParser.inspect(output)
        } catch {
            throw AoVSerializedAssetBundleWriteError.validationFailed(error.localizedDescription)
        }
        guard verified.fileSize == UInt64(output.count),
              let rewritten = verified.assetBundleRecords.first(where: { $0.pathID == record.pathID }),
              rewritten == record else {
            throw AoVSerializedAssetBundleWriteError.validationFailed("AssetBundle record sau khi parse lại khác record yêu cầu.")
        }
        return output
    }

    private static func validateRecordShape(
        _ record: AoVSerializedAssetBundleRecord,
        unityVersion: String
    ) throws {
        _ = try parseUnityVersion(unityVersion)
        var keys = Set<String>()
        for entry in record.containerEntries {
            guard !entry.key.isEmpty else {
                throw AoVSerializedAssetBundleWriteError.malformedRecord("container key rỗng.")
            }
            guard keys.insert(entry.key).inserted else {
                throw AoVSerializedAssetBundleWriteError.duplicateContainerKey(entry.key)
            }
            try validateRange(
                index: entry.preloadIndex,
                size: entry.preloadSize,
                count: record.preloadReferences.count,
                field: "m_Container[\(entry.key)]"
            )
        }
        try validateRange(
            index: record.mainAssetPreloadIndex,
            size: record.mainAssetPreloadSize,
            count: record.preloadReferences.count,
            field: "m_MainAsset"
        )
        guard record.classCompatibilityIDs == record.classCompatibilityEntries.map(\.classID) else {
            throw AoVSerializedAssetBundleWriteError.malformedRecord("classCompatibilityIDs không khớp m_ClassCompatibility.")
        }
        for entry in record.classCompatibilityEntries {
            try validateRange(
                index: entry.info.preloadIndex,
                size: entry.info.preloadSize,
                count: record.preloadReferences.count,
                field: "m_ClassCompatibility[\(entry.classID)]"
            )
        }
    }

    private static func validateReferences(
        _ record: AoVSerializedAssetBundleRecord,
        summary: AoVSerializedFileSummary
    ) throws {
        let pointers: [(String, AoVSerializedAssetPointer)] =
            record.preloadReferences.enumerated().map { ("m_PreloadTable[\($0.offset)]", $0.element) } +
            record.containerEntries.map { ("m_Container[\($0.key)]", $0.asset) } +
            [("m_MainAsset", record.mainAsset)] +
            record.classCompatibilityEntries.map { ("m_ClassCompatibility[\($0.classID)]", $0.info.asset) }
        let objectIDs = summary.serializedObjectPathIDs
        let externalCount = summary.externalReferences.count

        for (field, pointer) in pointers {
            if pointer.fileID == 0 {
                if pointer.pathID != 0 && !objectIDs.contains(pointer.pathID) {
                    throw AoVSerializedAssetBundleWriteError.unresolvedPointer(field)
                }
            } else {
                guard pointer.fileID > 0, Int(pointer.fileID) <= externalCount else {
                    throw AoVSerializedAssetBundleWriteError.invalidExternalFileID(field)
                }
            }
        }
    }

    private static func validateRange(
        index: Int32,
        size: Int32,
        count: Int,
        field: String
    ) throws {
        let start = Int64(index)
        let length = Int64(size)
        let end = start.addingReportingOverflow(length)
        guard index >= 0, size >= 0, !end.overflow, end.partialValue <= Int64(count) else {
            throw AoVSerializedAssetBundleWriteError.invalidPreloadRange(field)
        }
    }

    private static func parseUnityVersion(_ value: String) throws -> (major: Int, minor: Int, patch: Int) {
        let parts = value.split(separator: ".").prefix(3)
        guard parts.count == 3 else {
            throw AoVSerializedAssetBundleWriteError.malformedRecord("Unity version không phân tích được.")
        }
        var result: [Int] = []
        for part in parts {
            let digits = part.prefix(while: { $0.isNumber })
            guard !digits.isEmpty, let parsed = Int(digits) else {
                throw AoVSerializedAssetBundleWriteError.malformedRecord("Unity version không hợp lệ.")
            }
            result.append(parsed)
        }
        return (result[0], result[1], result[2])
    }

    private static func aligned16(_ value: Int) -> Int {
        let remainder = value % 16
        return remainder == 0 ? value : value + (16 - remainder)
    }

    private static func patchUInt32(
        _ data: inout Data,
        at offset: Int,
        value: UInt32,
        littleEndian: Bool
    ) {
        var bytes = Data()
        if littleEndian {
            bytes.append(UInt8(value & 0xFF))
            bytes.append(UInt8((value >> 8) & 0xFF))
            bytes.append(UInt8((value >> 16) & 0xFF))
            bytes.append(UInt8((value >> 24) & 0xFF))
        } else {
            bytes.append(UInt8((value >> 24) & 0xFF))
            bytes.append(UInt8((value >> 16) & 0xFF))
            bytes.append(UInt8((value >> 8) & 0xFF))
            bytes.append(UInt8(value & 0xFF))
        }
        data.replaceSubrange(offset..<(offset + 4), with: bytes)
    }

    private static func patchInt64(
        _ data: inout Data,
        at offset: Int,
        value: Int64,
        littleEndian: Bool
    ) {
        let bits = UInt64(bitPattern: value)
        var bytes = Data()
        if littleEndian {
            for shift in stride(from: 0, through: 56, by: 8) {
                bytes.append(UInt8((bits >> UInt64(shift)) & 0xFF))
            }
        } else {
            for shift in stride(from: 56, through: 0, by: -8) {
                bytes.append(UInt8((bits >> UInt64(shift)) & 0xFF))
            }
        }
        data.replaceSubrange(offset..<(offset + 8), with: bytes)
    }
}
