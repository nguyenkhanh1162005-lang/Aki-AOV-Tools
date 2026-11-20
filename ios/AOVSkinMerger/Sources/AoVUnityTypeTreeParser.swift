import Foundation

struct AoVUnityTypeTreeNode: Codable, Hashable, Sendable {
    let type: String
    let name: String
    let level: UInt8
    let byteSize: Int32
    let metaFlags: UInt32
}

struct AoVUnityTypeTreeMapEntry: Sendable, Equatable {
    let key: AoVUnityTypeTreeValue
    let value: AoVUnityTypeTreeValue
}

indirect enum AoVUnityTypeTreeValue: Sendable, Equatable {
    case signed(Int64)
    case unsigned(UInt64)
    case floating(Double)
    case boolean(Bool)
    case string(String)
    case array([AoVUnityTypeTreeValue])
    case object([String: AoVUnityTypeTreeValue])
    case map([AoVUnityTypeTreeMapEntry])
    case bytes(Data)

    var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var intValue: Int64? {
        switch self {
        case .signed(let value): return value
        case .unsigned(let value): return Int64(exactly: value)
        default: return nil
        }
    }

    var arrayValue: [AoVUnityTypeTreeValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    var objectValue: [String: AoVUnityTypeTreeValue]? {
        if case .object(let value) = self { return value }
        return nil
    }
}

enum AoVUnityTypeTreeError: LocalizedError {
    case emptyTree
    case invalidTree(String)
    case truncated(String)
    case invalidValue(String)
    case resourceLimit(String)
    case unsupportedLeaf(String)
    case unconsumedBytes(expected: Int, actual: Int)

    var errorDescription: String? {
        switch self {
        case .emptyTree:
            return "TypeTree rỗng."
        case .invalidTree(let reason):
            return "TypeTree không hợp lệ: \(reason)"
        case .truncated(let field):
            return "Dữ liệu MonoBehaviour bị cắt tại \(field)."
        case .invalidValue(let reason):
            return "Giá trị TypeTree không hợp lệ: \(reason)"
        case .resourceLimit(let reason):
            return "TypeTree vượt giới hạn kiểm tra: \(reason)"
        case .unsupportedLeaf(let type):
            return "Chưa hỗ trợ TypeTree leaf type: \(type)"
        case .unconsumedBytes(let expected, let actual):
            return "TypeTree chỉ đọc \(actual)/\(expected) byte của MonoBehaviour."
        }
    }
}

private struct AoVUnityTypeTreeBinaryReader {
    let data: Data
    var offset = 0
    let littleEndian: Bool

    mutating func readByte(_ field: String) throws -> UInt8 {
        guard offset >= 0, offset < data.count else {
            throw AoVUnityTypeTreeError.truncated(field)
        }
        let value = data[offset]
        offset += 1
        return value
    }

    mutating func readBytes(_ count: Int, _ field: String) throws -> Data {
        guard count >= 0, offset >= 0, offset <= data.count,
              count <= data.count - offset else {
            throw AoVUnityTypeTreeError.truncated(field)
        }
        let value = data.subdata(in: offset..<(offset + count))
        offset += count
        return value
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
        return littleEndian
            ? (a | (b << 8) | (c << 16) | (d << 24))
            : ((a << 24) | (b << 16) | (c << 8) | d)
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

    mutating func align(_ boundary: Int) throws {
        guard boundary > 0, boundary.nonzeroBitCount == 1 else {
            throw AoVUnityTypeTreeError.invalidValue("alignment không hợp lệ.")
        }
        let remainder = offset % boundary
        if remainder != 0 {
            let amount = boundary - remainder
            guard amount <= data.count - offset else {
                throw AoVUnityTypeTreeError.truncated("alignment")
            }
            offset += amount
        }
    }

    mutating func readAlignedString(_ field: String, maximumBytes: Int) throws -> String {
        let rawLength = try readInt32("\(field) length")
        guard rawLength >= 0, rawLength <= maximumBytes else {
            throw AoVUnityTypeTreeError.resourceLimit(
                "\(field) có độ dài \(rawLength), giới hạn \(maximumBytes)."
            )
        }
        let bytes = try readBytes(Int(rawLength), field)
        guard let value = String(data: bytes, encoding: .utf8) else {
            throw AoVUnityTypeTreeError.invalidValue("\(field) không phải UTF-8.")
        }
        try align(4)
        return value
    }

    mutating func readCount(_ field: String, maximum: Int) throws -> Int {
        let raw = try readInt32(field)
        guard raw >= 0, raw <= maximum else {
            throw AoVUnityTypeTreeError.resourceLimit(
                "\(field)=\(raw), giới hạn \(maximum)."
            )
        }
        return Int(raw)
    }
}

enum AoVUnityTypeTreeReader {
    static let maximumNodes = 100_000
    static let maximumArrayElements = 100_000
    static let maximumTotalValues = 250_000
    static let maximumDepth = 64
    static let maximumStringBytes = 1_048_576
    static let alignmentFlag: UInt32 = 0x4000

    static func read(
        nodes: [AoVUnityTypeTreeNode],
        data: Data,
        littleEndian: Bool
    ) throws -> AoVUnityTypeTreeValue {
        guard !nodes.isEmpty else {
            throw AoVUnityTypeTreeError.emptyTree
        }
        guard nodes.count <= maximumNodes else {
            throw AoVUnityTypeTreeError.resourceLimit("quá \(maximumNodes) TypeTree nodes.")
        }
        guard nodes[0].level == 0 else {
            throw AoVUnityTypeTreeError.invalidTree("root node phải có level 0.")
        }
        guard data.count <= 16 * 1024 * 1024 else {
            throw AoVUnityTypeTreeError.resourceLimit("MonoBehaviour object lớn hơn 16 MiB.")
        }

        let subtreeEnds = try buildSubtreeEnds(nodes)
        var reader = AoVUnityTypeTreeBinaryReader(data: data, littleEndian: littleEndian)
        var remainingValues = maximumTotalValues
        let value = try parseNode(
            at: 0,
            nodes: nodes,
            subtreeEnds: subtreeEnds,
            reader: &reader,
            depth: 0,
            remainingValues: &remainingValues
        )
        guard reader.offset == data.count else {
            throw AoVUnityTypeTreeError.unconsumedBytes(
                expected: data.count,
                actual: reader.offset
            )
        }
        return value
    }

    private static func buildSubtreeEnds(
        _ nodes: [AoVUnityTypeTreeNode]
    ) throws -> [Int] {
        var ends = Array(repeating: nodes.count, count: nodes.count)
        var stack: [Int] = []
        stack.reserveCapacity(64)

        for index in nodes.indices {
            let level = nodes[index].level
            while let parent = stack.last, nodes[index].level <= nodes[parent].level {
                ends[parent] = index
                stack.removeLast()
            }
            if let parent = stack.last {
                guard Int(level) == Int(nodes[parent].level) + 1 else {
                    throw AoVUnityTypeTreeError.invalidTree(
                        "level nhảy bất thường tại node \(index) (\(level))."
                    )
                }
            } else if index != 0 {
                throw AoVUnityTypeTreeError.invalidTree("nhiều hơn một root node.")
            }
            stack.append(index)
        }

        while let index = stack.popLast() {
            ends[index] = nodes.count
        }
        return ends
    }

    private static func parseNode(
        at index: Int,
        nodes: [AoVUnityTypeTreeNode],
        subtreeEnds: [Int],
        reader: inout AoVUnityTypeTreeBinaryReader,
        depth: Int,
        remainingValues: inout Int
    ) throws -> AoVUnityTypeTreeValue {
        guard index >= 0, index < nodes.count else {
            throw AoVUnityTypeTreeError.invalidTree("node index \(index) vượt TypeTree.")
        }
        guard depth <= maximumDepth else {
            throw AoVUnityTypeTreeError.resourceLimit("độ sâu TypeTree vượt \(maximumDepth).")
        }
        remainingValues -= 1
        guard remainingValues >= 0 else {
            throw AoVUnityTypeTreeError.resourceLimit("quá nhiều giá trị TypeTree.")
        }

        let node = nodes[index]
        let end = subtreeEnds[index]
        let firstChild = index + 1
        let childCount = end - firstChild
        var shouldAlign = (node.metaFlags & alignmentFlag) != 0
        let type = node.type

        let value: AoVUnityTypeTreeValue
        switch type {
        case "SInt8":
            value = .signed(Int64(Int8(bitPattern: try reader.readByte(node.name))))
        case "UInt8", "char":
            value = .unsigned(UInt64(try reader.readByte(node.name)))
        case "short", "SInt16":
            value = .signed(Int64(try reader.readInt16(node.name)))
        case "UInt16", "unsigned short":
            value = .unsigned(UInt64(try reader.readUInt16(node.name)))
        case "int", "SInt32":
            value = .signed(Int64(try reader.readInt32(node.name)))
        case "UInt32", "unsigned int", "Type*":
            value = .unsigned(UInt64(try reader.readUInt32(node.name)))
        case "long long", "SInt64":
            value = .signed(try reader.readInt64(node.name))
        case "UInt64", "unsigned long long", "FileSize":
            value = .unsigned(try reader.readUInt64(node.name))
        case "float":
            value = .floating(Double(Float(bitPattern: try reader.readUInt32(node.name))))
        case "double":
            value = .floating(Double(bitPattern: try reader.readUInt64(node.name)))
        case "bool":
            value = .boolean(try reader.readByte(node.name) != 0)
        case "string":
            value = .string(try reader.readAlignedString(
                node.name,
                maximumBytes: maximumStringBytes
            ))
        case "TypelessData":
            let size = try reader.readCount(
                "\(node.name) byte count",
                maximum: 8 * 1024 * 1024
            )
            value = .bytes(try reader.readBytes(size, node.name))
        case "map":
            guard childCount >= 5, firstChild < end,
                  nodes[firstChild].type == "Array" else {
                throw AoVUnityTypeTreeError.invalidTree(
                    "map \(node.name) không có layout Array/Size/pair hợp lệ."
                )
            }
            if (nodes[firstChild].metaFlags & alignmentFlag) != 0 {
                shouldAlign = true
            }
            let keyIndex = index + 4
            guard keyIndex < end else {
                throw AoVUnityTypeTreeError.invalidTree("map \(node.name) thiếu key node.")
            }
            let valueIndex = subtreeEnds[keyIndex]
            guard valueIndex < end else {
                throw AoVUnityTypeTreeError.invalidTree("map \(node.name) thiếu value node.")
            }
            let count = try reader.readCount(
                "\(node.name) map count",
                maximum: maximumArrayElements
            )
            guard count <= remainingValues / 2 else {
                throw AoVUnityTypeTreeError.resourceLimit("map \(node.name) vượt giới hạn giá trị.")
            }
            var entries: [AoVUnityTypeTreeMapEntry] = []
            entries.reserveCapacity(count)
            for _ in 0..<count {
                let key = try parseNode(
                    at: keyIndex,
                    nodes: nodes,
                    subtreeEnds: subtreeEnds,
                    reader: &reader,
                    depth: depth + 1,
                    remainingValues: &remainingValues
                )
                let entryValue = try parseNode(
                    at: valueIndex,
                    nodes: nodes,
                    subtreeEnds: subtreeEnds,
                    reader: &reader,
                    depth: depth + 1,
                    remainingValues: &remainingValues
                )
                entries.append(AoVUnityTypeTreeMapEntry(key: key, value: entryValue))
            }
            value = .map(entries)
        default:
            if childCount > 0 && nodes[firstChild].type == "Array" {
                if (nodes[firstChild].metaFlags & alignmentFlag) != 0 {
                    shouldAlign = true
                }
                let dataIndex = index + 3
                guard dataIndex < end else {
                    throw AoVUnityTypeTreeError.invalidTree(
                        "array \(node.name) thiếu Data element node."
                    )
                }
                let count = try reader.readCount(
                    "\(node.name) array count",
                    maximum: maximumArrayElements
                )
                guard count <= remainingValues else {
                    throw AoVUnityTypeTreeError.resourceLimit(
                        "array \(node.name) vượt tổng số phần tử cho phép."
                    )
                }
                var items: [AoVUnityTypeTreeValue] = []
                items.reserveCapacity(count)
                for _ in 0..<count {
                    items.append(
                        try parseNode(
                            at: dataIndex,
                            nodes: nodes,
                            subtreeEnds: subtreeEnds,
                            reader: &reader,
                            depth: depth + 1,
                            remainingValues: &remainingValues
                        )
                    )
                }
                value = .array(items)
            } else if childCount > 0 {
                var fields: [String: AoVUnityTypeTreeValue] = [:]
                var childIndex = firstChild
                while childIndex < end {
                    let child = nodes[childIndex]
                    guard fields[child.name] == nil else {
                        throw AoVUnityTypeTreeError.invalidTree(
                            "tên field \(child.name) trùng trong \(node.name)."
                        )
                    }
                    fields[child.name] = try parseNode(
                        at: childIndex,
                        nodes: nodes,
                        subtreeEnds: subtreeEnds,
                        reader: &reader,
                        depth: depth + 1,
                        remainingValues: &remainingValues
                    )
                    childIndex = subtreeEnds[childIndex]
                }
                value = .object(fields)
            } else if node.byteSize > 0 {
                let size = Int(node.byteSize)
                guard size <= 8 * 1024 * 1024 else {
                    throw AoVUnityTypeTreeError.resourceLimit(
                        "leaf \(node.name) yêu cầu \(size) byte."
                    )
                }
                value = .bytes(try reader.readBytes(size, node.name))
            } else {
                throw AoVUnityTypeTreeError.unsupportedLeaf(type)
            }
        }

        if shouldAlign {
            try reader.align(4)
        }
        return value
    }
}
