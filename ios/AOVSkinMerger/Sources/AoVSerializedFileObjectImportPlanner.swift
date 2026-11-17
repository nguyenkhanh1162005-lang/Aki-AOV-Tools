import Foundation

enum AoVSerializedFileObjectImportPlanError: LocalizedError {
    case invalidSerializedFile(String)
    case unsupportedFormat(UInt32)
    case targetPlatformMismatch(Int32, Int32)
    case noObjects
    case tooManyObjects(Int)
    case duplicatePathID(String, Int64)
    case selectedObjectMissing(Int64)
    case missingTypeTree(Int64)
    case incompatibleType(Int64, Int32)
    case pathIDOverflow
    case resourceLimit(String)
    case externalReferenceMismatch(Int32, Int32)
    case pointerAuditFailed(Int64, String)
    case invalidPreservedPathIDMapping(Int64, Int64)

    var errorDescription: String? {
        switch self {
        case .invalidSerializedFile(let side):
            return "\(side) SerializedFile có kích thước không khớp header."
        case .unsupportedFormat(let version):
            return "SerializedFile format \(version) chưa được object importer hỗ trợ."
        case .targetPlatformMismatch(let source, let destination):
            return "TargetPlatform nguồn \(source) khác RES đích \(destination)."
        case .noObjects:
            return "Không có object nào được chọn để nhập."
        case .tooManyObjects(let count):
            return "Batch nhập \(count) object vượt giới hạn an toàn."
        case .duplicatePathID(let side, let pathID):
            return "\(side) SerializedFile có pathID trùng: \(pathID)."
        case .selectedObjectMissing(let pathID):
            return "Không tìm thấy source object pathID \(pathID) trong batch nhập."
        case .missingTypeTree(let pathID):
            return "Object pathID \(pathID) không có TypeTree đầy đủ để tái dựng an toàn."
        case .incompatibleType(let pathID, let classID):
            return "Không có SerializedType tương thích chính xác cho object \(pathID), classID \(classID) trong RES đích."
        case .pathIDOverflow:
            return "Không thể cấp pathID mới vì dải Int64 của SerializedFile đích đã hết."
        case .resourceLimit(let reason):
            return "Object import vượt giới hạn tài nguyên: \(reason)"
        case .externalReferenceMismatch(let sourceFileID, let destinationFileID):
            return "External fileID \(sourceFileID) không ánh xạ đúng GUID/path sang fileID đích \(destinationFileID)."
        case .pointerAuditFailed(let pathID, let reason):
            return "Không thể audit PPtr của object \(pathID): \(reason)"
        case .invalidPreservedPathIDMapping(let sourcePathID, let destinationPathID):
            return "Không thể giữ PPtr source pathID \(sourcePathID) thành destination pathID \(destinationPathID)."
        }
    }
}

struct AoVSerializedObjectImportPayload: Sendable {
    let sourcePathID: Int64
    let destinationPathID: Int64
    let classID: Int32
    let sourceTypeID: Int32
    let destinationTypeID: Int32
    let data: Data
    let remappedLocalPointers: Int
    let remappedExternalPointers: Int
    let auditedExternalPointers: Int
}

struct AoVSerializedFileObjectImportPlan: Sendable {
    let sourceUnityVersion: String
    let destinationUnityVersion: String
    let sourceFormatVersion: UInt32
    let destinationFormatVersion: UInt32
    let targetPlatform: Int32
    let pathIDMap: [Int64: Int64]
    let objects: [AoVSerializedObjectImportPayload]
    let totalPayloadBytes: UInt64
    let roundTripVerified: Bool
}

private struct AoVSerializedTypeSchemaKey: Hashable {
    let classID: Int32
    let scriptID: Data?
    let oldTypeHash: Data
    let typeTreeNodes: [AoVUnityTypeTreeNode]
    let dependencies: [Int32]

    init(_ type: AoVSerializedFileTypeInfo) {
        classID = type.classID
        scriptID = type.scriptID
        oldTypeHash = type.oldTypeHash
        typeTreeNodes = type.typeTreeNodes
        dependencies = type.dependencies
    }
}

