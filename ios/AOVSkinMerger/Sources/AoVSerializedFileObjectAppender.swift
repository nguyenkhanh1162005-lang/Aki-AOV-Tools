import Foundation

struct AoVSerializedObjectAppendInput: Sendable {
    let pathID: Int64
    let classID: Int32
    let typeID: Int32
    let payload: Data
}

struct AoVSerializedObjectAppendReport: Sendable {
    let data: Data
    let appendedPathIDs: [Int64]
    let originalObjectCount: Int
    let finalObjectCount: Int
    let originalFileSize: UInt64
    let finalFileSize: UInt64
    let roundTripVerified: Bool
}

enum AoVSerializedFileObjectAppenderError: LocalizedError {
    case unsupportedFormat(UInt32)
    case invalidSource(String)
    case noObjects
    case duplicatePathID(Int64)
    case objectAlreadyExists(Int64)
    case invalidType(Int32, Int32)
    case emptyPayload(Int64)
    case payloadTooLarge(Int64)
    case invalidPayload(Int64)
    case resourceLimit

    var errorDescription: String? {
        switch self {
        case .unsupportedFormat(let version):
            return "Object appender chỉ hỗ trợ SerializedFile format 22–30, nhận \(version)."
        case .invalidSource(let reason):
            return "SerializedFile nền không hợp lệ: \(reason)"
        case .noObjects:
            return "Không có object mới để thêm."
        case .duplicatePathID(let id):
            return "Batch import có pathID bị trùng: \(id)."
        case .objectAlreadyExists(let id):
            return "SerializedFile nền đã có pathID \(id)."
        case .invalidType(let id, let classID):
            return "TypeID \(id) không tồn tại hoặc không khớp classID \(classID)."
        case .emptyPayload(let id):
            return "Object pathID \(id) có payload rỗng."
        case .payloadTooLarge(let id):
            return "Object pathID \(id) vượt giới hạn 16 MiB."
        case .invalidPayload(let id):
            return "Payload object pathID \(id) không khớp TypeTree schema của RES đích."
        case .resourceLimit:
            return "SerializedFile sau import vượt giới hạn 512 MiB hoặc tràn số."
        }
    }
}

private struct AoVSerializedFileAppendWriter {
    var data = Data()
    let littleEndian: Bool

    mutating func appendUInt32(_ value: UInt32) {
        if littleEndian {
            for shift in stride(from: 0, through: 24, by: 8) {
                data.append(UInt8(truncatingIfNeeded: value >> shift))
            }
        } else {
            for shift in stride(from: 24, through: 0, by: -8) {
                data.append(UInt8(truncatingIfNeeded: value >> shift))
            }
        }
    }

    mutating func appendUInt64(_ value: UInt64) {
        if littleEndian {
            for shift in stride(from: 0, through: 56, by: 8) {
                data.append(UInt8(truncatingIfNeeded: value >> shift))
            }
        } else {
            for shift in stride(from: 56, through: 0, by: -8) {
                data.append(UInt8(truncatingIfNeeded: value >> shift))
            }
        }
    }

    mutating func appendInt32(_ value: Int32) {
        appendUInt32(UInt32(bitPattern: value))
    }

    mutating func appendInt64(_ value: Int64) {
        appendUInt64(UInt64(bitPattern: value))
    }

    mutating func align(_ boundary: Int) {
        while data.count % boundary != 0 { data.append(0) }
    }

    mutating func patchInt32(at offset: Int, value: Int32) throws {
        guard offset >= 0, offset <= data.count, data.count - offset >= 4 else {
            throw AoVSerializedFileObjectAppenderError.invalidSource("objectCount field offset is outside metadata.")
        }
        var patch = AoVSerializedFileAppendWriter(littleEndian: littleEndian)
        patch.appendInt32(value)
        data.replaceSubrange(offset..<(offset + 4), with: patch.data)
    }

