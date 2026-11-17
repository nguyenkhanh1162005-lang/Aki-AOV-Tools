import Foundation

struct AoVSerializedFileObject: Codable, Hashable, Sendable, Identifiable {
    let pathID: Int64
    let classID: Int32
    let typeID: Int32
    let byteOffset: UInt64
    let byteSize: UInt32
    var id: String { "\(classID):\(pathID)" }
}

struct AoVSerializedFileSummary: Codable, Sendable {
    let formatVersion: UInt32
    let unityVersion: String
    let targetPlatform: Int32
    let fileSize: UInt64
    let dataOffset: UInt64
    let typeCount: Int
    let objectCount: Int
    let assetBundleObjects: [AoVSerializedFileObject]

    var assetBundleObjectCount: Int { assetBundleObjects.count }
}

enum AoVSerializedFileError: LocalizedError {
    case notSerializedFile
    case truncated(String)
    case invalidHeader(String)
    case resourceLimit(String)
    case unsupportedTypeTreeVersion(UInt32)
    case invalidTypeIndex(Int32)
    case invalidObjectRange(Int64)

    var errorDescription: String? {
        switch self {
        case .notSerializedFile:
            return "Node không bắt đầu bằng một SerializedFile Unity hợp lệ."
        case .truncated(let field):
            return "SerializedFile bị cắt cụt tại \(field)."
        case .invalidHeader(let reason):
            return "Header SerializedFile không hợp lệ: \(reason)"
        case .resourceLimit(let reason):
            return "SerializedFile vượt giới hạn kiểm tra: \(reason)"
        case .unsupportedTypeTreeVersion(let version):
            return "TypeTree legacy của SerializedFile version \(version) chưa được hỗ trợ."
        case .invalidTypeIndex(let index):
            return "SerializedFile có type index không hợp lệ: \(index)."
        case .invalidObjectRange(let pathID):
            return "Object pathID \(pathID) nằm ngoài vùng dữ liệu SerializedFile."
        }
    }
}

private struct AoVSerializedReader {
    let data: Data
    var offset: Int = 0
    var littleEndian = false

    mutating func readByte(_ field: String) throws -> UInt8 {
        guard offset >= 0, offset < data.count else {
            throw AoVSerializedFileError.truncated(field)
        }
        let value = data[offset]
        offset += 1
        return value
    }

    mutating func readBytes(_ count: Int, _ field: String) throws -> Data {
        guard count >= 0, offset >= 0, offset <= data.count,
              count <= data.count - offset else {
            throw AoVSerializedFileError.truncated(field)
        }
        let value = data.subdata(in: offset..<(offset + count))
        offset += count
        return value
    }

    mutating func skip(_ count: Int, _ field: String) throws {
        guard count >= 0, offset >= 0, offset <= data.count,
              count <= data.count - offset else {
            throw AoVSerializedFileError.truncated(field)
        }
        offset += count
    }

    mutating func align(_ boundary: Int) throws {
        guard boundary > 0, boundary.nonzeroBitCount == 1 else {
            throw AoVSerializedFileError.invalidHeader("alignment không hợp lệ.")
        }
        let remainder = offset % boundary
        if remainder != 0 {
            try skip(boundary - remainder, "alignment")
        }
    }

    mutating func readUInt16(_ field: String) throws -> UInt16 {
        let a = UInt16(try readByte(field))
        let b = UInt16(try readByte(field))
        return littleEndian ? (a | (b << 8)) : ((a << 8) | b)
    }

    mutating func readInt16(_ field: String) throws -> Int16 {
        Int16(bitPattern: try readUInt16(field))
    }

    mutating func readUInt32(_ field: String) throws -> UInt32 {
        let a = UInt32(try readByte(field))
        let b = UInt32(try readByte(field))
        let c = UInt32(try readByte(field))
        let d = UInt32(try readByte(field))
        if littleEndian {
            return a | (b << 8) | (c << 16) | (d << 24)
        }
        return (a << 24) | (b << 16) | (c << 8) | d
    }

