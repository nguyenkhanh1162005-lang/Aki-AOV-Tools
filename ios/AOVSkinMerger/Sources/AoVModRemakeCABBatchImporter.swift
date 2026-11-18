import Foundation
import ZIPFoundation

struct AoVModRemakeCABBatchedMergeReport: Sendable {
    let candidateFindings: [AoVRemakeCABCandidateFinding]
    let mergedResults: [AoVModRemakeAssetBundleMergeResult]
    let completedCandidateCount: Int
    let unchangedCandidateCount: Int
    let blockedCandidateFindings: [AoVRemakeCABCandidateFinding]
    let failures: [String]

    var candidateCount: Int {
        candidateFindings.filter { $0.status == .candidate }.count
    }

    var importedObjectCount: Int {
        mergedResults.reduce(0) { $0 + $1.importedObjectCount }
    }

    var isComplete: Bool {
        candidateCount > 0 &&
            completedCandidateCount == candidateCount &&
            failures.isEmpty
    }
}

private struct AoVRemakeCABGroupKey: Hashable {
    let resourcePath: String
    let nodePath: String
}

private struct AoVRemakeCABNodePayload {
    let data: Data
    let summary: AoVSerializedFileSummary
}

private struct AoVRemakeCABPreparedInput {
    let finding: AoVRemakeCABCandidateFinding
    let serializedFile: Data
    let record: AoVSerializedAssetBundleRecord
}

enum AoVModRemakeCABBatchImporter {
    private static let maximumArchiveBytes: UInt64 = 512 * 1024 * 1024
    private static let maximumInputCount = 10

