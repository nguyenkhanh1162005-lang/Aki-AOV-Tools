import Foundation

private struct AoVUnityTypeTreeBinaryWriter {
    var data = Data()
    let littleEndian: Bool

    mutating func appendByte(_ value: UInt8) {
        data.append(value)
    }

    mutating func appendBytes(_ value: Data) {
        data.append(value)
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

    mutating func appendAlignedString(_ value: String, field: String) throws {
        let bytes = Data(value.utf8)
        guard bytes.count <= AoVUnityTypeTreeWriter.maximumStringBytes,
              let length = Int32(exactly: bytes.count) else {
            throw AoVUnityTypeTreeError.resourceLimit("\(field) string quá lớn để ghi.")
        }
        appendInt32(length)
        appendBytes(bytes)
        align(4)
    }

    mutating func align(_ boundary: Int) {
        guard boundary > 0 else { return }
        while data.count % boundary != 0 { data.append(0) }
    }
}

/// Writes values against the same bounded TypeTree model used by the reader.
/// The writer follows TypeTree child order, never dictionary iteration order,
/// and refuses missing, extra or incompatible fields instead of guessing.
enum AoVUnityTypeTreeWriter {
    static let maximumStringBytes = 1_048_576
    static let maximumOutputBytes = 64 * 1024 * 1024

    static func write(
        nodes: [AoVUnityTypeTreeNode],
        value: AoVUnityTypeTreeValue,
        littleEndian: Bool
    ) throws -> Data {
        guard !nodes.isEmpty else { throw AoVUnityTypeTreeError.emptyTree }
        guard nodes.count <= AoVUnityTypeTreeReader.maximumNodes,
              nodes[0].level == 0 else {
            throw AoVUnityTypeTreeError.invalidTree("TypeTree writer cần một root hợp lệ.")
        }
        let subtreeEnds = try buildSubtreeEnds(nodes)
        var writer = AoVUnityTypeTreeBinaryWriter(littleEndian: littleEndian)
        var remainingValues = AoVUnityTypeTreeReader.maximumTotalValues
        try writeNode(
            at: 0,
            nodes: nodes,
            subtreeEnds: subtreeEnds,
            value: value,
            writer: &writer,
            depth: 0,
            remainingValues: &remainingValues
        )
        guard writer.data.count <= maximumOutputBytes else {
            throw AoVUnityTypeTreeError.resourceLimit("TypeTree output vượt 64 MiB.")
        }
        return writer.data
    }

    private static func buildSubtreeEnds(
        _ nodes: [AoVUnityTypeTreeNode]
    ) throws -> [Int] {
        var ends = Array(repeating: nodes.count, count: nodes.count)
        var stack: [Int] = []
        stack.reserveCapacity(64)
        for index in nodes.indices {
            let level = nodes[index].level
            while let parent = stack.last, level <= nodes[parent].level {
                ends[parent] = index
                stack.removeLast()
            }
            if let parent = stack.last {
                guard Int(level) == Int(nodes[parent].level) + 1 else {
                    throw AoVUnityTypeTreeError.invalidTree(
                        "TypeTree writer gặp level nhảy bất thường tại node \(index)."
                    )
                }
            } else if index != 0 {
                throw AoVUnityTypeTreeError.invalidTree("TypeTree writer gặp nhiều root.")
            }
            stack.append(index)
        }
        while let index = stack.popLast() { ends[index] = nodes.count }
        return ends
    }