/// Plans a conservative, TypeTree-driven import of complete object payloads.
/// It does not write the destination object table yet. All selected source
/// objects must have an exact destination SerializedType match and every
/// PPtr must resolve after remapping. External fileID mappings are accepted
/// only when the source and destination GUID/path identity matches exactly.
enum AoVSerializedFileObjectImportPlanner {
    private static let maximumObjects = 100_000
    private static let maximumSingleObjectBytes: UInt64 = 16 * 1024 * 1024
    private static let maximumBatchBytes: UInt64 = 512 * 1024 * 1024

    /// Expands seed objects to their transitive local-PPtr dependency
    /// closure before planning the import. External PPtrs are deliberately
    /// not followed here; the main planner must still validate their exact
    /// GUID/path and target pathID mappings.
    static func planDependencyClosure(
        source: Data,
        destination: Data,
        seedPathIDs: Set<Int64>,
        externalFileIDMap: [Int32: Int32] = [:],
        externalPathIDMaps: [Int32: [Int64: Int64]] = [:],
        availableExternalTargetPathIDs: [Int32: Set<Int64>] = [:]
    ) throws -> AoVSerializedFileObjectImportPlan {
        guard !seedPathIDs.isEmpty else {
            throw AoVSerializedFileObjectImportPlanError.noObjects
        }
        let sourceSummary = try AoVSerializedFileParser.inspect(source)
        guard sourceSummary.fileSize == UInt64(source.count) else {
            throw AoVSerializedFileObjectImportPlanError.invalidSerializedFile("nguồn")
        }
        guard sourceSummary.serializedObjects.count <= maximumObjects else {
            throw AoVSerializedFileObjectImportPlanError.tooManyObjects(
                sourceSummary.serializedObjects.count
            )
        }
        let sourceIDs = sourceSummary.serializedObjects.map { $0.pathID }
        guard Set(sourceIDs).count == sourceIDs.count else {
            let duplicate = firstDuplicate(sourceIDs) ?? 0
            throw AoVSerializedFileObjectImportPlanError.duplicatePathID("nguồn", duplicate)
        }
        let sourceObjectsByID = Dictionary(uniqueKeysWithValues: sourceSummary.serializedObjects.map {
            ($0.pathID, $0)
        })
        var pending = Array(seedPathIDs)
        var closure = Set<Int64>()
        while let pathID = pending.popLast() {
            guard let object = sourceObjectsByID[pathID] else {
                throw AoVSerializedFileObjectImportPlanError.selectedObjectMissing(pathID)
            }
            guard closure.insert(pathID).inserted else { continue }
            guard closure.count <= maximumObjects else {
                throw AoVSerializedFileObjectImportPlanError.tooManyObjects(closure.count)
            }
            guard let typeInfo = sourceSummary.typeInfo(for: object),
                  !typeInfo.typeTreeNodes.isEmpty else {
                throw AoVSerializedFileObjectImportPlanError.missingTypeTree(pathID)
            }
            let value = try AoVSerializedFileParser.decodeObjectWithTypeTree(
                pathID: pathID,
                in: source,
                summary: sourceSummary
            )
            let localTargets = try AoVUnityTypeTreePointerRemapper.collectLocalTargets(
                nodes: typeInfo.typeTreeNodes,
                value: value
            )
            for targetPathID in localTargets where !closure.contains(targetPathID) {
                guard sourceObjectsByID[targetPathID] != nil else {
                    throw AoVSerializedFileObjectImportPlanError.pointerAuditFailed(
                        pathID,
                        "local PPtr pathID \\(targetPathID) không tồn tại trong source SerializedFile."
                    )
                }
                pending.append(targetPathID)
            }
        }
        return try plan(
            source: source,
            destination: destination,
            selectedPathIDs: closure,
            externalFileIDMap: externalFileIDMap,
            externalPathIDMaps: externalPathIDMaps,
            availableExternalTargetPathIDs: availableExternalTargetPathIDs
        )
    }

