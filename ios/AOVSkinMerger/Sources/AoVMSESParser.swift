import Foundation
import CryptoKit

enum AoVMSESError: LocalizedError {
    case badHeader
    case unsupportedVersion(UInt32)
    case invalidCounts
    case checksumMismatch
    case truncatedRow(Int)
    case invalidRowLength(Int)
    case trailingBytes(Int)
    case shortRow
    case duplicateKey(UInt32)
    case actorInventoryMissing
    case actorInventoryOverlap
    case unownedOverlayRows([UInt32])
    case noOverlayOwnedRows
    case unresolvedConflict([UInt32])
    case unsupportedTable(String)
    case schemaMismatch
    case allowlistViolation([UInt32])
    case unsafeSchemaConflict([UInt32])
    case encodeVerificationFailed

    var errorDescription: String? {
        switch self {
        case .badHeader: return "Không nhận diện được header MSES."
        case .unsupportedVersion(let version): return "MSES version \(version) chưa được hỗ trợ."
        case .invalidCounts: return "Field count hoặc row count MSES không hợp lệ."
        case .checksumMismatch: return "MD5 phần dữ liệu MSES không khớp header."
        case .truncatedRow(let index): return "Thiếu độ dài bản ghi MSES thứ \(index)."
        case .invalidRowLength(let index): return "Độ dài bản ghi MSES thứ \(index) không hợp lệ."
        case .trailingBytes(let count): return "MSES còn \(count) byte dư sau bản ghi cuối."
        case .shortRow: return "Bản ghi MSES quá ngắn để đọc khóa."
        case .duplicateKey(let key): return String(format: "Khóa MSES trùng: 0x%08x.", key)
        case .actorInventoryMissing: return "Cần Actor_Action inventory không rỗng của cả hai nguồn."
        case .actorInventoryOverlap: return "Actor_Action inventory giao nhau giữa base và overlay."
        case .unownedOverlayRows(let keys):
            return "Có hàng mới không thuộc Actor của overlay: " + keys.prefix(20).map { String(format: "0x%08x", $0) }.joined(separator: ", ")
        case .noOverlayOwnedRows: return "Không tìm thấy hàng MSES thuộc Actor trong overlay."
        case .unresolvedConflict(let keys):
            return "Có hàng MSES xung đột không xác định được Actor: " + keys.prefix(20).map { String(format: "0x%08x", $0) }.joined(separator: ", ")
        case .unsupportedTable(let path): return "\(path): chưa có quy tắc MSES đã xác minh."
        case .schemaMismatch: return "Schema MSES không khớp."
        case .allowlistViolation(let keys):
            return "Khóa mới ngoài allowlist: " + keys.prefix(20).map { String(format: "0x%08x", $0) }.joined(separator: ", ")
        case .unsafeSchemaConflict(let keys):
            return "Schema khác và có row xung đột chưa được cấp quyền: " + keys.prefix(20).map { String(format: "0x%08x", $0) }.joined(separator: ", ")
        case .encodeVerificationFailed: return "MSES sau khi ghi không vượt qua xác minh checksum/schema."
        }
    }
}

struct AoVMSESTable: Sendable {
    let version: UInt32
    let fieldCount: UInt32
    let rows: [Data]
    let header: Data
    let checksumValid: Bool
}

struct AoVMSESReport: Sendable {
    let resourcePath: String
    let adapter: String
    let baseFields: UInt32
    let overlayFields: UInt32
    let outputFields: UInt32
    let baseRows: Int
    let overlayRows: Int
    let outputRows: Int
    let appendedKeys: [UInt32]
    let overlayOwnedConflictKeys: [UInt32]
    let retainedBaseConflictKeys: [UInt32]
    let verified: Bool
}

enum AoVMSESParser {
    static let headerSize = 0x8C
    private static let checksumStart = 0x60
    private static let checksumEnd = 0x80
    private static let maximumRowBytes = 64 * 1024 * 1024
    private static let maximumRows: UInt32 = 2_000_000
    private static let actorKeyDivisors: [UInt32] = [10, 100, 1_000, 10_000, 100_000, 1_000_000]

    private enum OwnershipRule {
        case divisor(UInt32)
        case bank10000
        case sound1000
        case mixedScale
        case liteBulletPath
        case genericPrefix
    }

