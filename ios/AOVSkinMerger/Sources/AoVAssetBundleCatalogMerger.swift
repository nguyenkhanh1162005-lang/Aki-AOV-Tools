import Foundation

enum AoVAssetBundleCatalogMergeError: LocalizedError {
    case bundleIdentityMismatch
    case duplicateCurrentKey(String)
    case duplicateLegacyKey(String)
    case invalidLegacyPreloadRange(String)
    case unresolvedLocalPointer(String)
    case unresolvedExternalReference(String)
    case ambiguousExternalReference(String)
    case nullAssetPointer(String)
    case dependencyUnavailable(String)
    case integerOverflow

    var errorDescription: String? {
        switch self {
        case .bundleIdentityMismatch:
            return "Tên AssetBundle của RES và MOD cũ không khớp."
        case .duplicateCurrentKey(let key):
            return "RES mới có container key bị lặp: \(key)."
        case .duplicateLegacyKey(let key):
            return "MOD cũ có container key bị lặp: \(key)."
        case .invalidLegacyPreloadRange(let key):
            return "Preload range của MOD cũ không hợp lệ tại key \(key)."
        case .unresolvedLocalPointer(let field):
            return "Không tìm thấy local PPtr trong RES mới: \(field)."
        case .unresolvedExternalReference(let field):
            return "Không tìm được external reference tương ứng trong RES mới: \(field)."
        case .ambiguousExternalReference(let field):
            return "External reference trùng GUID/path, không thể chọn an toàn: \(field)."
        case .nullAssetPointer(let field):
            return "Container key có asset PPtr rỗng: \(field)."
        case .dependencyUnavailable(let name):
            return "Dependency bắt buộc chưa có trong Packer Catalog RES mới: \(name)."
        case .integerOverflow:
            return "Số lượng preload hoặc index vượt giới hạn của SerializedFile."
        }
    }
}

struct AoVAssetBundleCatalogMergePlan: Sendable {
    let mergedRecord: AoVSerializedAssetBundleRecord
    let addedContainerKeys: [String]
    let alreadyPresentContainerKeys: [String]
    let currentVersionRetainedPointerConflictKeys: [String]
    let addedPreloadReferenceCount: Int
    let addedDependencies: [String]
    let remappedExternalPointerCount: Int
    let externalTargetAuditRequired: Bool
    let externalTargetAuditSamples: [String]
}

