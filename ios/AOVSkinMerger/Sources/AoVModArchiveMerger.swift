import Foundation
import CryptoKit
import ZIPFoundation

enum AoVModArchiveMergeError: LocalizedError {
    case tooFewInputs
    case tooManyInputs
    case totalInputTooLarge
    case invalidArchive(String)
    case unsafeArchivePath(String)
    case duplicateResourcePath(String)
    case noBaseWrapper
    case baseMissingResource(String)
    case versionMismatch(String, String, String)
    case unsupportedConflict(String, String)
    case assetBundleMetadataRequired(String)
    case wwiseNotSupported(String)
    case assetReferenceConflict(String)
    case commonActionsFailure(String)
    case msesFailure(String)
    case ifixFailure(String)
    case outputValidation(String)

    var errorDescription: String? {
        switch self {
        case .tooFewInputs: return "Cần ít nhất hai gói ZIP để ghép."
        case .tooManyInputs: return "Bản iOS hiện giới hạn tối đa 10 gói ZIP."
        case .totalInputTooLarge: return "Tổng dung lượng ZIP đầu vào vượt 500 MiB."
        case .invalidArchive(let name): return "\(name): ZIP không hợp lệ hoặc bị lỗi CRC."
        case .unsafeArchivePath(let path): return "Đường dẫn không an toàn trong ZIP: \(path)"
        case .duplicateResourcePath(let path): return "Có nhiều entry chuẩn hóa về cùng đường dẫn: \(path)"
        case .noBaseWrapper: return "Không tìm thấy gói nền có cả Resources.zip và DanhSachSkin.txt."
        case .baseMissingResource(let path): return "Gói nền thiếu tài nguyên bắt buộc: \(path)"
        case .versionMismatch(let source, let candidate, let base):
            return "\(source): version.txt \(candidate) khác bản nền \(base). Không trộn khác build."
        case .unsupportedConflict(let path, let reason):
            return "\(path): \(reason)"
        case .assetBundleMetadataRequired(let path):
            return "\(path): AssetBundle mới cần ghép Packer/Verification metadata, adapter iOS chưa hoàn tất."
        case .wwiseNotSupported(let path):
            return "\(path): Wwise BNK/WEM cần parser tham chiếu HIRC/Media; app chưa cho phép ghép phần này."
        case .assetReferenceConflict(let path):
            return "\(path): AssetReferenceSet cùng đường dẫn nhưng payload khác. Chưa có parser subset để hợp nhất an toàn."
        case .commonActionsFailure(let reason):
            return "CommonActions không ghép được an toàn: \(reason)"
        case .msesFailure(let reason):
            return "MSES không ghép được an toàn: \(reason)"
        case .ifixFailure(let reason):
            return "IFix không ghép được an toàn: \(reason)"
        case .outputValidation(let reason):
            return "ZIP đầu ra không vượt qua kiểm tra: \(reason)"
        }
    }
}

struct AoVModMergeReport: Sendable {
    let outputURL: URL
    let baseSource: String
    let sourceCount: Int
    let actorIDsBySource: [String: [String]]
    let addedResources: Int
    let replacedResources: Int
    let commonActionsFiles: [String]
    let mergedMSESTables: [String]
    let mergedIFixPatches: [String]
    let preservedAssetReferenceSets: [String]
    let preservedWwiseBanks: [String]
    let structurallyValidated: Bool
    let gameCompatibilityTested: Bool
}

private struct AoVResourceReference {
    let archive: Archive
    let entry: Entry
    let sourceName: String
    let originalPath: String
}

private struct AoVModSource {
    let id: UUID
    let name: String
    let outerArchive: Archive
    let resourceArchive: Archive
    let innerResourcesURL: URL?
    let outerResourcesEntry: Entry?
    let outerSkinEntry: Entry?
    let isWrapper: Bool
    let resources: [String: AoVResourceReference]
    let actorEvidence: AoVActorEvidence
    let skinText: String
    let version: String?
    let uncompressedBytes: UInt64
}

