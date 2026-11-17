import Foundation

enum AoVUnityFSAssetBundleMergeExecutorError: LocalizedError {
    case expandedArchiveTooLarge
    case nodeNotFound(String)
    case ambiguousNode(String)
    case objectNotFound(Int64)
    case ambiguousObject(Int64)
    case externalTargetAuditRequired([String])
    case verificationFailed(String)

    var errorDescription: String? {
        switch self {
        case .expandedArchiveTooLarge:
            return "UnityFS AssetBundle ghép vượt giới hạn giải nén 512 MiB."
        case .nodeNotFound(let path):
            return "Không tìm thấy SerializedFile node \(path) trong UnityFS."
        case .ambiguousNode(let path):
            return "UnityFS có nhiều node trùng đường dẫn \(path)."
        case .objectNotFound(let pathID):
            return "Không tìm thấy AssetBundle object pathID \(pathID) trong SerializedFile."
        case .ambiguousObject(let pathID):
            return "SerializedFile có nhiều AssetBundle object pathID \(pathID)."
        case .externalTargetAuditRequired(let samples):
            return "PPtr external đã đổi fileID nhưng chưa audit pathID đích: \(samples.prefix(4).joined(separator: ", "))."
        case .verificationFailed(let reason):
            return "UnityFS AssetBundle merge không vượt qua round-trip: \(reason)"
        }
    }
}

struct AoVUnityFSAssetBundleMergeExecutionReport: Sendable {
    let data: Data
    let nodePath: String
    let objectPathID: Int64
    let plan: AoVAssetBundleCatalogMergePlan
    let rewrite: AoVUnityFSContentRewriteReport
    let roundTripVerified: Bool
}

struct AoVAssetBundleLegacyRecordInput: Sendable {
    let source: String
    let record: AoVSerializedAssetBundleRecord
    let serializedFile: AoVSerializedFileSummary
}

struct AoVAssetBundleLegacyObjectImportInput: Sendable {
    let source: String
    let serializedFile: Data
    let legacyObjectPathID: Int64
    let destinationObjectPathID: Int64
}

struct AoVUnityFSAssetBundleObjectImportExecutionReport: Sendable {
    let data: Data
    let nodePath: String
    let importedPathIDs: [Int64]
    let importedObjectCount: Int
    let plans: [AoVAssetBundleCatalogMergePlan]
    let rewrite: AoVUnityFSContentRewriteReport
    let roundTripVerified: Bool
}

struct AoVUnityFSAssetBundleBatchMergeExecutionReport: Sendable {
    let data: Data
    let nodePath: String
    let rewrittenObjectPathIDs: [Int64]
    let plans: [AoVAssetBundleCatalogMergePlan]
    let rewrite: AoVUnityFSContentRewriteReport
    let roundTripVerified: Bool
}

/// Executes the class-142 merge plan against the actual SerializedFile node in
/// a UnityFS archive. It is deliberately a single-bundle primitive: callers
/// must still coordinate runtime Packer/Verification metadata and ZIP output.
enum AoVUnityFSAssetBundleMergeExecutor {
    private static let maximumExpandedBytes: UInt64 = 512 * 1024 * 1024

    /// Applies multiple legacy class-142 records to one SerializedFile and
    /// rebuilds its enclosing UnityFS bundle only once. Any failed plan or
    /// unresolved pointer aborts the entire in-memory operation.
    static func mergeBatch(
        in source: Data,
        serializedFileNodePath: String,
        legacyRecords: [AoVAssetBundleLegacyRecordInput],
        availableCurrentBundleNames: Set<String>,
        verifiedExternalTargetFiles: [String: Data] = [:]
    ) throws -> AoVUnityFSAssetBundleBatchMergeExecutionReport {
        guard !legacyRecords.isEmpty else {
            throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                "Không có legacy AssetBundle record để ghép."
            )
        }

        let info = try AoVUnityFSParser.inspect(source)
        try AoVUnityFSContentRewriter.validateWritableTemplate(info)
        guard info.totalUncompressedBytes <= maximumExpandedBytes else {
            throw AoVUnityFSAssetBundleMergeExecutorError.expandedArchiveTooLarge
        }
        let nodes = try AoVUnityFSParser.extractNodes(
            source,
            maximumExpandedBytes: maximumExpandedBytes,
            maximumNodeCount: max(info.nodes.count, 1)
        )
        let nodeMatches = nodes.filter { $0.path == serializedFileNodePath }
        guard nodeMatches.count == 1, let originalNode = nodeMatches.first else {
            if nodeMatches.count > 1 {
                throw AoVUnityFSAssetBundleMergeExecutorError.ambiguousNode(serializedFileNodePath)
            }
            throw AoVUnityFSAssetBundleMergeExecutorError.nodeNotFound(serializedFileNodePath)
        }

