import Foundation

enum AoVSerializedFileObjectImporterError: LocalizedError {
    case unsupportedFormat(UInt32)
    case externalTargetMissing(String)
    case externalTargetAmbiguous(String, Int32)
    case appendFailed(String)
    case verificationFailed(Int64)

    var errorDescription: String? {
        switch self {
        case .unsupportedFormat(let version):
            return "Object importer yêu cầu SerializedFile format 22–30, nhận \(version)."
        case .externalTargetMissing(let key):
            return "Thiếu byte CAB đích để audit external PPtr: \(key)."
        case .externalTargetAmbiguous(let key, let fileID):
            return "Nhiều CAB đích cùng ánh xạ tới \(key), fileID \(fileID)."
        case .appendFailed(let reason):
            return "Không thể thêm object vào SerializedFile đích: \(reason)"
        case .verificationFailed(let pathID):
            return "Object pathID đích \(pathID) không vượt qua kiểm tra sau khi import."
        }
    }
}

struct AoVSerializedFileObjectImportReport: Sendable {
    let data: Data
    let plan: AoVSerializedFileObjectImportPlan
    let appendReport: AoVSerializedObjectAppendReport
    let roundTripVerified: Bool
}

/// Executes the TypeTree-driven import plan and appends its remapped objects
/// into an existing Unity 2022+ SerializedFile. New SerializedType definitions
/// are not invented: every imported object must match one destination type
/// schema exactly, and every pointer must be resolvable before any bytes move.
enum AoVSerializedFileObjectImporter {
    static func importObjects(
        source: Data,
        destination: Data,
        selectedPathIDs: Set<Int64>? = nil,
        externalFileIDMap: [Int32: Int32] = [:],
        externalPathIDMaps: [Int32: [Int64: Int64]] = [:],
        externalTargetFilesByGUIDAndPath: [String: Data] = [:]
    ) throws -> AoVSerializedFileObjectImportReport {
        let sourceSummary = try AoVSerializedFileParser.inspect(source)
        let destinationSummary = try AoVSerializedFileParser.inspect(destination)
        guard (22...30).contains(sourceSummary.formatVersion) else {
            throw AoVSerializedFileObjectImporterError.unsupportedFormat(sourceSummary.formatVersion)
        }
        guard (22...30).contains(destinationSummary.formatVersion) else {
            throw AoVSerializedFileObjectImporterError.unsupportedFormat(destinationSummary.formatVersion)
        }

        let auditedByKey = try AoVAssetBundleCatalogMerger.auditExternalTargetPathIDs(
            serializedFilesByExternalTargetKey: externalTargetFilesByGUIDAndPath
        )
        var availableExternalTargetPathIDs: [Int32: Set<Int64>] = [:]
        for (sourceFileID, destinationFileID) in externalFileIDMap {
            guard sourceFileID > 0, destinationFileID > 0,
                  let sourceIndex = Int(exactly: sourceFileID - 1),
                  sourceSummary.externalReferences.indices.contains(sourceIndex) else {
                throw AoVSerializedFileObjectImporterError.externalTargetMissing(
                    "source fileID \(sourceFileID)"
                )
            }
            let reference = sourceSummary.externalReferences[sourceIndex]
            let key = AoVAssetBundleCatalogMerger.externalTargetKey(
                guidHex: reference.guidHex,
                path: reference.path
            )
            guard let targetIDs = auditedByKey[key] else {
                throw AoVSerializedFileObjectImporterError.externalTargetMissing(key)
            }
            if let previous = availableExternalTargetPathIDs[destinationFileID],
               previous != targetIDs {
                throw AoVSerializedFileObjectImporterError.externalTargetAmbiguous(
                    key,
                    destinationFileID
                )
            }
            availableExternalTargetPathIDs[destinationFileID] = targetIDs
        }

        let plan = try AoVSerializedFileObjectImportPlanner.plan(
            source: source,
            destination: destination,
            selectedPathIDs: selectedPathIDs,
            externalFileIDMap: externalFileIDMap,
            externalPathIDMaps: externalPathIDMaps,
            availableExternalTargetPathIDs: availableExternalTargetPathIDs
        )
        let appendInputs = plan.objects.map {
            AoVSerializedObjectAppendInput(
                pathID: $0.destinationPathID,
                classID: $0.classID,
                typeID: $0.destinationTypeID,
                payload: $0.data
            )
        }

        let appendReport: AoVSerializedObjectAppendReport
        do {
            appendReport = try AoVSerializedFileObjectAppender.append(
                to: destination,
                objects: appendInputs
            )
        } catch {
            throw AoVSerializedFileObjectImporterError.appendFailed(error.localizedDescription)
        }
        guard appendReport.roundTripVerified else {
            throw AoVSerializedFileObjectImporterError.appendFailed(
                "object appender did not return a verified result."
            )
        }

        let verifiedSummary = try AoVSerializedFileParser.inspect(appendReport.data)
        for imported in plan.objects {
            guard let object = verifiedSummary.serializedObjects.first(where: {
                $0.pathID == imported.destinationPathID
            }),
            object.classID == imported.classID,
            object.typeID == imported.destinationTypeID else {
                throw AoVSerializedFileObjectImporterError.verificationFailed(imported.destinationPathID)
            }
            let value = try AoVSerializedFileParser.decodeObjectWithTypeTree(
                pathID: imported.destinationPathID,
                in: appendReport.data,
                summary: verifiedSummary
            )
            guard let typeInfo = verifiedSummary.typeInfo(for: object) else {
                throw AoVSerializedFileObjectImporterError.verificationFailed(imported.destinationPathID)
            }
            let payload = try AoVUnityTypeTreeWriter.write(
                nodes: typeInfo.typeTreeNodes,
                value: value,
                littleEndian: verifiedSummary.littleEndian
            )
            let start = Int(object.byteOffset)
            guard start >= 0, start <= appendReport.data.count,
                  payload.count <= appendReport.data.count - start,
                  appendReport.data.subdata(in: start..<(start + payload.count)) == imported.data else {
                throw AoVSerializedFileObjectImporterError.verificationFailed(imported.destinationPathID)
            }
        }

        return AoVSerializedFileObjectImportReport(
            data: appendReport.data,
            plan: plan,
            appendReport: appendReport,
            roundTripVerified: true
        )
    }
}
