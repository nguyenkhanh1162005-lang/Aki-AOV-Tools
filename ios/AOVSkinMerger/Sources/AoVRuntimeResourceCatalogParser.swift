import Foundation

struct AoVRuntimeResourceItem: Codable, Hashable, Sendable, Identifiable {
    let hashCode: Int64
    let extensionName: String
    var id: String { "\(hashCode):\(extensionName)" }
}

struct AoVRuntimeResourcePackInfo: Codable, Hashable, Sendable, Identifiable {
    let pathInIFS: String
    let pkgType: Int32
    let crcXor: Int64
    let tag: String
    let flags: UInt16
    let resourceInfos: [AoVRuntimeResourceItem]
    var id: String { pathInIFS }
}

struct AoVPackInfoMapItem: Codable, Hashable, Sendable, Identifiable {
    let parentIdx: Int32
    let childIdx: Int32
    var id: String { "\(parentIdx):\(childIdx)" }
}

struct AoVRuntimeResourcePackerCatalog: Codable, Sendable {
    let name: String
    let version: String
    let svnRevision: String
    let setName: String
    let allResourcePackInfo: [AoVRuntimeResourcePackInfo]
    let allParentIndexToChild: [AoVPackInfoMapItem]
    let trailingData: Data
}

struct AoVRuntimeVerificationEntry: Codable, Hashable, Sendable, Identifiable {
    let pathInIFS: String
    let crcXor: Int64
    var id: String { pathInIFS }
}

struct AoVRuntimeResourceVerificationCatalog: Codable, Sendable {
    let name: String
    let version: String
    let svnRevision: String
    let allZipVerificationInfo: [AoVRuntimeVerificationEntry]
    let allDatabinVerificationInfo: [AoVRuntimeVerificationEntry]
    let trailingData: Data
}

enum AoVRuntimeResourceCatalog: Sendable {
    case packer(AoVRuntimeResourcePackerCatalog)
    case verification(AoVRuntimeResourceVerificationCatalog)
}

enum AoVRuntimeResourceCatalogParseError: LocalizedError {
    case invalidPayload(String)
    case resourceLimit(String)

    var errorDescription: String? {
        switch self {
        case .invalidPayload(let reason):
            return "Catalog MonoBehaviour không hợp lệ: \(reason)"
        case .resourceLimit(let reason):
            return "Catalog MonoBehaviour vượt giới hạn an toàn: \(reason)"
        }
    }
}

private struct AoVRuntimeResourceCatalogReader {
    let data: Data
    let littleEndian: Bool
    var offset = 0

    var remainingCount: Int { data.count - offset }

    mutating func readBytes(_ count: Int, _ field: String) throws -> Data {
        guard count >= 0, offset >= 0, offset <= data.count,
              count <= data.count - offset else {
            throw AoVRuntimeResourceCatalogParseError.invalidPayload("thiếu dữ liệu tại \(field).")
        }
        let result = data.subdata(in: offset..<(offset + count))
        offset += count
        return result
    }

    mutating func skip(_ count: Int, _ field: String) throws {
        guard count >= 0, offset >= 0, offset <= data.count,
              count <= data.count - offset else {
            throw AoVRuntimeResourceCatalogParseError.invalidPayload("không bỏ qua được \(field).")
        }
        offset += count
    }

    mutating func align(_ boundary: Int, _ field: String) throws {
        guard boundary > 0, boundary.nonzeroBitCount == 1 else {
            throw AoVRuntimeResourceCatalogParseError.invalidPayload("alignment \(field) không hợp lệ.")
        }
        let remainder = offset % boundary
        if remainder != 0 {
            try skip(boundary - remainder, "alignment \(field)")
        }
    }

    mutating func readByte(_ field: String) throws -> UInt8 {
        try readBytes(1, field)[0]
    }

    mutating func readUInt16(_ field: String) throws -> UInt16 {
        let bytes = try readBytes(2, field)
        let a = UInt16(bytes[bytes.startIndex])
        let b = UInt16(bytes[bytes.startIndex + 1])
        return littleEndian ? (a | (b << 8)) : ((a << 8) | b)
    }