    mutating func readInt32(_ field: String) throws -> Int32 {
        Int32(bitPattern: try readUInt32(field))
    }

    mutating func readUInt64(_ field: String) throws -> UInt64 {
        let a = UInt64(try readUInt32(field))
        let b = UInt64(try readUInt32(field))
        return littleEndian ? (a | (b << 32)) : ((a << 32) | b)
    }

    mutating func readInt64(_ field: String) throws -> Int64 {
        Int64(bitPattern: try readUInt64(field))
    }

    mutating func readCString(_ field: String, maximumBytes: Int = 1_048_576) throws -> String {
        guard maximumBytes > 0, offset >= 0, offset < data.count else {
            throw AoVSerializedFileError.truncated(field)
        }
        let start = offset
        let endLimit = min(data.count, start + maximumBytes + 1)
        while offset < endLimit {
            if data[offset] == 0 {
                let value = data.subdata(in: start..<offset)
                offset += 1
                guard let string = String(data: value, encoding: .utf8) else {
                    throw AoVSerializedFileError.invalidHeader("\(field) không phải UTF-8.")
                }
                return string
            }
            offset += 1
        }
        throw AoVSerializedFileError.truncated("\(field) (thiếu byte kết thúc hoặc quá dài)")
    }

    mutating func readAlignedString(_ field: String, maximumBytes: Int = 1_048_576) throws -> String {
        let rawLength = try readInt32("\(field) length")
        guard rawLength >= 0, rawLength <= maximumBytes else {
            throw AoVSerializedFileError.resourceLimit("\(field) có chiều dài \(rawLength) không hợp lệ.")
        }
        let bytes = try readBytes(Int(rawLength), field)
        guard let value = String(data: bytes, encoding: .utf8) else {
            throw AoVSerializedFileError.invalidHeader("\(field) không phải UTF-8.")
        }
        try align(4)
        return value
    }

    mutating func readCount(_ field: String, maximum: Int) throws -> Int {
        let raw = try readInt32(field)
        guard raw >= 0, raw <= maximum else {
            throw AoVSerializedFileError.resourceLimit("\(field) là \(raw), vượt giới hạn \(maximum).")
        }
        return Int(raw)
    }
}

enum AoVSerializedFileParser {
    private static let maximumFileBytes = 512 * 1024 * 1024
    private static let maximumTypes = 8_192
    private static let maximumObjects = 500_000
    private static let maximumTypeTreeNodes = 100_000
    private static let maximumStringBufferBytes = 16 * 1024 * 1024
    private static let assetBundleClassID: Int32 = 142

