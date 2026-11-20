import Foundation
import CryptoKit

enum AoVWwiseBNKError: LocalizedError {
    case emptyOverlay
    case invalidPath
    case duplicatePath(String)
    case bankTooLarge(String)
    case malformed(String)
    case missingBKHD(String)
    case duplicateChunk(String, String)
    case invalidDIDX(String)
    case invalidHIRC(String)
    case unknownChunks(String, [String])
    case externalMedia(String, Int)
    case versionMismatch(String, UInt32, String, UInt32)
    case bankIDConflict(String, UInt32, String)
    case mediaIDConflict(String, UInt32, String)
    case hircIDConflict(String, UInt32, String)

    var errorDescription: String? {
        switch self {
        case .emptyOverlay:
            return "Không có Wwise BNK overlay để kiểm tra."
        case .invalidPath:
            return "Đường dẫn Wwise BNK rỗng hoặc không hợp lệ."
        case .duplicatePath(let path):
            return "\(path): Wwise BNK xuất hiện trùng trong danh sách kiểm tra."
        case .bankTooLarge(let path):
            return "\(path): BNK vượt giới hạn 128 MiB của bộ kiểm tra iPhone."
        case .malformed(let detail):
            return "Wwise BNK không hợp lệ: \(detail)"
        case .missingBKHD(let path):
            return "\(path): phải có đúng một chunk BKHD hợp lệ."
        case .duplicateChunk(let path, let chunk):
            return "\(path): có nhiều chunk \(chunk), chưa được hỗ trợ."
        case .invalidDIDX(let detail):
            return "Wwise DIDX không hợp lệ: \(detail)"
        case .invalidHIRC(let detail):
            return "Wwise HIRC không hợp lệ: \(detail)"
        case .unknownChunks(let path, let chunks):
            return "\(path): có chunk Wwise chưa được nhận diện (\(chunks.joined(separator: ", ")))."
        case .externalMedia(let path, let count):
            return "\(path): có \(count) external WEM reference; adapter append-only chưa xác minh cặp BNK/WEM."
        case .versionMismatch(let path, let version, let otherPath, let otherVersion):
            return "\(path): Wwise version \(version) không khớp \(otherPath) (\(otherVersion))."
        case .bankIDConflict(let path, let id, let otherPath):
            return "\(path): Bank ID \(id) đã được dùng bởi \(otherPath) trong namespace có thể tải cùng nhau."
        case .mediaIDConflict(let path, let id, let otherPath):
            return "\(path): Media ID \(id) trùng nhưng payload khác với \(otherPath)."
        case .hircIDConflict(let path, let id, let otherPath):
            return "\(path): HIRC object ID \(id) trùng nhưng type/payload khác với \(otherPath)."
        }
    }
}

struct AoVWwiseBankInput: Sendable {
    let path: String
    let data: Data
}

struct AoVWwiseAppendReport: Sendable {
    let baseBankCount: Int
    let appendedBankPaths: [String]
    let checkedBankCount: Int
    let verified: Bool
}

private struct AoVWwiseChunk {
    let id: String
    let offset: Int
    let size: Int
}

private struct AoVWwiseMedia {
    let id: UInt32
    let size: Int
    let sha256: String
}

private struct AoVWwiseObject {
    let id: UInt32
    let type: UInt32
    let sha256: String
}

private struct AoVWwiseInventory {
    let version: UInt32
    let bankID: UInt32
    let unknownChunks: [String]
    let media: [AoVWwiseMedia]
    let objects: [AoVWwiseObject]
    let externalMediaCount: Int
}

enum AoVWwiseBNK {
    static let maximumBankBytes = 128 * 1024 * 1024
    private static let maximumChunks = 4_096
    private static let maximumHIRCObjects: UInt32 = 2_000_000

    private static let knownChunks: Set<String> = [
        "BKHD", "DIDX", "DATA", "HIRC", "STID", "INIT", "STMG", "PLAT",
        "ENVS", "FXPR", "STP ", "BANK", "BUSS", "USER", "UNKN"
    ]