    mutating func patchUInt32BE(at offset: Int, value: UInt32) throws {
        guard offset >= 0, offset <= data.count, data.count - offset >= 4 else {
            throw AoVSerializedFileObjectAppenderError.invalidSource("metadataSize field offset is outside the header.")
        }
        var patch = AoVSerializedFileAppendWriter(littleEndian: false)
        patch.appendUInt32(value)
        data.replaceSubrange(offset..<(offset + 4), with: patch.data)
    }

    mutating func patchInt64BE(at offset: Int, value: Int64) throws {
        guard offset >= 0, offset <= data.count, data.count - offset >= 8 else {
            throw AoVSerializedFileObjectAppenderError.invalidSource("extended header field offset is outside the header.")
        }
        var patch = AoVSerializedFileAppendWriter(littleEndian: false)
        patch.appendInt64(value)
        data.replaceSubrange(offset..<(offset + 8), with: patch.data)
    }
}

/// Appends already-serialized, TypeTree-backed objects to a Unity 2022+
/// SerializedFile while retaining the existing type table and metadata tail.
/// Type discovery, PPtr remapping and payload serialization happen upstream.
enum AoVSerializedFileObjectAppender {
    private static let maximumFileBytes = 512 * 1024 * 1024
    private static let maximumObjectBytes = 16 * 1024 * 1024

