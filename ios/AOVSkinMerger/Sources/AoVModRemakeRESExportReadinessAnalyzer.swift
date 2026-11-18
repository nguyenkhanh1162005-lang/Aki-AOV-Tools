import Foundation

enum AoVModRemakeRESProposalStatus: String, Sendable {
    case requiresExternalValidation
    case blocked
}

struct AoVModRemakeCABExportProposal: Sendable {
    let resourcePath: String
    let runtimePath: String
    let importedObjectCount: Int
    let sourceArchives: [String]
    let rawPayloadCRC32: UInt32?
    let proposedPackerEntry: AoVRuntimeResourcePackInfo?
    let proposedVerificationZIPChecksum: Int64?
    let status: AoVModRemakeRESProposalStatus
    let reasons: [String]
}

struct AoVModRemakeRESExportReadinessReport: Sendable {
    let baseRESName: String
    let proposals: [AoVModRemakeCABExportProposal]
    let packerXORMask: UInt32?
    let verificationZIPXORMask: UInt32?
    let warning: String

    var proposalCount: Int { proposals.count }
    var candidateCount: Int {
        proposals.filter { $0.status == .requiresExternalValidation }.count
    }
    var blockedCount: Int {
        proposals.filter { $0.status == .blocked }.count
    }
    var allChecksumsHaveCandidates: Bool {
        !proposals.isEmpty && blockedCount == 0
    }

    /// Candidate values are derived from fingerprints of the selected base RES.
    /// They are not yet independently verified against a game build, so this
    /// report must never be treated as permission to write a final RES.
    var safeToExport: Bool { false }
}

enum AoVModRemakeRESExportReadinessError: LocalizedError {
    case fingerprintBelongsToDifferentRES(expected: String, actual: String)
    case noMergedAssetBundles
    case duplicateMergedRuntimePath(String)
    case incompatibleRuntimeCatalogs(String)

    var errorDescription: String? {
        switch self {
        case .fingerprintBelongsToDifferentRES(let expected, let actual):
            return "Checksum fingerprint thuộc RES \(actual), không phải RES đang ghép \(expected)."
        case .noMergedAssetBundles:
            return "Chưa có AssetBundle candidate đã round-trip để lập metadata RES."
        case .duplicateMergedRuntimePath(let path):
            return "Có nhiều CAB candidate cùng runtime path \(path)."
        case .incompatibleRuntimeCatalogs(let reason):
            return "Không đọc được metadata catalog theo build hiện tại: \(reason)"
        }
    }
}

private struct AoVModRemakePackerStaticMetadata: Hashable {
    let pathInIFS: String
    let pkgType: Int32
    let tag: String
    let flags: UInt16
    let resourceInfos: [AoVRuntimeResourceItem]

    init(_ value: AoVRuntimeResourcePackInfo) {
        pathInIFS = value.pathInIFS
        pkgType = value.pkgType
        tag = value.tag
        flags = value.flags
        resourceInfos = value.resourceInfos
    }

    func applying(checksum: UInt32) -> AoVRuntimeResourcePackInfo {
        AoVRuntimeResourcePackInfo(
            pathInIFS: pathInIFS,
            pkgType: pkgType,
            crcXor: Int64(checksum),
            tag: tag,
            flags: flags,
            resourceInfos: resourceInfos
        )
    }
}

