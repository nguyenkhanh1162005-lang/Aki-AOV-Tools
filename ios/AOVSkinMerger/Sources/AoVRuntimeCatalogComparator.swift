import Foundation

enum AoVRuntimeCatalogDifference: String, Codable, Sendable {
    case identical
    case changed
    case legacyOnly
    case currentOnly
    case duplicateCurrent
    case conflictingLegacy
}

struct AoVRuntimeCatalogEntryFinding: Codable, Sendable, Identifiable {
    let path: String
    let difference: AoVRuntimeCatalogDifference
    let currentDetail: String?
    let legacyDetails: [String]
    var id: String { path }
}

struct AoVRuntimeCatalogSectionReport: Codable, Sendable {
    let title: String
    let currentCatalogVersions: [String]
    let legacyCatalogVersions: [String]
    let currentEntryCount: Int
    let legacyEntryCount: Int
    let uniqueLegacyPathCount: Int
    let identicalEntryCount: Int
    let changedEntryCount: Int
    let legacyOnlyEntryCount: Int
    let currentOnlyEntryCount: Int
    let duplicateCurrentPathCount: Int
    let duplicateLegacyPathCount: Int
    let conflictingLegacyPathCount: Int
    let invalidReferenceCount: Int
    let findings: [AoVRuntimeCatalogEntryFinding]
}

struct AoVRuntimeCatalogComparisonReport: Codable, Sendable {
    let packerResources: AoVRuntimeCatalogSectionReport
    let packerParentChildLinks: AoVRuntimeCatalogSectionReport
    let verificationZIP: AoVRuntimeCatalogSectionReport
    let verificationDatabin: AoVRuntimeCatalogSectionReport
}

private struct AoVRuntimeCatalogSourceValue<Value: Hashable> {
    let source: String
    let value: Value
}

enum AoVRuntimeCatalogComparator {
    private static let maximumFindings = 160

    static func compare(
        current inventory: ArchiveInventory,
        legacy inventories: [ArchiveInventory]
    ) -> AoVRuntimeCatalogComparisonReport {
        let currentPacker = packerCatalogs(in: inventory)
        let legacyPacker = packerCatalogSources(in: inventories)
        let currentVerification = verificationCatalogs(in: inventory)
        let legacyVerification = verificationCatalogSources(in: inventories)

        let currentPackerEntries = currentPacker.flatMap(\.allResourcePackInfo)
        let legacyPackerEntries = legacyPacker.flatMap { source in
            source.catalog.allResourcePackInfo.map {
                AoVRuntimeCatalogSourceValue(source: source.source, value: $0)
            }
        }
        let packerResources = makeSection(
            title: "Packer resources",
            current: currentPackerEntries,
            legacy: legacyPackerEntries,
            key: { $0.pathInIFS },
            detail: {
                "pkgType=\($0.pkgType), crcXor=\($0.crcXor), tag=\($0.tag), flags=\($0.flags), resources=\($0.resourceInfos.count)"
            },
            currentVersions: currentPacker.map { "\($0.version) / SVN \($0.svnRevision)" },
            legacyVersions: legacyPacker.map {
                "\($0.source): \($0.catalog.version) / SVN \($0.catalog.svnRevision)"
            }
        )

        let currentLinks = currentPacker.flatMap { catalog in
            parentChildLinks(in: catalog).links
        }
        let legacyLinkResults = legacyPacker.map { source in
            (source: source.source, result: parentChildLinks(in: source.catalog))
        }
        let legacyLinks = legacyLinkResults.flatMap { source in
            source.result.links.map {
                AoVRuntimeCatalogSourceValue(source: source.source, value: $0)
            }
        }
        let packerParentChildLinks = makeSection(
            title: "Packer parent-child links",
            current: currentLinks,
            legacy: legacyLinks,
            key: { $0 },
            detail: { $0 },
            currentVersions: currentPacker.map { "\($0.version) / SVN \($0.svnRevision)" },
            legacyVersions: legacyPacker.map {
                "\($0.source): \($0.catalog.version) / SVN \($0.catalog.svnRevision)"
            },
            additionalInvalidReferences: legacyLinkResults.reduce(0) { $0 + $1.result.invalidCount } +
                currentPacker.reduce(0) { $0 + parentChildLinks(in: $1).invalidCount }
        )

        let currentZIP = currentVerification.flatMap(\.allZipVerificationInfo)
        let legacyZIP = legacyVerification.flatMap { source in
            source.catalog.allZipVerificationInfo.map {
                AoVRuntimeCatalogSourceValue(source: source.source, value: $0)
            }
        }
        let verificationZIP = makeSection(
            title: "Verification ZIP",
            current: currentZIP,
            legacy: legacyZIP,
            key: { $0.pathInIFS },
            detail: { "crcXor=\($0.crcXor)" },
            currentVersions: currentVerification.map { "\($0.version) / SVN \($0.svnRevision)" },
            legacyVersions: legacyVerification.map {
                "\($0.source): \($0.catalog.version) / SVN \($0.catalog.svnRevision)"
            }
        )

        let currentDatabin = currentVerification.flatMap(\.allDatabinVerificationInfo)
        let legacyDatabin = legacyVerification.flatMap { source in
            source.catalog.allDatabinVerificationInfo.map {
                AoVRuntimeCatalogSourceValue(source: source.source, value: $0)
            }
        }
        let verificationDatabin = makeSection(
            title: "Verification Databin",
            current: currentDatabin,
            legacy: legacyDatabin,
            key: { $0.pathInIFS },
            detail: { "crcXor=\($0.crcXor)" },
            currentVersions: currentVerification.map { "\($0.version) / SVN \($0.svnRevision)" },
            legacyVersions: legacyVerification.map {
                "\($0.source): \($0.catalog.version) / SVN \($0.catalog.svnRevision)"
            }
        )

        return AoVRuntimeCatalogComparisonReport(
            packerResources: packerResources,
            packerParentChildLinks: packerParentChildLinks,
            verificationZIP: verificationZIP,
            verificationDatabin: verificationDatabin
        )
    }

