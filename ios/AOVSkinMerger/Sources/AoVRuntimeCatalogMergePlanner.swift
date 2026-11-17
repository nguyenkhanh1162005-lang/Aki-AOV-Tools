import Foundation

enum AoVRuntimeCatalogMergeBlock: String, Codable, Hashable, Sendable {
    case duplicateCurrentPath
    case conflictingLegacyPath
    case changedExistingAssetBundle
    case invalidParentChildIndex
    case duplicateCurrentVerificationPath
    case conflictingLegacyVerification
    case zipChecksumAlgorithmUnknown
    case databinPayloadRequired
    case emptyPath
}

struct AoVRuntimeCatalogMergeIssue: Codable, Hashable, Sendable, Identifiable {
    let path: String
    let reason: AoVRuntimeCatalogMergeBlock
    let sourceArchives: [String]

    var id: String { "\(reason.rawValue)|\(path)|\(sourceArchives.joined(separator: ","))" }
}

struct AoVRuntimePackerMergePlan: Sendable {
    let candidateCatalog: AoVRuntimeResourcePackerCatalog?
    let addedPaths: [String]
    let unchangedPaths: [String]
    let changedExistingPaths: [String]
    let addedParentChildLinkCount: Int
    let issues: [AoVRuntimeCatalogMergeIssue]

    var safeToWrite: Bool { candidateCatalog != nil && issues.isEmpty }
}

struct AoVRuntimeVerificationMergePlan: Sendable {
    let candidateCatalog: AoVRuntimeResourceVerificationCatalog?
    let addedZIPPaths: [String]
    let unchangedZIPPaths: [String]
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
/// the unknown 64-bit ZIP checksum algorithm. Databin values can be recalculated
/// from the final output bytes using the verified CRC-32 routine.
private struct AoVRuntimeMergeSourceValue<Value: Hashable> {
    let source: String
    let value: Value
}

enum AoVRuntimeCatalogMergePlanner {
    static func planPacker(
        current: AoVRuntimeResourcePackerCatalog,
        legacy: [(source: String, catalog: AoVRuntimeResourcePackerCatalog)]
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
        let changedExisting = legacyByPath.compactMap { path, rows -> String? in
            guard !path.isEmpty else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .emptyPath,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                return nil
            }
            let distinctValues = Set(rows.map(\.value))
            guard distinctValues.count <= 1 else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .conflictingLegacyPath,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                return nil
            }
            guard let base = currentByPath[path]?.first else { return nil }
            guard base != distinctValues.first else { return nil }
            issues.append(
                AoVRuntimeCatalogMergeIssue(
                    path: path,
                    reason: .changedExistingAssetBundle,
                    sourceArchives: Array(Set(rows.map(\.source))).sorted()
                )
            )
            return path
        }.sorted()

        var additions: [AoVRuntimeResourcePackInfo] = []
        var unchanged: [String] = []
        for path in legacyByPath.keys.sorted() {
            guard let rows = legacyByPath[path],
                  let candidate = rows.first?.value else { continue }
            if currentByPath[path] != nil {
                if currentByPath[path]?.first == candidate {
                    unchanged.append(path)
                }
                continue
            }
            if Set(rows.map(\.value)).count == 1 {
                additions.append(candidate)
            }
        }

        var indexesByPath: [String: Int32] = [:]
        var mergedEntries = current.allResourcePackInfo
        for (index, item) in mergedEntries.enumerated() {
            guard let intIndex = Int32(exactly: index) else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: item.pathInIFS,
                        reason: .invalidParentChildIndex,
                        sourceArchives: []
                    )
                )
                continue
            }
            indexesByPath[item.pathInIFS] = intIndex
        }
        for item in additions {
            guard let index = Int32(exactly: mergedEntries.count) else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: item.pathInIFS,
                        reason: .invalidParentChildIndex,
                        sourceArchives: []
                    )
                )
                continue
            }
            indexesByPath[item.pathInIFS] = index
            mergedEntries.append(item)
        }

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
            addedPaths: additions.map(\.pathInIFS).sorted(),
            unchangedPaths: unchanged.sorted(),
            changedExistingPaths: changedExisting,
            addedParentChildLinkCount: max(0, mergedLinks.count - current.allParentIndexToChild.count),
            issues: issues.sorted { $0.id < $1.id }
        )
    }

    static func planVerification(
        current: AoVRuntimeResourceVerificationCatalog,
        legacy: [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)],
        finalDatabinPayloads: [String: Data] = [:]
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

        var addedZIP: [AoVRuntimeVerificationEntry] = []
        var unchangedZIP: [String] = []
        for path in legacyZIP.keys.sorted() {
            guard let rows = legacyZIP[path], let candidate = rows.first?.value else { continue }
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
            if let existingRows = currentZIP[path] {
                if existingRows.count == 1, existingRows[0].crcXor == candidate.crcXor {
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
            } else {
                addedZIP.append(candidate)
            }
        }

        var databinEntries = current.allDatabinVerificationInfo
        var addedDatabin: [String] = []
        var updatedDatabin: [String] = []
        var unchangedDatabin: [String] = []
        for path in legacyDatabin.keys.sorted() {
            guard let rows = legacyDatabin[path], let candidate = rows.first?.value else { continue }
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
            if values.count > 1, finalDatabinPayloads[path] == nil {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .conflictingLegacyVerification,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
                continue
            }

            if let payload = finalDatabinPayloads[path] {
                let checksum = AoVRuntimeCatalogChecksum.verificationDatabinCRC32(payload)
                if let index = databinEntries.firstIndex(where: { $0.pathInIFS == path }) {
                    if databinEntries[index].crcXor != checksum {
                        databinEntries[index] = AoVRuntimeVerificationEntry(pathInIFS: path, crcXor: checksum)
                        updatedDatabin.append(path)
                    } else {
                        unchangedDatabin.append(path)
                    }
                } else {
                    databinEntries.append(AoVRuntimeVerificationEntry(pathInIFS: path, crcXor: checksum))
                    addedDatabin.append(path)
                }
                continue
            }

            if let existingRows = currentDatabin[path] {
                if existingRows.count == 1, existingRows[0].crcXor == candidate.crcXor {
                    unchangedDatabin.append(path)
                } else {
                    issues.append(
                        AoVRuntimeCatalogMergeIssue(
                            path: path,
                            reason: .databinPayloadRequired,
                            sourceArchives: Array(Set(rows.map(\.source))).sorted()
                        )
                    )
                }
            } else {
                issues.append(
                    AoVRuntimeCatalogMergeIssue(
                        path: path,
                        reason: .databinPayloadRequired,
                        sourceArchives: Array(Set(rows.map(\.source))).sorted()
                    )
                )
            }
        }

        let candidateCatalog: AoVRuntimeResourceVerificationCatalog?
        if issues.isEmpty {
            candidateCatalog = AoVRuntimeResourceVerificationCatalog(
                name: current.name,
                version: current.version,
                svnRevision: current.svnRevision,
                allZipVerificationInfo: current.allZipVerificationInfo + addedZIP,
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
            updatedDatabinPaths: updatedDatabin.sorted(),
            addedDatabinPaths: addedDatabin.sorted(),
            unchangedDatabinPaths: unchangedDatabin.sorted(),
            issues: issues.sorted { $0.id < $1.id }
        )
    }
}