enum AoVModArchiveMerger {
    private static let maximumInputs = 10
    private static let maximumInputBytes: UInt64 = 500 * 1024 * 1024
    private static let maximumEntries = 20_000
    private static let maximumSingleEntry: UInt64 = 512 * 1024 * 1024
    private static let maximumExpandedPerArchive: UInt64 = 1024 * 1024 * 1024
    private static let commonActionsPath = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
    private static let packerPath = "assetbundle/resourcepackerinfosetall.assetbundle"
    private static let verificationPath = "assetbundle/resourceverificationinfosetall.assetbundle"
    private static let resSkinPath = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"

    static func merge(_ archiveURLs: [URL]) throws -> AoVModMergeReport {
        guard archiveURLs.count >= 2 else { throw AoVModArchiveMergeError.tooFewInputs }
        guard archiveURLs.count <= maximumInputs else { throw AoVModArchiveMergeError.tooManyInputs }

        let totalInput = try archiveURLs.reduce(UInt64(0)) { partial, url in
            let size = (try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.uint64Value ?? 0
            let sum = partial.addingReportingOverflow(size)
            guard !sum.overflow, sum.partialValue <= maximumInputBytes else {
                throw AoVModArchiveMergeError.totalInputTooLarge
            }
            return sum.partialValue
        }
        _ = totalInput

        let tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-AOV-Merge-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempRoot) }

        var sources: [AoVModSource] = []
        for url in archiveURLs {
            sources.append(try makeSource(url, tempRoot: tempRoot))
        }

        let wrappers = sources.filter(\.isWrapper)
        guard !wrappers.isEmpty else { throw AoVModArchiveMergeError.noBaseWrapper }
        let base = wrappers.max { lhs, rhs in
            isLessComplete(lhs, rhs)
        }!
        guard base.outerResourcesEntry != nil,
              base.outerSkinEntry != nil else {
            throw AoVModArchiveMergeError.noBaseWrapper
        }
        for required in [commonActionsPath, packerPath, verificationPath] {
            guard base.resources[required] != nil else {
                throw AoVModArchiveMergeError.baseMissingResource(required)
            }
        }

        guard let baseVersion = base.version, !baseVersion.isEmpty else {
            throw AoVModArchiveMergeError.baseMissingResource("version.txt")
        }
        for source in sources {
            guard let version = source.version, !version.isEmpty else {
                throw AoVModArchiveMergeError.baseMissingResource("\(source.name)/version.txt")
            }
            guard version == baseVersion else {
                throw AoVModArchiveMergeError.versionMismatch(source.name, version, baseVersion)
            }
        }

        let ordered = [base] + sources.filter { $0.id != base.id }
        let allPaths = Set(ordered.flatMap { Array($0.resources.keys) }).sorted()
        var added: [String: AoVResourceReference] = [:]
        var replacements: [String: Data] = [:]
        var commonActionsFiles: [String] = []
        var mergedMSESTables: [String] = []
        var mergedIFixPatches: [String] = []
        var assetRefUnique: [String] = []
        var preservedWwiseBanks: [String] = []