    mutating func readInt32(_ field: String) throws -> Int32 {
        let bytes = try readBytes(4, field)
        let a = UInt32(bytes[bytes.startIndex])
        let b = UInt32(bytes[bytes.startIndex + 1])
        let c = UInt32(bytes[bytes.startIndex + 2])
        let d = UInt32(bytes[bytes.startIndex + 3])
        let value = littleEndian
            ? (a | (b << 8) | (c << 16) | (d << 24))
            : ((a << 24) | (b << 16) | (c << 8) | d)
        return Int32(bitPattern: value)
    }

    mutating func readInt64(_ field: String) throws -> Int64 {
        let bytes = try readBytes(8, field)
        var value: UInt64 = 0
        if littleEndian {
            for index in 0..<8 {
                value |= UInt64(bytes[bytes.startIndex + index]) << (index * 8)
            }
        } else {
            for index in 0..<8 {
                value = (value << 8) | UInt64(bytes[bytes.startIndex + index])
            }
        }
        return Int64(bitPattern: value)
    }

    mutating func readAlignedString(_ field: String, maximumBytes: Int = 4 * 1024 * 1024) throws -> String {
        let rawLength = try readInt32("\(field) length")
        guard rawLength >= 0, rawLength <= maximumBytes else {
            throw AoVRuntimeResourceCatalogParseError.resourceLimit(
                "\(field) có chiều dài \(rawLength), giới hạn \(maximumBytes)."
            )
        }
        let bytes = try readBytes(Int(rawLength), field)
        guard let value = String(data: bytes, encoding: .utf8) else {
            throw AoVRuntimeResourceCatalogParseError.invalidPayload("\(field) không phải UTF-8.")
        }
        try align(4, field)
        return value
    }

    mutating func readCount(_ field: String, maximum: Int) throws -> Int {
        let raw = try readInt32(field)
        guard raw >= 0, raw <= maximum else {
            throw AoVRuntimeResourceCatalogParseError.resourceLimit(
                "\(field) là \(raw), vượt giới hạn \(maximum)."
            )
        }
        return Int(raw)
    }

    func trailingData() -> Data {
        remainingCount == 0 ? Data() : data.subdata(in: offset..<data.count)
    }
}

enum AoVRuntimeResourceCatalogParser {
    private static let maximumMonoBehaviourBytes = 64 * 1024 * 1024
    private static let maximumPackInfoCount = 100_000
    private static let maximumResourceInfoCount = 1_000_000
    private static let maximumMapItemCount = 2_000_000
    private static let maximumVerificationCount = 500_000

    /// Parses only the two observed Arena of Valor catalog MonoBehaviours from
    /// Unity 2022.3 / SerializedFile v22. Other MonoBehaviours return nil.
    /// Once a known catalog name is identified, malformed data fails closed.
    static func inspectMonoBehaviour(
        _ payload: Data,
        serializedFormatVersion: UInt32,
        unityVersion: String,
        littleEndian: Bool
    ) throws -> AoVRuntimeResourceCatalog? {
        guard serializedFormatVersion >= 22,
              unityVersion.hasPrefix("2022.3."),
              payload.count >= 32,
              payload.count <= maximumMonoBehaviourBytes else {
            return nil
        }

        var reader = AoVRuntimeResourceCatalogReader(data: payload, littleEndian: littleEndian)
        let objectName: String
        do {
            // MonoBehaviour header: m_GameObject PPtr, m_Enabled, padding,
            // m_Script PPtr, then the aligned m_Name string.
            try reader.skip(12, "m_GameObject PPtr")
            let enabled = try reader.readByte("m_Enabled")
            guard enabled <= 1 else { return nil }
            try reader.align(4, "m_Script alignment")
            try reader.skip(12, "m_Script PPtr")
            objectName = try reader.readAlignedString("m_Name", maximumBytes: 4_096)
        } catch {
            // Most class 114 objects are unrelated game MonoBehaviours.
            return nil
        }

        switch objectName {
        case "ResourcePackerInfoSetXML":
            return .packer(try parsePacker(afterName: &reader, name: objectName))
        case "ResourceVerificationInfoSetXML":
            return .verification(try parseVerification(afterName: &reader, name: objectName))
        default:
            return nil
        }
    }

