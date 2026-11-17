import Foundation

enum AoVSerializedFileRewriteError: LocalizedError {
    case unsupportedFormat(UInt32)
    case fileSizeMismatch
    case missingObject(Int64)
    case objectIsNotAssetBundle(Int64)
    case invalidObjectLayout(Int64)
    case verificationFailed(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedFormat(let version):
            return "Chỉ hỗ trợ tái dựng SerializedFile format 9–30; format \(version) cần layout legacy riêng."
        case .fileSizeMismatch:
            return "Kích thước SerializedFile đầu vào không khớp header."
        case .missingObject(let pathID):
            return "Không tìm thấy object pathID \(pathID) trong SerializedFile."
        case .objectIsNotAssetBundle(let pathID):
            return "Object pathID \(pathID) không phải AssetBundle class 142."
        case .invalidObjectLayout(let pathID):
            return "Object pathID \(pathID) có layout dữ liệu không hợp lệ."
        case .verificationFailed(let reason):
            return "SerializedFile sau ghi catalog không vượt qua kiểm tra: \(reason)"
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

enum AoVSerializedFileRewriter {
    /// Delegates object serialization and pointer validation to the stricter
    /// AssetBundle writer. That writer appends the replacement object at an
    /// aligned data offset, then updates the object's metadata and file size.
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
            throw AoVSerializedFileRewriteError.verificationFailed("pathID replacement không trùng target.")
        }

        let matchingObjects = summary.serializedObjects.filter { $0.pathID == pathID }
        guard matchingObjects.count == 1, let object = matchingObjects.first else {
            throw AoVSerializedFileRewriteError.missingObject(pathID)
        }
        guard object.classID == 142,
              summary.assetBundleObjects.contains(where: { $0.pathID == pathID }),
              summary.assetBundleRecords.contains(where: { $0.pathID == pathID }) else {
            throw AoVSerializedFileRewriteError.objectIsNotAssetBundle(pathID)
        }

        let originalSize = object.byteSize
        let rewritten = try AoVSerializedAssetBundleWriter.rewrite(
            serializedFile: source,
            assetBundleRecord: replacement
        )
        let verified = try AoVSerializedFileParser.inspect(rewritten)
        guard verified.fileSize == UInt64(rewritten.count),
              verified.serializedObjects.count == summary.serializedObjects.count,
              let updatedObject = verified.serializedObjects.first(where: { $0.pathID == pathID }),
              let updatedRecord = verified.assetBundleRecords.first(where: { $0.pathID == pathID }),
              updatedRecord == replacement else {
            throw AoVSerializedFileRewriteError.verificationFailed("Object table hoặc AssetBundle record sau khi parse lại không khớp.")
        }

        return AoVSerializedFileRewriteReport(
            data: rewritten,
            replacedPathIDs: [pathID],
            originalFileSize: summary.fileSize,
            rewrittenFileSize: UInt64(rewritten.count),
            originalObjectSizes: [pathID: originalSize],
            rewrittenObjectSizes: [pathID: updatedObject.byteSize],
            roundTripVerified: true
        )
    }
}