    private static let knownLanguages: Set<String> = [
        "arabic", "chinese", "chinesesimplified", "chinesetraditional",
        "dutch", "english", "englishus", "englishuk", "french", "german",
        "greek", "hindi", "indonesian", "italian", "japanese", "korean",
        "malay", "polish", "portuguese", "portuguesebrazil", "russian",
        "spanish", "thai", "turkish", "ukrainian", "vietnamese"
    ]

    static func validateAppendOnly(
        baseBanks: [AoVWwiseBankInput],
        overlayBanks: [AoVWwiseBankInput]
    ) throws -> AoVWwiseAppendReport {
        guard !overlayBanks.isEmpty else { throw AoVWwiseBNKError.emptyOverlay }

        var inventories: [(role: String, path: String, scope: String, value: AoVWwiseInventory)] = []
        var seenPaths = Set<String>()

        for (role, inputs) in [("base", baseBanks), ("overlay", overlayBanks)] {
            for input in inputs {
                let path = normalizePath(input.path)
                guard !path.isEmpty, !path.hasPrefix("/"),
                      !path.split(separator: "/").contains("..") else {
                    throw AoVWwiseBNKError.invalidPath
                }
                guard seenPaths.insert(path.lowercased()).inserted else {
                    throw AoVWwiseBNKError.duplicatePath(path)
                }
                let inventory = try inspect(input.data, path: path)
                if !inventory.unknownChunks.isEmpty {
                    throw AoVWwiseBNKError.unknownChunks(path, inventory.unknownChunks)
                }
                if inventory.externalMediaCount > 0 {
                    throw AoVWwiseBNKError.externalMedia(path, inventory.externalMediaCount)
                }
                inventories.append((
                    role: role,
                    path: path,
                    scope: identityScope(path),
                    value: inventory
                ))
            }
        }

        for currentIndex in inventories.indices {
            let current = inventories[currentIndex]
            for previousIndex in 0..<currentIndex {
                let previous = inventories[previousIndex]
                guard current.value.version == previous.value.version else {
                    throw AoVWwiseBNKError.versionMismatch(
                        current.path, current.value.version,
                        previous.path, previous.value.version
                    )
                }
                guard scopesOverlap(current.scope, previous.scope) else { continue }

                if current.value.bankID == previous.value.bankID {
                    throw AoVWwiseBNKError.bankIDConflict(
                        current.path, current.value.bankID, previous.path
                    )
                }

                let previousMedia = Dictionary(
                    uniqueKeysWithValues: previous.value.media.map { ($0.id, $0) }
                )
                for media in current.value.media {
                    guard let old = previousMedia[media.id] else { continue }
                    guard old.sha256 == media.sha256 else {
                        throw AoVWwiseBNKError.mediaIDConflict(
                            current.path, media.id, previous.path
                        )
                    }
                }

                let previousObjects = Dictionary(
                    uniqueKeysWithValues: previous.value.objects.map { ($0.id, $0) }
                )
                for object in current.value.objects {
                    guard let old = previousObjects[object.id] else { continue }
                    guard old.type == object.type, old.sha256 == object.sha256 else {
                        throw AoVWwiseBNKError.hircIDConflict(
                            current.path, object.id, previous.path
                        )
                    }
                }
            }
        }

        return AoVWwiseAppendReport(
            baseBankCount: baseBanks.count,
            appendedBankPaths: overlayBanks.map { normalizePath($0.path) }.sorted(),
            checkedBankCount: inventories.count,
            verified: true
        )
    }