    static func isMSES(_ data: Data) -> Bool {
        data.count >= 4 && data.prefix(4) == Data("MSES".utf8)
    }

    static func parse(_ data: Data, verifyChecksum: Bool = true) throws -> AoVMSESTable {
        guard data.count >= headerSize, isMSES(data) else {
            throw AoVMSESError.badHeader
        }
        let version = try readU32(data, 4)
        let fields = try readU32(data, 8)
        let rowCount = try readU32(data, 12)
        guard version == 7 else { throw AoVMSESError.unsupportedVersion(version) }
        guard fields > 0, fields <= 10_000, rowCount <= maximumRows else {
            throw AoVMSESError.invalidCounts
        }

        let body = data.subdata(in: headerSize..<data.count)
        let actualHash = Data(Insecure.MD5.hash(data: body))
        let actualHex = Data(actualHash.map { String(format: "%02x", $0) }.joined().utf8)
        let stored = data.subdata(in: checksumStart..<checksumEnd)
        let checksumValid = stored == actualHex
        if verifyChecksum && !checksumValid { throw AoVMSESError.checksumMismatch }

        var rows: [Data] = []
        rows.reserveCapacity(Int(rowCount))
        var offset = headerSize
        for index in 0..<Int(rowCount) {
            guard offset + 4 <= data.count else { throw AoVMSESError.truncatedRow(index) }
            let rowSize = Int(try readU32(data, offset))
            let start = offset + 4
            let end = start + rowSize
            guard rowSize >= 4, rowSize <= maximumRowBytes, end <= data.count else {
                throw AoVMSESError.invalidRowLength(index)
            }
            rows.append(data.subdata(in: start..<end))
            offset = end
        }
        guard offset == data.count else {
            throw AoVMSESError.trailingBytes(data.count - offset)
        }
        return AoVMSESTable(
            version: version,
            fieldCount: fields,
            rows: rows,
            header: data.subdata(in: 0..<headerSize),
            checksumValid: checksumValid
        )
    }

    static func build(_ table: AoVMSESTable, rows: [Data], fieldCount: UInt32? = nil) throws -> Data {
        let fields = fieldCount ?? table.fieldCount
        guard fields > 0, fields <= 10_000, rows.count <= Int(maximumRows) else {
            throw AoVMSESError.invalidCounts
        }
        var body = Data()
        for row in rows {
            guard row.count >= 4, row.count <= maximumRowBytes else {
                throw AoVMSESError.invalidRowLength(0)
            }
            appendU32(UInt32(row.count), to: &body)
            body.append(row)
        }
        var header = table.header
        guard header.count == headerSize else { throw AoVMSESError.badHeader }
        writeU32(table.version, into: &header, at: 4)
        writeU32(fields, into: &header, at: 8)
        writeU32(UInt32(rows.count), into: &header, at: 12)
        let checksum = Data(Insecure.MD5.hash(data: body))
        let hex = Data(checksum.map { String(format: "%02x", $0) }.joined().utf8)
        header.replaceSubrange(checksumStart..<checksumEnd, with: hex)
        return header + body
    }