    static func inspect(_ data: Data) throws -> AoVSerializedFileSummary {
        guard data.count >= 20 else {
            throw AoVSerializedFileError.truncated("header")
        }
        guard data.count <= maximumFileBytes else {
            throw AoVSerializedFileError.resourceLimit("file lớn hơn 512 MiB.")
        }

        var reader = AoVSerializedReader(data: data)
        let initialMetadataSize = UInt64(try reader.readUInt32("metadata size"))
        let initialFileSize = UInt64(try reader.readUInt32("file size"))
        let formatVersion = try reader.readUInt32("format version")
        let initialDataOffset = UInt64(try reader.readUInt32("data offset"))

        guard (5...30).contains(formatVersion) else {
            throw AoVSerializedFileError.invalidHeader("format version \(formatVersion) ngoài dải kiểm tra 5–30.")
        }

        let metadataSize: UInt64
        let fileSize: UInt64
        let dataOffset: UInt64

        if formatVersion >= 9 {
            let endianFlag = try reader.readByte("endianness")
            guard endianFlag <= 1 else {
                throw AoVSerializedFileError.invalidHeader("endianness flag \(endianFlag) không hợp lệ.")
            }
            try reader.skip(3, "reserved header bytes")
            reader.littleEndian = endianFlag == 0

            if formatVersion >= 22 {
                // Unity 2020+ extended header replaces the legacy size/offset fields.
                metadataSize = UInt64(try reader.readUInt32("extended metadata size"))
                let extendedFileSize = try reader.readInt64("extended file size")
                let extendedDataOffset = try reader.readInt64("extended data offset")
                _ = try reader.readInt64("extended unknown header field")
                guard extendedFileSize >= 0, extendedDataOffset >= 0 else {
                    throw AoVSerializedFileError.invalidHeader("extended size/offset âm.")
                }
                fileSize = UInt64(extendedFileSize)
                dataOffset = UInt64(extendedDataOffset)
            } else {
                metadataSize = initialMetadataSize
                fileSize = initialFileSize
                dataOffset = initialDataOffset
            }
        } else {
            guard initialFileSize <= UInt64(data.count),
                  initialMetadataSize > 0,
                  initialMetadataSize <= initialFileSize else {
                throw AoVSerializedFileError.invalidHeader("legacy file/metadata size không hợp lệ.")
            }
            fileSize = initialFileSize
            metadataSize = initialMetadataSize
            dataOffset = initialDataOffset
            guard let metadataStart = Int(exactly: fileSize - metadataSize),
                  metadataStart <= data.count else {
                throw AoVSerializedFileError.invalidHeader("metadata start vượt file.")
            }
            reader.offset = metadataStart
            let endianFlag = try reader.readByte("legacy endianness")
            guard endianFlag <= 1 else {
                throw AoVSerializedFileError.invalidHeader("legacy endianness flag không hợp lệ.")
            }
            reader.littleEndian = endianFlag == 0
        }

        guard fileSize <= UInt64(data.count), dataOffset <= fileSize,
              dataOffset <= UInt64(Int.max) else {
            throw AoVSerializedFileError.invalidHeader("fileSize/dataOffset vượt dữ liệu đầu vào.")
        }

        let metadataStart = reader.offset
        let metadataEndValue = UInt64(metadataStart).addingReportingOverflow(metadataSize)
        guard !metadataEndValue.overflow, metadataEndValue.partialValue <= fileSize else {
            throw AoVSerializedFileError.invalidHeader("metadata size vượt phạm vi file.")
        }
        let metadataEnd = Int(metadataEndValue.partialValue)

        var unityVersion = "unknown"
        if formatVersion >= 7 {
            unityVersion = try reader.readCString("Unity version")
        }

        var targetPlatform: Int32 = 0
        if formatVersion >= 8 {
            targetPlatform = try reader.readInt32("target platform")
        }

        var enableTypeTree = false
        if formatVersion >= 13 {
            let flag = try reader.readByte("type tree enabled")
            guard flag <= 1 else {
                throw AoVSerializedFileError.invalidHeader("type tree flag không hợp lệ.")
            }
            enableTypeTree = flag == 1
        }

        let typeCount = try reader.readCount("type count", maximum: maximumTypes)
        var classIDs: [Int32] = []
        classIDs.reserveCapacity(typeCount)

        for index in 0..<typeCount {
            let classID = try reader.readInt32("type \(index) class ID")

            if formatVersion >= 16 {
                _ = try reader.readByte("type \(index) stripped flag")
            }

            var scriptTypeIndex: Int16 = -1
            if formatVersion >= 17 {
                scriptTypeIndex = try reader.readInt16("type \(index) script type index")
            }

            if formatVersion >= 13 {
                let hasScriptID = (formatVersion < 16 && classID < 0) ||
                    (formatVersion >= 16 && classID == 114)
                if hasScriptID {
                    try reader.skip(16, "type \(index) script ID")
                }
                try reader.skip(16, "type \(index) old type hash")
            }

            if enableTypeTree {
                try skipTypeTree(
                    &reader,
                    formatVersion: formatVersion,
                    label: "type \(index)"
                )
            }

            if formatVersion >= 21 {
                // SerializedType dependencies; reference types instead carry three names.
                // The normal type table uses the dependency array.
                let dependencyCount = try reader.readCount("type \(index) dependency count", maximum: 1_000_000)
                try reader.skip(dependencyCount * 4, "type \(index) dependencies")
            }

            _ = scriptTypeIndex
            classIDs.append(classID)
        }

        var bigIDEnabled = false
        if formatVersion >= 7 && formatVersion < 14 {
            bigIDEnabled = try reader.readInt32("big ID enabled") != 0
        }

        let objectCount = try reader.readCount("object count", maximum: maximumObjects)
        var assetBundleObjects: [AoVSerializedFileObject] = []

        for index in 0..<objectCount {
            let pathID: Int64
            if bigIDEnabled {
                pathID = try reader.readInt64("object \(index) path ID")
            } else if formatVersion < 14 {
                pathID = Int64(try reader.readInt32("object \(index) path ID"))
            } else {
                try reader.align(4)
                pathID = try reader.readInt64("object \(index) path ID")
            }

            let rawByteStart: UInt64
            if formatVersion >= 22 {
                let value = try reader.readInt64("object \(index) byte start")
                guard value >= 0 else {
                    throw AoVSerializedFileError.invalidObjectRange(pathID)
                }
                rawByteStart = UInt64(value)
            } else {
                rawByteStart = UInt64(try reader.readUInt32("object \(index) byte start"))
            }

            let byteSize = try reader.readUInt32("object \(index) byte size")
            let typeID = try reader.readInt32("object \(index) type ID")

            let classID: Int32
            if formatVersion < 16 {
                classID = Int32(try reader.readUInt16("object \(index) class ID"))
            } else {
                guard typeID >= 0, Int(typeID) < classIDs.count else {
                    throw AoVSerializedFileError.invalidTypeIndex(typeID)
                }
                classID = classIDs[Int(typeID)]
            }

            if formatVersion < 11 {
                _ = try reader.readUInt16("object \(index) destroyed flag")
            }
            if formatVersion >= 11 && formatVersion < 17 {
                _ = try reader.readInt16("object \(index) script type index")
            }
            if formatVersion == 15 || formatVersion == 16 {
                _ = try reader.readByte("object \(index) stripped flag")
            }

            let absoluteOffset = dataOffset.addingReportingOverflow(rawByteStart)
            guard !absoluteOffset.overflow else {
                throw AoVSerializedFileError.invalidObjectRange(pathID)
            }
            let end = absoluteOffset.partialValue.addingReportingOverflow(UInt64(byteSize))
            guard !end.overflow, end.partialValue <= fileSize else {
                throw AoVSerializedFileError.invalidObjectRange(pathID)
            }

            if classID == assetBundleClassID {
                assetBundleObjects.append(
                    AoVSerializedFileObject(
                        pathID: pathID,
                        classID: classID,
                        typeID: typeID,
                        byteOffset: absoluteOffset.partialValue,
                        byteSize: byteSize
                    )
                )
            }
        }

        if formatVersion >= 11 {
            let scriptCount = try reader.readCount("script count", maximum: 1_000_000)
            for index in 0..<scriptCount {
                _ = try reader.readInt32("script \(index) file index")
                if formatVersion < 14 {
                    _ = try reader.readInt32("script \(index) local ID")
                } else {
                    try reader.align(4)
                    _ = try reader.readInt64("script \(index) local ID")
                }
            }
        }

        let externalCount = try reader.readCount("external count", maximum: 100_000)
        for index in 0..<externalCount {
            if formatVersion >= 6 {
                _ = try reader.readCString("external \(index) temporary name")
            }
            if formatVersion >= 5 {
                try reader.skip(16, "external \(index) GUID")
                _ = try reader.readInt32("external \(index) type")
            }
            _ = try reader.readCString("external \(index) path")
        }

        if formatVersion >= 20 {
            let refTypeCount = try reader.readCount("reference type count", maximum: maximumTypes)
            for index in 0..<refTypeCount {
                try skipReferenceType(
                    &reader,
                    formatVersion: formatVersion,
                    enableTypeTree: enableTypeTree,
                    label: "reference type \(index)"
                )
            }
        }

        if formatVersion >= 5 {
            _ = try reader.readCString("user information")
        }

        guard reader.offset <= metadataEnd else {
            throw AoVSerializedFileError.invalidHeader(
                "metadata parser đọc vượt metadataEnd (\(reader.offset) > \(metadataEnd))."
            )
        }

        return AoVSerializedFileSummary(
            formatVersion: formatVersion,
            unityVersion: unityVersion,
            targetPlatform: targetPlatform,
            fileSize: fileSize,
            dataOffset: dataOffset,
            typeCount: typeCount,
            objectCount: objectCount,
            assetBundleObjects: assetBundleObjects
        )
    }

