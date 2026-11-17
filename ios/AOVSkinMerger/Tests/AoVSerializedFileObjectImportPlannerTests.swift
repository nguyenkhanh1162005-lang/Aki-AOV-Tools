import Foundation
import XCTest
@testable import AOVSkinMerger

final class AoVSerializedFileObjectImportPlannerTests: XCTestCase {
    func testPlansTypeTreeObjectImportWithFreshDestinationPathID() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "Texture2D", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_TestPathID", level: 1, byteSize: 8, metaFlags: 0)
        ]
        let value: AoVUnityTypeTreeValue = .object([
            "m_TestPathID": .signed(1001)
        ])
        let payload = try AoVUnityTypeTreeWriter.write(nodes: nodes, value: value, littleEndian: true)
        let file = makeSerializedFile(
            pathIDs: [1234],
            payloads: [payload],
            typeTreeNodes: nodes
        )

        let plan = try AoVSerializedFileObjectImportPlanner.plan(
            source: file,
            destination: file
        )

        XCTAssertTrue(plan.roundTripVerified)
        XCTAssertEqual(plan.pathIDMap, [1234: 1235])
        XCTAssertEqual(plan.objects.count, 1)
        XCTAssertEqual(plan.objects[0].sourcePathID, 1234)
        XCTAssertEqual(plan.objects[0].destinationPathID, 1235)
        XCTAssertEqual(plan.objects[0].classID, 28)
        XCTAssertEqual(plan.objects[0].data, payload)
        XCTAssertEqual(plan.totalPayloadBytes, UInt64(payload.count))
    }

    func testAppendsPlannedTypeTreeObjectsAndUpdatesSerializedFileHeader() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "Texture2D", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_TestPathID", level: 1, byteSize: 8, metaFlags: 0)
        ]
        let value: AoVUnityTypeTreeValue = .object([
            "m_TestPathID": .signed(1001)
        ])
        let payload = try AoVUnityTypeTreeWriter.write(nodes: nodes, value: value, littleEndian: true)
        let file = makeSerializedFile(
            pathIDs: [1234],
            payloads: [payload],
            typeTreeNodes: nodes
        )
        let plan = try AoVSerializedFileObjectImportPlanner.plan(
            source: file,
            destination: file
        )
        let appendInputs = plan.objects.map {
            AoVSerializedObjectAppendInput(
                pathID: $0.destinationPathID,
                classID: $0.classID,
                typeID: $0.destinationTypeID,
                payload: $0.data
            )
        }

        let report = try AoVSerializedFileObjectAppender.append(
            to: file,
            objects: appendInputs
        )
        let summary = try AoVSerializedFileParser.inspect(report.data)
        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(report.originalObjectCount, 1)
        XCTAssertEqual(report.finalObjectCount, 2)
        XCTAssertEqual(report.appendedPathIDs, [1235])
        XCTAssertEqual(summary.serializedObjects.map(\.pathID), [1234, 1235])
        XCTAssertEqual(readUInt32BE(report.data, at: 4), UInt32(report.data.count))
        XCTAssertEqual(readUInt32BE(report.data, at: 12), UInt32(summary.dataOffset))

        let imported = try AoVSerializedFileParser.decodeObjectWithTypeTree(
            pathID: 1235,
            in: report.data,
            summary: summary
        )
        XCTAssertEqual(imported, value)
    }

    func testRemapsLocalPointersAcrossImportedObjects() throws {
        let nodes = pointerNodes()
        let first = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 1002),
            littleEndian: true
        )
        let second = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 0),
            littleEndian: true
        )
        let file = makeSerializedFile(
            pathIDs: [1001, 1002],
            payloads: [first, second],
            typeTreeNodes: nodes
        )

        let plan = try AoVSerializedFileObjectImportPlanner.plan(
            source: file,
            destination: file
        )

        XCTAssertEqual(plan.pathIDMap, [1001: 1003, 1002: 1004])
        XCTAssertEqual(plan.objects.count, 2)
        XCTAssertEqual(plan.objects[0].remappedLocalPointers, 1)
        XCTAssertEqual(plan.objects[1].remappedLocalPointers, 0)

        let decoded = try AoVUnityTypeTreeReader.read(
            nodes: nodes,
            data: plan.objects[0].data,
            littleEndian: true
        )
        XCTAssertEqual(
            decoded.objectValue?["m_GameObject"]?.objectValue?["m_PathID"]?.intValue,
            1004
        )
        XCTAssertEqual(
            decoded.objectValue?["m_GameObject"]?.objectValue?["m_FileID"]?.intValue,
            0
        )
    }

    func testPreservesExplicitSourceRootPointerWhileImportingSelectedObjects() throws {
        let nodes = pointerNodes()
        let rootPayload = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 0),
            littleEndian: true
        )
        let childPayload = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 1001),
            littleEndian: true
        )
        let source = makeSerializedFile(
            pathIDs: [1001, 1002],
            payloads: [rootPayload, childPayload],
            typeTreeNodes: nodes
        )
        let destination = makeSerializedFile(
            pathIDs: [5001],
            payloads: [rootPayload],
            typeTreeNodes: nodes
        )

        let plan = try AoVSerializedFileObjectImportPlanner.plan(
            source: source,
            destination: destination,
            selectedPathIDs: [1002],
            preservedLocalPathIDMap: [1001: 5001]
        )

        XCTAssertEqual(plan.pathIDMap, [1001: 5001, 1002: 5002])
        XCTAssertEqual(plan.objects.map(\.destinationPathID), [5002])
        let imported = try AoVUnityTypeTreeReader.read(
            nodes: nodes,
            data: try XCTUnwrap(plan.objects.first?.data),
            littleEndian: true
        )
        XCTAssertEqual(
            imported.objectValue?["m_GameObject"]?.objectValue?["m_PathID"]?.intValue,
            5001
        )
    }

    func testPlansTransitiveLocalDependencyClosureFromSeedObject() throws {
        let nodes = pointerNodes()
        let payloads = try [
            AoVUnityTypeTreeWriter.write(
                nodes: nodes,
                value: pointerValue(fileID: 0, pathID: 1002),
                littleEndian: true
            ),
            AoVUnityTypeTreeWriter.write(
                nodes: nodes,
                value: pointerValue(fileID: 0, pathID: 1003),
                littleEndian: true
            ),
            AoVUnityTypeTreeWriter.write(
                nodes: nodes,
                value: pointerValue(fileID: 0, pathID: 0),
                littleEndian: true
            )
        ]
        let file = makeSerializedFile(
            pathIDs: [1001, 1002, 1003],
            payloads: payloads,
            typeTreeNodes: nodes
        )

        let plan = try AoVSerializedFileObjectImportPlanner.planDependencyClosure(
            source: file,
            destination: file,
            seedPathIDs: [1001]
        )

        XCTAssertEqual(plan.pathIDMap, [1001: 1004, 1002: 1005, 1003: 1006])
        XCTAssertEqual(plan.objects.map(\.sourcePathID), [1001, 1002, 1003])
        XCTAssertEqual(plan.objects.map(\.remappedLocalPointers), [1, 1, 0])
        let first = try AoVUnityTypeTreeReader.read(
            nodes: nodes,
            data: plan.objects[0].data,
            littleEndian: true
        )
        let second = try AoVUnityTypeTreeReader.read(
            nodes: nodes,
            data: plan.objects[1].data,
            littleEndian: true
        )
        XCTAssertEqual(first.objectValue?["m_GameObject"]?.objectValue?["m_PathID"]?.intValue, 1005)
        XCTAssertEqual(second.objectValue?["m_GameObject"]?.objectValue?["m_PathID"]?.intValue, 1006)
    }

    func testDependencyClosureRejectsMissingLocalTarget() throws {
        let nodes = pointerNodes()
        let first = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 1003),
            littleEndian: true
        )
        let second = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 0),
            littleEndian: true
        )
        let file = makeSerializedFile(
            pathIDs: [1001, 1002],
            payloads: [first, second],
            typeTreeNodes: nodes
        )

        XCTAssertThrowsError(
            try AoVSerializedFileObjectImportPlanner.planDependencyClosure(
                source: file,
                destination: file,
                seedPathIDs: [1001]
            )
        ) { error in
            guard let planError = error as? AoVSerializedFileObjectImportPlanError,
                  case .pointerAuditFailed(1001, _) = planError else {
                return XCTFail("Expected missing dependency rejection, got \\(error)")
            }
        }
    }

    func testRejectsReferencesToSourceObjectsOutsideSelectedImportBatch() throws {
        let nodes = pointerNodes()
        let first = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 1002),
            littleEndian: true
        )
        let second = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 0),
            littleEndian: true
        )
        let file = makeSerializedFile(
            pathIDs: [1001, 1002],
            payloads: [first, second],
            typeTreeNodes: nodes
        )

        XCTAssertThrowsError(
            try AoVSerializedFileObjectImportPlanner.plan(
                source: file,
                destination: file,
                selectedPathIDs: [1001]
            )
        ) { error in
            guard let planError = error as? AoVSerializedFileObjectImportPlanError,
                  case .pointerAuditFailed(1001, _) = planError else {
                return XCTFail("Expected an unresolved local PPtr rejection, got \(error)")
            }
        }
    }

    func testRejectsLocalPointerToMissingSourceObjectEvenIfItMatchesNextAllocatedID() throws {
        let nodes = pointerNodes()
        let first = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 1003),
            littleEndian: true
        )
        let second = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 0),
            littleEndian: true
        )
        let file = makeSerializedFile(
            pathIDs: [1001, 1002],
            payloads: [first, second],
            typeTreeNodes: nodes
        )

        XCTAssertThrowsError(
            try AoVSerializedFileObjectImportPlanner.plan(
                source: file,
                destination: file
            )
        ) { error in
            guard let planError = error as? AoVSerializedFileObjectImportPlanError,
                  case .pointerAuditFailed(1001, _) = planError else {
                return XCTFail("Expected a missing source-local PPtr rejection, got \\(error)")
            }
        }
    }

    func testRejectsExternalPointerWithoutExactDestinationMapping() throws {
        let nodes = pointerNodes()
        let first = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 1, pathID: 2002),
            littleEndian: true
        )
        let second = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: pointerValue(fileID: 0, pathID: 0),
            littleEndian: true
        )
        let file = makeSerializedFile(
            pathIDs: [1001, 1002],
            payloads: [first, second],
            typeTreeNodes: nodes
        )

        XCTAssertThrowsError(
            try AoVSerializedFileObjectImportPlanner.plan(
                source: file,
                destination: file
            )
        ) { error in
            guard let planError = error as? AoVSerializedFileObjectImportPlanError,
                  case .pointerAuditFailed(1001, _) = planError else {
                return XCTFail("Expected an unresolved external PPtr rejection, got \(error)")
            }
        }
    }

    func testRejectsIncompatibleDestinationTypeTreeAndPlatform() throws {
        let sourceNodes = [
            AoVUnityTypeTreeNode(type: "Texture2D", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_TestPathID", level: 1, byteSize: 8, metaFlags: 0)
        ]
        let sourcePayload = try AoVUnityTypeTreeWriter.write(
            nodes: sourceNodes,
            value: .object(["m_TestPathID": .signed(1001)]),
            littleEndian: true
        )
        let source = makeSerializedFile(
            pathIDs: [1234],
            payloads: [sourcePayload],
            typeTreeNodes: sourceNodes
        )
        let incompatibleNodes = [
            AoVUnityTypeTreeNode(type: "Texture2D", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "UInt64", name: "m_TestPathID", level: 1, byteSize: 8, metaFlags: 0)
        ]
        let incompatiblePayload = try AoVUnityTypeTreeWriter.write(
            nodes: incompatibleNodes,
            value: .object(["m_TestPathID": .unsigned(1001)]),
            littleEndian: true
        )
        let incompatible = makeSerializedFile(
            pathIDs: [9000],
            payloads: [incompatiblePayload],
            typeTreeNodes: incompatibleNodes
        )

        XCTAssertThrowsError(
            try AoVSerializedFileObjectImportPlanner.plan(
                source: source,
                destination: incompatible
            )
        ) { error in
            guard let planError = error as? AoVSerializedFileObjectImportPlanError,
                  case .incompatibleType(1234, 28) = planError else {
                return XCTFail("Expected TypeTree mismatch rejection, got \(error)")
            }
        }

        let wrongPlatform = makeSerializedFile(
            pathIDs: [9000],
            payloads: [incompatiblePayload],
            typeTreeNodes: sourceNodes,
            targetPlatform: 9
        )
        XCTAssertThrowsError(
            try AoVSerializedFileObjectImportPlanner.plan(
                source: source,
                destination: wrongPlatform
            )
        ) { error in
            guard let planError = error as? AoVSerializedFileObjectImportPlanError,
                  case .targetPlatformMismatch(13, 9) = planError else {
                return XCTFail("Expected TargetPlatform mismatch rejection, got \(error)")
            }
        }
    }

    func testImportsAndAppendsTypeTreeObjectIntoDestinationSerializedFile() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "Texture2D", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_TestPathID", level: 1, byteSize: 8, metaFlags: 0)
        ]
        let value: AoVUnityTypeTreeValue = .object([
            "m_TestPathID": .signed(1001)
        ])
        let payload = try AoVUnityTypeTreeWriter.write(nodes: nodes, value: value, littleEndian: true)
        let sourceAndDestination = makeSerializedFile(
            pathIDs: [1234],
            payloads: [payload],
            typeTreeNodes: nodes
        )

        let plan = try AoVSerializedFileObjectImportPlanner.plan(
            source: sourceAndDestination,
            destination: sourceAndDestination
        )
        let appendInputs = plan.objects.map {
            AoVSerializedObjectAppendInput(
                pathID: $0.destinationPathID,
                classID: $0.classID,
                typeID: $0.destinationTypeID,
                payload: $0.data
            )
        }
        let appendReport = try AoVSerializedFileObjectAppender.append(
            to: sourceAndDestination,
            objects: appendInputs
        )

        XCTAssertTrue(plan.roundTripVerified)
        XCTAssertTrue(appendReport.roundTripVerified)
        XCTAssertEqual(appendReport.originalObjectCount, 1)
        XCTAssertEqual(appendReport.finalObjectCount, 2)
        XCTAssertEqual(appendReport.appendedPathIDs, [1235])

        let finalSummary = try AoVSerializedFileParser.inspect(appendReport.data)
        XCTAssertEqual(finalSummary.fileSize, UInt64(appendReport.data.count))
        XCTAssertEqual(readUInt32BE(appendReport.data, at: 0), UInt32(finalSummary.metadataSize))
        XCTAssertEqual(readUInt32BE(appendReport.data, at: 4), UInt32(finalSummary.fileSize))
        XCTAssertEqual(readUInt32BE(appendReport.data, at: 12), UInt32(finalSummary.dataOffset))
        XCTAssertEqual(readUInt32BE(appendReport.data, at: 20), UInt32(finalSummary.metadataSize))
        XCTAssertEqual(readUInt64BE(appendReport.data, at: 24), finalSummary.fileSize)
        XCTAssertEqual(readUInt64BE(appendReport.data, at: 32), finalSummary.dataOffset)
        XCTAssertEqual(finalSummary.serializedObjects.map(\.pathID), [1234, 1235])
        let importedValue = try AoVSerializedFileParser.decodeObjectWithTypeTree(
            pathID: 1235,
            in: appendReport.data,
            summary: finalSummary
        )
        XCTAssertEqual(importedValue.objectValue?["m_TestPathID"]?.intValue, 1001)
    }


    private func pointerNodes() -> [AoVUnityTypeTreeNode] {
        [
            AoVUnityTypeTreeNode(type: "MonoBehaviour", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<GameObject>", name: "m_GameObject", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 2, byteSize: 8, metaFlags: 0)
        ]
    }

    private func pointerValue(fileID: Int64, pathID: Int64) -> AoVUnityTypeTreeValue {
        .object([
            "m_GameObject": .object([
                "m_FileID": .signed(fileID),
                "m_PathID": .signed(pathID)
            ])
        ])
    }

    private func makeSerializedFile(
        pathIDs: [Int64],
        payloads: [Data],
        typeTreeNodes: [AoVUnityTypeTreeNode]?,
        targetPlatform: Int32 = 13,
        formatVersion: UInt32 = 22,
        unityVersion: String = "2022.3.5f1"
    ) -> Data {
        precondition(pathIDs.count == payloads.count)
        var byteStarts: [Int64] = []
        var dataSection = Data()
        for payload in payloads {
            while dataSection.count % 16 != 0 { dataSection.append(0) }
            byteStarts.append(Int64(dataSection.count))
            dataSection.append(payload)
        }

        var metadata = Data()
        appendCString(unityVersion, to: &metadata)
        appendInt32(targetPlatform, to: &metadata)
        metadata.append(typeTreeNodes == nil ? 0 : 1)
        appendInt32(1, to: &metadata)
        appendInt32(28, to: &metadata)
        metadata.append(0)
        appendInt16(-1, to: &metadata)
        metadata.append(Data(repeating: 0, count: 16))
        if let typeTreeNodes {
            appendTypeTree(typeTreeNodes, to: &metadata, formatVersion: formatVersion)
        }
        if formatVersion >= 21 { appendInt32(0, to: &metadata) }
        appendInt32(Int32(pathIDs.count), to: &metadata)
        align4(&metadata)
        for index in pathIDs.indices {
            appendInt64(pathIDs[index], to: &metadata)
            appendInt64(byteStarts[index], to: &metadata)
            appendUInt32(UInt32(payloads[index].count), to: &metadata)
            appendInt32(0, to: &metadata)
        }
        appendInt32(0, to: &metadata) // script count
        appendInt32(0, to: &metadata) // external reference count
        if formatVersion >= 20 { appendInt32(0, to: &metadata) }
        metadata.append(0)

        let headerSize = formatVersion >= 22 ? 48 : 20
        let dataOffset = ((headerSize + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + dataSection.count
        var file = Data()
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt32(fileSize), to: &file)
        appendBE(formatVersion, to: &file)
        appendBE(UInt32(dataOffset), to: &file)
        file.append(0)
        file.append(contentsOf: [0, 0, 0])
        if formatVersion >= 22 {
            appendBE(UInt32(metadata.count), to: &file)
            appendBE(UInt64(fileSize), to: &file)
            appendBE(UInt64(dataOffset), to: &file)
            appendBE(UInt64(0), to: &file)
        }
        file.append(metadata)
        while file.count < dataOffset { file.append(0) }
        file.append(dataSection)
        return file
    }

    private func readUInt32BE(_ data: Data, at offset: Int) -> UInt32 {
        let b0 = UInt32(data[offset]) << 24
        let b1 = UInt32(data[offset + 1]) << 16
        let b2 = UInt32(data[offset + 2]) << 8
        let b3 = UInt32(data[offset + 3])
        return b0 | b1 | b2 | b3
    }

    private func readUInt64BE(_ data: Data, at offset: Int) -> UInt64 {
        (UInt64(data[offset]) << 56)
            | (UInt64(data[offset + 1]) << 48)
            | (UInt64(data[offset + 2]) << 40)
            | (UInt64(data[offset + 3]) << 32)
            | (UInt64(data[offset + 4]) << 24)
            | (UInt64(data[offset + 5]) << 16)
            | (UInt64(data[offset + 6]) << 8)
            | UInt64(data[offset + 7])
    }

    private func appendTypeTree(
        _ nodes: [AoVUnityTypeTreeNode],
        to data: inout Data,
        formatVersion: UInt32
    ) {
        var strings = Data()
        var offsets: [String: UInt32] = [:]
        func intern(_ value: String) -> UInt32 {
            if let offset = offsets[value] { return offset }
            let offset = UInt32(strings.count)
            strings.append(Data(value.utf8))
            strings.append(0)
            offsets[value] = offset
            return offset
        }
        let pairs = nodes.map { (intern($0.type), intern($0.name)) }
        appendInt32(Int32(nodes.count), to: &data)
        appendInt32(Int32(strings.count), to: &data)
        for (index, node) in nodes.enumerated() {
            appendInt16(1, to: &data)
            data.append(node.level)
            data.append(0)
            appendUInt32(pairs[index].0, to: &data)
            appendUInt32(pairs[index].1, to: &data)
            appendInt32(node.byteSize, to: &data)
            appendInt32(Int32(index), to: &data)
            appendUInt32(node.metaFlags, to: &data)
            if formatVersion >= 19 { appendUInt64(0, to: &data) }
        }
        data.append(strings)
    }

    private func appendCString(_ value: String, to data: inout Data) {
        data.append(Data((value + "\0").utf8))
    }

    private func appendInt16(_ value: Int16, to data: inout Data) {
        let bits = UInt16(bitPattern: value)
        data.append(UInt8(truncatingIfNeeded: bits))
        data.append(UInt8(truncatingIfNeeded: bits >> 8))
    }

    private func appendInt32(_ value: Int32, to data: inout Data) {
        appendUInt32(UInt32(bitPattern: value), to: &data)
    }

    private func appendUInt32(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 0, through: 24, by: 8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendInt64(_ value: Int64, to data: inout Data) {
        appendUInt64(UInt64(bitPattern: value), to: &data)
    }

    private func appendUInt64(_ value: UInt64, to data: inout Data) {
        for shift in stride(from: 0, through: 56, by: 8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendBE(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 24, through: 0, by: -8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendBE(_ value: UInt64, to data: inout Data) {
        for shift in stride(from: 56, through: 0, by: -8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func align4(_ data: inout Data) {
        while data.count % 4 != 0 { data.append(0) }
    }
}
