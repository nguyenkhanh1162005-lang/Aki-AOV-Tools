import Foundation

enum AoVSerializedRuntimeCatalogWriteError: LocalizedError {
    case unsupportedFileVersion(UInt32)
    case objectNotFound(Int64)
    case duplicateObjectPathID(Int64)
    case objectNotMonoBehaviour(Int64)
    case malformedObject(String)
    case objectTooLarge
    case fileTooLarge
    case integerOverflow
    case validationFailed(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedFileVersion(let version):
            return "Chưa hỗ trợ ghi runtime catalog trong SerializedFile v\(version)."
        case .objectNotFound(let pathID):
            return "Không tìm thấy MonoBehaviour pathID \(pathID)."
        case .duplicateObjectPathID(let pathID):
            return "SerializedFile có nhiều MonoBehaviour pathID \(pathID)."
        case .objectNotMonoBehaviour(let pathID):
            return "Object pathID \(pathID) không thuộc class MonoBehaviour (114)."
        case .malformedObject(let reason):
            return "MonoBehaviour runtime catalog không hợp lệ: \(reason)"
        case .objectTooLarge:
            return "MonoBehaviour sau khi ghi vượt giới hạn 64 MiB."
        case .fileTooLarge:
            return "SerializedFile sau khi ghi vượt giới hạn 512 MiB."
        case .integerOverflow:
            return "Offset hoặc kích thước vượt giới hạn biểu diễn số nguyên."
        case .validationFailed(let reason):
            return "SerializedFile runtime catalog không qua kiểm tra: \(reason)"
        }
    }
}

struct AoVSerializedRuntimeCatalogWriteReport: Sendable {
    let data: Data
    let objectPathID: Int64
    let catalogName: String
    let originalObjectBytes: UInt64
    let rewrittenObjectBytes: UInt64
    let originalFileBytes: UInt64
    let rewrittenFileBytes: UInt64
    let roundTripVerified: Bool
}

/// Rewrites a single class-114 AoV Packer or Verification MonoBehaviour in a
/// Unity 2022.3 / SerializedFile v22 node. All unrelated objects and metadata
/// remain byte-for-byte in place; the replacement object is appended and its
/// object-table offset/size are updated before a full parser round-trip check.
enum AoVSerializedRuntimeCatalogWriter {
    private static let maximumObjectBytes = 64 * 1024 * 1024
    private static let maximumFileBytes = 512 * 1024 * 1024

