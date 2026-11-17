import Foundation

enum AoVUnityTypeTreePointerRemapError: LocalizedError {
    case invalidTree(String)
    case invalidPointer(String)
    case unresolvedLocalPointer(String, Int64)
    case unresolvedExternalFileID(String, Int32)
    case unresolvedExternalTarget(String, Int32, Int64)

    var errorDescription: String? {
        switch self {
        case .invalidTree(let path):
            return "TypeTree pointer remapper gặp cấu trúc không hợp lệ tại \(path)."
        case .invalidPointer(let path):
            return "PPtr không có m_FileID/m_PathID hợp lệ tại \(path)."
        case .unresolvedLocalPointer(let field, let pathID):
            return "Không tìm thấy local PPtr \(field) pathID \(pathID) trong CAB đích."
        case .unresolvedExternalFileID(let field, let fileID):
            return "Không có ánh xạ external fileID \(fileID) tại \(field)."
        case .unresolvedExternalTarget(let field, let fileID, let pathID):
            return "Không tìm thấy external PPtr \(field) fileID \(fileID) pathID \(pathID) trong CAB đích."
        }
    }
}

struct AoVUnityTypeTreePointerRemapReport: Sendable {
    let value: AoVUnityTypeTreeValue
    let remappedLocalPointers: Int
    let remappedExternalPointers: Int
    let auditedExternalPointers: Int
}

/// Rewrites PPtr<T> values by walking a parsed TypeTree, not by searching raw
/// bytes. Every non-null destination pointer must resolve against supplied
/// destination object inventories before the rewritten value is returned.
enum AoVUnityTypeTreePointerRemapper {
    static func remap(
        nodes: [AoVUnityTypeTreeNode],
        value: AoVUnityTypeTreeValue,
        localPathIDMap: [Int64: Int64] = [:],
        availableLocalPathIDs: Set<Int64>,
        externalFileIDMap: [Int32: Int32],
        externalPathIDMaps: [Int32: [Int64: Int64]] = [:],
        availableExternalTargetPathIDs: [Int32: Set<Int64>]
    ) throws -> AoVUnityTypeTreePointerRemapReport {
        guard !nodes.isEmpty, nodes[0].level == 0 else {
            throw AoVUnityTypeTreePointerRemapError.invalidTree("root")
        }
        let subtreeEnds = try buildSubtreeEnds(nodes)
        var localCount = 0
        var externalCount = 0
        var auditedExternalCount = 0
        let rewritten = try remapNode(
            at: 0,
            nodes: nodes,
            subtreeEnds: subtreeEnds,
            value: value,
            path: nodes[0].name,
            localPathIDMap: localPathIDMap,
            availableLocalPathIDs: availableLocalPathIDs,
            externalFileIDMap: externalFileIDMap,
            externalPathIDMaps: externalPathIDMaps,
            availableExternalTargetPathIDs: availableExternalTargetPathIDs,
            localCount: &localCount,
            externalCount: &externalCount,
            auditedExternalCount: &auditedExternalCount
        )
        return AoVUnityTypeTreePointerRemapReport(
            value: rewritten,
            remappedLocalPointers: localCount,
            remappedExternalPointers: externalCount,
            auditedExternalPointers: auditedExternalCount
        )
    }

    private static func buildSubtreeEnds(_ nodes: [AoVUnityTypeTreeNode]) throws -> [Int] {
        guard nodes.count <= AoVUnityTypeTreeReader.maximumNodes else {
            throw AoVUnityTypeTreePointerRemapError.invalidTree("node limit")
        }
        var ends = Array(repeating: nodes.count, count: nodes.count)
        var stack: [Int] = []
        for index in nodes.indices {
            while let parent = stack.last, nodes[index].level <= nodes[parent].level {
                ends[parent] = index
                stack.removeLast()
            }
            if let parent = stack.last {
                guard Int(nodes[index].level) == Int(nodes[parent].level) + 1 else {
                    throw AoVUnityTypeTreePointerRemapError.invalidTree("node \(index) level")
                }
            } else if index != 0 {
                throw AoVUnityTypeTreePointerRemapError.invalidTree("multiple roots")
            }
            stack.append(index)
        }
        while let index = stack.popLast() { ends[index] = nodes.count }
        return ends
    }