        for path in allPaths {
            let providers = ordered.compactMap { source -> (AoVModSource, AoVResourceReference)? in
                guard let ref = source.resources[path] else { return nil }
                return (source, ref)
            }
            guard let baseProvider = providers.first else { continue }
            let baseDigest = try sha256(baseProvider.1)
            var distinct: [(AoVModSource, AoVResourceReference, Data)] = []
            var seenDigests: Set<String> = [baseDigest]
            for (source, reference) in providers.dropFirst() {
                let digest = try sha256(reference)
                if seenDigests.insert(digest).inserted {
                    distinct.append((source, reference, Data()))
                }
            }

            if distinct.isEmpty {
                if baseProvider.0.id != base.id {
                    let lowerPath = path.lowercased()
                    if lowerPath.hasSuffix(".bnk") {
                        added[path] = baseProvider.1
                        preservedWwiseBanks.append(path)
                        continue
                    }
                    if lowerPath.hasSuffix(".wem") {
                        throw AoVModArchiveMergeError.wwiseNotSupported(
                            "\(path): WEM rời chưa có adapter ghép cặp external-media đã xác minh."
                        )
                    }
                    if lowerPath == "thanos/heroskin_1.bytes" || lowerPath == "thanos/heroskin_2.bytes" {
                        throw AoVModArchiveMergeError.unsupportedConflict(
                            path,
                            "IFix chỉ có ở overlay, chưa có patch nền để remap index."
                        )
                    }
                    try classifyNewResource(path, sourceName: baseProvider.0.name)
                    added[path] = baseProvider.1
                    if isAssetReferencePath(path) { assetRefUnique.append(path) }
                }
                continue
            }

            let lowerPath = path.lowercased()
            if lowerPath.hasSuffix(".bnk") {
                throw AoVModArchiveMergeError.wwiseNotSupported(
                    "\(path): cùng đường dẫn BNK có payload khác nhau; không ghi đè bank."
                )
            }
            if lowerPath.hasSuffix(".wem") {
                throw AoVModArchiveMergeError.wwiseNotSupported(
                    "\(path): WEM rời chưa có adapter ghép cặp external-media đã xác minh."
                )
            }
            if lowerPath == "thanos/heroskin_1.bytes" || lowerPath == "thanos/heroskin_2.bytes" {
                guard baseProvider.0.id == base.id else {
                    throw AoVModArchiveMergeError.unsupportedConflict(
                        path,
                        "IFix chỉ có ở overlay, chưa có patch nền để remap index."
                    )
                }
                do {
                    var current = try readData(baseProvider.1)
                    for (_, reference, _) in distinct {
                        let overlay = try readData(reference)
                        let (merged, report) = try AoVIFixMerger.mergeResource(
                            current,
                            overlay,
                            resourcePath: path
                        )
                        guard report.verified else { throw AoVIFixError.roundTripFailure }
                        current = merged
                    }
                    replacements[path] = current
                    mergedIFixPatches.append(path)
                    continue
                } catch {
                    throw AoVModArchiveMergeError.ifixFailure(
                        "\(path): \(error.localizedDescription)"
                    )
                }
            }

            if path == commonActionsPath {
                var current = try readData(baseProvider.1)
                for (source, reference, _) in distinct {
                    let overlay = try readData(reference)
                    let inventory = try AoVCommonActionsMerger.inspectActorSkinIDs(
                        packageData: overlay,
                        actorIDs: source.actorEvidence.allActorIDs,
                        resourceName: path
                    )
                    guard !inventory.skinIDs.isEmpty else {
                        throw AoVModArchiveMergeError.commonActionsFailure(
                            "\(source.name) không có SkinOrAvatarList/skinId/skinIdArray thuộc Actor \(source.actorEvidence.allActorIDs.sorted())."
                        )
                    }
                    do {
                        let (merged, report) = try AoVCommonActionsMerger.mergeCommonActionsPackage(
                            basePackage: current,
                            overlayPackage: overlay,
                            resourceName: path,
                            specialSkinIDs: inventory.skinIDs
                        )
                        guard report.verified else {
                            throw AoVModArchiveMergeError.commonActionsFailure("round-trip verification chưa đạt.")
                        }
                        current = merged
                    } catch {
                        throw AoVModArchiveMergeError.commonActionsFailure(
                            "\(source.name): \(error.localizedDescription)"
                        )
                    }
                }
                replacements[path] = current
                commonActionsFiles.append(path)
                continue
            }

            if isAssetReferencePath(path) {
                throw AoVModArchiveMergeError.assetReferenceConflict(path)
            }
            if path == packerPath || path == verificationPath {
                throw AoVModArchiveMergeError.unsupportedConflict(
                    path,
                    "Packer/Verification catalog có payload khác; parser cập nhật dependency/checksum chưa được port sang iOS."
                )
            }
            if path.lowercased().hasSuffix(".assetbundle") {
                throw AoVModArchiveMergeError.assetBundleMetadataRequired(path)
            }
            if path.lowercased().hasSuffix(".pkg.bytes") {
                throw AoVModArchiveMergeError.unsupportedConflict(
                    path,
                    "Package nhị phân trùng đường dẫn nhưng chưa có adapter chuyên biệt; từ chối ghi đè."
                )
            }
            if path.lowercased().hasSuffix(".bytes") {
                guard baseProvider.0.id == base.id else {
                    throw AoVModArchiveMergeError.unsupportedConflict(
                        path,
                        "Tài nguyên chỉ xuất hiện ở các overlay, chưa có bản nền để so sánh."
                    )
                }
                do {
                    var current = try readData(baseProvider.1)
                    var accumulatedActorIDs = baseProvider.0.actorEvidence.allActorIDs
                    for (source, reference, _) in distinct {
                        let overlayData = try readData(reference)
                        let basePayload = try AoVBytesDecoder.decodeResource(current, resourceName: path)
                        let overlayPayload = try AoVBytesDecoder.decodeResource(overlayData, resourceName: path)
                        guard AoVMSESParser.isMSES(basePayload.data),
                              AoVMSESParser.isMSES(overlayPayload.data) else {
                            throw AoVMSESError.unsupportedTable("Payload không phải MSES v7.")
                        }
                        let (merged, report) = try AoVMSESParser.mergeResource(
                            baseResource: current,
                            overlayResource: overlayData,
                            resourcePath: path,
                            baseActorIDs: accumulatedActorIDs,
                            overlayActorIDs: source.actorEvidence.allActorIDs
                        )
                        guard report.verified else { throw AoVMSESError.encodeVerificationFailed }
                        current = merged
                        accumulatedActorIDs.formUnion(source.actorEvidence.allActorIDs)
                    }
                    replacements[path] = current
                    mergedMSESTables.append(path)
                    continue
                } catch {
                    throw AoVModArchiveMergeError.msesFailure(
                        "\(path): \(error.localizedDescription)"
                    )
                }
            }
            throw AoVModArchiveMergeError.unsupportedConflict(
                path,
                "Payload khác nhau nhưng chưa có adapter chuyên biệt; từ chối ghi đè."
            )
        }

