import Foundation

enum AoVUnityFSRuntimeCatalogMergeExecutorError: LocalizedError {
    case expandedArchiveTooLarge
    case nodeNotFound(String)
    case ambiguousNode(String)
    case objectNotFound(Int64)
    case ambiguousObject(Int64)
    case objectNotMonoBehaviour(Int64)
    case catalogNotRecognized(Int64)
    case catalogNotFound(String)
    case ambiguousCatalog(String, [String])
    case wrongCatalogType
    case planBlocked([AoVRuntimeCatalogMergeIssue])
    case verificationFailed(String)

    var errorDescription: String? {
        switch self {
        case .expandedArchiveTooLarge:
            return "UnityFS runtime catalog vượt giới hạn giải nén 512 MiB."
        case .nodeNotFound(let path):
            return "Không tìm thấy UnityFS node \(path)."
        case .ambiguousNode(let path):
            return "UnityFS có nhiều node trùng đường dẫn \(path)."
        case .objectNotFound(let pathID):
            return "Không tìm thấy MonoBehaviour pathID \(pathID)."
        case .ambiguousObject(let pathID):
            return "SerializedFile có nhiều object pathID \(pathID)."
        case .objectNotMonoBehaviour(let pathID):
            return "Object pathID \(pathID) không thuộc MonoBehaviour class 114."
        case .catalogNotRecognized(let pathID):
            return "MonoBehaviour pathID \(pathID) không phải runtime catalog Packer/Verification được hỗ trợ."
        case .catalogNotFound(let kind):
            return "Không tìm thấy runtime catalog \(kind) trong UnityFS."
        case .ambiguousCatalog(let kind, let matches):
            return "UnityFS có nhiều runtime catalog \(kind): \(matches.prefix(6).joined(separator: ", "))."
        case .wrongCatalogType:
            return "Loại runtime catalog không khớp yêu cầu ghi."
        case .planBlocked(let issues):
            let first = issues.first.map { "\($0.path): \($0.reason.rawValue)" } ?? "không rõ"
            return "Runtime catalog merge plan bị chặn: \(issues.count) vấn đề. Ví dụ: \(first)."
        case .verificationFailed(let reason):
            return "Runtime catalog UnityFS không vượt qua round-trip: \(reason)"
        }
    }
}

struct AoVUnityFSRuntimeCatalogTarget: Sendable {
    let nodePath: String
    let objectPathID: Int64
    let catalog: AoVRuntimeResourceCatalog
}

struct AoVUnityFSRuntimePackerMergeExecutionReport: Sendable {
    let data: Data
    let nodePath: String
    let objectPathID: Int64
    let plan: AoVRuntimePackerMergePlan
    let rewrite: AoVSerializedRuntimeCatalogWriteReport?
    let roundTripVerified: Bool
}

struct AoVUnityFSRuntimeVerificationMergeExecutionReport: Sendable {
    let data: Data
    let nodePath: String
    let objectPathID: Int64
    let plan: AoVRuntimeVerificationMergePlan
    let rewrite: AoVSerializedRuntimeCatalogWriteReport?
    let roundTripVerified: Bool
}

/// Executes conservative Packer/Verification plans in their actual UnityFS
/// SerializedFile node. Final resource bytes and checksum values must already
/// be verified by the upstream resource adapters; this executor never guesses
/// bundle or checksum metadata.
enum AoVUnityFSRuntimeCatalogMergeExecutor {
    private static let maximumExpandedBytes: UInt64 = 512 * 1024 * 1024

    static func locatePackerCatalog(in source: Data) throws -> AoVUnityFSRuntimeCatalogTarget {
        try locateCatalog(in: source, expectedName: "ResourcePackerInfoSetXML")
    }

    static func locateVerificationCatalog(in source: Data) throws -> AoVUnityFSRuntimeCatalogTarget {
        try locateCatalog(in: source, expectedName: "ResourceVerificationInfoSetXML")
    }

    private static func locateCatalog(
        in source: Data,
        expectedName: String
    ) throws -> AoVUnityFSRuntimeCatalogTarget {
        let bundle = try AoVUnityFSParser.inspect(source)
        try AoVUnityFSContentRewriter.validateWritableTemplate(bundle)
        guard bundle.totalUncompressedBytes <= maximumExpandedBytes else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.expandedArchiveTooLarge
        }