enum AoVModRemakeRESExportReadinessAnalyzer {
    static func analyze(
        baseRESURL: URL,
        legacyMODURLs: [URL],
        assetBundleMergeResults: [AoVModRemakeAssetBundleMergeResult],
        checksumFingerprint: AoVRuntimeChecksumFingerprintReport
    ) throws -> AoVModRemakeRESExportReadinessReport {
        guard checksumFingerprint.baseRESName == baseRESURL.lastPathComponent else {
            throw AoVModRemakeRESExportReadinessError.fingerprintBelongsToDifferentRES(
                expected: baseRESURL.lastPathComponent,
                actual: checksumFingerprint.baseRESName
            )
        }
        let catalogInputs: AoVModRemakeCatalogInputs
        do {
            catalogInputs = try AoVModRemakeAnalyzer.loadRuntimeCatalogInputs(
                baseRESURL: baseRESURL,
                legacyMODURLs: legacyMODURLs
            )
        } catch {
            throw AoVModRemakeRESExportReadinessError.incompatibleRuntimeCatalogs(
                error.localizedDescription
            )
        }
        return try buildProposals(
            baseRESName: baseRESURL.lastPathComponent,
            assetBundleMergeResults: assetBundleMergeResults,
            legacyPackerCatalogs: catalogInputs.packer,
            checksumFingerprint: checksumFingerprint
        )
    }