/// Builds a candidate AssetBundle record while keeping the current RES record
/// as the baseline. It never copies a legacy pathID blindly: local IDs must exist
/// in the current SerializedFile and external IDs must map through an exact
/// GUID + path match. External target-file pathIDs are writable only when the
/// caller supplies a verified target-object inventory for that exact reference.
enum AoVAssetBundleCatalogMerger {
    static func plan(
        current: AoVSerializedAssetBundleRecord,
        legacy: AoVSerializedAssetBundleRecord,
        currentSerializedFile: AoVSerializedFileSummary,
        legacySerializedFile: AoVSerializedFileSummary,
        availableCurrentBundleNames: Set<String>,
        verifiedExternalTargetPathIDs: [String: Set<Int64>] = [:]
    ) throws -> AoVAssetBundleCatalogMergePlan {
        guard identity(for: current) == identity(for: legacy) else {
            throw AoVAssetBundleCatalogMergeError.bundleIdentityMismatch
        }

        var currentByKey: [String: AoVSerializedAssetBundleEntry] = [:]
        for entry in current.containerEntries {
            guard currentByKey[entry.key] == nil else {
                throw AoVAssetBundleCatalogMergeError.duplicateCurrentKey(entry.key)
            }
            currentByKey[entry.key] = entry
            try validatePreloadRange(
                entry,
                preloadCount: current.preloadReferences.count
            )
        }

        var legacyKeys = Set<String>()
        for entry in legacy.containerEntries {
            guard legacyKeys.insert(entry.key).inserted else {
                throw AoVAssetBundleCatalogMergeError.duplicateLegacyKey(entry.key)
            }
            try validatePreloadRange(
                entry,
                preloadCount: legacy.preloadReferences.count
            )
        }

        var mergedPreloads = current.preloadReferences
        var mergedEntries = current.containerEntries
        var addedKeys: [String] = []
        var alreadyPresent: [String] = []
        var currentVersionRetainedPointerConflicts: [String] = []
        var externalRemaps = 0
        var auditedExternalRemaps = 0
        var unauditedExternalSamples: [String] = []

        for legacyEntry in legacy.containerEntries {
            if let currentEntry = currentByKey[legacyEntry.key] {
                alreadyPresent.append(legacyEntry.key)
                if currentEntry.asset != legacyEntry.asset ||
                    currentEntry.preloadIndex != legacyEntry.preloadIndex ||
                    currentEntry.preloadSize != legacyEntry.preloadSize {
                    currentVersionRetainedPointerConflicts.append(legacyEntry.key)
                }
                continue
            }

            guard legacyEntry.asset.pathID != 0 else {
                throw AoVAssetBundleCatalogMergeError.nullAssetPointer(legacyEntry.key)
            }
            let start = Int(legacyEntry.preloadIndex)
            let count = Int(legacyEntry.preloadSize)
            guard start >= 0, count >= 0,
                  start <= legacy.preloadReferences.count,
                  count <= legacy.preloadReferences.count - start else {
                throw AoVAssetBundleCatalogMergeError.invalidLegacyPreloadRange(legacyEntry.key)
            }
            guard let newPreloadStart = Int32(exactly: mergedPreloads.count),
                  let newPreloadSize = Int32(exactly: count) else {
                throw AoVAssetBundleCatalogMergeError.integerOverflow
            }

            let legacyPreloadSlice = legacy.preloadReferences[start..<(start + count)]
            var remappedPreloads: [AoVSerializedAssetPointer] = []
            remappedPreloads.reserveCapacity(count)
            for (index, pointer) in legacyPreloadSlice.enumerated() {
                let result = try remapPointer(
                    pointer,
                    field: "\(legacyEntry.key).preload[\(index)]",
                    from: legacySerializedFile,
                    to: currentSerializedFile,
                    verifiedExternalTargetPathIDs: verifiedExternalTargetPathIDs
                )
                remappedPreloads.append(result.pointer)
                if result.wasExternal {
                    externalRemaps += 1
                    if result.targetAudited {
                        auditedExternalRemaps += 1
                    } else if unauditedExternalSamples.count < 16 {
                        unauditedExternalSamples.append("\(legacyEntry.key).preload[\(index)]")
                    }
                }
            }

            let remappedAsset = try remapPointer(
                legacyEntry.asset,
                field: "\(legacyEntry.key).asset",
                from: legacySerializedFile,
                to: currentSerializedFile,
                    verifiedExternalTargetPathIDs: verifiedExternalTargetPathIDs
            )
            if remappedAsset.wasExternal {
                externalRemaps += 1
                if remappedAsset.targetAudited {
                    auditedExternalRemaps += 1
                } else if unauditedExternalSamples.count < 16 {
                    unauditedExternalSamples.append("\(legacyEntry.key).asset")
                }
            }

            mergedPreloads.append(contentsOf: remappedPreloads)
            mergedEntries.append(
                AoVSerializedAssetBundleEntry(
                    key: legacyEntry.key,
                    preloadIndex: newPreloadStart,
                    preloadSize: newPreloadSize,
                    asset: remappedAsset.pointer
                )
            )
            addedKeys.append(legacyEntry.key)
        }

        let currentDependencies = Set(current.dependencies)
        let requiredLegacyDependencies = Set(legacy.dependencies).subtracting(currentDependencies)
        let available = Set(availableCurrentBundleNames.map { $0.lowercased() })
        let missingDependencies = requiredLegacyDependencies
            .filter { !available.contains($0.lowercased()) }
            .sorted()
        guard missingDependencies.isEmpty else {
            throw AoVAssetBundleCatalogMergeError.dependencyUnavailable(missingDependencies[0])
        }
        let addedDependencies = requiredLegacyDependencies.sorted()
        let mergedDependencies = current.dependencies + addedDependencies

        let mergedRecord = AoVSerializedAssetBundleRecord(
            pathID: current.pathID,
            name: current.name,
            preloadReferences: mergedPreloads,
            containerEntries: mergedEntries,
            mainAsset: current.mainAsset,
            mainAssetPreloadIndex: current.mainAssetPreloadIndex,
            mainAssetPreloadSize: current.mainAssetPreloadSize,
            scriptCompatibilityEntries: current.scriptCompatibilityEntries,
            classCompatibilityIDs: current.classCompatibilityIDs,
            classCompatibilityEntries: current.classCompatibilityEntries,
            runtimeCompatibility: current.runtimeCompatibility,
            assetBundleName: current.assetBundleName,
            dependencies: mergedDependencies,
            isStreamedSceneAssetBundle: current.isStreamedSceneAssetBundle,
            trailingBytes: current.trailingBytes,
            trailingData: current.trailingData
        )

        // Serialize once here as a structural and pointer-range preflight.
        // This does not write or mutate the source file.
        _ = try AoVSerializedAssetBundleWriter.serialize(
            mergedRecord,
            formatVersion: currentSerializedFile.formatVersion,
            unityVersion: currentSerializedFile.unityVersion,
            littleEndian: currentSerializedFile.littleEndian
        )

        return AoVAssetBundleCatalogMergePlan(
            mergedRecord: mergedRecord,
            addedContainerKeys: addedKeys,
            alreadyPresentContainerKeys: alreadyPresent,
            currentVersionRetainedPointerConflictKeys: currentVersionRetainedPointerConflicts,
            addedPreloadReferenceCount: mergedPreloads.count - current.preloadReferences.count,
            addedDependencies: addedDependencies,
            remappedExternalPointerCount: externalRemaps,
            externalTargetAuditRequired: auditedExternalRemaps < externalRemaps,
            externalTargetAuditSamples: unauditedExternalSamples
        )
    }