    private static func inspect(_ data: Data, path: String) throws -> AoVWwiseInventory {
        guard !data.isEmpty, data.count <= maximumBankBytes else {
            throw AoVWwiseBNKError.bankTooLarge(path)
        }
        let chunks = try parseChunks(data)
        var byID: [String: [AoVWwiseChunk]] = [:]
        for chunk in chunks { byID[chunk.id, default: []].append(chunk) }

        guard let headers = byID["BKHD"], headers.count == 1,
              headers[0].size >= 8 else {
            throw AoVWwiseBNKError.missingBKHD(path)
        }
        let header = headers[0]
        let version = try readUInt32(data, at: header.offset)
        let bankID = try readUInt32(data, at: header.offset + 4)

        let didxChunks = byID["DIDX"] ?? []
        let dataChunks = byID["DATA"] ?? []
        guard didxChunks.count <= 1 else {
            throw AoVWwiseBNKError.duplicateChunk(path, "DIDX")
        }
        guard dataChunks.count <= 1 else {
            throw AoVWwiseBNKError.duplicateChunk(path, "DATA")
        }

        var media: [AoVWwiseMedia] = []
        if let didx = didxChunks.first {
            guard let audio = dataChunks.first else {
                throw AoVWwiseBNKError.invalidDIDX("\(path): có DIDX nhưng thiếu DATA.")
            }
            guard didx.size % 12 == 0 else {
                throw AoVWwiseBNKError.invalidDIDX("\(path): kích thước DIDX không chia hết cho 12.")
            }

            var seenIDs = Set<UInt32>()
            var intervals: [(start: Int, end: Int, id: UInt32)] = []
            var cursor = didx.offset
            let didxEnd = didx.offset + didx.size
            while cursor < didxEnd {
                let mediaID = try readUInt32(data, at: cursor)
                let rawOffset = try readUInt32(data, at: cursor + 4)
                let rawSize = try readUInt32(data, at: cursor + 8)
                guard seenIDs.insert(mediaID).inserted else {
                    throw AoVWwiseBNKError.invalidDIDX("\(path): Media ID \(mediaID) bị lặp.")
                }
                let mediaOffset = Int(rawOffset)
                let mediaSize = Int(rawSize)
                guard mediaOffset <= audio.size, mediaSize <= audio.size - mediaOffset else {
                    throw AoVWwiseBNKError.invalidDIDX("\(path): Media ID \(mediaID) vượt kích thước DATA.")
                }
                if mediaSize > 0 {
                    intervals.append((mediaOffset, mediaOffset + mediaSize, mediaID))
                }
                let payloadStart = audio.offset + mediaOffset
                let payload = data.subdata(in: payloadStart..<(payloadStart + mediaSize))
                media.append(AoVWwiseMedia(
                    id: mediaID,
                    size: mediaSize,
                    sha256: digest(payload)
                ))
                cursor += 12
            }
            if intervals.count > 1 {
                intervals.sort {
                    if $0.start == $1.start { return $0.end < $1.end }
                    return $0.start < $1.start
                }
                for index in 1..<intervals.count {
                    if intervals[index].start < intervals[index - 1].end {
                        throw AoVWwiseBNKError.invalidDIDX(
                            "\(path): vùng media \(intervals[index - 1].id) và \(intervals[index].id) chồng lấn."
                        )
                    }
                }
            }
        }

        let hircChunks = byID["HIRC"] ?? []
        guard hircChunks.count <= 1 else {
            throw AoVWwiseBNKError.duplicateChunk(path, "HIRC")
        }
        var objects: [AoVWwiseObject] = []
        if let hirc = hircChunks.first {
            guard hirc.size >= 4 else {
                throw AoVWwiseBNKError.invalidHIRC("\(path): thiếu số lượng object.")
            }
            let count = try readUInt32(data, at: hirc.offset)
            guard count <= maximumHIRCObjects else {
                throw AoVWwiseBNKError.invalidHIRC("\(path): số lượng object vượt giới hạn.")
            }

            let width = version <= 48 ? 4 : 1
            let headerSize = width + 4
            var cursor = hirc.offset + 4
            let end = hirc.offset + hirc.size
            var seenIDs = Set<UInt32>()

            for index in 0..<Int(count) {
                guard cursor <= end - headerSize else {
                    throw AoVWwiseBNKError.invalidHIRC("\(path): object \(index) bị cắt cụt.")
                }
                let objectType: UInt32
                if width == 4 {
                    objectType = try readUInt32(data, at: cursor)
                } else {
                    objectType = UInt32(data[cursor])
                }
                let objectSize = Int(try readUInt32(data, at: cursor + width))
                let bodyStart = cursor + headerSize
                guard objectSize >= 4, objectSize <= end - bodyStart else {
                    throw AoVWwiseBNKError.invalidHIRC("\(path): kích thước object \(index) không hợp lệ.")
                }
                let bodyEnd = bodyStart + objectSize
                let objectID = try readUInt32(data, at: bodyStart)
                guard seenIDs.insert(objectID).inserted else {
                    throw AoVWwiseBNKError.invalidHIRC("\(path): object ID \(objectID) bị lặp.")
                }
                objects.append(AoVWwiseObject(
                    id: objectID,
                    type: objectType,
                    sha256: digest(data.subdata(in: cursor..<bodyEnd))
                ))
                cursor = bodyEnd
            }
            guard cursor == end else {
                throw AoVWwiseBNKError.invalidHIRC("\(path): còn dữ liệu sau object cuối.")
            }
        }

        let unknown = Set(chunks.map(\.id)).subtracting(knownChunks).sorted()
        return AoVWwiseInventory(
            version: version,
            bankID: bankID,
            unknownChunks: unknown,
            media: media,
            objects: objects,
            externalMediaCount: media.filter { $0.size == 0 }.count
        )
    }