    private static func parsePacker(
        afterName reader: inout AoVRuntimeResourceCatalogReader,
        name: String
    ) throws -> AoVRuntimeResourcePackerCatalog {
        let version = try reader.readAlignedString("Version")
        let svnRevision = try reader.readAlignedString("SVNRevision")
        let setName = try reader.readAlignedString("SetName")
        let packInfoCount = try reader.readCount("AllResourcePackInfo count", maximum: maximumPackInfoCount)
        var packInfo: [AoVRuntimeResourcePackInfo] = []
        packInfo.reserveCapacity(packInfoCount)

        for index in 0..<packInfoCount {
            let path = try reader.readAlignedString("AllResourcePackInfo[\(index)].PathInIFS")
            let pkgType = try reader.readInt32("AllResourcePackInfo[\(index)].pkgType")
            let crcXor = try reader.readInt64("AllResourcePackInfo[\(index)].CrcXor")
            let tag = try reader.readAlignedString("AllResourcePackInfo[\(index)].Tag")
            let flags = try reader.readUInt16("AllResourcePackInfo[\(index)].Flags")
            // Unity aligns the ResourceInfos vector count to four bytes after Flags.
            try reader.align(4, "AllResourcePackInfo[\(index)].ResourceInfos")
            let resourceCount = try reader.readCount(
                "AllResourcePackInfo[\(index)].ResourceInfos count",
                maximum: maximumResourceInfoCount
            )
            var resources: [AoVRuntimeResourceItem] = []
            resources.reserveCapacity(resourceCount)
            for resourceIndex in 0..<resourceCount {
                resources.append(
                    AoVRuntimeResourceItem(
                        hashCode: try reader.readInt64("AllResourcePackInfo[\(index)].ResourceInfos[\(resourceIndex)].HashCode"),
                        extensionName: try reader.readAlignedString("AllResourcePackInfo[\(index)].ResourceInfos[\(resourceIndex)].Extension")
                    )
                )
            }
            packInfo.append(
                AoVRuntimeResourcePackInfo(
                    pathInIFS: path,
                    pkgType: pkgType,
                    crcXor: crcXor,
                    tag: tag,
                    flags: flags,
                    resourceInfos: resources
                )
            )
        }

        let mapCount = try reader.readCount("AllParentIdexToChild count", maximum: maximumMapItemCount)
        var mapItems: [AoVPackInfoMapItem] = []
        mapItems.reserveCapacity(mapCount)
        for index in 0..<mapCount {
            mapItems.append(
                AoVPackInfoMapItem(
                    parentIdx: try reader.readInt32("AllParentIdexToChild[\(index)].parentIdx"),
                    childIdx: try reader.readInt32("AllParentIdexToChild[\(index)].childIdx")
                )
            )
        }

        return AoVRuntimeResourcePackerCatalog(
            name: name,
            version: version,
            svnRevision: svnRevision,
            setName: setName,
            allResourcePackInfo: packInfo,
            allParentIndexToChild: mapItems,
            trailingData: reader.trailingData()
        )
    }

    private static func parseVerification(
        afterName reader: inout AoVRuntimeResourceCatalogReader,
        name: String
    ) throws -> AoVRuntimeResourceVerificationCatalog {
        let version = try reader.readAlignedString("Version")
        let svnRevision = try reader.readAlignedString("SVNRevision")

        let zipCount = try reader.readCount("AllZipVerificationInfo count", maximum: maximumVerificationCount)
        var zipEntries: [AoVRuntimeVerificationEntry] = []
        zipEntries.reserveCapacity(zipCount)
        for index in 0..<zipCount {
            zipEntries.append(
                AoVRuntimeVerificationEntry(
                    pathInIFS: try reader.readAlignedString("AllZipVerificationInfo[\(index)].PathInIFS"),
                    crcXor: try reader.readInt64("AllZipVerificationInfo[\(index)].CrcXor")
                )
            )
        }

        let databinCount = try reader.readCount("AllDatabinVerificationInfo count", maximum: maximumVerificationCount)
        var databinEntries: [AoVRuntimeVerificationEntry] = []
        databinEntries.reserveCapacity(databinCount)
        for index in 0..<databinCount {
            databinEntries.append(
                AoVRuntimeVerificationEntry(
                    pathInIFS: try reader.readAlignedString("AllDatabinVerificationInfo[\(index)].PathInIFS"),
                    crcXor: try reader.readInt64("AllDatabinVerificationInfo[\(index)].CrcXor")
                )
            )
        }

        return AoVRuntimeResourceVerificationCatalog(
            name: name,
            version: version,
            svnRevision: svnRevision,
            allZipVerificationInfo: zipEntries,
            allDatabinVerificationInfo: databinEntries,
            trailingData: reader.trailingData()
        )
    }
}
