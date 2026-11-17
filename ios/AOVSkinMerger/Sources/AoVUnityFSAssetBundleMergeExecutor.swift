import Foundation

enum AoVUnityFSAssetBundleMergeExecutorError: LocalizedError {
    case expandedArchiveTooLarge
    case nodeNotFound(String)
    case ambiguousNode(String)
    case objectNotFound(Int64)
    case ambiguousObject(Int64)
    case verificationFailed(String)

    var errorDescription: String? {
        switch self {
        case .expandedArchiveTooLarge:
            return "UnityFS AssetBundle ghép vượt giới hạn giải nén 512 MiB."
        case .nodeNotFound(let path):
            return "Không tìm thấy SerializedFile node (path) trong UnityFS."
        case .ambiguousNode(let path):
            return "UnityFS có nhiều node trùng đường dẫn (path)."
        case .objectNotFound(let pathID):
            return "Không tìm thấy AssetBundle object pathID (pathID) trong SerializedFile."
        case .ambiguousObject(let pathID):
            return "SerializedFile có nhiều AssetBundle object pathID (pathID)."
        case .verificationFailed(let reason):
            return "UnityFS AssetBundle merge không vượt qua round-trip: (reason)"
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

/// Executes the class-142 merge plan against the actual SerializedFile node in
/// a UnityFS archive. It is deliberately a single-bundle primitive: callers
/// must still coordinate runtime Packer/Verification metadata and ZIP output.
enum AoVUnityFSAssetBundleMergeExecutor {
    private static let maximumExpandedBytes: UInt64 = 512 * 1024 * 1024

    static func merge(
        in source: Data,
        serializedFileNodePath: String,
        objectPathID: Int64,
        legacyRecord: AoVSerializedAssetBundleRecord,
        legacySerializedFile: AoVSerializedFileSummary,
        availableCurrentBundleNames: Set<String>
    ) throws -> AoVUnityFSAssetBundleMergeExecutionReport {
        let info = try AoVUnityFSParser.inspect(source)
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

        let plan = try AoVAssetBundleCatalogMerger.plan(
            current: currentRecord,
            legacy: legacyRecord,
            currentSerializedFile: currentSerializedFile,
            legacySerializedFile: legacySerializedFile,
            availableCurrentBundleNames: availableCurrentBundleNames
        )
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