        let nodes = try AoVUnityFSParser.extractNodes(
            source,
            maximumExpandedBytes: maximumExpandedBytes,
            maximumNodeCount: max(bundle.nodes.count, 1)
        )
        var matches: [AoVUnityFSRuntimeCatalogTarget] = []
        for node in nodes {
            guard let serialized = try? AoVSerializedFileParser.inspect(node.data) else { continue }
            for object in serialized.serializedObjects where object.classID == 114 {
                guard let start = Int(exactly: object.byteOffset),
                      let size = Int(exactly: object.byteSize),
                      start >= 0, size > 0,
                      start <= node.data.count, size <= node.data.count - start else {
                    continue
                }
                let payload = node.data.subdata(in: start..<(start + size))
                guard let catalog = try? AoVRuntimeResourceCatalogParser.inspectMonoBehaviour(
                    payload,
                    serializedFormatVersion: serialized.formatVersion,
                    unityVersion: serialized.unityVersion,
                    littleEndian: serialized.littleEndian
                ) else { continue }

                let name: String
                switch catalog {
                case .packer(let value): name = value.name
                case .verification(let value): name = value.name
                }
                if name == expectedName {
                    matches.append(
                        AoVUnityFSRuntimeCatalogTarget(
                            nodePath: node.path,
                            objectPathID: object.pathID,
                            catalog: catalog
                        )
                    )
                }
            }
        }