    static func rewrite(
        serializedFile data: Data,
        objectPathID: Int64,
        with replacement: AoVRuntimeResourceCatalog
    ) throws -> AoVSerializedRuntimeCatalogWriteReport {
        let summary: AoVSerializedFileSummary
        do {
            summary = try AoVSerializedFileParser.inspect(data)
        } catch {
            throw AoVSerializedRuntimeCatalogWriteError.validationFailed(error.localizedDescription)
        }

        guard summary.formatVersion >= 22,
              summary.unityVersion.hasPrefix("2022.3.") else {
            throw AoVSerializedRuntimeCatalogWriteError.unsupportedFileVersion(summary.formatVersion)
        }
        guard summary.fileSize == UInt64(data.count) else {
            throw AoVSerializedRuntimeCatalogWriteError.validationFailed(
                "fileSize không trùng kích thước dữ liệu đầu vào."
            )
        }

        let matches = summary.serializedObjects.filter { $0.pathID == objectPathID }
        guard !matches.isEmpty else {
            throw AoVSerializedRuntimeCatalogWriteError.objectNotFound(objectPathID)
        }
        guard matches.count == 1, let target = matches.first else {
            throw AoVSerializedRuntimeCatalogWriteError.duplicateObjectPathID(objectPathID)
        }
        guard target.classID == 114 else {
            throw AoVSerializedRuntimeCatalogWriteError.objectNotMonoBehaviour(objectPathID)
        }
        guard target.byteSize > 0,
              target.byteSize <= maximumObjectBytes,
              let start = Int(exactly: target.byteOffset),
              let size = Int(exactly: target.byteSize),
              start <= data.count, size <= data.count - start else {
            throw AoVSerializedRuntimeCatalogWriteError.malformedObject(
                "Object pathID \(objectPathID) rỗng hoặc vượt giới hạn."
            )
        }

        let originalPayload = data.subdata(in: start..<(start + size))
        let replacementPayload: Data
        do {
            replacementPayload = try AoVRuntimeResourceCatalogParser.rewriteMonoBehaviour(
                originalPayload,
                with: replacement,
                serializedFormatVersion: summary.formatVersion,
                unityVersion: summary.unityVersion,
                littleEndian: summary.littleEndian
            )
        } catch {
            throw AoVSerializedRuntimeCatalogWriteError.malformedObject(error.localizedDescription)
        }
        guard replacementPayload.count <= maximumObjectBytes,
              let replacementSize = UInt32(exactly: replacementPayload.count) else {
            throw AoVSerializedRuntimeCatalogWriteError.objectTooLarge
        }

        let remainder = data.count % 16
        let alignedStartResult = data.count.addingReportingOverflow(remainder == 0 ? 0 : 16 - remainder)
        guard !alignedStartResult.overflow else {
            throw AoVSerializedRuntimeCatalogWriteError.integerOverflow
        }
        let alignedStart = alignedStartResult.partialValue
        guard let absoluteStart = UInt64(exactly: alignedStart),
              absoluteStart >= summary.dataOffset else {
            throw AoVSerializedRuntimeCatalogWriteError.integerOverflow
        }
        let relativeStart = absoluteStart - summary.dataOffset
        let newFileSize = alignedStart.addingReportingOverflow(replacementPayload.count)
        guard !newFileSize.overflow,
              newFileSize.partialValue <= maximumFileBytes,
              let newFileSize64 = UInt64(exactly: newFileSize.partialValue),
              let relativeOffset = Int64(exactly: relativeStart),
              let extendedFileSize = Int64(exactly: newFileSize64) else {
            throw AoVSerializedRuntimeCatalogWriteError.fileTooLarge
        }

        var output = data
        if alignedStart > output.count {
            output.append(Data(repeating: 0, count: alignedStart - output.count))
        }
        output.append(replacementPayload)

        patchInt64(
            &output,
            at: target.byteStartFieldOffset,
            value: relativeOffset,
            littleEndian: summary.littleEndian
        )
        patchUInt32(
            &output,
            at: target.byteSizeFieldOffset,
            value: replacementSize,
            littleEndian: summary.littleEndian
        )
        patchInt64(
            &output,
            at: summary.fileSizeFieldOffset,
            value: extendedFileSize,
            littleEndian: false
        )

        let verified: AoVSerializedFileSummary
        do {
            verified = try AoVSerializedFileParser.inspect(output)
        } catch {
            throw AoVSerializedRuntimeCatalogWriteError.validationFailed(error.localizedDescription)
        }

        let replacementMatches: Bool
        switch replacement {
        case .packer(let expected):
            replacementMatches = verified.resourcePackerCatalog == expected &&
                verified.resourceVerificationCatalog == summary.resourceVerificationCatalog
        case .verification(let expected):
            replacementMatches = verified.resourceVerificationCatalog == expected &&
                verified.resourcePackerCatalog == summary.resourcePackerCatalog
        }
        let unaffectedObjectsMatch = verified.serializedObjects
            .filter { $0.pathID != objectPathID } ==
            summary.serializedObjects.filter { $0.pathID != objectPathID }

        guard verified.fileSize == UInt64(output.count),
              replacementMatches,
              unaffectedObjectsMatch else {
            throw AoVSerializedRuntimeCatalogWriteError.validationFailed(
                "Catalog mới, fileSize hoặc object table không khớp sau khi parse lại."
            )
        }

        let catalogName: String
        switch replacement {
        case .packer(let catalog): catalogName = catalog.name
        case .verification(let catalog): catalogName = catalog.name
        }
        return AoVSerializedRuntimeCatalogWriteReport(
            data: output,
            objectPathID: objectPathID,
            catalogName: catalogName,
            originalObjectBytes: UInt64(originalPayload.count),
            rewrittenObjectBytes: UInt64(replacementPayload.count),
            originalFileBytes: UInt64(data.count),
            rewrittenFileBytes: UInt64(output.count),
            roundTripVerified: true
        )
    }

    private static func patchUInt32(
        _ data: inout Data,
        at offset: Int,
        value: UInt32,
        littleEndian: Bool
    ) {
        var bytes = Data()
        if littleEndian {
            for shift in stride(from: 0, through: 24, by: 8) {
                bytes.append(UInt8(truncatingIfNeeded: value >> shift))
            }
        } else {
            for shift in stride(from: 24, through: 0, by: -8) {
                bytes.append(UInt8(truncatingIfNeeded: value >> shift))
            }
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
                bytes.append(UInt8(truncatingIfNeeded: bits >> UInt64(shift)))
            }
        } else {
            for shift in stride(from: 56, through: 0, by: -8) {
                bytes.append(UInt8(truncatingIfNeeded: bits >> UInt64(shift)))
            }
        }
        data.replaceSubrange(offset..<(offset + 8), with: bytes)
    }
}