        if !preservedWwiseBanks.isEmpty {
            do {
                let baseBanks = try base.resources.keys
                    .filter { $0.lowercased().hasSuffix(".bnk") }
                    .sorted()
                    .map { path in
                        AoVWwiseBankInput(
                            path: path,
                            data: try readData(
                                base.resources[path]!,
                                maxBytes: UInt64(AoVWwiseBNK.maximumBankBytes)
                            )
                        )
                    }
                let overlayBanks = try preservedWwiseBanks.sorted().map { path in
                    guard let reference = added[path] else {
                        throw AoVWwiseBNKError.invalidPath
                    }
                    return AoVWwiseBankInput(
                        path: path,
                        data: try readData(
                            reference,
                            maxBytes: UInt64(AoVWwiseBNK.maximumBankBytes)
                        )
                    )
                }
                let report = try AoVWwiseBNK.validateAppendOnly(
                    baseBanks: baseBanks,
                    overlayBanks: overlayBanks
                )
                guard report.verified else {
                    throw AoVWwiseBNKError.malformed("kiểm tra append-only chưa xác nhận.")
                }
            } catch {
                throw AoVModArchiveMergeError.wwiseNotSupported(error.localizedDescription)
            }
        }

        let innerOutputURL = tempRoot.appendingPathComponent("Resources-merged.zip")
        try writeMergedResources(
            base: base,
            replacements: replacements,
            added: added,
            outputURL: innerOutputURL,
            tempRoot: tempRoot
        )