    static func mergeResource(
        baseResource: Data,
        overlayResource: Data,
        resourcePath: String,
        baseActorIDs: Set<String>,
        overlayActorIDs: Set<String>
    ) throws -> (Data, AoVMSESReport) {
        let baseDecoded = try AoVBytesDecoder.decodeResource(baseResource, resourceName: resourcePath)
        let overlayDecoded = try AoVBytesDecoder.decodeResource(overlayResource, resourceName: resourcePath)
        let baseTable = try parse(baseDecoded.data)
        let overlayTable = try parse(overlayDecoded.data)

        let normalized = resourcePath.replacingOccurrences(of: "\\", with: "/").trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let lower = normalized.lowercased()

        let outputData: Data
        let adapter: String
        var appended: [UInt32] = []
        var overlayConflicts: [UInt32] = []
        var retainedConflicts: [UInt32] = []

        if lower == "databin/client/huanhua/resbillboardcfg.bytes" ||
           lower == "databin/client/huanhua/resbillboardskincfg.bytes" ||
           lower == "databin/client/huanhua/reskillbillboardcfg.bytes" {
            (outputData, appended, retainedConflicts) = try mergeExplicitRows(
                base: baseTable,
                overlay: overlayTable,
                allowedNewKeys: [0x253],
                requiredOverlayKeys: [0x253],
                rejectConflictingKeys: [0x253],
                allowSchemaMismatch: true
            )
            adapter = "verified_huanhua_key_0x253"
        } else if lower == "databin/client/skill/skillcombine.bytes" {
            let keys: Set<UInt32> = [0x000915A7, 0x000915A8, 0x000915A9, 0x000915AA, 0x000915AB]
            (outputData, appended, retainedConflicts) = try mergeExplicitRows(
                base: baseTable,
                overlay: overlayTable,
                allowedNewKeys: keys,
                requiredOverlayKeys: keys,
                rejectConflictingKeys: keys,
                allowSchemaMismatch: true
            )
            adapter = "verified_skillcombine_key_allowlist"
        } else if lower == "databin/client/actor/resskinseniorlabelcfg.bytes" {
            (outputData, appended, retainedConflicts) = try mergeExplicitRows(
                base: baseTable,
                overlay: overlayTable,
                allowedNewKeys: [59_500],
                requiredOverlayKeys: [59_500],
                rejectConflictingKeys: [59_500],
                allowSchemaMismatch: true
            )
            adapter = "verified_res_skin_59500"
        } else if lower == "databin/client/character/rescharactercomponent.bytes" {
            guard baseTable.fieldCount == 188, overlayTable.fieldCount == 188 else {
                throw AoVMSESError.schemaMismatch
            }
            (outputData, appended, retainedConflicts) = try mergeExplicitRows(
                base: baseTable,
                overlay: overlayTable,
                allowedNewKeys: nil,
                requiredOverlayKeys: [],
                rejectAnyConflicts: true,
                allowSchemaMismatch: false
            )
            adapter = "same_schema_character_component_union"
        } else if let rule = ownershipRule(for: lower) {
            (outputData, appended, overlayConflicts, retainedConflicts) = try mergeActorOwned(
                base: baseTable,
                overlay: overlayTable,
                baseActorIDs: baseActorIDs,
                overlayActorIDs: overlayActorIDs,
                rule: rule,
                path: normalized
            )
            adapter = "actor_owned_mses_rows"
        } else {
            (outputData, appended, overlayConflicts, retainedConflicts) = try mergeActorPrefix(
                base: baseTable,
                overlay: overlayTable,
                baseActorIDs: baseActorIDs,
                overlayActorIDs: overlayActorIDs
            )
            adapter = "heuristic_actor_prefix_mses"
        }

        let verifiedTable = try parse(outputData)
        let wrapped = try AoVBytesDecoder.encodeResource(
            outputData,
            resourceName: resourcePath,
            wrapperMode: baseDecoded.wrapperMode
        )
        let check = try AoVBytesDecoder.decodeResource(wrapped, resourceName: resourcePath)
        guard check.data == outputData,
              check.wrapperMode == baseDecoded.wrapperMode,
              verifiedTable.checksumValid else {
            throw AoVMSESError.encodeVerificationFailed
        }

        return (wrapped, AoVMSESReport(
            resourcePath: normalized,
            adapter: adapter,
            baseFields: baseTable.fieldCount,
            overlayFields: overlayTable.fieldCount,
            outputFields: verifiedTable.fieldCount,
            baseRows: baseTable.rows.count,
            overlayRows: overlayTable.rows.count,
            outputRows: verifiedTable.rows.count,
            appendedKeys: appended,
            overlayOwnedConflictKeys: overlayConflicts,
            retainedBaseConflictKeys: retainedConflicts,
            verified: true
        ))
    }