    static func buildProposals(
        baseRESName: String,
        assetBundleMergeResults: [AoVModRemakeAssetBundleMergeResult],
        legacyPackerCatalogs: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)],
        checksumFingerprint: AoVRuntimeChecksumFingerprintReport
    ) throws -> AoVModRemakeRESExportReadinessReport {
        guard checksumFingerprint.baseRESName == baseRESName else {
            throw AoVModRemakeRESExportReadinessError.fingerprintBelongsToDifferentRES(
                expected: baseRESName,
                actual: checksumFingerprint.baseRESName
            )
        }
        guard !assetBundleMergeResults.isEmpty else {
            throw AoVModRemakeRESExportReadinessError.noMergedAssetBundles
        }

        var seenRuntimePaths = Set<String>()
        var rowsByPath: [String: [(source: String, value: AoVRuntimeResourcePackInfo)]] = [:]
        for source in legacyPackerCatalogs {
            var seenSourcePaths = Set<String>()
            for row in source.catalog.allResourcePackInfo {
                let path = normalize(row.pathInIFS)
                guard !path.isEmpty else { continue }
                guard seenSourcePaths.insert(path).inserted else {
                    rowsByPath[path, default: []].append((source.source + " · duplicate", row))
                    continue
                }
                rowsByPath[path, default: []].append((source.source, row))
            }
        }

        let packerFingerprintValid =
            checksumFingerprint.packerAssetBundles.status == .consistentSample &&
            checksumFingerprint.packerAssetBundles.constantXORMask != nil &&
            checksumFingerprint.packerAssetBundles.evaluatedSamples >= 8
        let verificationFingerprintValid =
            checksumFingerprint.zipVerification.status == .consistentSample &&
            checksumFingerprint.zipVerification.constantXORMask != nil &&
            checksumFingerprint.zipVerification.evaluatedSamples >= 8

        var proposals: [AoVModRemakeCABExportProposal] = []
        for result in assetBundleMergeResults.sorted(by: { $0.resourcePath < $1.resourcePath }) {
            let runtimePath = normalize(result.resourcePayload.ifsPath)
            var reasons: [String] = []
            let sourceRows = rowsByPath[runtimePath] ?? []
            let sourceArchives = Array(Set(sourceRows.map { $0.source.replacingOccurrences(of: " · duplicate", with: "") })).sorted()

            if !seenRuntimePaths.insert(runtimePath).inserted {
                throw AoVModRemakeRESExportReadinessError.duplicateMergedRuntimePath(runtimePath)
            }
            guard runtimePath.lowercased().hasSuffix(".assetbundle") else {
                reasons.append("Runtime path của candidate không có đuôi .assetbundle.")
                proposals.append(blockedProposal(result, runtimePath: runtimePath, sourceArchives: sourceArchives, reasons: reasons))
                continue
            }
            if !result.roundTripVerified {
                reasons.append("CAB candidate chưa vượt qua round-trip verification.")
            }
            if result.importedObjectCount < 0 || result.importedPathIDs.count != result.importedObjectCount {
                reasons.append("Số object và danh sách pathID nhập vào không khớp.")
            }

            let duplicateSourceRow = sourceRows.contains { $0.source.hasSuffix(" · duplicate") }
            if duplicateSourceRow {
                reasons.append("Một MOD có nhiều Packer entry cùng runtime path.")
            }
            let staticMetadata = Set(sourceRows.map { AoVModRemakePackerStaticMetadata($0.value) })
            if sourceRows.isEmpty {
                reasons.append("Không tìm thấy Packer metadata nguồn cho AssetBundle này.")
            } else if staticMetadata.count != 1 {
                reasons.append("Các MOD không thống nhất pkgType/tag/flags/resourceInfos của Packer entry.")
            }
            if sourceRows.contains(where: { $0.value.pathInIFS != result.resourcePayload.ifsPath }) {
                reasons.append("IFS path trong Packer metadata khác chính xác path của CAB candidate.")
            }

            if !packerFingerprintValid {
                reasons.append("Packer AssetBundle CRC32/XOR fingerprint chưa nhất quán hoặc chưa đủ mẫu.")
            }
            if !verificationFingerprintValid {
                reasons.append("Verification ZIP CRC32/XOR fingerprint chưa nhất quán hoặc chưa đủ mẫu.")
            }

            let rawCRC32 = AoVRuntimeChecksumFingerprintAnalyzer.crc32(result.resourcePayload.data)
            let proposedPacker: AoVRuntimeResourcePackInfo?
            if reasons.isEmpty,
               let metadata = staticMetadata.first,
               let mask = checksumFingerprint.packerAssetBundles.constantXORMask {
                proposedPacker = metadata.applying(checksum: rawCRC32 ^ mask)
            } else {
                proposedPacker = nil
            }
            let proposedVerification: Int64?
            if reasons.isEmpty,
               let mask = checksumFingerprint.zipVerification.constantXORMask {
                proposedVerification = Int64(rawCRC32 ^ mask)
            } else {
                proposedVerification = nil
            }

            if !result.roundTripVerified && !reasons.contains("CAB candidate chưa vượt qua round-trip verification.") {
                reasons.append("CAB candidate chưa vượt qua round-trip verification.")
            }
            let status: AoVModRemakeRESProposalStatus = reasons.isEmpty
                ? .requiresExternalValidation : .blocked
            proposals.append(
                AoVModRemakeCABExportProposal(
                    resourcePath: result.resourcePath,
                    runtimePath: runtimePath,
                    importedObjectCount: result.importedObjectCount,
                    sourceArchives: sourceArchives,
                    rawPayloadCRC32: rawCRC32,
                    proposedPackerEntry: proposedPacker,
                    proposedVerificationZIPChecksum: proposedVerification,
                    status: status,
                    reasons: reasons
                )
            )
        }

        return AoVModRemakeRESExportReadinessReport(
            baseRESName: baseRESName,
            proposals: proposals,
            packerXORMask: checksumFingerprint.packerAssetBundles.constantXORMask,
            verificationZIPXORMask: checksumFingerprint.zipVerification.constantXORMask,
            warning: "Giá trị Packer và Verification bên dưới chỉ là checksum candidate được suy ra từ mask quan sát trên RES nền. Chưa được xác nhận bằng thuật toán gốc hoặc kiểm thử trong game; tuyệt đối chưa dùng để ghi RES cuối."
        )
    }

    private static func blockedProposal(
        _ result: AoVModRemakeAssetBundleMergeResult,
        runtimePath: String,
        sourceArchives: [String],
        reasons: [String]
    ) -> AoVModRemakeCABExportProposal {
        AoVModRemakeCABExportProposal(
            resourcePath: result.resourcePath,
            runtimePath: runtimePath,
            importedObjectCount: result.importedObjectCount,
            sourceArchives: sourceArchives,
            rawPayloadCRC32: nil,
            proposedPackerEntry: nil,
            proposedVerificationZIPChecksum: nil,
            status: .blocked,
            reasons: reasons
        )
    }

    private static func normalize(_ value: String) -> String {
        value.replacingOccurrences(of: "\\", with: "/")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
    }
}