        let exportDirectory = try FileManager.default.url(
            for: .documentDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("AKI AOV SKIN Exports", isDirectory: true)
        try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
        let finalURL = exportDirectory.appendingPathComponent(
            "AKI_AOV_SKIN_\(UUID().uuidString.prefix(8)).zip"
        )
        let stagedURL = tempRoot.appendingPathComponent("AKI-AOV-output.zip")
        try writeOuterArchive(base: base, innerURL: innerOutputURL, outputURL: stagedURL,
                              sources: ordered, replacements: replacements, tempRoot: tempRoot)
        try verifyArchive(stagedURL, label: "ZIP ngoài")
        try FileManager.default.moveItem(at: stagedURL, to: finalURL)

        var actorSummary: [String: [String]] = [:]
        for (index, source) in ordered.enumerated() {
            actorSummary["\(index + 1):\(source.name)"] = source.actorEvidence.allActorIDs.sorted()
        }
        return AoVModMergeReport(
            outputURL: finalURL,
            baseSource: base.name,
            sourceCount: ordered.count,
            actorIDsBySource: actorSummary,
            addedResources: added.count,
            replacedResources: replacements.count,
            commonActionsFiles: commonActionsFiles,
            mergedMSESTables: mergedMSESTables,
            mergedIFixPatches: mergedIFixPatches,
            preservedAssetReferenceSets: assetRefUnique,
            preservedWwiseBanks: preservedWwiseBanks.sorted(),
            structurallyValidated: true,
            gameCompatibilityTested: false
        )
    }

    private static func sourceCompleteness(_ source: AoVModSource) -> (Int, Int, UInt64) {
        let paths = Set(source.resources.keys)
        let required = [commonActionsPath, packerPath, verificationPath, resSkinPath, "version.txt"]
        return (required.filter { paths.contains($0) }.count, paths.count, source.uncompressedBytes)
    }

    private static func isLessComplete(_ lhs: AoVModSource, _ rhs: AoVModSource) -> Bool {
        let a = sourceCompleteness(lhs)
        let b = sourceCompleteness(rhs)
        if a.0 != b.0 { return a.0 < b.0 }
        if a.1 != b.1 { return a.1 < b.1 }
        return a.2 < b.2
    }