    private static func mergeExplicitRows(
        base: AoVMSESTable,
        overlay: AoVMSESTable,
        allowedNewKeys: Set<UInt32>?,
        requiredOverlayKeys: Set<UInt32>,
        rejectConflictingKeys: Set<UInt32> = [],
        rejectAnyConflicts: Bool = false,
        allowSchemaMismatch: Bool
    ) throws -> (Data, [UInt32], [UInt32]) {
        guard base.version == overlay.version else { throw AoVMSESError.schemaMismatch }
        if !allowSchemaMismatch && base.fieldCount != overlay.fieldCount {
            throw AoVMSESError.schemaMismatch
        }
        let baseMap = try indexRows(base.rows)
        let overlayMap = try indexRows(overlay.rows)
        if base.fieldCount != overlay.fieldCount {
            let sharedKeys = Set(baseMap.keys).intersection(overlayMap.keys)
            let differing = sharedKeys.filter { baseMap[$0] != overlayMap[$0] }
            if !differing.isEmpty {
                throw AoVMSESError.unresolvedConflict(differing.sorted())
            }
        }
        guard requiredOverlayKeys.isSubset(of: Set(overlayMap.keys)) else {
            throw AoVMSESError.allowlistViolation(Array(requiredOverlayKeys.subtracting(overlayMap.keys)).sorted())
        }
        let newKeys = Set(overlayMap.keys).subtracting(baseMap.keys)
        if let allowedNewKeys, !newKeys.isSubset(of: allowedNewKeys) {
            throw AoVMSESError.allowlistViolation(Array(newKeys.subtracting(allowedNewKeys)).sorted())
        }
        if allowedNewKeys == nil, base.fieldCount != overlay.fieldCount {
            throw AoVMSESError.schemaMismatch
        }
        var result = baseMap
        var appended: [UInt32] = []
        var retained: [UInt32] = []
        for key in overlayMap.keys.sorted() {
            guard let candidate = overlayMap[key] else { continue }
            if let existing = result[key] {
                if existing != candidate {
                    if rejectAnyConflicts || rejectConflictingKeys.contains(key) {
                        throw AoVMSESError.unresolvedConflict([key])
                    }
                    retained.append(key)
                }
            } else {
                result[key] = candidate
                appended.append(key)
            }
        }
        let order = base.rows.compactMap { try? rowKey($0) } + overlay.rows.compactMap {
            let key = (try? rowKey($0)) ?? 0
            return baseMap[key] == nil ? key : nil
        }
        let merged = order.compactMap { result[$0] }
        let data = try build(base, rows: merged, fieldCount: base.fieldCount)
        _ = try parse(data)
        return (data, appended, retained)
    }

    private static func mergeActorOwned(
        base: AoVMSESTable,
        overlay: AoVMSESTable,
        baseActorIDs: Set<String>,
        overlayActorIDs: Set<String>,
        rule: OwnershipRule,
        path: String
    ) throws -> (Data, [UInt32], [UInt32], [UInt32]) {
        guard !baseActorIDs.isEmpty, !overlayActorIDs.isEmpty else {
            throw AoVMSESError.actorInventoryMissing
        }
        let baseIDs = try normalizedActorIDs(baseActorIDs)
        let overlayIDs = try normalizedActorIDs(overlayActorIDs)
        guard baseIDs.isDisjoint(with: overlayIDs) else { throw AoVMSESError.actorInventoryOverlap }
        guard base.version == overlay.version, base.fieldCount == overlay.fieldCount else {
            throw AoVMSESError.schemaMismatch
        }
        let baseMap = try indexRows(base.rows)
        let overlayMap = try indexRows(overlay.rows)
        let known = baseIDs.union(overlayIDs)

        func owner(_ key: UInt32, _ row: Data) -> Int? {
            switch rule {
            case .liteBulletPath:
                return liteBulletActor(row)
            case .divisor(let value):
                let actor = Int(key / value)
                return known.contains(actor) ? actor : nil
            case .bank10000:
                let encoded = Int(key / 10_000)
                let actor = encoded <= 999 ? encoded : encoded / 100
                return known.contains(actor) ? actor : nil
            case .sound1000:
                let encoded = Int(key / 1_000)
                let actor = encoded <= 999 ? encoded : encoded / 100
                return known.contains(actor) ? actor : nil
            case .mixedScale, .genericPrefix:
                return inferUniqueActor(key, actorIDs: known)
            }
        }

        let overlayOwnedKeys = overlayMap.compactMap { key, row in
            owner(key, row).map { overlayIDs.contains($0) ? key : nil } ?? nil
        }
        guard !overlayOwnedKeys.isEmpty else { throw AoVMSESError.noOverlayOwnedRows }

        let newKeys = Set(overlayMap.keys).subtracting(baseMap.keys)
        let invalidNew = newKeys.filter { key in
            guard let row = overlayMap[key], let id = owner(key, row) else { return true }
            return !overlayIDs.contains(id)
        }
        guard invalidNew.isEmpty else {
            throw AoVMSESError.unownedOverlayRows(invalidNew.sorted())
        }

        let shared = Set(baseMap.keys).intersection(overlayMap.keys)
        var conflicts: [UInt32] = []
        for key in shared where baseMap[key] != overlayMap[key] {
            let baseOwner = owner(key, baseMap[key]!)
            let overlayOwner = owner(key, overlayMap[key]!)
            guard let baseOwner, let overlayOwner,
                  baseOwner == overlayOwner,
                  known.contains(baseOwner) else {
                conflicts.append(key)
                continue
            }
        }
        guard conflicts.isEmpty else { throw AoVMSESError.unresolvedConflict(conflicts.sorted()) }

        var result = baseMap
        var appended: [UInt32] = []
        var overlayConflicts: [UInt32] = []
        var retainedConflicts: [UInt32] = []
        for key in overlayMap.keys.sorted() {
            guard let candidate = overlayMap[key] else { continue }
            if let existing = baseMap[key] {
                guard existing != candidate else { continue }
                let sourceOwner = owner(key, candidate)
                if let sourceOwner, overlayIDs.contains(sourceOwner) {
                    result[key] = candidate
                    overlayConflicts.append(key)
                } else {
                    retainedConflicts.append(key)
                }
            } else {
                result[key] = candidate
                appended.append(key)
            }
        }
        let order = base.rows.compactMap { try? rowKey($0) } + overlay.rows.compactMap {
            let key = (try? rowKey($0)) ?? 0
            return baseMap[key] == nil ? key : nil
        }
        let merged = order.compactMap { result[$0] }
        let data = try build(base, rows: merged)
        let checked = try parse(data)
        guard checked.fieldCount == base.fieldCount, checked.rows.count == merged.count,
              checked.checksumValid else { throw AoVMSESError.encodeVerificationFailed }
        _ = path
        return (data, appended, overlayConflicts, retainedConflicts)
    }