    private static func skipTypeTree(
        _ reader: inout AoVSerializedReader,
        formatVersion: UInt32,
        label: String
    ) throws {
        if formatVersion >= 12 || formatVersion == 10 {
            let nodeCount = try reader.readCount("\(label) type tree node count", maximum: maximumTypeTreeNodes)
            let stringSize = try reader.readCount(
                "\(label) type tree string buffer size",
                maximum: maximumStringBufferBytes
            )
            let nodeStride = formatVersion >= 19 ? 32 : 24
            let nodeBytes = nodeCount.multipliedReportingOverflow(by: nodeStride)
            guard !nodeBytes.overflow else {
                throw AoVSerializedFileError.resourceLimit("\(label) type tree kích thước bị tràn.")
            }
            try reader.skip(nodeBytes.partialValue, "\(label) type tree nodes")
            try reader.skip(stringSize, "\(label) type tree strings")
            return
        }

        guard formatVersion < 12 else {
            throw AoVSerializedFileError.unsupportedTypeTreeVersion(formatVersion)
        }

        var remainingNodes = 1
        var parsed = 0
        while remainingNodes > 0 {
            parsed += 1
            guard parsed <= maximumTypeTreeNodes else {
                throw AoVSerializedFileError.resourceLimit("\(label) legacy type tree có quá nhiều node.")
            }
            remainingNodes -= 1
            _ = try reader.readCString("\(label) legacy node type")
            _ = try reader.readCString("\(label) legacy node name")
            _ = try reader.readInt32("\(label) legacy node byte size")
            if formatVersion == 2 {
                _ = try reader.readInt32("\(label) legacy variable count")
            }
            if formatVersion != 3 {
                _ = try reader.readInt32("\(label) legacy node index")
            }
            _ = try reader.readInt32("\(label) legacy type flags")
            _ = try reader.readInt32("\(label) legacy node version")
            if formatVersion != 3 {
                _ = try reader.readInt32("\(label) legacy meta flags")
            }
            let children = try reader.readCount("\(label) legacy child count", maximum: maximumTypeTreeNodes)
            let next = remainingNodes.addingReportingOverflow(children)
            guard !next.overflow else {
                throw AoVSerializedFileError.resourceLimit("\(label) legacy node count overflow.")
            }
            remainingNodes = next.partialValue
        }
    }

    private static func skipReferenceType(
        _ reader: inout AoVSerializedReader,
        formatVersion: UInt32,
        enableTypeTree: Bool,
        label: String
    ) throws {
        let classID = try reader.readInt32("\(label) class ID")
        if formatVersion >= 16 {
            _ = try reader.readByte("\(label) stripped flag")
        }
        var scriptTypeIndex: Int16 = -1
        if formatVersion >= 17 {
            scriptTypeIndex = try reader.readInt16("\(label) script type index")
        }
        if formatVersion >= 13 {
            if scriptTypeIndex >= 0 {
                try reader.skip(16, "\(label) script ID")
            }
            try reader.skip(16, "\(label) old type hash")
        }
        if enableTypeTree {
            try skipTypeTree(&reader, formatVersion: formatVersion, label: label)
        }
        if formatVersion >= 21 {
            _ = try reader.readCString("\(label) class name")
            _ = try reader.readCString("\(label) namespace")
            _ = try reader.readCString("\(label) assembly name")
        }
        _ = classID
    }
}