    private static func packerCatalogs(
        in inventory: ArchiveInventory
    ) -> [AoVRuntimeResourcePackerCatalog] {
        inventory.unityFSBundles
            .filter { ($0.path as NSString).lastPathComponent.lowercased() == "resourcepackerinfosetall.assetbundle" }
            .flatMap(\.catalogNodeSamples)
            .compactMap { $0.serializedFile?.resourcePackerCatalog }
    }

    private static func verificationCatalogs(
        in inventory: ArchiveInventory
    ) -> [AoVRuntimeResourceVerificationCatalog] {
        inventory.unityFSBundles
            .filter { ($0.path as NSString).lastPathComponent.lowercased() == "resourceverificationinfosetall.assetbundle" }
            .flatMap(\.catalogNodeSamples)
            .compactMap { $0.serializedFile?.resourceVerificationCatalog }
    }

    private static func packerCatalogSources(
        in inventories: [ArchiveInventory]
    ) -> [(source: String, catalog: AoVRuntimeResourcePackerCatalog)] {
        inventories.flatMap { inventory in
            inventory.unityFSBundles
                .filter { ($0.path as NSString).lastPathComponent.lowercased() == AoVRemakeCatalogKind.packer.fileName }
                .flatMap(\.catalogNodeSamples)
                .compactMap { sample -> (source: String, catalog: AoVRuntimeResourcePackerCatalog)? in
                    guard let catalog = sample.serializedFile?.resourcePackerCatalog else { return nil }
                    return (inventory.fileName, catalog)
                }
        }
    }

    private static func verificationCatalogSources(
        in inventories: [ArchiveInventory]
    ) -> [(source: String, catalog: AoVRuntimeResourceVerificationCatalog)] {
        inventories.flatMap { inventory in
            inventory.unityFSBundles
                .filter { ($0.path as NSString).lastPathComponent.lowercased() == AoVRemakeCatalogKind.verification.fileName }
                .flatMap(\.catalogNodeSamples)
                .compactMap { sample -> (source: String, catalog: AoVRuntimeResourceVerificationCatalog)? in
                    guard let catalog = sample.serializedFile?.resourceVerificationCatalog else { return nil }
                    return (inventory.fileName, catalog)
                }
        }
    }