    private static func mergeActorPrefix(
        base: AoVMSESTable,
        overlay: AoVMSESTable,
        baseActorIDs: Set<String>,
        overlayActorIDs: Set<String>
    ) throws -> (Data, [UInt32], [UInt32], [UInt32]) {
        guard !baseActorIDs.isEmpty, !overlayActorIDs.isEmpty else {
            throw AoVMSESError.actorInventoryMissing
        }
        let baseIDs = try normalizedActorIDs(baseActorIDs)
        let overlayIDs = try normalizedActorIDs(overlayActorIDs)
        guard baseIDs.isDisjoint(with: overlayIDs) else { throw AoVMSESError.actorInventoryOverlap }
        guard base.version == overlay.version, base.fieldCount == overlay.fieldCount else {
            throw AoVMSESError.schemaMismatch
        }
        let baseMap = try indexRows(base.rows)
        let overlayMap = try indexRows(overlay.rows)
        let known = baseIDs.union(overlayIDs)
        let newKeys = Set(overlayMap.keys).subtracting(baseMap.keys)
        let unowned = newKeys.filter {
            inferUniqueActor($0, actorIDs: known).map { !overlayIDs.contains($0) } ?? true
        }
        guard unowned.isEmpty else { throw AoVMSESError.unownedOverlayRows(unowned.sorted()) }
        let overlayOwned = overlayMap.keys.filter {
            inferUniqueActor($0, actorIDs: known).map { overlayIDs.contains($0) } ?? false
        }
        guard !overlayOwned.isEmpty else { throw AoVMSESError.noOverlayOwnedRows }

        var output = baseMap
        var appended: [UInt32] = []
        var overlayConflicts: [UInt32] = []
        var retained: [UInt32] = []
        for key in overlayMap.keys.sorted() {
            guard let candidate = overlayMap[key] else { continue }
            if let baseRow = baseMap[key] {
                if baseRow == candidate { continue }
                guard let owner = inferUniqueActor(key, actorIDs: known) else {
                    throw AoVMSESError.unresolvedConflict([key])
                }
                if baseIDs.contains(owner) {
                    retained.append(key)
                } else if overlayIDs.contains(owner) {
                    output[key] = candidate
                    overlayConflicts.append(key)
                } else {
                    throw AoVMSESError.unresolvedConflict([key])
                }
            } else {
                output[key] = candidate
                appended.append(key)
            }
        }
        let order = base.rows.compactMap { try? rowKey($0) } + overlay.rows.compactMap {
            let key = (try? rowKey($0)) ?? 0
            return baseMap[key] == nil ? key : nil
        }
        let data = try build(base, rows: order.compactMap { output[$0] })
        _ = try parse(data)
        return (data, appended, overlayConflicts, retained)
    }

