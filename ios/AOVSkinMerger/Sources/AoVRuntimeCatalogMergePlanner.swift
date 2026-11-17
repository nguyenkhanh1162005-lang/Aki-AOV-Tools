import Foundation

enum AoVRuntimeCatalogMergeBlock: String, Codable, Hashable, Sendable {
    case duplicateCurrentPath
    case duplicateLegacyPath
    case duplicateLegacyVerificationPath
    case conflictingLegacyPath
    case changedExistingAssetBundle
    case assetBundlePayloadRequired
    case invalidParentChildIndex
    case duplicateCurrentVerificationPath
    case conflictingLegacyVerification
    case zipChecksumAlgorithmUnknown
    case databinChecksumAlgorithmUnknown
    case emptyPath
}

struct AoVRuntimeCatalogMergeIssue: Codable, Hashable, Sendable, Identifiable {
    let path: String
    let reason: AoVRuntimeCatalogMergeBlock
    let sourceArchives: [String]

    var id: String { "\(reason.rawValue)|\(path)|\(sourceArchives.joined(separator: ","))" }
}

struct AoVRuntimePackerMergePlan: Codable, Sendable {
    let candidateCatalog: AoVRuntimeResourcePackerCatalog?
    let addedPaths: [String]
    let updatedPaths: [String]
    let unchangedPaths: [String]
    let changedExistingPaths: [String]
    let addedParentChildLinkCount: Int
    let issues: [AoVRuntimeCatalogMergeIssue]

    var safeToWrite: Bool { candidateCatalog != nil && issues.isEmpty }
}

struct AoVRuntimeVerificationMergePlan: Codable, Sendable {
    let candidateCatalog: AoVRuntimeResourceVerificationCatalog?
    let addedZIPPaths: [String]
    let unchangedZIPPaths: [String]
    let updatedZIPPaths: [String]
    let updatedDatabinPaths: [String]
    let addedDatabinPaths: [String]
    let unchangedDatabinPaths: [String]
    let issues: [AoVRuntimeCatalogMergeIssue]

    var safeToWrite: Bool { candidateCatalog != nil && issues.isEmpty }
}

/// Conservative planners for the two actual class-114 catalogs. Packer paths
/// that already exist with different contents are blocked until their
/// underlying AssetBundles have been semantically merged. Verification ZIP
/// checksum values are preserved only when equal; this planner never guesses
/// the unknown ZIP or Databin checksum algorithms. Updated/new entries require
/// a checksum produced by an independently verified, version-specific adapter.
private struct AoVRuntimeMergeSourceValue<Value: Hashable> {
    let source: String
    let value: Value
}