    private static func parentChildLinks(
        in catalog: AoVRuntimeResourcePackerCatalog
    ) -> (links: [String], invalidCount: Int) {
        var links: [String] = []
        var invalid = 0
        for item in catalog.allParentIndexToChild {
            guard let parentIndex = Int(exactly: item.parentIdx),
                  let childIndex = Int(exactly: item.childIdx),
                  catalog.allResourcePackInfo.indices.contains(parentIndex),
                  catalog.allResourcePackInfo.indices.contains(childIndex) else {
                invalid += 1
                continue
            }
            let parent = catalog.allResourcePackInfo[parentIndex].pathInIFS
            let child = catalog.allResourcePackInfo[childIndex].pathInIFS
            links.append("\(parent) -> \(child)")
        }
        return (links, invalid)
    }

    private static func makeSection<Value: Hashable>(
        title: String,
        current: [Value],
        legacy: [AoVRuntimeCatalogSourceValue<Value>],
        key: (Value) -> String,
        detail: (Value) -> String,
        currentVersions: [String],
        legacyVersions: [String],
        additionalInvalidReferences: Int = 0
    ) -> AoVRuntimeCatalogSectionReport {
        let currentByPath = Dictionary(grouping: current, by: key)
        let legacyByPath = Dictionary(grouping: legacy, by: { key($0.value) })
        let allPaths = Set(currentByPath.keys).union(legacyByPath.keys).sorted()

        var identical = 0
        var changed = 0
        var legacyOnly = 0
        var currentOnly = 0
        var duplicateCurrent = 0
        var duplicateLegacy = 0
        var conflictingLegacy = 0
        var invalidReferences = additionalInvalidReferences
        var findings: [AoVRuntimeCatalogEntryFinding] = []

        for path in allPaths {
            guard !path.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                invalidReferences += 1
                continue
            }
            let currentRows = currentByPath[path] ?? []
            let legacyRows = legacyByPath[path] ?? []
            let distinctLegacy = Set(legacyRows.map(\.value))
            if currentRows.count > 1 { duplicateCurrent += 1 }
            if legacyRows.count > 1 { duplicateLegacy += 1 }
            if distinctLegacy.count > 1 { conflictingLegacy += 1 }

            let difference: AoVRuntimeCatalogDifference
            if currentRows.count > 1 {
                difference = .duplicateCurrent
            } else if legacyRows.isEmpty {
                currentOnly += 1
                difference = .currentOnly
            } else if currentRows.isEmpty {
                legacyOnly += 1
                difference = .legacyOnly
            } else if distinctLegacy.count > 1 {
                difference = .conflictingLegacy
            } else if currentRows.first == distinctLegacy.first {
                identical += 1
                difference = .identical
            } else {
                changed += 1
                difference = .changed
            }

            guard difference != .identical else { continue }
            let legacyDetails = Array(legacyRows.prefix(4).map {
                "\($0.source): \(detail($0.value))"
            })
            findings.append(
                AoVRuntimeCatalogEntryFinding(
                    path: path,
                    difference: difference,
                    currentDetail: currentRows.first.map(detail),
                    legacyDetails: legacyDetails
                )
            )
        }

        return AoVRuntimeCatalogSectionReport(
            title: title,
            currentCatalogVersions: Array(Set(currentVersions)).sorted(),
            legacyCatalogVersions: Array(Set(legacyVersions)).sorted(),
            currentEntryCount: current.count,
            legacyEntryCount: legacy.count,
            uniqueLegacyPathCount: legacyByPath.count,
            identicalEntryCount: identical,
            changedEntryCount: changed,
            legacyOnlyEntryCount: legacyOnly,
            currentOnlyEntryCount: currentOnly,
            duplicateCurrentPathCount: duplicateCurrent,
            duplicateLegacyPathCount: duplicateLegacy,
            conflictingLegacyPathCount: conflictingLegacy,
            invalidReferenceCount: invalidReferences,
            findings: Array(findings.prefix(maximumFindings))
        )
    }
}