    private static func identity(for record: AoVSerializedAssetBundleRecord) -> String {
        let name = record.assetBundleName.trimmingCharacters(in: .whitespacesAndNewlines)
        return (name.isEmpty ? record.name : name).lowercased()
    }

    private static func validatePreloadRange(
        _ entry: AoVSerializedAssetBundleEntry,
        preloadCount: Int
    ) throws {
        let start = Int64(entry.preloadIndex)
        let size = Int64(entry.preloadSize)
        let end = start.addingReportingOverflow(size)
        guard entry.preloadIndex >= 0,
              entry.preloadSize >= 0,
              !end.overflow,
              end.partialValue <= Int64(preloadCount) else {
            throw AoVAssetBundleCatalogMergeError.invalidLegacyPreloadRange(entry.key)
        }
    }

    private static func remapPointer(
        _ pointer: AoVSerializedAssetPointer,
        field: String,
        from source: AoVSerializedFileSummary,
        to destination: AoVSerializedFileSummary,
        verifiedExternalTargetPathIDs: [String: Set<Int64>]
    ) throws -> (pointer: AoVSerializedAssetPointer, wasExternal: Bool, targetAudited: Bool) {
        guard pointer.fileID >= 0 else {
            throw AoVAssetBundleCatalogMergeError.unresolvedExternalReference(field)
        }
        if pointer.fileID == 0 {
            if pointer.pathID == 0 {
                return (pointer, false, false)
            }
            guard destination.serializedObjectPathIDs.contains(pointer.pathID) else {
                throw AoVAssetBundleCatalogMergeError.unresolvedLocalPointer(field)
            }
            return (pointer, false, false)
        }

        guard pointer.pathID != 0,
              let sourceIndex = Int(exactly: pointer.fileID - 1),
              sourceIndex >= 0,
              source.externalReferences.indices.contains(sourceIndex) else {
            throw AoVAssetBundleCatalogMergeError.unresolvedExternalReference(field)
        }
        let sourceReference = source.externalReferences[sourceIndex]
        let sourceGUID = sourceReference.guidHex.lowercased()
        let sourcePath = normalizedReferencePath(sourceReference.path)
        let matches = destination.externalReferences.enumerated().filter { _, candidate in
            candidate.guidHex.lowercased() == sourceGUID &&
                normalizedReferencePath(candidate.path) == sourcePath
        }
        guard !matches.isEmpty else {
            throw AoVAssetBundleCatalogMergeError.unresolvedExternalReference(field)
        }
        guard matches.count == 1,
              let mappedFileID = Int32(exactly: matches[0].offset + 1) else {
            throw AoVAssetBundleCatalogMergeError.ambiguousExternalReference(field)
        }
        let destinationReference = matches[0].element
        let targetKey = externalTargetKey(
            guidHex: destinationReference.guidHex,
            path: destinationReference.path
        )
        let targetAudited = verifiedExternalTargetPathIDs[targetKey]?.contains(pointer.pathID) == true
        return (
            AoVSerializedAssetPointer(fileID: mappedFileID, pathID: pointer.pathID),
            true,
            targetAudited
        )
    }

    static func externalTargetKey(guidHex: String, path: String) -> String {
        "\(guidHex.lowercased())|\(normalizedReferencePath(path))"
    }

    private static func normalizedReferencePath(_ value: String) -> String {
        value.replacingOccurrences(of: "\\", with: "/")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
    }
}