        let auditedExternalTargets = try AoVAssetBundleCatalogMerger.auditExternalTargetPathIDs(
            serializedFilesByExternalTargetKey: verifiedExternalTargetFiles
        )
        var serializedOutput = originalNode.data
        var plans: [AoVAssetBundleCatalogMergePlan] = []
        var rewrittenPathIDs = Set<Int64>()

        for input in legacyRecords {
            let currentSummary = try AoVSerializedFileParser.inspect(serializedOutput)
            let objectMatches = currentSummary.serializedObjects.filter {
                $0.pathID == input.record.pathID
            }
            guard objectMatches.count == 1, let object = objectMatches.first else {
                if objectMatches.count > 1 {
                    throw AoVUnityFSAssetBundleMergeExecutorError.ambiguousObject(input.record.pathID)
                }
                throw AoVUnityFSAssetBundleMergeExecutorError.objectNotFound(input.record.pathID)
            }
            guard object.classID == 142,
                  let currentRecord = currentSummary.assetBundleRecords.first(where: {
                      $0.pathID == input.record.pathID
                  }) else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "Object pathID \(input.record.pathID) không phải AssetBundle class 142 có record đọc được."
                )
            }

            let plan = try AoVAssetBundleCatalogMerger.plan(
                current: currentRecord,
                legacy: input.record,
                currentSerializedFile: currentSummary,
                legacySerializedFile: input.serializedFile,
                availableCurrentBundleNames: availableCurrentBundleNames,
                verifiedExternalTargetPathIDs: auditedExternalTargets
            )
            guard !plan.externalTargetAuditRequired else {
                throw AoVUnityFSAssetBundleMergeExecutorError.externalTargetAuditRequired(
                    plan.externalTargetAuditSamples
                )
            }
            plans.append(plan)
            guard plan.mergedRecord != currentRecord else { continue }

            let rewritten = try AoVSerializedFileRewriter.replaceAssetBundleRecord(
                in: serializedOutput,
                pathID: input.record.pathID,
                with: plan.mergedRecord
            )
            guard rewritten.roundTripVerified else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "SerializedFile pathID \(input.record.pathID) không qua round-trip."
                )
            }
            serializedOutput = rewritten.data
            rewrittenPathIDs.insert(input.record.pathID)
        }

        guard !rewrittenPathIDs.isEmpty else {
            throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                "Không có thay đổi catalog nào cần ghi."
            )
        }

        let rewrittenSerializedSummary = try AoVSerializedFileParser.inspect(serializedOutput)
        let finalPlansByPathID = Dictionary(
            plans.map { ($0.mergedRecord.pathID, $0) },
            uniquingKeysWith: { _, latest in latest }
        )
        for plan in finalPlansByPathID.values {
            let finalRecord = rewrittenSerializedSummary.assetBundleRecords.first(where: {
                $0.pathID == plan.mergedRecord.pathID
            })
            guard finalRecord == plan.mergedRecord else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "AssetBundle pathID \(plan.mergedRecord.pathID) không khớp batch merge plan cuối cùng."
                )
            }
        }

        let rewrite = try AoVUnityFSContentRewriter.replaceNode(
            in: source,
            nodePath: serializedFileNodePath,
            with: serializedOutput
        )
        let verifiedBundle = try AoVUnityFSParser.inspect(rewrite.data)
        let verifiedNodes = try AoVUnityFSParser.extractNodes(
            rewrite.data,
            maximumExpandedBytes: maximumExpandedBytes,
            maximumNodeCount: max(verifiedBundle.nodes.count, 1)
        )
        let verifiedMatches = verifiedNodes.filter { $0.path == serializedFileNodePath }
        guard verifiedMatches.count == 1,
              let verifiedNode = verifiedMatches.first,
              verifiedNode.data == serializedOutput,
              rewrite.roundTripVerified else {
            throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                "SerializedFile node không giữ nguyên dữ liệu sau khi UnityFS repack."
            )
        }

        return AoVUnityFSAssetBundleBatchMergeExecutionReport(
            data: rewrite.data,
            nodePath: serializedFileNodePath,
            rewrittenObjectPathIDs: rewrittenPathIDs.sorted(),
            plans: plans,
            rewrite: rewrite,
            roundTripVerified: true
        )
    }

    static func merge(
        in source: Data,
        serializedFileNodePath: String,
        objectPathID: Int64,
        legacyRecord: AoVSerializedAssetBundleRecord,
        legacySerializedFile: AoVSerializedFileSummary,
        availableCurrentBundleNames: Set<String>,
        verifiedExternalTargetFiles: [String: Data] = [:]
    ) throws -> AoVUnityFSAssetBundleMergeExecutionReport {
        let info = try AoVUnityFSParser.inspect(source)
        try AoVUnityFSContentRewriter.validateWritableTemplate(info)
        guard info.totalUncompressedBytes <= maximumExpandedBytes else {
            throw AoVUnityFSAssetBundleMergeExecutorError.expandedArchiveTooLarge
        }

        let nodes = try AoVUnityFSParser.extractNodes(
            source,
            maximumExpandedBytes: maximumExpandedBytes,
            maximumNodeCount: max(info.nodes.count, 1)
        )
        let nodeMatches = nodes.filter { $0.path == serializedFileNodePath }
        guard nodeMatches.count == 1, let node = nodeMatches.first else {
            if nodeMatches.count > 1 {
                throw AoVUnityFSAssetBundleMergeExecutorError.ambiguousNode(serializedFileNodePath)
            }
            throw AoVUnityFSAssetBundleMergeExecutorError.nodeNotFound(serializedFileNodePath)
        }

        let currentSerializedFile = try AoVSerializedFileParser.inspect(node.data)
        let recordMatches = currentSerializedFile.assetBundleRecords.filter {
            $0.pathID == objectPathID
        }
        guard recordMatches.count == 1, let currentRecord = recordMatches.first else {
            if recordMatches.count > 1 {
                throw AoVUnityFSAssetBundleMergeExecutorError.ambiguousObject(objectPathID)
            }
            throw AoVUnityFSAssetBundleMergeExecutorError.objectNotFound(objectPathID)
        }

        let auditedExternalTargets = try AoVAssetBundleCatalogMerger.auditExternalTargetPathIDs(
            serializedFilesByExternalTargetKey: verifiedExternalTargetFiles
        )
        let plan = try AoVAssetBundleCatalogMerger.plan(
            current: currentRecord,
            legacy: legacyRecord,
            currentSerializedFile: currentSerializedFile,
            legacySerializedFile: legacySerializedFile,
            availableCurrentBundleNames: availableCurrentBundleNames,
            verifiedExternalTargetPathIDs: auditedExternalTargets
        )
        guard !plan.externalTargetAuditRequired else {
            throw AoVUnityFSAssetBundleMergeExecutorError.externalTargetAuditRequired(
                plan.externalTargetAuditSamples
            )
        }
        let rewrite = try AoVUnityFSContentRewriter.replaceAssetBundleRecord(
            in: source,
            serializedFileNodePath: serializedFileNodePath,
            objectPathID: objectPathID,
            with: plan.mergedRecord
        )

        let verifiedBundle = try AoVUnityFSParser.inspect(rewrite.data)
        let verifiedNodes = try AoVUnityFSParser.extractNodes(
            rewrite.data,
            maximumExpandedBytes: maximumExpandedBytes,
            maximumNodeCount: max(verifiedBundle.nodes.count, 1)
        )
        let verifiedMatches = verifiedNodes.filter { $0.path == serializedFileNodePath }
        guard verifiedMatches.count == 1, let verifiedNode = verifiedMatches.first else {
            throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                "SerializedFile node đích không còn duy nhất sau khi đóng gói."
            )
        }
        let verifiedSerializedFile = try AoVSerializedFileParser.inspect(verifiedNode.data)
        let verifiedRecords = verifiedSerializedFile.assetBundleRecords.filter {
            $0.pathID == objectPathID
        }
        guard verifiedRecords.count == 1,
              verifiedRecords.first == plan.mergedRecord,
              rewrite.roundTripVerified else {
            throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                "AssetBundle class 142 không trùng bản ghi merge plan sau round-trip."
            )
        }

        return AoVUnityFSAssetBundleMergeExecutionReport(
            data: rewrite.data,
            nodePath: serializedFileNodePath,
            objectPathID: objectPathID,
            plan: plan,
            rewrite: rewrite,
            roundTripVerified: true
        )
    }
    /// Imports the object dependency closure for every missing legacy container
    /// entry, remaps local PPtrs to fresh IDs, updates the class-142 catalog, and
    /// repacks the destination UnityFS node once. Unsupported or unaudited links
    /// abort the entire in-memory operation.
    static func mergeImportingLegacyObjects(
        in destinationBundle: Data,
        serializedFileNodePath: String,
        legacyInputs: [AoVAssetBundleLegacyObjectImportInput],
        availableCurrentBundleNames: Set<String>,
        verifiedExternalTargetFiles: [String: Data] = [:]
    ) throws -> AoVUnityFSAssetBundleObjectImportExecutionReport {
        guard !legacyInputs.isEmpty else {
            throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                "Không có SerializedFile MOD cũ để nhập."
            )
        }
        let bundleInfo = try AoVUnityFSParser.inspect(destinationBundle)
        try AoVUnityFSContentRewriter.validateWritableTemplate(bundleInfo)
        guard bundleInfo.totalUncompressedBytes <= maximumExpandedBytes else {
            throw AoVUnityFSAssetBundleMergeExecutorError.expandedArchiveTooLarge
        }
        let destinationNodes = try AoVUnityFSParser.extractNodes(
            destinationBundle,
            maximumExpandedBytes: maximumExpandedBytes,
            maximumNodeCount: max(bundleInfo.nodes.count, 1)
        )
        let nodeMatches = destinationNodes.filter { $0.path == serializedFileNodePath }
        guard nodeMatches.count == 1, let destinationNode = nodeMatches.first else {
            if nodeMatches.count > 1 {
                throw AoVUnityFSAssetBundleMergeExecutorError.ambiguousNode(serializedFileNodePath)
            }
            throw AoVUnityFSAssetBundleMergeExecutorError.nodeNotFound(serializedFileNodePath)
        }

        let verifiedExternalTargetPathIDs = try AoVAssetBundleCatalogMerger.auditExternalTargetPathIDs(
            serializedFilesByExternalTargetKey: verifiedExternalTargetFiles
        )
        var serializedOutput = destinationNode.data
        var allImportedPathIDs = Set<Int64>()
        var plans: [AoVAssetBundleCatalogMergePlan] = []
        var changedCatalogPathIDs = Set<Int64>()

        for input in legacyInputs {
            let legacySummary = try AoVSerializedFileParser.inspect(input.serializedFile)
            guard legacySummary.fileSize == UInt64(input.serializedFile.count) else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "SerializedFile nguồn \(input.source) có fileSize không khớp."
                )
            }
            let legacyRecords = legacySummary.assetBundleRecords.filter {
                $0.pathID == input.legacyObjectPathID
            }
            guard legacyRecords.count == 1, let legacyRecord = legacyRecords.first else {
                if legacyRecords.count > 1 {
                    throw AoVUnityFSAssetBundleMergeExecutorError.ambiguousObject(input.legacyObjectPathID)
                }
                throw AoVUnityFSAssetBundleMergeExecutorError.objectNotFound(input.legacyObjectPathID)
            }

            let destinationSummary = try AoVSerializedFileParser.inspect(serializedOutput)
            let currentRecords = destinationSummary.assetBundleRecords.filter {
                $0.pathID == input.destinationObjectPathID
            }
            guard currentRecords.count == 1, let currentRecord = currentRecords.first else {
                if currentRecords.count > 1 {
                    throw AoVUnityFSAssetBundleMergeExecutorError.ambiguousObject(input.destinationObjectPathID)
                }
                throw AoVUnityFSAssetBundleMergeExecutorError.objectNotFound(input.destinationObjectPathID)
            }

            let selectedPathIDs = try requiredLocalObjectClosure(
                legacyRecord: legacyRecord,
                currentRecord: currentRecord,
                sourceData: input.serializedFile,
                sourceSummary: legacySummary
            )
            let externalMappings = auditedExternalMappings(
                source: legacySummary,
                destination: destinationSummary,
                auditedTargets: verifiedExternalTargetPathIDs
            )
            let preservedRootMap = [
                input.legacyObjectPathID: input.destinationObjectPathID
            ]

            var importedPathIDMap = preservedRootMap
            var importedObjectIDs = Set<Int64>()
            if !selectedPathIDs.isEmpty {
                let importPlan = try AoVSerializedFileObjectImportPlanner.plan(
                    source: input.serializedFile,
                    destination: serializedOutput,
                    selectedPathIDs: selectedPathIDs,
                    preservedLocalPathIDMap: preservedRootMap,
                    externalFileIDMap: externalMappings.fileIDMap,
                    availableExternalTargetPathIDs: externalMappings.availableTargetPathIDs
                )
                let appendInputs = importPlan.objects.map {
                    AoVSerializedObjectAppendInput(
                        pathID: $0.destinationPathID,
                        classID: $0.classID,
                        typeID: $0.destinationTypeID,
                        payload: $0.data
                    )
                }
                let appendReport = try AoVSerializedFileObjectAppender.append(
                    to: serializedOutput,
                    objects: appendInputs
                )
                guard appendReport.roundTripVerified else {
                    throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                        "Object appender không xác minh được SerializedFile sau khi nhập."
                    )
                }
                serializedOutput = appendReport.data
                importedPathIDMap = importPlan.pathIDMap
                importedObjectIDs = Set(importPlan.objects.map(\.destinationPathID))
                allImportedPathIDs.formUnion(importedObjectIDs)
            }

            let catalogPlan = try AoVAssetBundleCatalogMerger.plan(
                current: currentRecord,
                legacy: legacyRecord,
                currentSerializedFile: destinationSummary,
                legacySerializedFile: legacySummary,
                availableCurrentBundleNames: availableCurrentBundleNames,
                verifiedExternalTargetPathIDs: verifiedExternalTargetPathIDs,
                importedLocalPathIDMap: importedPathIDMap,
                availableImportedLocalPathIDs: importedObjectIDs
            )
            guard !catalogPlan.externalTargetAuditRequired else {
                throw AoVUnityFSAssetBundleMergeExecutorError.externalTargetAuditRequired(
                    catalogPlan.externalTargetAuditSamples
                )
            }
            guard catalogPlan.mergedRecord != currentRecord else {
                if !importedObjectIDs.isEmpty {
                    throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                        "Có object được nhập nhưng class-142 catalog không tham chiếu tới object mới."
                    )
                }
                continue
            }

            let rewritten = try AoVSerializedFileRewriter.replaceAssetBundleRecord(
                in: serializedOutput,
                pathID: input.destinationObjectPathID,
                with: catalogPlan.mergedRecord
            )
            guard rewritten.roundTripVerified else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "AssetBundle class-142 pathID \(input.destinationObjectPathID) không qua round-trip."
                )
            }
            serializedOutput = rewritten.data
            plans.append(catalogPlan)
            changedCatalogPathIDs.insert(input.destinationObjectPathID)
        }

        guard !changedCatalogPathIDs.isEmpty else {
            throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                "Không có container entry mới nào để nhập."
            )
        }

        let finalSerializedSummary = try AoVSerializedFileParser.inspect(serializedOutput)
        for plan in plans {
            guard finalSerializedSummary.assetBundleRecords.first(where: {
                $0.pathID == plan.mergedRecord.pathID
            }) == plan.mergedRecord else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "AssetBundle class-142 \(plan.mergedRecord.pathID) không khớp merge plan cuối."
                )
            }
        }

        let rewrite = try AoVUnityFSContentRewriter.replaceNode(
            in: destinationBundle,
            nodePath: serializedFileNodePath,
            with: serializedOutput
        )
        let verifiedBundle = try AoVUnityFSParser.inspect(rewrite.data)
        let verifiedNodes = try AoVUnityFSParser.extractNodes(
            rewrite.data,
            maximumExpandedBytes: maximumExpandedBytes,
            maximumNodeCount: max(verifiedBundle.nodes.count, 1)
        )
        let verifiedTargets = verifiedNodes.filter { $0.path == serializedFileNodePath }
        guard verifiedTargets.count == 1,
              verifiedTargets.first?.data == serializedOutput,
              rewrite.roundTripVerified else {
            throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                "UnityFS SerializedFile node không khớp dữ liệu sau repack."
            )
        }

        return AoVUnityFSAssetBundleObjectImportExecutionReport(
            data: rewrite.data,
            nodePath: serializedFileNodePath,
            importedPathIDs: allImportedPathIDs.sorted(),
            importedObjectCount: allImportedPathIDs.count,
            plans: plans,
            rewrite: rewrite,
            roundTripVerified: true
        )
    }

    static func requiredLocalObjectClosure(
        legacyRecord: AoVSerializedAssetBundleRecord,
        currentRecord: AoVSerializedAssetBundleRecord,
        sourceData: Data,
        sourceSummary: AoVSerializedFileSummary
    ) throws -> Set<Int64> {
        let currentKeys = Set(currentRecord.containerEntries.map(\.key))
        var pending: [Int64] = []
        for entry in legacyRecord.containerEntries where !currentKeys.contains(entry.key) {
            if entry.asset.fileID == 0, entry.asset.pathID != 0 {
                pending.append(entry.asset.pathID)
            }
            let start = Int(entry.preloadIndex)
            let count = Int(entry.preloadSize)
            guard start >= 0, count >= 0,
                  start <= legacyRecord.preloadReferences.count,
                  count <= legacyRecord.preloadReferences.count - start else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "Preload range không hợp lệ tại \(entry.key)."
                )
            }
            for pointer in legacyRecord.preloadReferences[start..<(start + count)] {
                if pointer.fileID == 0, pointer.pathID != 0 {
                    pending.append(pointer.pathID)
                }
            }
        }

        var selected = Set<Int64>()
        let sourceObjectsByID = Dictionary(
            grouping: sourceSummary.serializedObjects,
            by: \.pathID
        )
        let sourceBundleRootIDs = Set(sourceSummary.assetBundleRecords.map(\.pathID))
        while let pathID = pending.popLast() {
            if pathID == legacyRecord.pathID { continue }
            guard let matches = sourceObjectsByID[pathID], matches.count == 1,
                  let object = matches.first else {
                throw AoVUnityFSAssetBundleMergeExecutorError.objectNotFound(pathID)
            }
            guard !sourceBundleRootIDs.contains(pathID), object.classID != 142 else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "PPtr dependency \(pathID) trỏ tới AssetBundle root khác, chưa có quy tắc ánh xạ."
                )
            }
            guard selected.insert(pathID).inserted else { continue }
            guard let typeInfo = sourceSummary.typeInfo(for: object),
                  !typeInfo.typeTreeNodes.isEmpty else {
                throw AoVUnityFSAssetBundleMergeExecutorError.verificationFailed(
                    "Object dependency \(pathID) không có TypeTree đầy đủ."
                )
            }
            let decoded = try AoVSerializedFileParser.decodeObjectWithTypeTree(
                pathID: pathID,
                in: sourceData,
                summary: sourceSummary
            )
            let targets = try AoVUnityTypeTreePointerRemapper.collectLocalTargets(
                nodes: typeInfo.typeTreeNodes,
                value: decoded
            )
            pending.append(contentsOf: targets)
        }
        return selected
    }

    private static func auditedExternalMappings(
        source: AoVSerializedFileSummary,
        destination: AoVSerializedFileSummary,
        auditedTargets: [String: Set<Int64>]
    ) -> (fileIDMap: [Int32: Int32], availableTargetPathIDs: [Int32: Set<Int64>]) {
        var fileIDMap: [Int32: Int32] = [:]
        var available: [Int32: Set<Int64>] = [:]
        for (sourceIndex, sourceReference) in source.externalReferences.enumerated() {
            let matching = destination.externalReferences.enumerated().filter { _, candidate in
                candidate.guidHex.lowercased() == sourceReference.guidHex.lowercased() &&
                normalizedExternalPath(candidate.path) == normalizedExternalPath(sourceReference.path)
            }
            guard matching.count == 1, let match = matching.first,
                  let sourceFileID = Int32(exactly: sourceIndex + 1),
                  let destinationFileID = Int32(exactly: match.offset + 1) else {
                continue
            }
            let key = AoVAssetBundleCatalogMerger.externalTargetKey(
                guidHex: match.element.guidHex,
                path: match.element.path
            )
            guard let pathIDs = auditedTargets[key] else { continue }
            fileIDMap[sourceFileID] = destinationFileID
            available[destinationFileID] = pathIDs
        }
        return (fileIDMap, available)
    }

    private static func normalizedExternalPath(_ value: String) -> String {
        value.replacingOccurrences(of: "\\", with: "/")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
    }


}