    static func append(
        to source: Data,
        objects: [AoVSerializedObjectAppendInput]
    ) throws -> AoVSerializedObjectAppendReport {
        guard !objects.isEmpty else { throw AoVSerializedFileObjectAppenderError.noObjects }
        let summary = try AoVSerializedFileParser.inspect(source)
        guard (22...30).contains(summary.formatVersion) else {
            throw AoVSerializedFileObjectAppenderError.unsupportedFormat(summary.formatVersion)
        }
        guard summary.fileSize == UInt64(source.count),
              summary.metadataStartOffset == 48,
              summary.metadataStartOffset <= summary.objectTableStartOffset,
              summary.objectTableStartOffset <= summary.objectTableEndOffset,
              summary.objectTableEndOffset <= summary.metadataEndOffset,
              summary.metadataEndOffset <= source.count,
              summary.metadataSizeFieldOffset == 20,
              summary.fileSizeFieldOffset == 24,
              summary.dataOffsetFieldOffset == 32 else {
            throw AoVSerializedFileObjectAppenderError.invalidSource(
                "metadata/object table offsets are not a supported Unity 2022+ layout."
            )
        }

        var seen = Set<Int64>()
        let existingIDs = summary.serializedObjectPathIDs
        for object in objects {
            guard seen.insert(object.pathID).inserted else {
                throw AoVSerializedFileObjectAppenderError.duplicatePathID(object.pathID)
            }
            guard !existingIDs.contains(object.pathID) else {
                throw AoVSerializedFileObjectAppenderError.objectAlreadyExists(object.pathID)
            }
            guard object.pathID > 0,
                  object.typeID >= 0,
                  let typeIndex = Int(exactly: object.typeID),
                  summary.serializedTypes.indices.contains(typeIndex),
                  summary.serializedTypes[typeIndex].classID == object.classID else {
                throw AoVSerializedFileObjectAppenderError.invalidType(object.typeID, object.classID)
            }
            guard !summary.serializedTypes[typeIndex].typeTreeNodes.isEmpty else {
                throw AoVSerializedFileObjectAppenderError.invalidPayload(object.pathID)
            }
            guard !object.payload.isEmpty else {
                throw AoVSerializedFileObjectAppenderError.emptyPayload(object.pathID)
            }
            guard object.payload.count <= maximumObjectBytes else {
                throw AoVSerializedFileObjectAppenderError.payloadTooLarge(object.pathID)
            }
            do {
                let nodes = summary.serializedTypes[typeIndex].typeTreeNodes
                let decoded = try AoVUnityTypeTreeReader.read(
                    nodes: nodes,
                    data: object.payload,
                    littleEndian: summary.littleEndian
                )
                let encoded = try AoVUnityTypeTreeWriter.write(
                    nodes: nodes,
                    value: decoded,
                    littleEndian: summary.littleEndian
                )
                guard encoded == object.payload else {
                    throw AoVSerializedFileObjectAppenderError.invalidPayload(object.pathID)
                }
            } catch {
                throw AoVSerializedFileObjectAppenderError.invalidPayload(object.pathID)
            }
        }

        let orderedObjects = objects.sorted { $0.pathID < $1.pathID }
        let beforeObjectTable = source.subdata(
            in: summary.metadataStartOffset..<summary.objectTableStartOffset
        )
        let existingObjectTable = source.subdata(
            in: summary.objectTableStartOffset..<summary.objectTableEndOffset
        )
        let afterObjectTable = source.subdata(
            in: summary.objectTableEndOffset..<summary.metadataEndOffset
        )
        var metadata = AoVSerializedFileAppendWriter(littleEndian: summary.littleEndian)
        metadata.data = beforeObjectTable

        let newObjectCount = summary.objectCount.addingReportingOverflow(objects.count)
        guard !newObjectCount.overflow,
              let objectCountValue = Int32(exactly: newObjectCount.partialValue) else {
            throw AoVSerializedFileObjectAppenderError.resourceLimit
        }
        try metadata.patchInt32(
            at: summary.objectCountFieldOffset - summary.metadataStartOffset,
            value: objectCountValue
        )

        // Preserve the original object table byte-for-byte, including its
        // initial pathID alignment padding, before appending new records.
        metadata.data.append(existingObjectTable)
        // Object records use 4-byte alignment for pathID in SerializedFile v22+.
        metadata.align(4)
        var dataRegion = source.subdata(
            in: Int(summary.dataOffset)..<Int(summary.fileSize)
        )
        alignData(&dataRegion, to: 16)

        for object in orderedObjects {
            alignData(&dataRegion, to: 16)
            guard let relativeOffset = UInt64(exactly: dataRegion.count) else {
                throw AoVSerializedFileObjectAppenderError.resourceLimit
            }
            dataRegion.append(object.payload)

            metadata.appendInt64(object.pathID)
            metadata.appendInt64(Int64(relativeOffset))
            metadata.appendUInt32(UInt32(object.payload.count))
            metadata.appendInt32(object.typeID)
        }
        metadata.data.append(afterObjectTable)

        let metadataSize = metadata.data.count
        let newDataOffset = aligned16(summary.metadataStartOffset + metadataSize)
        let newFileSize = UInt64(newDataOffset).addingReportingOverflow(UInt64(dataRegion.count))
        guard !newFileSize.overflow,
              newFileSize.partialValue <= UInt64(maximumFileBytes),
              let newFileSizeInt64 = Int64(exactly: newFileSize.partialValue),
              let newDataOffsetInt64 = Int64(exactly: newDataOffset),
              let metadataSizeUInt32 = UInt32(exactly: metadataSize) else {
            throw AoVSerializedFileObjectAppenderError.resourceLimit
        }

        guard let newFileSizeUInt32 = UInt32(exactly: newFileSize.partialValue),
              let newDataOffsetUInt32 = UInt32(exactly: newDataOffset) else {
            throw AoVSerializedFileObjectAppenderError.resourceLimit
        }
        var header = AoVSerializedFileAppendWriter(littleEndian: false)
        header.data = Data(source.prefix(summary.metadataStartOffset))
        // Keep the legacy 32-bit header mirrors consistent with the widened
        // Unity 2022+ header fields. The appender's 512 MiB cap keeps them exact.
        try header.patchUInt32BE(at: 0, value: metadataSizeUInt32)
        try header.patchUInt32BE(at: 4, value: newFileSizeUInt32)
        try header.patchUInt32BE(at: 12, value: newDataOffsetUInt32)
        try header.patchUInt32BE(
            at: summary.metadataSizeFieldOffset,
            value: metadataSizeUInt32
        )
        try header.patchInt64BE(
            at: summary.fileSizeFieldOffset,
            value: newFileSizeInt64
        )
        try header.patchInt64BE(
            at: summary.dataOffsetFieldOffset,
            value: newDataOffsetInt64
        )
        var output = header.data
        output.append(metadata.data)
        if output.count < newDataOffset {
            output.append(Data(repeating: 0, count: newDataOffset - output.count))
        }
        output.append(dataRegion)

        let verified = try AoVSerializedFileParser.inspect(output)
        guard verified.fileSize == UInt64(output.count),
              verified.objectCount == newObjectCount.partialValue,
              verified.serializedObjects.count == newObjectCount.partialValue else {
            throw AoVSerializedFileObjectAppenderError.invalidSource(
                "object table count did not survive parser round-trip."
            )
        }
        let dataOffsetDelta = UInt64(newDataOffset).subtractingReportingOverflow(summary.dataOffset)
        guard !dataOffsetDelta.overflow else {
            throw AoVSerializedFileObjectAppenderError.invalidSource("dataOffset unexpectedly moved backwards.")
        }
        for originalObject in summary.serializedObjects {
            guard let verifiedObject = verified.serializedObjectsByPathID[originalObject.pathID],
            verifiedObject.classID == originalObject.classID,
            verifiedObject.typeID == originalObject.typeID,
            verifiedObject.byteSize == originalObject.byteSize else {
                throw AoVSerializedFileObjectAppenderError.invalidSource(
                    "an existing object identity or size changed during append."
                )
            }
            let expectedOffset = originalObject.byteOffset.addingReportingOverflow(dataOffsetDelta.partialValue)
            guard !expectedOffset.overflow, verifiedObject.byteOffset == expectedOffset.partialValue,
                  let oldStart = Int(exactly: originalObject.byteOffset),
                  let newStart = Int(exactly: verifiedObject.byteOffset),
                  let oldSize = Int(exactly: originalObject.byteSize),
                  oldStart >= 0, newStart >= 0,
                  oldStart <= source.count, oldSize <= source.count - oldStart,
                  newStart <= output.count, oldSize <= output.count - newStart,
                  source[oldStart..<(oldStart + oldSize)].elementsEqual(
                    output[newStart..<(newStart + oldSize)]
                  ) else {
                throw AoVSerializedFileObjectAppenderError.invalidSource(
                    "an existing object payload changed during append."
                )
            }
        }
        for object in orderedObjects {
            guard let verifiedObject = verified.serializedObjectsByPathID[object.pathID],
            verifiedObject.classID == object.classID,
            verifiedObject.typeID == object.typeID,
            let start = Int(exactly: verifiedObject.byteOffset),
            let size = Int(exactly: verifiedObject.byteSize),
            size == object.payload.count,
            start >= 0, start <= output.count, size <= output.count - start,
            output.subdata(in: start..<(start + size)) == object.payload else {
                throw AoVSerializedFileObjectAppenderError.invalidSource(
                    "appended object \(object.pathID) differs after parser round-trip."
                )
            }
        }

        return AoVSerializedObjectAppendReport(
            data: output,
            appendedPathIDs: orderedObjects.map(\.pathID),
            originalObjectCount: summary.objectCount,
            finalObjectCount: verified.objectCount,
            originalFileSize: summary.fileSize,
            finalFileSize: UInt64(output.count),
            roundTripVerified: true
        )
    }

    private static func aligned16(_ value: Int) -> Int {
        let remainder = value % 16
        return remainder == 0 ? value : value + (16 - remainder)
    }

    private static func alignData(_ data: inout Data, to boundary: Int) {
        guard boundary > 0 else { return }
        let remainder = data.count % boundary
        if remainder != 0 {
            data.append(Data(repeating: 0, count: boundary - remainder))
        }
    }
}