    private static func writeNode(
        at index: Int,
        nodes: [AoVUnityTypeTreeNode],
        subtreeEnds: [Int],
        value: AoVUnityTypeTreeValue,
        writer: inout AoVUnityTypeTreeBinaryWriter,
        depth: Int,
        remainingValues: inout Int
    ) throws {
        guard index >= 0, index < nodes.count else {
            throw AoVUnityTypeTreeError.invalidTree("TypeTree writer node index vượt phạm vi.")
        }
        guard depth <= AoVUnityTypeTreeReader.maximumDepth else {
            throw AoVUnityTypeTreeError.resourceLimit("TypeTree writer vượt giới hạn độ sâu.")
        }
        remainingValues -= 1
        guard remainingValues >= 0 else {
            throw AoVUnityTypeTreeError.resourceLimit("TypeTree writer vượt số giá trị cho phép.")
        }

        let node = nodes[index]
        let firstChild = index + 1
        let end = subtreeEnds[index]
        let childCount = end - firstChild
        var shouldAlign = (node.metaFlags & AoVUnityTypeTreeReader.alignmentFlag) != 0

        switch node.type {
        case "SInt8":
            let number = try signedInteger(value, field: node.name)
            guard let exact = Int8(exactly: number) else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) không vừa SInt8.")
            }
            writer.appendByte(UInt8(bitPattern: exact))
        case "UInt8", "char":
            writer.appendByte(try unsigned8(value, field: node.name))
        case "short", "SInt16":
            let number = try signedInteger(value, field: node.name)
            guard let exact = Int16(exactly: number) else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) không vừa SInt16.")
            }
            writer.appendUInt16(UInt16(bitPattern: exact))
        case "UInt16", "unsigned short":
            writer.appendUInt16(try unsigned16(value, field: node.name))
        case "int", "SInt32":
            let number = try signedInteger(value, field: node.name)
            guard let exact = Int32(exactly: number) else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) không vừa SInt32.")
            }
            writer.appendInt32(exact)
        case "UInt32", "unsigned int", "Type*":
            writer.appendUInt32(try unsigned32(value, field: node.name))
        case "long long", "SInt64":
            writer.appendInt64(try signedInteger(value, field: node.name))
        case "UInt64", "unsigned long long", "FileSize":
            writer.appendUInt64(try unsigned64(value, field: node.name))
        case "float":
            guard case .floating(let number) = value,
                  number.isFinite,
                  let exact = Float(exactly: number) else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) phải là Float hữu hạn.")
            }
            writer.appendUInt32(exact.bitPattern)
        case "double":
            guard case .floating(let number) = value, number.isFinite else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) phải là Double hữu hạn.")
            }
            writer.appendUInt64(number.bitPattern)
        case "bool":
            guard case .boolean(let flag) = value else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) phải là Bool.")
            }
            writer.appendByte(flag ? 1 : 0)
        case "string":
            guard case .string(let string) = value else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) phải là String.")
            }
            try writer.appendAlignedString(string, field: node.name)
        case "TypelessData":
            guard case .bytes(let bytes) = value,
                  bytes.count <= 8 * 1024 * 1024,
                  let length = Int32(exactly: bytes.count) else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) phải là TypelessData hợp lệ.")
            }
            writer.appendInt32(length)
            writer.appendBytes(bytes)
        case "map":
            guard childCount >= 5, firstChild < end,
                  nodes[firstChild].type == "Array" else {
                throw AoVUnityTypeTreeError.invalidTree(
                    "map \(node.name) không có layout Array/Size/pair hợp lệ."
                )
            }
            if (nodes[firstChild].metaFlags & AoVUnityTypeTreeReader.alignmentFlag) != 0 {
                shouldAlign = true
            }
            guard case .map(let entries) = value,
                  entries.count <= AoVUnityTypeTreeReader.maximumArrayElements,
                  let keyIndex = (index + 4) as Int?,
                  keyIndex < end else {
                throw AoVUnityTypeTreeError.invalidValue("\(node.name) phải là map hợp lệ.")
            }
            let valueIndex = subtreeEnds[keyIndex]
            guard valueIndex < end else {
                throw AoVUnityTypeTreeError.invalidTree("map \(node.name) thiếu value node.")
            }
            guard entries.count <= remainingValues / 2 else {
                throw AoVUnityTypeTreeError.resourceLimit("map \(node.name) vượt số giá trị cho phép.")
            }
            writer.appendInt32(Int32(entries.count))
            for entry in entries {
                try writeNode(
                    at: keyIndex,
                    nodes: nodes,
                    subtreeEnds: subtreeEnds,
                    value: entry.key,
                    writer: &writer,
                    depth: depth + 1,
                    remainingValues: &remainingValues
                )
                try writeNode(
                    at: valueIndex,
                    nodes: nodes,
                    subtreeEnds: subtreeEnds,
                    value: entry.value,
                    writer: &writer,
                    depth: depth + 1,
                    remainingValues: &remainingValues
                )
            }
        default:
            if childCount > 0 && nodes[firstChild].type == "Array" {
                if (nodes[firstChild].metaFlags & AoVUnityTypeTreeReader.alignmentFlag) != 0 {
                    shouldAlign = true
                }
                let dataIndex = index + 3
                guard dataIndex < end, case .array(let values) = value,
                      values.count <= AoVUnityTypeTreeReader.maximumArrayElements else {
                    throw AoVUnityTypeTreeError.invalidValue("\(node.name) phải là array hợp lệ.")
                }
                guard values.count <= remainingValues else {
                    throw AoVUnityTypeTreeError.resourceLimit("array \(node.name) vượt số giá trị cho phép.")
                }
                writer.appendInt32(Int32(values.count))
                for item in values {
                    try writeNode(
                        at: dataIndex,
                        nodes: nodes,
                        subtreeEnds: subtreeEnds,
                        value: item,
                        writer: &writer,
                        depth: depth + 1,
                        remainingValues: &remainingValues
                    )
                }
            } else if childCount > 0 {
                guard case .object(let fields) = value else {
                    throw AoVUnityTypeTreeError.invalidValue("\(node.name) phải là object.")
                }
                var expectedNames = Set<String>()
                var childIndex = firstChild
                while childIndex < end {
                    let child = nodes[childIndex]
                    guard expectedNames.insert(child.name).inserted,
                          let childValue = fields[child.name] else {
                        throw AoVUnityTypeTreeError.invalidValue(
                            "\(node.name) thiếu field hoặc lặp field \(child.name)."
                        )
                    }
                    try writeNode(
                        at: childIndex,
                        nodes: nodes,
                        subtreeEnds: subtreeEnds,
                        value: childValue,
                        writer: &writer,
                        depth: depth + 1,
                        remainingValues: &remainingValues
                    )
                    childIndex = subtreeEnds[childIndex]
                }
                guard expectedNames == Set(fields.keys) else {
                    throw AoVUnityTypeTreeError.invalidValue("\(node.name) có field không nằm trong TypeTree.")
                }
            } else if node.byteSize > 0 {
                guard case .bytes(let bytes) = value, bytes.count == Int(node.byteSize) else {
                    throw AoVUnityTypeTreeError.invalidValue(
                        "Leaf \(node.name) cần đúng \(node.byteSize) byte."
                    )
                }
                writer.appendBytes(bytes)
            } else {
                throw AoVUnityTypeTreeError.unsupportedLeaf(node.type)
            }
        }

        if shouldAlign { writer.align(4) }
        guard writer.data.count <= maximumOutputBytes else {
            throw AoVUnityTypeTreeError.resourceLimit("TypeTree output vượt 64 MiB.")
        }
    }

    private static func signedInteger(_ value: AoVUnityTypeTreeValue, field: String) throws -> Int64 {
        guard let number = value.intValue else {
            throw AoVUnityTypeTreeError.invalidValue("\(field) phải là số nguyên có dấu.")
        }
        return number
    }

    private static func unsigned8(_ value: AoVUnityTypeTreeValue, field: String) throws -> UInt8 {
        guard let number = unsigned64Value(value), let exact = UInt8(exactly: number) else {
            throw AoVUnityTypeTreeError.invalidValue("\(field) không vừa UInt8.")
        }
        return exact
    }

    private static func unsigned16(_ value: AoVUnityTypeTreeValue, field: String) throws -> UInt16 {
        guard let number = unsigned64Value(value), let exact = UInt16(exactly: number) else {
            throw AoVUnityTypeTreeError.invalidValue("\(field) không vừa UInt16.")
        }
        return exact
    }

    private static func unsigned32(_ value: AoVUnityTypeTreeValue, field: String) throws -> UInt32 {
        guard let number = unsigned64Value(value), let exact = UInt32(exactly: number) else {
            throw AoVUnityTypeTreeError.invalidValue("\(field) không vừa UInt32.")
        }
        return exact
    }

    private static func unsigned64(_ value: AoVUnityTypeTreeValue, field: String) throws -> UInt64 {
        guard let number = unsigned64Value(value) else {
            throw AoVUnityTypeTreeError.invalidValue("\(field) phải là số nguyên không dấu.")
        }
        return number
    }

    private static func unsigned64Value(_ value: AoVUnityTypeTreeValue) -> UInt64? {
        switch value {
        case .unsigned(let number): return number
        case .signed(let number): return UInt64(exactly: number)
        default: return nil
        }
    }
}