    static func plan(
        source: Data,
        destination: Data,
        selectedPathIDs: Set<Int64>? = nil,
        preservedLocalPathIDMap: [Int64: Int64] = [:],
        externalFileIDMap: [Int32: Int32] = [:],
        externalPathIDMaps: [Int32: [Int64: Int64]] = [:],
        availableExternalTargetPathIDs: [Int32: Set<Int64>] = [:]
    ) throws -> AoVSerializedFileObjectImportPlan {
        let sourceSummary = try AoVSerializedFileParser.inspect(source)
        let destinationSummary = try AoVSerializedFileParser.inspect(destination)
        guard sourceSummary.fileSize == UInt64(source.count) else {
            throw AoVSerializedFileObjectImportPlanError.invalidSerializedFile("nguồn")
        }
        guard destinationSummary.fileSize == UInt64(destination.count) else {
            throw AoVSerializedFileObjectImportPlanError.invalidSerializedFile("đích")
        }
        guard (9...30).contains(sourceSummary.formatVersion) else {
            throw AoVSerializedFileObjectImportPlanError.unsupportedFormat(sourceSummary.formatVersion)
        }
        guard (9...30).contains(destinationSummary.formatVersion) else {
            throw AoVSerializedFileObjectImportPlanError.unsupportedFormat(destinationSummary.formatVersion)
        }
        guard sourceSummary.targetPlatform == destinationSummary.targetPlatform else {
            throw AoVSerializedFileObjectImportPlanError.targetPlatformMismatch(
                sourceSummary.targetPlatform,
                destinationSummary.targetPlatform
            )
        }
        guard !sourceSummary.serializedObjects.isEmpty else {
            throw AoVSerializedFileObjectImportPlanError.noObjects
        }
        guard sourceSummary.serializedObjects.count <= maximumObjects,
              destinationSummary.serializedObjects.count <= maximumObjects else {
            throw AoVSerializedFileObjectImportPlanError.tooManyObjects(
                max(sourceSummary.serializedObjects.count, destinationSummary.serializedObjects.count)
            )
        }

        let sourceIDs = sourceSummary.serializedObjects.map { $0.pathID }
        let destinationIDs = destinationSummary.serializedObjects.map(\.pathID)
        guard Set(sourceIDs).count == sourceIDs.count else {
            let duplicate = firstDuplicate(sourceIDs) ?? 0
            throw AoVSerializedFileObjectImportPlanError.duplicatePathID("nguồn", duplicate)
        }
        guard Set(destinationIDs).count == destinationIDs.count else {
            let duplicate = firstDuplicate(destinationIDs) ?? 0
            throw AoVSerializedFileObjectImportPlanError.duplicatePathID("đích", duplicate)
        }

        let selected = selectedPathIDs ?? Set(sourceIDs)
        guard !selected.isEmpty else { throw AoVSerializedFileObjectImportPlanError.noObjects }
        let sourceObjectsByID = Dictionary(uniqueKeysWithValues: sourceSummary.serializedObjects.map {
            ($0.pathID, $0)
        })
        for pathID in selected where sourceObjectsByID[pathID] == nil {
            throw AoVSerializedFileObjectImportPlanError.selectedObjectMissing(pathID)
        }
        guard selected.count <= maximumObjects else {
            throw AoVSerializedFileObjectImportPlanError.tooManyObjects(selected.count)
        }

        let destinationIDSet = Set(destinationIDs)
        for (sourcePathID, destinationPathID) in preservedLocalPathIDMap {
            guard sourceObjectsByID[sourcePathID] != nil,
                  destinationIDSet.contains(destinationPathID),
                  !selected.contains(sourcePathID) else {
                throw AoVSerializedFileObjectImportPlanError.invalidPreservedPathIDMapping(
                    sourcePathID,
                    destinationPathID
                )
            }
        }
        guard Set(preservedLocalPathIDMap.values).count == preservedLocalPathIDMap.count else {
            let first = preservedLocalPathIDMap.first ?? (0, 0)
            throw AoVSerializedFileObjectImportPlanError.invalidPreservedPathIDMapping(
                first.key,
                first.value
            )
        }
        let maximumKnownPathID = max(0, max(sourceIDs.max() ?? 0, destinationIDs.max() ?? 0))
        guard maximumKnownPathID < Int64.max else {
            throw AoVSerializedFileObjectImportPlanError.pathIDOverflow
        }
        var nextPathID = maximumKnownPathID + 1
        var pathIDMap: [Int64: Int64] = [:]
        var allocatedPathIDs = Set<Int64>()
        let selectedIDsInOrder = sourceIDs.filter { selected.contains($0) }
        for (index, pathID) in selectedIDsInOrder.enumerated() {
            while destinationIDSet.contains(nextPathID) || !allocatedPathIDs.insert(nextPathID).inserted {
                guard nextPathID < Int64.max else {
                    throw AoVSerializedFileObjectImportPlanError.pathIDOverflow
                }
                nextPathID += 1
            }
            pathIDMap[pathID] = nextPathID
            if nextPathID < Int64.max {
                nextPathID += 1
            } else if index != selectedIDsInOrder.count - 1 {
                throw AoVSerializedFileObjectImportPlanError.pathIDOverflow
            }
        }

        var unavailableSentinel = Int64.min
        while destinationIDSet.contains(unavailableSentinel) ||
            allocatedPathIDs.contains(unavailableSentinel) {
            guard unavailableSentinel < Int64.max else {
                throw AoVSerializedFileObjectImportPlanError.pathIDOverflow
            }
            unavailableSentinel += 1
        }
        var completeLocalPathIDMap: [Int64: Int64] = [:]
        for sourcePathID in sourceIDs {
            completeLocalPathIDMap[sourcePathID] =
                pathIDMap[sourcePathID] ??
                preservedLocalPathIDMap[sourcePathID] ??
                unavailableSentinel
        }
        // Imported objects receive fresh IDs; explicitly preserved source roots
        // (for example the AssetBundle class-142 object) map to an existing
        // destination object. No other source-local PPtr may bind by coincidence.
        let availableLocalPathIDs = allocatedPathIDs.union(
            Set(preservedLocalPathIDMap.values)
        )
        var completeReportedPathIDMap = pathIDMap
        for (sourcePathID, destinationPathID) in preservedLocalPathIDMap {
            completeReportedPathIDMap[sourcePathID] = destinationPathID
        }

        try validateExternalMappings(
            source: sourceSummary,
            destination: destinationSummary,
            fileIDMap: externalFileIDMap,
            availableTargetPathIDs: availableExternalTargetPathIDs
        )

        let destinationTypesBySchema = Dictionary(
            grouping: destinationSummary.serializedTypes,
            by: AoVSerializedTypeSchemaKey.init
        )
        var imports: [AoVSerializedObjectImportPayload] = []
        imports.reserveCapacity(selected.count)
        var totalBytes: UInt64 = 0

        for sourceObject in sourceSummary.serializedObjects where selected.contains(sourceObject.pathID) {
            guard let sourceType = sourceSummary.typeInfo(for: sourceObject),
                  !sourceType.typeTreeNodes.isEmpty else {
                throw AoVSerializedFileObjectImportPlanError.missingTypeTree(sourceObject.pathID)
            }
            let compatibleTypes = destinationTypesBySchema[AoVSerializedTypeSchemaKey(sourceType)] ?? []
            guard compatibleTypes.count == 1, let destinationType = compatibleTypes.first else {
                throw AoVSerializedFileObjectImportPlanError.incompatibleType(
                    sourceObject.pathID,
                    sourceObject.classID
                )
            }

            let decoded = try AoVSerializedFileParser.decodeObjectWithTypeTree(
                pathID: sourceObject.pathID,
                in: source,
                summary: sourceSummary
            )
            let remapped: AoVUnityTypeTreePointerRemapReport
            do {
                remapped = try AoVUnityTypeTreePointerRemapper.remap(
                    nodes: sourceType.typeTreeNodes,
                    value: decoded,
                    localPathIDMap: completeLocalPathIDMap,
                    availableLocalPathIDs: availableLocalPathIDs,
                    sourceLocalPathIDs: Set(sourceIDs),
                    externalFileIDMap: externalFileIDMap,
                    externalPathIDMaps: externalPathIDMaps,
                    availableExternalTargetPathIDs: availableExternalTargetPathIDs
                )
            } catch {
                throw AoVSerializedFileObjectImportPlanError.pointerAuditFailed(
                    sourceObject.pathID,
                    error.localizedDescription
                )
            }

            let payload = try AoVUnityTypeTreeWriter.write(
                nodes: destinationType.typeTreeNodes,
                value: remapped.value,
                littleEndian: destinationSummary.littleEndian
            )
            let payloadSize = UInt64(payload.count)
            guard payloadSize > 0, payloadSize <= maximumSingleObjectBytes else {
                throw AoVSerializedFileObjectImportPlanError.resourceLimit(
                    "object \(sourceObject.pathID) có \(payloadSize) byte."
                )
            }
            let nextTotal = totalBytes.addingReportingOverflow(payloadSize)
            guard !nextTotal.overflow, nextTotal.partialValue <= maximumBatchBytes else {
                throw AoVSerializedFileObjectImportPlanError.resourceLimit(
                    "batch vượt 512 MiB."
                )
            }
            totalBytes = nextTotal.partialValue

            let verifiedValue = try AoVUnityTypeTreeReader.read(
                nodes: destinationType.typeTreeNodes,
                data: payload,
                littleEndian: destinationSummary.littleEndian
            )
            guard verifiedValue == remapped.value else {
                throw AoVSerializedFileObjectImportPlanError.pointerAuditFailed(
                    sourceObject.pathID,
                    "TypeTree payload không round-trip."
                )
            }

            guard let destinationPathID = pathIDMap[sourceObject.pathID] else {
                throw AoVSerializedFileObjectImportPlanError.selectedObjectMissing(sourceObject.pathID)
            }
            imports.append(
                AoVSerializedObjectImportPayload(
                    sourcePathID: sourceObject.pathID,
                    destinationPathID: destinationPathID,
                    classID: sourceObject.classID,
                    sourceTypeID: sourceObject.typeID,
                    destinationTypeID: Int32(destinationType.typeID),
                    data: payload,
                    remappedLocalPointers: remapped.remappedLocalPointers,
                    remappedExternalPointers: remapped.remappedExternalPointers,
                    auditedExternalPointers: remapped.auditedExternalPointers
                )
            )
        }

        return AoVSerializedFileObjectImportPlan(
            sourceUnityVersion: sourceSummary.unityVersion,
            destinationUnityVersion: destinationSummary.unityVersion,
            sourceFormatVersion: sourceSummary.formatVersion,
            destinationFormatVersion: destinationSummary.formatVersion,
            targetPlatform: destinationSummary.targetPlatform,
            pathIDMap: completeReportedPathIDMap,
            objects: imports,
            totalPayloadBytes: totalBytes,
            roundTripVerified: true
        )
    }