    private static func remapNode(
        at index: Int,
        nodes: [AoVUnityTypeTreeNode],
        subtreeEnds: [Int],
        value: AoVUnityTypeTreeValue,
        path: String,
        localPathIDMap: [Int64: Int64],
        availableLocalPathIDs: Set<Int64>,
        externalFileIDMap: [Int32: Int32],
        externalPathIDMaps: [Int32: [Int64: Int64]],
        availableExternalTargetPathIDs: [Int32: Set<Int64>],
        localCount: inout Int,
        externalCount: inout Int,
        auditedExternalCount: inout Int
    ) throws -> AoVUnityTypeTreeValue {
        guard nodes.indices.contains(index) else {
            throw AoVUnityTypeTreePointerRemapError.invalidTree(path)
        }
        let node = nodes[index]
        let firstChild = index + 1
        let end = subtreeEnds[index]
        let childCount = end - firstChild

        if node.type.hasPrefix("PPtr<") {
            guard case .object(var fields) = value,
                  let fileIDValue = fields["m_FileID"],
                  let pathIDValue = fields["m_PathID"],
                  let rawFileID64 = fileIDValue.intValue,
                  let rawFileID = Int32(exactly: rawFileID64),
                  let rawPathID = pathIDValue.intValue else {
                throw AoVUnityTypeTreePointerRemapError.invalidPointer(path)
            }

            if rawFileID == 0 {
                if rawPathID == 0 { return value }
                let destinationPathID = localPathIDMap[rawPathID] ?? rawPathID
                guard availableLocalPathIDs.contains(destinationPathID) else {
                    throw AoVUnityTypeTreePointerRemapError.unresolvedLocalPointer(path, destinationPathID)
                }
                if destinationPathID != rawPathID {
                    fields["m_PathID"] = try replacingInteger(pathIDValue, with: destinationPathID, field: path)
                    localCount += 1
                }
            } else {
                guard rawFileID > 0,
                      let destinationFileID = externalFileIDMap[rawFileID],
                      destinationFileID > 0 else {
                    throw AoVUnityTypeTreePointerRemapError.unresolvedExternalFileID(path, rawFileID)
                }
                guard rawPathID != 0 else {
                    throw AoVUnityTypeTreePointerRemapError.invalidPointer(path)
                }
                let destinationPathID = externalPathIDMaps[rawFileID]?[rawPathID] ?? rawPathID
                guard availableExternalTargetPathIDs[destinationFileID]?.contains(destinationPathID) == true else {
                    throw AoVUnityTypeTreePointerRemapError.unresolvedExternalTarget(
                        path,
                        destinationFileID,
                        destinationPathID
                    )
                }
                auditedExternalCount += 1
                var pointerChanged = false
                if destinationFileID != rawFileID {
                    fields["m_FileID"] = try replacingInteger(fileIDValue, with: Int64(destinationFileID), field: path)
                    pointerChanged = true
                }
                if destinationPathID != rawPathID {
                    fields["m_PathID"] = try replacingInteger(pathIDValue, with: destinationPathID, field: path)
                    pointerChanged = true
                }
                if pointerChanged { externalCount += 1 }
            }
            return .object(fields)
        }

        if node.type == "map" {
            guard childCount >= 5, firstChild < end,
                  nodes[firstChild].type == "Array",
                  case .map(let entries) = value else {
                throw AoVUnityTypeTreePointerRemapError.invalidTree(path)
            }
            let keyIndex = index + 4
            guard keyIndex < end else {
                throw AoVUnityTypeTreePointerRemapError.invalidTree(path + ".key")
            }
            let valueIndex = subtreeEnds[keyIndex]
            guard valueIndex < end else {
                throw AoVUnityTypeTreePointerRemapError.invalidTree(path + ".value")
            }
            var remapped: [AoVUnityTypeTreeMapEntry] = []
            remapped.reserveCapacity(entries.count)
            for (entryIndex, entry) in entries.enumerated() {
                let key = try remapNode(
                    at: keyIndex, nodes: nodes, subtreeEnds: subtreeEnds,
                    value: entry.key, path: "\(path).key[\(entryIndex)]",
                    localPathIDMap: localPathIDMap,
                    availableLocalPathIDs: availableLocalPathIDs,
                    externalFileIDMap: externalFileIDMap,
                    externalPathIDMaps: externalPathIDMaps,
                    availableExternalTargetPathIDs: availableExternalTargetPathIDs,
                    localCount: &localCount,
                    externalCount: &externalCount,
                    auditedExternalCount: &auditedExternalCount
                )
                let mappedValue = try remapNode(
                    at: valueIndex, nodes: nodes, subtreeEnds: subtreeEnds,
                    value: entry.value, path: "\(path).value[\(entryIndex)]",
                    localPathIDMap: localPathIDMap,
                    availableLocalPathIDs: availableLocalPathIDs,
                    externalFileIDMap: externalFileIDMap,
                    externalPathIDMaps: externalPathIDMaps,
                    availableExternalTargetPathIDs: availableExternalTargetPathIDs,
                    localCount: &localCount,
                    externalCount: &externalCount,
                    auditedExternalCount: &auditedExternalCount
                )
                remapped.append(AoVUnityTypeTreeMapEntry(key: key, value: mappedValue))
            }
            return .map(remapped)
        }

        if childCount > 0 && nodes[firstChild].type == "Array" {
            guard case .array(let values) = value else {
                throw AoVUnityTypeTreePointerRemapError.invalidTree(path + ".array")
            }
            let dataIndex = index + 3
            guard dataIndex < end else {
                throw AoVUnityTypeTreePointerRemapError.invalidTree(path + ".data")
            }
            var remapped: [AoVUnityTypeTreeValue] = []
            remapped.reserveCapacity(values.count)
            for (valueIndex, item) in values.enumerated() {
                remapped.append(
                    try remapNode(
                        at: dataIndex, nodes: nodes, subtreeEnds: subtreeEnds,
                        value: item, path: "\(path)[\(valueIndex)]",
                        localPathIDMap: localPathIDMap,
                        availableLocalPathIDs: availableLocalPathIDs,
                        externalFileIDMap: externalFileIDMap,
                        externalPathIDMaps: externalPathIDMaps,
                        availableExternalTargetPathIDs: availableExternalTargetPathIDs,
                        localCount: &localCount,
                        externalCount: &externalCount,
                        auditedExternalCount: &auditedExternalCount
                    )
                )
            }
            return .array(remapped)
        }

        if childCount > 0 {
            guard case .object(var fields) = value else {
                throw AoVUnityTypeTreePointerRemapError.invalidTree(path + ".object")
            }
            var childIndex = firstChild
            while childIndex < end {
                let child = nodes[childIndex]
                guard let childValue = fields[child.name] else {
                    throw AoVUnityTypeTreePointerRemapError.invalidTree(path + "." + child.name)
                }
                fields[child.name] = try remapNode(
                    at: childIndex, nodes: nodes, subtreeEnds: subtreeEnds,
                    value: childValue, path: path + "." + child.name,
                    localPathIDMap: localPathIDMap,
                    availableLocalPathIDs: availableLocalPathIDs,
                    externalFileIDMap: externalFileIDMap,
                    externalPathIDMaps: externalPathIDMaps,
                    availableExternalTargetPathIDs: availableExternalTargetPathIDs,
                    localCount: &localCount,
                    externalCount: &externalCount,
                    auditedExternalCount: &auditedExternalCount
                )
                childIndex = subtreeEnds[childIndex]
            }
            return .object(fields)
        }

        return value
    }

    private static func replacingInteger(
        _ original: AoVUnityTypeTreeValue,
        with replacement: Int64,
        field: String
    ) throws -> AoVUnityTypeTreeValue {
        switch original {
        case .signed:
            return .signed(replacement)
        case .unsigned:
            guard let exact = UInt64(exactly: replacement) else {
                throw AoVUnityTypeTreePointerRemapError.invalidPointer(field)
            }
            return .unsigned(exact)
        default:
            throw AoVUnityTypeTreePointerRemapError.invalidPointer(field)
        }
    }
}