    /// Finds class-142 matches from the selected archives and runs the existing
    /// TypeTree/PPtr object-closure importer against each destination CAB.
    /// It creates only in-memory AssetBundle candidates; it does not write RES
    /// catalogs or invent Packer/Verification checksums.
    static func mergeMatchedCandidates(
        baseRESURL: URL,
        legacyMODURLs: [URL],
        verifiedExternalTargetFiles: [String: Data] = [:]
    ) throws -> AoVModRemakeCABBatchedMergeReport {
        guard !legacyMODURLs.isEmpty else {
            throw AoVModRemakeError.noLegacyMods
        }
        guard legacyMODURLs.count <= maximumInputCount else {
            throw AoVModRemakeError.tooManyLegacyMods
        }
        let sourceNames = legacyMODURLs.map { $0.lastPathComponent }
        guard Set(sourceNames).count == sourceNames.count else {
            throw AoVModRemakeExporterError.catalogMergeFailed(
                "Có nhiều MOD cũ trùng tên file; cần tên riêng để ánh xạ nguồn CAB."
            )
        }

        // This validates version.txt and the runtime catalog build markers
        // before any CAB import is attempted.
        _ = try AoVModRemakeAnalyzer.loadRuntimeCatalogInputs(
            baseRESURL: baseRESURL,
            legacyMODURLs: legacyMODURLs
        )

        let tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-AOV-CAB-BATCH-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempRoot) }

        let baseScanURL = try resolveResourceArchive(baseRESURL, tempRoot: tempRoot)
        var legacyScanURLs: [URL] = []
        for url in legacyMODURLs {
            legacyScanURLs.append(try resolveResourceArchive(url, tempRoot: tempRoot))
        }

        guard let baseInventory = try ArchiveScanner.analyze([baseScanURL]).archives.first else {
            throw AoVModRemakeError.invalidArchive(baseRESURL.lastPathComponent)
        }
        let legacyInventories = try legacyScanURLs.map { url -> ArchiveInventory in
            guard let inventory = try ArchiveScanner.analyze([url]).archives.first else {
                throw AoVModRemakeError.invalidArchive(url.lastPathComponent)
            }
            return inventory
        }

        var archiveByName: [String: Archive] = [:]
        let baseArchive = try Archive(url: baseScanURL, accessMode: .read)
        archiveByName[baseScanURL.lastPathComponent] = baseArchive
        for url in legacyScanURLs {
            guard archiveByName[url.lastPathComponent] == nil else {
                throw AoVModRemakeExporterError.catalogMergeFailed(
                    "Tên archive sau khi mở Resources.zip bị trùng: \(url.lastPathComponent)."
                )
            }
            archiveByName[url.lastPathComponent] = try Archive(url: url, accessMode: .read)
        }

        let findings = AoVModRemakeAnalyzer.buildCABCandidates(
            base: baseInventory.unityFSBundles,
            legacy: legacyInventories
        )
        let candidateFindings = findings.filter { $0.status == .candidate }
        let blockedFindings = findings.filter { $0.status != .candidate }
        guard !candidateFindings.isEmpty else {
            return AoVModRemakeCABBatchedMergeReport(
                candidateFindings: findings,
                mergedResults: [],
                completedCandidateCount: 0,
                unchangedCandidateCount: 0,
                blockedCandidateFindings: blockedFindings,
                failures: []
            )
        }

        var nodeCache: [String: AoVRemakeCABNodePayload] = [:]
        var bundleCache: [String: Data] = [:]

        func cacheKey(_ archiveName: String, _ resourcePath: String, _ nodePath: String) -> String {
            "\(archiveName)\u{1F}\(resourcePath)\u{1F}\(nodePath)"
        }

        func readBundle(_ archiveName: String, resourcePath: String) throws -> Data {
            let key = "\(archiveName)\u{1F}\(resourcePath)"
            if let cached = bundleCache[key] { return cached }
            guard let archive = archiveByName[archiveName] else {
                throw AoVModRemakeExporterError.invalidBaseArchive(archiveName)
            }
            let matches = archive.filter {
                $0.type == .file && normalizedPath($0.path) == normalizedPath(resourcePath)
            }
            guard matches.count == 1, let entry = matches.first else {
                throw AoVModRemakeExporterError.integrityFailure(
                    "Không tìm thấy duy nhất AssetBundle \(archiveName)/\(resourcePath)."
                )
            }
            guard entry.uncompressedSize > 0, entry.uncompressedSize <= maximumArchiveBytes else {
                throw AoVModRemakeExporterError.resourceTooLarge(resourcePath)
            }
            var data = Data()
            data.reserveCapacity(Int(entry.uncompressedSize))
            _ = try archive.extract(entry, bufferSize: 64 * 1024) { chunk in
                data.append(chunk)
            }
            guard UInt64(data.count) == entry.uncompressedSize else {
                throw AoVModRemakeExporterError.integrityFailure(
                    "Kích thước AssetBundle \(resourcePath) không khớp ZIP entry."
                )
            }
            bundleCache[key] = data
            return data
        }

        func readNode(
            archiveName: String,
            resourcePath: String,
            nodePath: String
        ) throws -> AoVRemakeCABNodePayload {
            let key = cacheKey(archiveName, resourcePath, nodePath)
            if let cached = nodeCache[key] { return cached }
            let bundle = try readBundle(archiveName, resourcePath: resourcePath)
            let info = try AoVUnityFSParser.inspect(bundle)
            guard info.totalUncompressedBytes <= maximumArchiveBytes else {
                throw AoVUnityFSAssetBundleMergeExecutorError.expandedArchiveTooLarge
            }
            let nodes = try AoVUnityFSParser.extractNodes(
                bundle,
                maximumExpandedBytes: maximumArchiveBytes,
                maximumNodeCount: max(info.nodes.count, 1)
            )
            let matches = nodes.filter { $0.path == nodePath }
            guard matches.count == 1, let node = matches.first else {
                throw AoVUnityFSAssetBundleMergeExecutorError.nodeNotFound(nodePath)
            }
            let summary = try AoVSerializedFileParser.inspect(node.data)
            guard summary.fileSize == UInt64(node.data.count) else {
                throw AoVModRemakeExporterError.integrityFailure(
                    "SerializedFile node \(nodePath) có fileSize không khớp."
                )
            }
            let payload = AoVRemakeCABNodePayload(data: node.data, summary: summary)
            nodeCache[key] = payload
            return payload
        }

        func auditedExternalTargets(
            for references: [AoVSerializedFileExternalReference]
        ) throws -> [String: Data] {
            var result = verifiedExternalTargetFiles
            for reference in references {
                let targetKey = AoVAssetBundleCatalogMerger.externalTargetKey(
                    guidHex: reference.guidHex,
                    path: reference.path
                )
                if result[targetKey] != nil { continue }

                let normalizedReference = normalizedPath(reference.path)
                guard !normalizedReference.isEmpty else { continue }

                var matches: [(archiveName: String, resourcePath: String, nodePath: String)] = []
                for bundle in baseInventory.unityFSBundles {
                    let exactNodeMatches = bundle.catalogNodeSamples.filter {
                        $0.serializedFile != nil &&
                            normalizedPath($0.path) == normalizedReference
                    }
                    if !exactNodeMatches.isEmpty {
                        matches.append(contentsOf: exactNodeMatches.map {
                            (bundle.archiveName, bundle.path, $0.path)
                        })
                        continue
                    }

                    // A resource-path reference is accepted only when that
                    // AssetBundle contains exactly one parsed SerializedFile.
                    if normalizedPath(bundle.path) == normalizedReference {
                        let serializedNodes = bundle.catalogNodeSamples.filter {
                            $0.serializedFile != nil
                        }
                        if serializedNodes.count == 1, let onlyNode = serializedNodes.first {
                            matches.append((bundle.archiveName, bundle.path, onlyNode.path))
                        }
                    }
                }

                let uniqueMatches = Dictionary(
                    matches.map { ("\\($0.archiveName)|\\($0.resourcePath)|\\($0.nodePath)", $0) },
                    uniquingKeysWith: { first, _ in first }
                )
                guard uniqueMatches.count == 1,
                      let match = uniqueMatches.values.first else {
                    // Missing or ambiguous target is intentionally not inferred.
                    continue
                }
                let target = try readNode(
                    archiveName: match.archiveName,
                    resourcePath: match.resourcePath,
                    nodePath: match.nodePath
                )
                result[targetKey] = target.data
            }
            return result
        }

        var preparedByGroup: [AoVRemakeCABGroupKey: [AoVRemakeCABPreparedInput]] = [:]
        var failures: [String] = []

        for finding in candidateFindings {
            guard let destinationPath = finding.destinationResourcePath,
                  let destinationNode = finding.destinationNodePath,
                  let destinationPathID = finding.destinationObjectPathID else {
                failures.append("\(finding.bundleName): thiếu thông tin CAB đích trong candidate.")
                continue
            }
            do {
                let sourceNode = try readNode(
                    archiveName: finding.sourceArchive,
                    resourcePath: finding.sourceResourcePath,
                    nodePath: finding.sourceNodePath
                )
                let sourceRecords = sourceNode.summary.assetBundleRecords.filter {
                    $0.pathID == finding.sourceObjectPathID
                }
                guard sourceRecords.count == 1, let sourceRecord = sourceRecords.first else {
                    throw AoVUnityFSAssetBundleMergeExecutorError.objectNotFound(
                        finding.sourceObjectPathID
                    )
                }
                let sourceIdentity = identity(for: sourceRecord)
                guard sourceIdentity == finding.bundleName else {
                    throw AoVModRemakeExporterError.integrityFailure(
                        "Bundle identity nguồn đổi sau lần quét: \(finding.bundleName)."
                    )
                }

                let destination = try readNode(
                    archiveName: baseScanURL.lastPathComponent,
                    resourcePath: destinationPath,
                    nodePath: destinationNode
                )
                let destinationRecords = destination.summary.assetBundleRecords.filter {
                    $0.pathID == destinationPathID
                }
                guard destinationRecords.count == 1,
                      identity(for: destinationRecords[0]) == finding.bundleName else {
                    throw AoVModRemakeExporterError.integrityFailure(
                        "CAB đích không còn khớp identity/pathID \(destinationPathID)."
                    )
                }

                let key = AoVRemakeCABGroupKey(
                    resourcePath: destinationPath,
                    nodePath: destinationNode
                )
                preparedByGroup[key, default: []].append(
                    AoVRemakeCABPreparedInput(
                        finding: finding,
                        serializedFile: sourceNode.data,
                        record: sourceRecord
                    )
                )
            } catch {
                failures.append("\(finding.bundleName) · \(finding.sourceArchive): \(error.localizedDescription)")
            }
        }

        let availableCurrentBundleNames = Set(
            baseInventory.unityFSBundles
                .flatMap(\.catalogNodeSamples)
                .compactMap(\.serializedFile)
                .flatMap(\.assetBundleRecords)
                .flatMap { record in
                    [record.assetBundleName, record.name]
                        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
                        .filter { !$0.isEmpty }
                }
        )

        var mergedResults: [AoVModRemakeAssetBundleMergeResult] = []
        var completedCandidateIDs = Set<String>()
        var unchangedCandidateCount = 0

        for key in preparedByGroup.keys.sorted(by: {
            if $0.resourcePath != $1.resourcePath { return $0.resourcePath < $1.resourcePath }
            return $0.nodePath < $1.nodePath
        }) {
            guard let prepared = preparedByGroup[key]?.sorted(by: {
                if $0.finding.sourceArchive != $1.finding.sourceArchive {
                    return $0.finding.sourceArchive < $1.finding.sourceArchive
                }
                return $0.finding.sourceObjectPathID < $1.finding.sourceObjectPathID
            }), !prepared.isEmpty else { continue }

            // Never let iteration order decide which MOD wins an overlapping
            // container key for the same destination class-142 object.
            var keysByDestinationObject: [Int64: Set<String>] = [:]
            var overlapSamples: [String] = []
            for item in prepared {
                guard let destinationObjectID = item.finding.destinationObjectPathID else { continue }
                let sourceKeys = Set(item.record.containerEntries.map(\.key))
                let existing = keysByDestinationObject[destinationObjectID] ?? []
                let overlap = existing.intersection(sourceKeys)
                if !overlap.isEmpty {
                    overlapSamples.append(
                        "\(item.finding.bundleName): \(overlap.sorted().prefix(4).joined(separator: ", "))"
                    )
                }
                keysByDestinationObject[destinationObjectID, default: []].formUnion(sourceKeys)
            }
            guard overlapSamples.isEmpty else {
                failures.append(
                    "\(key.resourcePath): nhiều MOD cùng sửa container key; đã chặn để không tự chọn MOD thắng: \(overlapSamples.joined(separator: " · "))."
                )
                continue
            }

            do {
                let destinationBundle = try readBundle(
                    baseScanURL.lastPathComponent,
                    resourcePath: key.resourcePath
                )
                let destinationNode = try readNode(
                    archiveName: baseScanURL.lastPathComponent,
                    resourcePath: key.resourcePath,
                    nodePath: key.nodePath
                )
                var needsMutation = false
                for item in prepared {
                    guard let destinationPathID = item.finding.destinationObjectPathID,
                          let currentRecord = destinationNode.summary.assetBundleRecords.first(where: {
                              $0.pathID == destinationPathID
                          }) else {
                        throw AoVUnityFSAssetBundleMergeExecutorError.objectNotFound(
                            item.finding.destinationObjectPathID ?? 0
                        )
                    }
                    let additions = Set(item.record.containerEntries.map(\.key))
                        .subtracting(currentRecord.containerEntries.map(\.key))
                    let replacements = try AoVUnityFSAssetBundleMergeExecutor.changedContainerKeys(
                        legacyRecord: item.record,
                        currentRecord: currentRecord,
                        sourceData: item.serializedFile,
                        destinationData: destinationNode.data,
                        sourceSummary: try AoVSerializedFileParser.inspect(item.serializedFile),
                        destinationSummary: destinationNode.summary
                    )
                    let newDependencies = Set(item.record.dependencies)
                        .subtracting(currentRecord.dependencies)
                    if !additions.isEmpty || !replacements.isEmpty || !newDependencies.isEmpty {
                        needsMutation = true
                    }
                }

                guard needsMutation else {
                    unchangedCandidateCount += prepared.count
                    completedCandidateIDs.formUnion(prepared.map { $0.finding.id })
                    continue
                }

                let legacyInputs = prepared.map { item in
                    AoVAssetBundleLegacyObjectImportInput(
                        source: item.finding.sourceArchive,
                        serializedFile: item.serializedFile,
                        legacyObjectPathID: item.finding.sourceObjectPathID,
                        destinationObjectPathID: item.finding.destinationObjectPathID ?? 0
                    )
                }
                let externalTargets = try auditedExternalTargets(
                    for: destinationNode.summary.externalReferences
                )
                let merged = try AoVModRemakeExporter.mergeAssetBundleResource(
                    resourcePath: key.resourcePath,
                    runtimePath: runtimePath(for: key.resourcePath),
                    baseAssetBundle: destinationBundle,
                    serializedFileNodePath: key.nodePath,
                    legacyInputs: legacyInputs,
                    availableCurrentBundleNames: availableCurrentBundleNames,
                    verifiedExternalTargetFiles: externalTargets
                )
                guard merged.roundTripVerified else {
                    throw AoVModRemakeExporterError.integrityFailure(
                        "CAB \(key.resourcePath) không qua round-trip."
                    )
                }
                mergedResults.append(merged)
                completedCandidateIDs.formUnion(prepared.map { $0.finding.id })
            } catch {
                failures.append("\(key.resourcePath): \(error.localizedDescription)")
            }
        }

        let processedCount = completedCandidateIDs.count
        return AoVModRemakeCABBatchedMergeReport(
            candidateFindings: findings,
            mergedResults: mergedResults,
            completedCandidateCount: processedCount,
            unchangedCandidateCount: unchangedCandidateCount,
            blockedCandidateFindings: blockedFindings,
            failures: failures
        )
    }

    private static func resolveResourceArchive(_ inputURL: URL, tempRoot: URL) throws -> URL {
        let archive: Archive
        do {
            archive = try Archive(url: inputURL, accessMode: .read)
        } catch {
            throw AoVModRemakeError.invalidArchive(inputURL.lastPathComponent)
        }
        let matches = archive.filter {
            $0.type == .file &&
                ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("Resources.zip") == .orderedSame
        }
        guard matches.count <= 1 else {
            throw AoVModRemakeError.multipleResourceArchives(inputURL.lastPathComponent)
        }
        guard let nested = matches.first else { return inputURL }
        guard nested.uncompressedSize <= maximumArchiveBytes else {
            throw AoVModRemakeError.resourceArchiveTooLarge(inputURL.lastPathComponent)
        }
        let destination = tempRoot.appendingPathComponent("resources-\(UUID().uuidString).zip")
        do {
            _ = try archive.extract(nested, to: destination, bufferSize: 64 * 1024)
            _ = try Archive(url: destination, accessMode: .read)
        } catch {
            try? FileManager.default.removeItem(at: destination)
            throw AoVModRemakeError.invalidArchive("\(inputURL.lastPathComponent)/Resources.zip")
        }
        return destination
    }

    private static func normalizedPath(_ path: String) -> String {
        path.replacingOccurrences(of: "\\", with: "/")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func identity(for record: AoVSerializedAssetBundleRecord) -> String {
        let assetBundleName = record.assetBundleName.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = assetBundleName.isEmpty ? record.name : assetBundleName
        return name.replacingOccurrences(of: "\\", with: "/")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
    }

    private static func runtimePath(for archivePath: String) -> String {
        let normalized = normalizedPath(archivePath)
        var components = normalized.split(separator: "/").map(String.init)
        let versionPattern = #"^\d+(?:\.\d+){1,3}$"#
        if components.first == "Resources" {
            components.removeFirst()
        }
        if let first = components.first,
           first.range(of: versionPattern, options: .regularExpression) != nil {
            components.removeFirst()
        }
        return components.joined(separator: "/")
    }
}