    private static func parseChunks(_ data: Data) throws -> [AoVWwiseChunk] {
        guard data.count >= 8 else {
            throw AoVWwiseBNKError.malformed("file quá ngắn để chứa chunk.")
        }
        var chunks: [AoVWwiseChunk] = []
        var cursor = 0
        while cursor < data.count {
            guard chunks.count < maximumChunks, cursor <= data.count - 8 else {
                throw AoVWwiseBNKError.malformed("chunk bị cắt cụt hoặc vượt số lượng tối đa.")
            }
            guard let id = String(
                data: data.subdata(in: cursor..<(cursor + 4)),
                encoding: .ascii
            ) else {
                throw AoVWwiseBNKError.malformed("mã chunk không phải ASCII.")
            }
            let size = Int(try readUInt32(data, at: cursor + 4))
            let payloadOffset = cursor + 8
            guard size <= data.count - payloadOffset else {
                throw AoVWwiseBNKError.malformed("chunk \(id) vượt cuối file.")
            }
            chunks.append(AoVWwiseChunk(id: id, offset: payloadOffset, size: size))
            cursor = payloadOffset + size
        }
        return chunks
    }

    private static func readUInt32(_ data: Data, at offset: Int) throws -> UInt32 {
        guard offset >= 0, offset <= data.count - 4 else {
            throw AoVWwiseBNKError.malformed("số nguyên 32-bit bị cắt cụt.")
        }
        return UInt32(data[offset]) |
            (UInt32(data[offset + 1]) << 8) |
            (UInt32(data[offset + 2]) << 16) |
            (UInt32(data[offset + 3]) << 24)
    }

    private static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func normalizePath(_ path: String) -> String {
        path.replacingOccurrences(of: "\\", with: "/")
            .split(separator: "/", omittingEmptySubsequences: true)
            .joined(separator: "/")
    }

    private static func identityScope(_ path: String) -> String {
        let parts = normalizePath(path).split(separator: "/").map(String.init)
        let lowered = parts.map { $0.lowercased() }
        for index in lowered.indices {
            guard lowered[index] == "sound", index + 2 < parts.count else { continue }
            let platform = lowered[index + 1]
            guard platform == "ios" || platform == "android" else { continue }

            let possibleLocale = parts[index + 2]
            let normalizedLocale = String(
                possibleLocale.lowercased().filter { $0.isLetter || $0.isNumber }
            )
            let isLanguage = knownLanguages.contains(normalizedLocale) ||
                possibleLocale.range(
                    of: #"^[a-z]{2,3}(?:[-_][a-z0-9]{2,8})?$"#,
                    options: .regularExpression
                ) != nil
            let hasBankBelowLocale = index + 3 < parts.count
            if isLanguage && hasBankBelowLocale {
                return "\(platform)|\(normalizedLocale)"
            }
            return "\(platform)|default"
        }
        return "global"
    }

    private static func scopesOverlap(_ left: String, _ right: String) -> Bool {
        if left == "global" || right == "global" { return true }
        let lhs = left.split(separator: "|", maxSplits: 1).map(String.init)
        let rhs = right.split(separator: "|", maxSplits: 1).map(String.init)
        guard lhs.count == 2, rhs.count == 2 else { return true }
        guard lhs[0] == rhs[0] else { return false }
        return lhs[1] == "default" || rhs[1] == "default" || lhs[1] == rhs[1]
    }
}