    private static func indexRows(_ rows: [Data]) throws -> [UInt32: Data] {
        var result: [UInt32: Data] = [:]
        for row in rows {
            let key = try rowKey(row)
            guard result[key] == nil else { throw AoVMSESError.duplicateKey(key) }
            result[key] = row
        }
        return result
    }

    private static func rowKey(_ row: Data) throws -> UInt32 {
        guard row.count >= 4 else { throw AoVMSESError.shortRow }
        return try readU32(row, 0)
    }

    private static func normalizedActorIDs(_ values: Set<String>) throws -> Set<Int> {
        let ids = Set(values.compactMap { Int($0) })
        guard ids.count == values.count, ids.allSatisfy({ 0 < $0 && $0 <= 999 }) else {
            throw AoVMSESError.actorInventoryMissing
        }
        return ids
    }

    private static func inferUniqueActor(_ key: UInt32, actorIDs: Set<Int>) -> Int? {
        let candidates = Set(actorKeyDivisors.compactMap { divisor -> Int? in
            let actor = Int(key / divisor)
            return actorIDs.contains(actor) ? actor : nil
        })
        return candidates.count == 1 ? candidates.first : nil
    }

    private static func liteBulletActor(_ row: Data) -> Int? {
        let marker = Data("prefab_skill_effects/hero_skill_effects/".utf8)
        guard let range = row.range(of: marker),
              range.lowerBound >= 4,
              row.range(of: marker, options: [], in: range.upperBound..<row.count) == nil else { return nil }
        let sizeRange = (range.lowerBound - 4)..<range.lowerBound
        let pathSize = try? readU32(row, sizeRange.lowerBound)
        guard let pathSize, Int(pathSize) == row.count - range.lowerBound else { return nil }
        let path = row.subdata(in: range.lowerBound..<row.count)
        guard let text = String(data: path, encoding: .ascii),
              let regex = try? NSRegularExpression(pattern: #"hero_skill_effects/(\d+)_ "#.replacingOccurrences(of: " ", with: "")) else {
            return nil
        }
        let ns = text as NSString
        let matches = regex.matches(in: text, range: NSRange(location: 0, length: ns.length))
        let ids = Set(matches.compactMap { match -> Int? in
            guard match.numberOfRanges > 1 else { return nil }
            return Int(ns.substring(with: match.range(at: 1)))
        })
        return ids.count == 1 ? ids.first : nil
    }

    private static func ownershipRule(for path: String) -> OwnershipRule? {
        switch path {
        case "databin/client/actor/heroskin.bytes",
             "databin/client/shop/heroskinshop.bytes",
             "databin/client/skill/skillmark.bytes":
            return .divisor(100)
        case "databin/client/motion/resskinmotionbasecfg.bytes":
            return .divisor(10_000)
        case "databin/client/sound/battlebank.bytes",
             "databin/client/sound/lobbybank.bytes",
             "databin/client/sound/lobbysound.bytes":
            return .bank10000
        case "databin/client/sound/chatsound.bytes",
             "databin/client/sound/couplesound.bytes":
            return .sound1000
        case "databin/client/sound/herosound.bytes":
            return .mixedScale
        case "databin/client/skill/litebulletcfg.bytes":
            return .liteBulletPath
        default:
            return nil
        }
    }

    private static func readU32(_ data: Data, _ offset: Int) throws -> UInt32 {
        guard offset >= 0, offset + 4 <= data.count else { throw AoVMSESError.badHeader }
        return UInt32(data[offset]) |
            (UInt32(data[offset + 1]) << 8) |
            (UInt32(data[offset + 2]) << 16) |
            (UInt32(data[offset + 3]) << 24)
    }

    private static func writeU32(_ value: UInt32, into data: inout Data, at offset: Int) {
        data[offset] = UInt8(value & 0xFF)
        data[offset + 1] = UInt8((value >> 8) & 0xFF)
        data[offset + 2] = UInt8((value >> 16) & 0xFF)
        data[offset + 3] = UInt8((value >> 24) & 0xFF)
    }

    private static func appendU32(_ value: UInt32, to data: inout Data) {
        data.append(UInt8(value & 0xFF))
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8((value >> 16) & 0xFF))
        data.append(UInt8((value >> 24) & 0xFF))
    }
}
