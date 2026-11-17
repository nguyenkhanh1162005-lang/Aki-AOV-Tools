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

struct AoVRuntimeResourcePackerCatalog: Codable, Hashable, Sendable {
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

struct AoVRuntimeResourceVerificationCatalog: Codable, Hashable, Sendable {
    let name: String
    let version: String
    let svnRevision: String
    let allZipVerificationInfo: [AoVRuntimeVerificationEntry]
    let allDatabinVerificationInfo: [AoVRuntimeVerificationEntry]
    let trailingData: Data
}

enum AoVRuntimeResourceCatalog: Equatable, Sendable {
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

    /// Rebuilds one observed AoV catalog MonoBehaviour while retaining its
    /// GameObject/script/name header and any version-specific trailing bytes.
    /// Only the two parsed 2022.3 / SerializedFile v22 catalog layouts are writable.
    static func rewriteMonoBehaviour(
        _ payload: Data,
        with replacement: AoVRuntimeResourceCatalog,
        serializedFormatVersion: UInt32,
        unityVersion: String,
        littleEndian: Bool
    ) throws -> Data {
        guard let original = try inspectMonoBehaviour(
            payload,
            serializedFormatVersion: serializedFormatVersion,
            unityVersion: unityVersion,
            littleEndian: littleEndian
        ) else {
            throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                "Object nguồn không phải Packer hoặc Verification catalog được hỗ trợ."
            )
        }