    private static func validateExternalMappings(
        source: AoVSerializedFileSummary,
        destination: AoVSerializedFileSummary,
        fileIDMap: [Int32: Int32],
        availableTargetPathIDs: [Int32: Set<Int64>]
    ) throws {
        for (sourceFileID, destinationFileID) in fileIDMap {
            guard sourceFileID > 0, destinationFileID > 0,
                  let sourceIndex = Int(exactly: sourceFileID - 1),
                  let destinationIndex = Int(exactly: destinationFileID - 1),
                  source.externalReferences.indices.contains(sourceIndex),
                  destination.externalReferences.indices.contains(destinationIndex) else {
                throw AoVSerializedFileObjectImportPlanError.externalReferenceMismatch(
                    sourceFileID,
                    destinationFileID
                )
            }
            let sourceReference = source.externalReferences[sourceIndex]
            let destinationReference = destination.externalReferences[destinationIndex]
            let sourcePath = sourceReference.path.replacingOccurrences(of: "\\", with: "/")
            let destinationPath = destinationReference.path.replacingOccurrences(of: "\\", with: "/")
            guard sourceReference.guidHex.lowercased() == destinationReference.guidHex.lowercased(),
                  sourcePath == destinationPath,
                  availableTargetPathIDs[destinationFileID] != nil else {
                throw AoVSerializedFileObjectImportPlanError.externalReferenceMismatch(
                    sourceFileID,
                    destinationFileID
                )
            }
        }
        for sourceFileID in fileIDMap.keys {
            guard sourceFileID <= Int32(source.externalReferences.count) else {
                throw AoVSerializedFileObjectImportPlanError.externalReferenceMismatch(
                    sourceFileID,
                    fileIDMap[sourceFileID] ?? 0
                )
            }
        }
    }

    private static func firstDuplicate(_ values: [Int64]) -> Int64? {
        var seen = Set<Int64>()
        for value in values where !seen.insert(value).inserted {
            return value
        }
        return nil
    }
}