enum AoVRuntimeCatalogMergePlanner {
    /// A Packer entry may be added or replaced only when a downstream asset
    /// bundle adapter has already produced the final bundle and verified its
    /// Packer metadata/checksum. Catalog-only copying is not enough.
    static func planPacker(
        current: AoVRuntimeResourcePackerCatalog,
        legacy: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)],
        verifiedFinalEntries: [String: AoVRuntimeResourcePackInfo] = [:]
    ) -> AoVRuntimePackerMergePlan {
        var issues: [AoVRuntimeCatalogMergeIssue] = []
        let currentByPath = Dictionary(grouping: current.allResourcePackInfo, by: \.pathInIFS)
        let currentDuplicatePaths = currentByPath
            .filter { $0.key.isEmpty || $0.value.count > 1 }
            .keys.sorted()
        if !currentDuplicatePaths.isEmpty {
            issues.append(contentsOf: currentDuplicatePaths.map {
                AoVRuntimeCatalogMergeIssue(
                    path: $0,
                    reason: $0.isEmpty ? .emptyPath : .duplicateCurrentPath,
                    sourceArchives: []
                )
            })
        }

        let legacyRows = legacy.flatMap { source in
            source.catalog.allResourcePackInfo.map {
                AoVRuntimeMergeSourceValue(source: source.source, value: $0)
            }
        }
        let legacyByPath = Dictionary(grouping: legacyRows, by: { $0.value.pathInIFS })
        var mergedEntries = current.allResourcePackInfo
        var mergedIndexByPath: [String: Int32] = [:]
        for (index, item) in mergedEntries.enumerated() {
            guard let encodedIndex = Int32(exactly: index) else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: item.pathInIFS,
                        reason: .invalidParentChildIndex,
                        sourceArchives: []
                    )
                )
                continue
            }
            // Duplicate paths were recorded above; retain the first index only
            // so malformed input cannot trap Dictionary(uniqueKeysWithValues:).
            if mergedIndexByPath[item.pathInIFS] == nil {
                mergedIndexByPath[item.pathInIFS] = encodedIndex
            }
        }
        var additions: [String] = []
        var updates: [String] = []
        var unchanged: [String] = []
        var changedExisting: [String] = []

        for path in legacyByPath.keys.sorted() {
            guard let rows = legacyByPath[path], let candidate = rows.first?.value else { continue }
            let sourceNames = Array(Set(rows.map(\.source))).sorted()
            let perSourceCounts = Dictionary(grouping: rows, by: \.source).mapValues(\.count)
            if perSourceCounts.values.contains(where: { $0 > 1 }) {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .duplicateLegacyPath,
                        sourceArchives: sourceNames
                    )
                )
                continue
            }
            guard !path.isEmpty else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .emptyPath,
                        sourceArchives: sourceNames
                    )
                )
                continue
            }

            let existingRows = currentByPath[path] ?? []
            if existingRows.count > 1 {
                continue // Already reported as a duplicate RES path above.
            }
            let distinctValues = Set(rows.map(\.value))
            let verifiedFinal = verifiedFinalEntries[path]

            if let verifiedFinal {
                guard verifiedFinal.pathInIFS == path else {
                    issues.append(
                        AoVRuntimeCatalogMergeIssue(
                            path: path,
                            reason: .assetBundlePayloadRequired,
                            sourceArchives: sourceNames
                        )
                    )
                    continue
                }
                if let existingIndexValue = mergedIndexByPath[path] {
                    let existingIndex = Int(existingIndexValue)
                    guard mergedEntries.indices.contains(existingIndex) else {
                        issues.append(
                            AoVRuntimeCatalogMergeIssue(
                                path: path,
                                reason: .invalidParentChildIndex,
                                sourceArchives: sourceNames
                            )
                        )
                        continue
                    }
                    if mergedEntries[existingIndex] != verifiedFinal {
                        mergedEntries[existingIndex] = verifiedFinal
                        updates.append(path)
                    } else {
                        unchanged.append(path)
                    }
                } else {
                    guard let newIndex = Int32(exactly: mergedEntries.count) else {
                        issues.append(
                            AoVRuntimeCatalogMergeIssue(
                                path: path,
                                reason: .invalidParentChildIndex,
                                sourceArchives: sourceNames
                            )
                        )
                        continue
                    }
                    mergedIndexByPath[path] = newIndex
                    mergedEntries.append(verifiedFinal)
                    additions.append(path)
                }
                continue
            }

            if distinctValues.count > 1 {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .conflictingLegacyPath,
                        sourceArchives: sourceNames
                    )
                )
                continue
            }

            if let existing = existingRows.first {
                if existing == candidate {
                    unchanged.append(path)
                } else {
                    changedExisting.append(path)
                    issues.append(
                        AoVRuntimeCatalogMergeIssue(
                            path: path,
                            reason: .changedExistingAssetBundle,
                            sourceArchives: sourceNames
                        )
                    )
                }
            } else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .assetBundlePayloadRequired,
                        sourceArchives: sourceNames
                    )
                )
            }
        }

        let indexesByPath = mergedIndexByPath

        var mergedLinks = current.allParentIndexToChild
        var linkSet = Set(mergedLinks)
        for (sourceName, catalog) in legacy.map({ ($0.source, $0.catalog) }) {
            for link in catalog.allParentIndexToChild {
                guard let parentIndex = Int(exactly: link.parentIdx),
                      let childIndex = Int(exactly: link.childIdx),
                      catalog.allResourcePackInfo.indices.contains(parentIndex),
                      catalog.allResourcePackInfo.indices.contains(childIndex),
                      let mappedParent = indexesByPath[catalog.allResourcePackInfo[parentIndex].pathInIFS],
                      let mappedChild = indexesByPath[catalog.allResourcePackInfo[childIndex].pathInIFS] else {
                    issues.append(
                        AoVRuntimeCatalogMergeIssue(
                            path: "parent-child",
                            reason: .invalidParentChildIndex,
                            sourceArchives: [sourceName]
                        )
                    )
                    continue
                }
                let mapped = AoVPackInfoMapItem(parentIdx: mappedParent, childIdx: mappedChild)
                if linkSet.insert(mapped).inserted {
                    mergedLinks.append(mapped)
                }
            }
        }

        // Validate links already present in the current RES catalog too.
        for link in current.allParentIndexToChild {
            guard current.allResourcePackInfo.indices.contains(Int(link.parentIdx)),
                  current.allResourcePackInfo.indices.contains(Int(link.childIdx)) else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: "current-parent-child",
                        reason: .invalidParentChildIndex,
                        sourceArchives: []
                    )
                )
                continue
            }
        }

        let candidate: AoVRuntimeResourcePackerCatalog?
        if issues.isEmpty {
            candidate = AoVRuntimeResourcePackerCatalog(
                name: current.name,
                version: current.version,
                svnRevision: current.svnRevision,
                setName: current.setName,
                allResourcePackInfo: mergedEntries,
                allParentIndexToChild: mergedLinks,
                trailingData: current.trailingData
            )
        } else {
            candidate = nil
        }

        return AoVRuntimePackerMergePlan(
            candidateCatalog: candidate,
            addedPaths: additions.sorted(),
            updatedPaths: updates.sorted(),
            unchangedPaths: unchanged.sorted(),
            changedExistingPaths: changedExisting,
            addedParentChildLinkCount: max(0, mergedLinks.count - current.allParentIndexToChild.count),
            issues: issues.sorted { $0.id < $1.id }
        )
    }

    static func planVerification(
        current: AoVRuntimeResourceVerificationCatalog,
        legacy: [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)],
        verifiedFinalDatabinChecksumValues: [String: Int64] = [:],
        verifiedFinalZIPChecksumValues: [String: Int64] = [:]
    ) -> AoVRuntimeVerificationMergePlan {
        var issues: [AoVRuntimeCatalogMergeIssue] = []

        let currentZIP = Dictionary(grouping: current.allZipVerificationInfo, by: \.pathInIFS)
        let currentDatabin = Dictionary(grouping: current.allDatabinVerificationInfo, by: \.pathInIFS)
        for (path, rows) in currentZIP where path.isEmpty || rows.count > 1 {
            issues.append(
                AoVRuntimeCatalogMergeIssue(
                    path: path,
                    reason: path.isEmpty ? .emptyPath : .duplicateCurrentVerificationPath,
                    sourceArchives: []
                )
            )
        }
        for (path, rows) in currentDatabin where path.isEmpty || rows.count > 1 {
            issues.append(
                AoVRuntimeCatalogMergeIssue(
                    path: path,
                    reason: path.isEmpty ? .emptyPath : .duplicateCurrentVerificationPath,
                    sourceArchives: []
                )
            )
        }

        let legacyZIPRows = legacy.flatMap { source in
            source.catalog.allZipVerificationInfo.map {
                AoVRuntimeMergeSourceValue(source: source.source, value: $0)
            }
        }
        let legacyDatabinRows = legacy.flatMap { source in
            source.catalog.allDatabinVerificationInfo.map {
                AoVRuntimeMergeSourceValue(source: source.source, value: $0)
            }
        }
        let legacyZIP = Dictionary(grouping: legacyZIPRows, by: { $0.value.pathInIFS })
        let legacyDatabin = Dictionary(grouping: legacyDatabinRows, by: { $0.value.pathInIFS })

        var zipEntries = current.allZipVerificationInfo
        var addedZIP: [AoVRuntimeVerificationEntry] = []
        var unchangedZIP: [String] = []
        var updatedZIP: [String] = []
        for path in legacyZIP.keys.sorted() {
            guard let rows = legacyZIP[path], let candidate = rows.first?.value else { continue }
            let perSourceCounts = Dictionary(grouping: rows, by: \.source).mapValues(\.count)
            if perSourceCounts.values.contains(where: { $0 > 1 }) {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .duplicateLegacyVerificationPath,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                continue
            }
            guard !path.isEmpty else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .emptyPath,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                continue
            }
            let values = Set(rows.map(\.value))
            if values.count > 1 {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .conflictingLegacyVerification,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                continue
            }

            guard let verifiedChecksum = verifiedFinalZIPChecksumValues[path] else {
                if let existingRows = currentZIP[path],
                   existingRows.count == 1,
                   existingRows[0].crcXor == candidate.crcXor {
                    unchangedZIP.append(path)
                } else {
                    issues.append(
                        AoVRuntimeCatalogMergeIssue(
                            path: path,
                            reason: .zipChecksumAlgorithmUnknown,
                            sourceArchives: Array(Set(rows.map(\.source))).sorted()
                        )
                    )
                }
                continue
            }

            let verifiedEntry = AoVRuntimeVerificationEntry(
                pathInIFS: path,
                crcXor: verifiedChecksum
            )
            if let existingRows = currentZIP[path] {
                guard existingRows.count == 1,
                      let index = zipEntries.firstIndex(where: { $0.pathInIFS == path }) else {
                    issues.append(
                        AoVRuntimeCatalogMergeIssue(
                            path: path,
                            reason: .duplicateCurrentVerificationPath,
                            sourceArchives: []
                        )
                    )
                    continue
                }
                if existingRows[0].crcXor == verifiedChecksum {
                    unchangedZIP.append(path)
                } else {
                    zipEntries[index] = verifiedEntry
                    updatedZIP.append(path)
                }
            } else {
                addedZIP.append(verifiedEntry)
            }
        }

        var databinEntries = current.allDatabinVerificationInfo
        var addedDatabin: [String] = []
        var updatedDatabin: [String] = []
        var unchangedDatabin: [String] = []
        for path in legacyDatabin.keys.sorted() {
            guard let rows = legacyDatabin[path], let candidate = rows.first?.value else { continue }
            let perSourceCounts = Dictionary(grouping: rows, by: \.source).mapValues(\.count)
            if perSourceCounts.values.contains(where: { $0 > 1 }) {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .duplicateLegacyVerificationPath,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                continue
            }
            guard !path.isEmpty else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .emptyPath,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                continue
            }
            let values = Set(rows.map(\.value))
            if values.count > 1, verifiedFinalDatabinChecksumValues[path] == nil {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .conflictingLegacyVerification,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                continue
            }

            guard let verifiedChecksum = verifiedFinalDatabinChecksumValues[path] else {
                if let existingRows = currentDatabin[path],
                   existingRows.count == 1,
                   existingRows[0].crcXor == candidate.crcXor {
                    unchangedDatabin.append(path)
                } else {
                    issues.append(
                        AoVRuntimeCatalogMergeIssue(
                            path: path,
                            reason: .databinChecksumAlgorithmUnknown,
                            sourceArchives: Array(Set(rows.map(\.source))).sorted()
                        )
                    )
                }
                continue
            }

            let verifiedEntry = AoVRuntimeVerificationEntry(
                pathInIFS: path,
                crcXor: verifiedChecksum
            )
            if let existingRows = currentDatabin[path] {
                guard existingRows.count == 1,
                      let index = databinEntries.firstIndex(where: { $0.pathInIFS == path }) else {
                    issues.append(
                        AoVRuntimeCatalogMergeIssue(
                            path: path,
                            reason: .duplicateCurrentVerificationPath,
                            sourceArchives: []
                        )
                    )
                    continue
                }
                if existingRows[0].crcXor == verifiedChecksum {
                    unchangedDatabin.append(path)
                } else {
                    databinEntries[index] = verifiedEntry
                    updatedDatabin.append(path)
                }
            } else {
                databinEntries.append(verifiedEntry)
                addedDatabin.append(path)
            }
        }

        let candidateCatalog: AoVRuntimeResourceVerificationCatalog?
        if issues.isEmpty {
            candidateCatalog = AoVRuntimeResourceVerificationCatalog(
                name: current.name,
                version: current.version,
                svnRevision: current.svnRevision,
                allZipVerificationInfo: zipEntries + addedZIP,
                allDatabinVerificationInfo: databinEntries,
                trailingData: current.trailingData
            )
        } else {
            candidateCatalog = nil
        }

        return AoVRuntimeVerificationMergePlan(
            candidateCatalog: candidateCatalog,
            addedZIPPaths: addedZIP.map(\.pathInIFS).sorted(),
            unchangedZIPPaths: unchangedZIP.sorted(),
            updatedZIPPaths: updatedZIP.sorted(),
            updatedDatabinPaths: updatedDatabin.sorted(),
            addedDatabinPaths: addedDatabin.sorted(),
            unchangedDatabinPaths: unchangedDatabin.sorted(),
            issues: issues.sorted { $0.id < $1.id }
        )
    }
}