        let expectedName: String
        switch (original, replacement) {
        case (.packer(let old), .packer(let new)):
            expectedName = old.name
            guard new.name == old.name else {
                throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                    "Không được thay tên MonoBehaviour Packer catalog."
                )
            }
            try validate(new)
        case (.verification(let old), .verification(let new)):
            expectedName = old.name
            guard new.name == old.name else {
                throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                    "Không được thay tên MonoBehaviour Verification catalog."
                )
            }
            try validate(new)
        default:
            throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                "Không thể thay Packer bằng Verification hoặc ngược lại."
            )
        }

        var reader = AoVRuntimeResourceCatalogReader(data: payload, littleEndian: littleEndian)
        try reader.skip(12, "m_GameObject PPtr")
        let enabled = try reader.readByte("m_Enabled")
        guard enabled <= 1 else {
            throw AoVRuntimeResourceCatalogParseError.invalidPayload("m_Enabled không hợp lệ.")
        }
        try reader.align(4, "m_Script alignment")
        try reader.skip(12, "m_Script PPtr")
        let objectName = try reader.readAlignedString("m_Name", maximumBytes: 4_096)
        guard objectName == expectedName else {
            throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                "Tên m_Name của object không khớp catalog cần thay."
            )
        }

        var writer = AoVRuntimeResourceCatalogWriter(littleEndian: littleEndian)
        switch replacement {
        case .packer(let catalog):
            try writer.appendAlignedString(catalog.version, field: "Packer.Version")
            try writer.appendAlignedString(catalog.svnRevision, field: "Packer.SVNRevision")
            try writer.appendAlignedString(catalog.setName, field: "Packer.SetName")
            try writer.appendCount(catalog.allResourcePackInfo.count, field: "Packer.AllResourcePackInfo")
            for (index, item) in catalog.allResourcePackInfo.enumerated() {
                try writer.appendAlignedString(item.pathInIFS, field: "Packer[\(index)].PathInIFS")
                writer.appendInt32(item.pkgType)
                writer.appendInt64(item.crcXor)
                try writer.appendAlignedString(item.tag, field: "Packer[\(index)].Tag")
                writer.appendUInt16(item.flags)
                writer.align4()
                try writer.appendCount(
                    item.resourceInfos.count,
                    field: "Packer[\(index)].ResourceInfos"
                )
                for (resourceIndex, resource) in item.resourceInfos.enumerated() {
                    writer.appendInt64(resource.hashCode)
                    try writer.appendAlignedString(
                        resource.extensionName,
                        field: "Packer[\(index)].ResourceInfos[\(resourceIndex)].Extension"
                    )
                }
            }
            try writer.appendCount(
                catalog.allParentIndexToChild.count,
                field: "Packer.AllParentIdexToChild"
            )
            for link in catalog.allParentIndexToChild {
                writer.appendInt32(link.parentIdx)
                writer.appendInt32(link.childIdx)
            }
            writer.data.append(catalog.trailingData)
        case .verification(let catalog):
            try writer.appendAlignedString(catalog.version, field: "Verification.Version")
            try writer.appendAlignedString(catalog.svnRevision, field: "Verification.SVNRevision")
            try writer.appendCount(
                catalog.allZipVerificationInfo.count,
                field: "Verification.AllZipVerificationInfo"
            )
            for (index, entry) in catalog.allZipVerificationInfo.enumerated() {
                try writer.appendAlignedString(
                    entry.pathInIFS,
                    field: "Verification.ZIP[\(index)].PathInIFS"
                )
                writer.appendInt64(entry.crcXor)
            }
            try writer.appendCount(
                catalog.allDatabinVerificationInfo.count,
                field: "Verification.AllDatabinVerificationInfo"
            )
            for (index, entry) in catalog.allDatabinVerificationInfo.enumerated() {
                try writer.appendAlignedString(
                    entry.pathInIFS,
                    field: "Verification.Databin[\(index)].PathInIFS"
                )
                writer.appendInt64(entry.crcXor)
            }
            writer.data.append(catalog.trailingData)
        }

        var output = payload.prefix(reader.offset)
        output.append(writer.data)
        guard let verified = try inspectMonoBehaviour(
            output,
            serializedFormatVersion: serializedFormatVersion,
            unityVersion: unityVersion,
            littleEndian: littleEndian
        ), verified == replacement else {
            throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                "Catalog MonoBehaviour không khớp sau khi serialize và parse lại."
            )
        }
        return output
    }

    private static func validate(_ catalog: AoVRuntimeResourcePackerCatalog) throws {
        guard catalog.allResourcePackInfo.count <= maximumPackInfoCount,
              catalog.allParentIndexToChild.count <= maximumMapItemCount else {
            throw AoVRuntimeResourceCatalogParseError.resourceLimit("Packer catalog có quá nhiều mục.")
        }
        var paths = Set<String>()
        for (index, item) in catalog.allResourcePackInfo.enumerated() {
            guard !item.pathInIFS.isEmpty,
                  paths.insert(item.pathInIFS).inserted else {
                throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                    "Packer path rỗng hoặc bị lặp tại mục \(index)."
                )
            }
            guard item.resourceInfos.count <= maximumResourceInfoCount else {
                throw AoVRuntimeResourceCatalogParseError.resourceLimit(
                    "Packer ResourceInfos[\(index)] vượt giới hạn."
                )
            }
        }
        for (index, link) in catalog.allParentIndexToChild.enumerated() {
            guard let parent = Int(exactly: link.parentIdx),
                  let child = Int(exactly: link.childIdx),
                  catalog.allResourcePackInfo.indices.contains(parent),
                  catalog.allResourcePackInfo.indices.contains(child) else {
                throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                    "Packer parent-child index không hợp lệ tại mục \(index)."
                )
            }
        }
    }

    private static func validate(_ catalog: AoVRuntimeResourceVerificationCatalog) throws {
        guard catalog.allZipVerificationInfo.count <= maximumVerificationCount,
              catalog.allDatabinVerificationInfo.count <= maximumVerificationCount else {
            throw AoVRuntimeResourceCatalogParseError.resourceLimit("Verification catalog có quá nhiều mục.")
        }
        for (label, entries) in [
            ("ZIP", catalog.allZipVerificationInfo),
            ("Databin", catalog.allDatabinVerificationInfo)
        ] {
            var paths = Set<String>()
            for (index, entry) in entries.enumerated() {
                guard !entry.pathInIFS.isEmpty,
                      paths.insert(entry.pathInIFS).inserted else {
                    throw AoVRuntimeResourceCatalogParseError.invalidPayload(
                        "Verification path \(label) rỗng hoặc bị lặp tại mục \(index)."
                    )
                }
            }
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


private struct AoVRuntimeResourceCatalogWriter {
    var data = Data()
    let littleEndian: Bool

    mutating func appendInt32(_ value: Int32) {
        let bits = UInt32(bitPattern: value)
        if littleEndian {
            for shift in stride(from: 0, through: 24, by: 8) {
                data.append(UInt8(truncatingIfNeeded: bits >> shift))
            }
        } else {
            for shift in stride(from: 24, through: 0, by: -8) {
                data.append(UInt8(truncatingIfNeeded: bits >> shift))
            }
        }
    }

    mutating func appendInt64(_ value: Int64) {
        let bits = UInt64(bitPattern: value)
        if littleEndian {
            for shift in stride(from: 0, through: 56, by: 8) {
                data.append(UInt8(truncatingIfNeeded: bits >> shift))
            }
        } else {
            for shift in stride(from: 56, through: 0, by: -8) {
                data.append(UInt8(truncatingIfNeeded: bits >> shift))
            }
        }
    }

    mutating func appendUInt16(_ value: UInt16) {
        if littleEndian {
            data.append(UInt8(truncatingIfNeeded: value))
            data.append(UInt8(truncatingIfNeeded: value >> 8))
        } else {
            data.append(UInt8(truncatingIfNeeded: value >> 8))
            data.append(UInt8(truncatingIfNeeded: value))
        }
    }

    mutating func appendCount(_ value: Int, field: String) throws {
        guard let count = Int32(exactly: value), count >= 0 else {
            throw AoVRuntimeResourceCatalogParseError.resourceLimit("\(field) vượt giới hạn Int32.")
        }
        appendInt32(count)
    }

    mutating func appendAlignedString(_ value: String, field: String) throws {
        let bytes = Data(value.utf8)
        guard bytes.count <= 4 * 1024 * 1024,
              let length = Int32(exactly: bytes.count) else {
            throw AoVRuntimeResourceCatalogParseError.resourceLimit("\(field) quá dài.")
        }
        appendInt32(length)
        data.append(bytes)
        align4()
    }

    mutating func align4() {
        while data.count % 4 != 0 { data.append(0) }
    }
}