        guard matches.count == 1, let target = matches.first else {
            if matches.isEmpty {
                throw AoVUnityFSRuntimeCatalogMergeExecutorError.catalogNotFound(expectedName)
            }
            let locations = matches.map { "\($0.nodePath)#\($0.objectPathID)" }
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.ambiguousCatalog(expectedName, locations)
        }
        return target
    }

    static func mergePacker(
        in source: Data,
        legacy: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)],
        verifiedFinalEntries: [String: AoVRuntimeResourcePackInfo]
    ) throws -> AoVUnityFSRuntimePackerMergeExecutionReport {
        let target = try locatePackerCatalog(in: source)
        return try mergePacker(
            in: source,
            serializedFileNodePath: target.nodePath,
            objectPathID: target.objectPathID,
            legacy: legacy,
            verifiedFinalEntries: verifiedFinalEntries
        )
    }

    static func mergeVerification(
        in source: Data,
        legacy: [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)],
        verifiedFinalDatabinChecksumValues: [String: Int64],
        verifiedFinalZIPChecksumValues: [String: Int64]
    ) throws -> AoVUnityFSRuntimeVerificationMergeExecutionReport {
        let target = try locateVerificationCatalog(in: source)
        return try mergeVerification(
            in: source,
            serializedFileNodePath: target.nodePath,
            objectPathID: target.objectPathID,
            legacy: legacy,
            verifiedFinalDatabinChecksumValues: verifiedFinalDatabinChecksumValues,
            verifiedFinalZIPChecksumValues: verifiedFinalZIPChecksumValues
        )
    }

    static func mergePacker(
        in source: Data,
        serializedFileNodePath: String,
        objectPathID: Int64,
        legacy: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)],
        verifiedFinalEntries: [String: AoVRuntimeResourcePackInfo]
    ) throws -> AoVUnityFSRuntimePackerMergeExecutionReport {
        let current = try loadCatalog(
            in: source,
            nodePath: serializedFileNodePath,
            objectPathID: objectPathID
        )
        guard case .packer(let currentCatalog) = current.catalog else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.wrongCatalogType
        }

        let plan = AoVRuntimeCatalogMergePlanner.planPacker(
            current: currentCatalog,
            legacy: legacy,
            verifiedFinalEntries: verifiedFinalEntries
        )
        guard plan.safeToWrite, let candidate = plan.candidateCatalog else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.planBlocked(plan.issues)
        }

        if candidate == currentCatalog {
            return AoVUnityFSRuntimePackerMergeExecutionReport(
                data: source,
                nodePath: serializedFileNodePath,
                objectPathID: objectPathID,
                plan: plan,
                rewrite: nil,
                roundTripVerified: true
            )
        }

        let rewrite = try AoVUnityFSContentRewriter.replaceRuntimeCatalog(
            in: source,
            serializedFileNodePath: serializedFileNodePath,
            objectPathID: objectPathID,
            with: .packer(candidate)
        )
        let verified = try loadCatalog(
            in: rewrite.data,
            nodePath: serializedFileNodePath,
            objectPathID: objectPathID
        )
        guard case .packer(let verifiedCatalog) = verified.catalog,
              verifiedCatalog == candidate,
              rewrite.roundTripVerified else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.verificationFailed(
                "Packer candidate không trùng runtime catalog sau khi ghi."
            )
        }

        return AoVUnityFSRuntimePackerMergeExecutionReport(
            data: rewrite.data,
            nodePath: serializedFileNodePath,
            objectPathID: objectPathID,
            plan: plan,
            rewrite: rewrite.runtimeCatalogRewrite,
            roundTripVerified: true
        )
    }

    static func mergeVerification(
        in source: Data,
        serializedFileNodePath: String,
        objectPathID: Int64,
        legacy: [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)],
        verifiedFinalDatabinChecksumValues: [String: Int64],
        verifiedFinalZIPChecksumValues: [String: Int64]
    ) throws -> AoVUnityFSRuntimeVerificationMergeExecutionReport {
        let current = try loadCatalog(
            in: source,
            nodePath: serializedFileNodePath,
            objectPathID: objectPathID
        )
        guard case .verification(let currentCatalog) = current.catalog else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.wrongCatalogType
        }

        let plan = AoVRuntimeCatalogMergePlanner.planVerification(
            current: currentCatalog,
            legacy: legacy,
            verifiedFinalDatabinChecksumValues: verifiedFinalDatabinChecksumValues,
            verifiedFinalZIPChecksumValues: verifiedFinalZIPChecksumValues
        )
        guard plan.safeToWrite, let candidate = plan.candidateCatalog else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.planBlocked(plan.issues)
        }

        if candidate == currentCatalog {
            return AoVUnityFSRuntimeVerificationMergeExecutionReport(
                data: source,
                nodePath: serializedFileNodePath,
                objectPathID: objectPathID,
                plan: plan,
                rewrite: nil,
                roundTripVerified: true
            )
        }

        let rewrite = try AoVUnityFSContentRewriter.replaceRuntimeCatalog(
            in: source,
            serializedFileNodePath: serializedFileNodePath,
            objectPathID: objectPathID,
            with: .verification(candidate)
        )
        let verified = try loadCatalog(
            in: rewrite.data,
            nodePath: serializedFileNodePath,
            objectPathID: objectPathID
        )
        guard case .verification(let verifiedCatalog) = verified.catalog,
              verifiedCatalog == candidate,
              rewrite.roundTripVerified else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.verificationFailed(
                "Verification candidate không trùng runtime catalog sau khi ghi."
            )
        }

        return AoVUnityFSRuntimeVerificationMergeExecutionReport(
            data: rewrite.data,
            nodePath: serializedFileNodePath,
            objectPathID: objectPathID,
            plan: plan,
            rewrite: rewrite.runtimeCatalogRewrite,
            roundTripVerified: true
        )
    }

    private static func loadCatalog(
        in source: Data,
        nodePath: String,
        objectPathID: Int64
    ) throws -> (catalog: AoVRuntimeResourceCatalog, serializedFile: AoVSerializedFileSummary) {
        let bundle = try AoVUnityFSParser.inspect(source)
        try AoVUnityFSContentRewriter.validateWritableTemplate(bundle)
        guard bundle.totalUncompressedBytes <= maximumExpandedBytes else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.expandedArchiveTooLarge
        }
        let nodes = try AoVUnityFSParser.extractNodes(
            source,
            maximumExpandedBytes: maximumExpandedBytes,
            maximumNodeCount: max(bundle.nodes.count, 1)
        )
        let matches = nodes.filter { $0.path == nodePath }
        guard matches.count == 1, let node = matches.first else {
            if matches.count > 1 {
                throw AoVUnityFSRuntimeCatalogMergeExecutorError.ambiguousNode(nodePath)
            }
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.nodeNotFound(nodePath)
        }

        let summary = try AoVSerializedFileParser.inspect(node.data)
        let objects = summary.serializedObjects.filter { $0.pathID == objectPathID }
        guard objects.count == 1, let object = objects.first else {
            if objects.count > 1 {
                throw AoVUnityFSRuntimeCatalogMergeExecutorError.ambiguousObject(objectPathID)
            }
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.objectNotFound(objectPathID)
        }
        guard object.classID == 114 else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.objectNotMonoBehaviour(objectPathID)
        }
        guard let start = Int(exactly: object.byteOffset),
              let size = Int(exactly: object.byteSize),
              start >= 0, size > 0,
              start <= node.data.count, size <= node.data.count - start else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.verificationFailed(
                "Object MonoBehaviour nằm ngoài SerializedFile."
            )
        }

        let payload = node.data.subdata(in: start..<(start + size))
        guard let catalog = try AoVRuntimeResourceCatalogParser.inspectMonoBehaviour(
            payload,
            serializedFormatVersion: summary.formatVersion,
            unityVersion: summary.unityVersion,
            littleEndian: summary.littleEndian
        ) else {
            throw AoVUnityFSRuntimeCatalogMergeExecutorError.catalogNotRecognized(objectPathID)
        }
        return (catalog, summary)
    }
}
