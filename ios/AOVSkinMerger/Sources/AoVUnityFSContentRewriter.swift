import Foundation

enum AoVUnityFSContentRewriteError: LocalizedError {
    case nodeNotFound(String)
    case ambiguousTarget(String)
    case expandedArchiveTooLarge

    var errorDescription: String? {
        switch self {
        case .nodeNotFound(let path):
            return "Không tìm thấy UnityFS node cần thay: \(path)"
        case .ambiguousTarget(let path):
            return "Có nhiều UnityFS node trùng đường dẫn: \(path)"
        case .expandedArchiveTooLarge:
            return "UnityFS giải nén vượt giới hạn 512 MiB."
        }
    }
}

struct AoVUnityFSContentRewriteReport: Sendable {
    let data: Data
    let targetNodePath: String
    let originalNodeBytes: UInt64
    let rewrittenNodeBytes: UInt64
    let originalBundleBytes: UInt64
    let rewrittenBundleBytes: UInt64
    let repack: AoVUnityFSRepackReport
    let serializedFileRewrite: AoVSerializedFileRewriteReport?
    let roundTripVerified: Bool
}

enum AoVUnityFSContentRewriter {
    /// Replaces exactly one UnityFS node, preserving all other node payloads,
    /// names and flags. The rebuilt archive uses uncompressed blocks and is
    /// round-trip validated before being returned.
    static func replaceNode(
        in source: Data,
        nodePath: String,
        with replacement: Data
    ) throws -> AoVUnityFSContentRewriteReport {
        let info = try AoVUnityFSParser.inspect(source)
        guard info.totalUncompressedBytes <= UInt64(512 * 1024 * 1024) else {
            throw AoVUnityFSContentRewriteError.expandedArchiveTooLarge
        }
        let extracted = try AoVUnityFSParser.extractNodes(
            source,
            maximumExpandedBytes: UInt64(512 * 1024 * 1024),
            maximumNodeCount: max(info.nodes.count, 1)
        )
        let matches = extracted.filter { $0.path == nodePath }
        guard matches.count == 1, let original = matches.first else {
            if matches.count > 1 { throw AoVUnityFSContentRewriteError.ambiguousTarget(nodePath) }
            throw AoVUnityFSContentRewriteError.nodeNotFound(nodePath)
        }

        let updatedNodes = extracted.map { node in
            AoVUnityFSRepackNode(
                path: node.path,
                data: node.path == nodePath ? replacement : node.data,
                flags: node.flags
            )
        }
        let report = try AoVUnityFSRepacker.repack(
            updatedNodes,
            formatVersion: info.formatVersion,
            playerVersion: info.playerVersion,
            engineVersion: info.engineVersion,
            blockInfoLayout: info.blockInfoLayout
        )
        let rechecked = try AoVUnityFSParser.inspect(report.data)
        let verifiedNodes = try AoVUnityFSParser.extractNodes(
            report.data,
            maximumExpandedBytes: UInt64(512 * 1024 * 1024),
            maximumNodeCount: max(rechecked.nodes.count, 1)
        )
        let verifiedTarget = verifiedNodes.first(where: { $0.path == nodePath })
        guard verifiedTarget?.data == replacement,
              verifiedNodes.count == extracted.count,
              rechecked.nodes.map(\.path) == info.nodes.map(\.path) else {
            throw AoVUnityFSRepackError.verificationFailed("Thay node \(nodePath) không vượt qua kiểm tra round-trip.")
        }

        return AoVUnityFSContentRewriteReport(
            data: report.data,
            targetNodePath: nodePath,
            originalNodeBytes: UInt64(original.data.count),
            rewrittenNodeBytes: UInt64(replacement.count),
            originalBundleBytes: UInt64(source.count),
            rewrittenBundleBytes: UInt64(report.data.count),
            repack: report,
            serializedFileRewrite: nil,
            roundTripVerified: true
        )
    }

    /// Rewrites a Packer or Verification MonoBehaviour in one SerializedFile
    /// node, then rebuilds and round-trip validates the enclosing UnityFS file.
    static func replaceRuntimeCatalog(
        in source: Data,
        serializedFileNodePath: String,
        objectPathID: Int64,
        with replacement: AoVRuntimeResourceCatalog
    ) throws -> AoVUnityFSContentRewriteReport {
        let info = try AoVUnityFSParser.inspect(source)
        let extracted = try AoVUnityFSParser.extractNodes(
            source,
            maximumExpandedBytes: UInt64(512 * 1024 * 1024),
            maximumNodeCount: max(info.nodes.count, 1)
        )
        let matches = extracted.filter { $0.path == serializedFileNodePath }
        guard matches.count == 1, let node = matches.first else {
            if matches.count > 1 {
                throw AoVUnityFSContentRewriteError.ambiguousTarget(serializedFileNodePath)
            }
            throw AoVUnityFSContentRewriteError.nodeNotFound(serializedFileNodePath)
        }

        let serializedReport = try AoVSerializedRuntimeCatalogWriter.rewrite(
            serializedFile: node.data,
            objectPathID: objectPathID,
            with: replacement
        )
        let contentReport = try replaceNode(
            in: source,
            nodePath: serializedFileNodePath,
            with: serializedReport.data
        )
        return AoVUnityFSContentRewriteReport(
            data: contentReport.data,
            targetNodePath: serializedFileNodePath,
            originalNodeBytes: UInt64(node.data.count),
            rewrittenNodeBytes: UInt64(serializedReport.data.count),
            originalBundleBytes: UInt64(source.count),
            rewrittenBundleBytes: UInt64(contentReport.data.count),
            repack: contentReport.repack,
            serializedFileRewrite: nil,
            roundTripVerified: contentReport.roundTripVerified && serializedReport.roundTripVerified
        )
    }

    /// Rewrites one AssetBundle class 142 record inside a SerializedFile node,
    /// then rebuilds and verifies the enclosing UnityFS container.
    static func replaceAssetBundleRecord(
        in source: Data,
        serializedFileNodePath: String,
        objectPathID: Int64,
        with replacement: AoVSerializedAssetBundleRecord
    ) throws -> AoVUnityFSContentRewriteReport {
        let info = try AoVUnityFSParser.inspect(source)
        let extracted = try AoVUnityFSParser.extractNodes(
            source,
            maximumExpandedBytes: UInt64(512 * 1024 * 1024),
            maximumNodeCount: max(info.nodes.count, 1)
        )
        guard let node = extracted.first(where: { $0.path == serializedFileNodePath }) else {
            throw AoVUnityFSContentRewriteError.nodeNotFound(serializedFileNodePath)
        }
        let serializedReport = try AoVSerializedFileRewriter.replaceAssetBundleRecord(
            in: node.data,
            pathID: objectPathID,
            with: replacement
        )
        let contentReport = try replaceNode(
            in: source,
            nodePath: serializedFileNodePath,
            with: serializedReport.data
        )
        return AoVUnityFSContentRewriteReport(
            data: contentReport.data,
            targetNodePath: serializedFileNodePath,
            originalNodeBytes: UInt64(node.data.count),
            rewrittenNodeBytes: UInt64(serializedReport.data.count),
            originalBundleBytes: UInt64(source.count),
            rewrittenBundleBytes: UInt64(contentReport.data.count),
            repack: contentReport.repack,
            serializedFileRewrite: serializedReport,
            roundTripVerified: contentReport.roundTripVerified && serializedReport.roundTripVerified
        )
    }
}