    private static func makeSource(_ url: URL, tempRoot: URL) throws -> AoVModSource {
        let outer: Archive
        do {
            outer = try Archive(url: url, accessMode: .read)
        } catch {
            throw AoVModArchiveMergeError.invalidArchive(url.lastPathComponent)
        }
        let outerEntries = try validatedEntries(outer, label: url.lastPathComponent)
        let resourcesEntries = outerEntries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("Resources.zip") == .orderedSame
        }
        let skinEntries = outerEntries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("DanhSachSkin.txt") == .orderedSame
        }
        guard resourcesEntries.count <= 1, skinEntries.count <= 1 else {
            throw AoVModArchiveMergeError.invalidArchive("\(url.lastPathComponent): wrapper lặp Resources.zip/DanhSachSkin.txt")
        }

        var innerURL: URL?
        let resourceArchive: Archive
        if let resourcesEntry = resourcesEntries.first {
            let dest = tempRoot.appendingPathComponent("inner-\(UUID().uuidString).zip")
            _ = try outer.extract(resourcesEntry, to: dest, bufferSize: 64 * 1024)
            innerURL = dest
            resourceArchive = try Archive(url: dest, accessMode: .read)
            _ = try validatedEntries(resourceArchive, label: "\(url.lastPathComponent)/Resources.zip")
        } else {
            resourceArchive = outer
        }

        var resources: [String: AoVResourceReference] = [:]
        var totalExpanded: UInt64 = 0
        try addResources(from: resourceArchive, sourceName: url.lastPathComponent, into: &resources, totalExpanded: &totalExpanded)

        // Wrappers can also contain loose Resources/ members alongside Resources.zip.
        if innerURL != nil {
            for entry in outerEntries where entry.type == .file {
                let path = entry.path.replacingOccurrences(of: "\\", with: "/")
                guard path.hasPrefix("Resources/") else { continue }
                let normalized = try normalizeResourcePath(path)
                if let existing = resources[normalized] {
                    if try sha256(existing) != sha256(AoVResourceReference(
                        archive: outer, entry: entry, sourceName: url.lastPathComponent,
                        originalPath: entry.path
                    )) {
                        throw AoVModArchiveMergeError.duplicateResourcePath(normalized)
                    }
                } else {
                    resources[normalized] = AoVResourceReference(
                        archive: outer, entry: entry, sourceName: url.lastPathComponent,
                        originalPath: entry.path
                    )
                }
            }
        }

        let actorEvidence = AoVActorDetector.detect(paths: Array(resources.keys))
        let skinText: String
        if let skinEntry = skinEntries.first {
            let data = try readEntry(outer, skinEntry, maxBytes: 8 * 1024 * 1024)
            skinText = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
        } else {
            skinText = ""
        }
        let version: String?
        if let versionRef = resources.first(where: { $0.key.caseInsensitiveCompare("version.txt") == .orderedSame })?.value {
            let data = try readData(versionRef, maxBytes: 1024 * 1024)
            version = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        } else {
            version = nil
        }

        return AoVModSource(
            id: UUID(),
            name: url.lastPathComponent,
            outerArchive: outer,
            resourceArchive: resourceArchive,
            innerResourcesURL: innerURL,
            outerResourcesEntry: resourcesEntries.first,
            outerSkinEntry: skinEntries.first,
            isWrapper: resourcesEntries.count == 1 && skinEntries.count == 1,
            resources: resources,
            actorEvidence: actorEvidence,
            skinText: skinText,
            version: version,
            uncompressedBytes: totalExpanded
        )
    }

    private static func addResources(
        from archive: Archive,
        sourceName: String,
        into resources: inout [String: AoVResourceReference],
        totalExpanded: inout UInt64
    ) throws {
        for entry in archive where entry.type == .file {
            let normalized = try normalizeResourcePath(entry.path)
            let sum = totalExpanded.addingReportingOverflow(entry.uncompressedSize)
            guard !sum.overflow, sum.partialValue <= 2 * 1024 * 1024 * 1024 else {
                throw AoVModArchiveMergeError.invalidArchive("\(sourceName): giải nén quá lớn")
            }
            totalExpanded = sum.partialValue
            let ref = AoVResourceReference(
                archive: archive, entry: entry, sourceName: sourceName,
                originalPath: entry.path
            )
            if let existing = resources[normalized] {
                if try sha256(existing) != sha256(ref) {
                    throw AoVModArchiveMergeError.duplicateResourcePath(normalized)
                }
            } else {
                resources[normalized] = ref
            }
        }
    }

    private static func validatedEntries(_ archive: Archive, label: String) throws -> [Entry] {
        var result: [Entry] = []
        var count = 0
        var expanded: UInt64 = 0
        var seen = Set<String>()
        for entry in archive {
            if entry.type == .directory { continue }
            guard entry.type == .file else {
                throw AoVModArchiveMergeError.unsafeArchivePath(entry.path)
            }
            count += 1
            guard count <= maximumEntries, entry.uncompressedSize <= maximumSingleEntry else {
                throw AoVModArchiveMergeError.invalidArchive("\(label): vượt giới hạn entry")
            }
            let path = entry.path.replacingOccurrences(of: "\\", with: "/")
            let parts = path.split(separator: "/", omittingEmptySubsequences: false)
            guard !path.hasPrefix("/"), !path.hasPrefix("~"),
                  !parts.contains(".."), !parts.contains("."),
                  !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                throw AoVModArchiveMergeError.unsafeArchivePath(entry.path)
            }
            guard seen.insert(path).inserted else {
                throw AoVModArchiveMergeError.duplicateResourcePath(path)
            }
            let sum = expanded.addingReportingOverflow(entry.uncompressedSize)
            guard !sum.overflow, sum.partialValue <= maximumExpandedPerArchive else {
                throw AoVModArchiveMergeError.invalidArchive("\(label): giải nén quá lớn")
            }
            expanded = sum.partialValue
            result.append(entry)
        }
        return result
    }

    private static func normalizeResourcePath(_ raw: String) throws -> String {
        let name = raw.replacingOccurrences(of: "\\", with: "/")
        let path = name as NSString
        if name.isEmpty || name.hasPrefix("/") || name.split(separator: "/").contains("..") ||
            name.split(separator: "/").contains(".") || name.unicodeScalars.contains(where: {
                CharacterSet.controlCharacters.contains($0)
            }) {
            throw AoVModArchiveMergeError.unsafeArchivePath(name)
        }
        var result = name
        if result.hasPrefix("Resources/") {
            let parts = result.split(separator: "/", omittingEmptySubsequences: false)
            if parts.count >= 3 {
                result = parts.dropFirst(2).joined(separator: "/")
            }
        }
        if result.lowercased().hasSuffix(".assetbundle") && !result.lowercased().hasPrefix("assetbundle/") {
            result = "assetbundle/" + result
        }
        _ = path
        return result
    }

    private static func classifyNewResource(_ path: String, sourceName: String) throws {
        if path.lowercased().hasSuffix(".assetbundle") {
            throw AoVModArchiveMergeError.assetBundleMetadataRequired(path)
        }
        if path == packerPath || path == verificationPath {
            throw AoVModArchiveMergeError.unsupportedConflict(path, "Không tự thêm Packer/Verification metadata đơn lẻ.")
        }
        if path.lowercased().hasSuffix(".bnk") || path.lowercased().hasSuffix(".wem") {
            throw AoVModArchiveMergeError.wwiseNotSupported(path)
        }
        if path.lowercased().hasPrefix("databin/"), path.lowercased().hasSuffix(".bytes") {
            throw AoVModArchiveMergeError.unsupportedConflict(
                path,
                "\(sourceName): tài nguyên Databin mới cần cập nhật Verification/Packer metadata trước khi thêm."
            )
        }
        _ = sourceName
    }

    private static func isAssetReferencePath(_ path: String) -> Bool {
        let normalized = path.replacingOccurrences(of: "\\", with: "/").lowercased()
        return normalized.contains("assetrefs/hero/") &&
            (normalized as NSString).lastPathComponent.hasSuffix("_assetref.bytes")
    }

    private static func sha256(_ reference: AoVResourceReference) throws -> String {
        var hasher = SHA256()
        _ = try reference.archive.extract(reference.entry, bufferSize: 64 * 1024, consumer: {
            hasher.update(data: $0)
        })
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func readEntry(_ archive: Archive, _ entry: Entry, maxBytes: UInt64) throws -> Data {
        guard entry.uncompressedSize <= maxBytes else {
            throw AoVModArchiveMergeError.invalidArchive("\(entry.path): vượt giới hạn đọc")
        }
        var data = Data()
        _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { data.append($0) })
        return data
    }

    private static func readData(_ reference: AoVResourceReference, maxBytes: UInt64 = 512 * 1024 * 1024) throws -> Data {
        try readEntry(reference.archive, reference.entry, maxBytes: maxBytes)
    }

    private static func copyEntry(
        _ entry: Entry,
        from source: Archive,
        to destination: Archive,
        path: String,
        tempRoot: URL
    ) throws {
        let scratch = tempRoot.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: scratch) }
        _ = try source.extract(entry, to: scratch, bufferSize: 64 * 1024)
        try destination.addEntry(with: path, fileURL: scratch, compressionMethod: .deflate, bufferSize: 64 * 1024)
    }

    private static func addData(_ data: Data, to destination: Archive, path: String, tempRoot: URL) throws {
        let scratch = tempRoot.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: scratch) }
        try data.write(to: scratch, options: .atomic)
        try destination.addEntry(with: path, fileURL: scratch, compressionMethod: .deflate, bufferSize: 64 * 1024)
    }

    private static func writeMergedResources(
        base: AoVModSource,
        replacements: [String: Data],
        added: [String: AoVResourceReference],
        outputURL: URL,
        tempRoot: URL
    ) throws {
        var prefix = ""
        for entry in base.resourceArchive where entry.type == .file {
            let path = entry.path.replacingOccurrences(of: "\\", with: "/")
            if path.hasPrefix("Resources/") {
                let parts = path.split(separator: "/", omittingEmptySubsequences: false)
                if parts.count >= 3 {
                    prefix = "Resources/\(parts[1])/"
                    break
                }
            }
        }

        do {
                let output = try Archive(url: outputURL, accessMode: .create)
            var emitted = Set<String>()
            for entry in base.resourceArchive where entry.type == .file {
                let relative = try normalizeResourcePath(entry.path)
                if let payload = replacements[relative] {
                    try addData(payload, to: output, path: entry.path, tempRoot: tempRoot)
                } else {
                    try copyEntry(entry, from: base.resourceArchive, to: output,
                                  path: entry.path, tempRoot: tempRoot)
                }
                emitted.insert(relative)
            }
            for (relative, reference) in added where !emitted.contains(relative) {
                let outputPath = prefix + relative
                try copyEntry(reference.entry, from: reference.archive, to: output,
                              path: outputPath, tempRoot: tempRoot)
                emitted.insert(relative)
            }
        }
        try verifyArchive(outputURL, label: "Resources.zip")
    }

    private static func writeOuterArchive(
        base: AoVModSource,
        innerURL: URL,
        outputURL: URL,
        sources: [AoVModSource],
        replacements: [String: Data],
        tempRoot: URL
    ) throws {
        guard let resourcesEntry = base.outerResourcesEntry,
              let skinEntry = base.outerSkinEntry else {
            throw AoVModArchiveMergeError.noBaseWrapper
        }
        let skinLines = mergeSkinLines(sources.map(\.skinText))
        let skinData = Data((skinLines.isEmpty ? "" : skinLines.joined(separator: "\n") + "\n").utf8)
        let output = try Archive(url: outputURL, accessMode: .create)
        for entry in base.outerArchive where entry.type == .file {
            if entry.path == resourcesEntry.path {
                try output.addEntry(with: entry.path, fileURL: innerURL,
                                    compressionMethod: .deflate, bufferSize: 64 * 1024)
            } else if entry.path == skinEntry.path {
                try addData(skinData, to: output, path: entry.path, tempRoot: tempRoot)
            } else if entry.path.replacingOccurrences(of: "\\", with: "/").hasPrefix("Resources/"),
                      let relative = try? normalizeResourcePath(entry.path),
                      let replacement = replacements[relative] {
                try addData(replacement, to: output, path: entry.path, tempRoot: tempRoot)
            } else {
                try copyEntry(entry, from: base.outerArchive, to: output,
                              path: entry.path, tempRoot: tempRoot)
            }
        }
    }

    private static func mergeSkinLines(_ texts: [String]) -> [String] {
        var seen = Set<String>()
        var result: [String] = []
        for text in texts {
            for raw in text.replacingOccurrences(of: "\r", with: "\n").components(separatedBy: "\n") {
                let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                    .drop(while: { "-*•· ".contains($0) })
                let value = String(line).trimmingCharacters(in: .whitespacesAndNewlines)
                guard !value.isEmpty, value.count <= 160 else { continue }
                let key = value.lowercased()
                if seen.insert(key).inserted { result.append(value) }
            }
        }
        return result
    }

    private static func verifyArchive(_ url: URL, label: String) throws {
        let archive = try Archive(url: url, accessMode: .read)
        for entry in archive where entry.type == .file {
            _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { _ in })
        }
        _ = label
    }
}
