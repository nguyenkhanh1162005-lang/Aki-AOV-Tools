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
}
