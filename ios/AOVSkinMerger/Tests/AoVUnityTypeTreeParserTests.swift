import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVUnityTypeTreeParserTests: XCTestCase {
    func testReadsMonoBehaviourNameVersionAndResourcePackRows() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "MonoBehaviour", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "string", name: "m_Name", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 2, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 3, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "char", name: "data", level: 3, byteSize: 1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "Version", level: 1, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "vector", name: "AllResourcePackInfo", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 2, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 3, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "ResourcePackInfo", name: "data", level: 3, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "string", name: "PathInIFS", level: 4, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 5, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 6, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "char", name: "data", level: 6, byteSize: 1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "pkgType", level: 4, byteSize: 4, metaFlags: 0)
        ]

        var data = Data()
        appendAlignedString("ResourcePackerInfoSetAll", to: &data)
        appendLE(Int32(7), to: &data)
        appendLE(Int32(1), to: &data)
        appendAlignedString("Databin/Client/Hero.bytes", to: &data)
        appendLE(Int32(3), to: &data)

        let value = try AoVUnityTypeTreeReader.read(nodes: nodes, data: data, littleEndian: true)
        let root = try XCTUnwrap(value.objectValue)
        XCTAssertEqual(root["m_Name"]?.stringValue, "ResourcePackerInfoSetAll")
        XCTAssertEqual(root["Version"]?.intValue, 7)
        let rows = try XCTUnwrap(root["AllResourcePackInfo"]?.arrayValue)
        XCTAssertEqual(rows.count, 1)
        let row = try XCTUnwrap(rows.first?.objectValue)
        XCTAssertEqual(row["PathInIFS"]?.stringValue, "Databin/Client/Hero.bytes")
        XCTAssertEqual(row["pkgType"]?.intValue, 3)
    }

    func testReadsMapPairAndTypelessData() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "Catalog", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "map", name: "Lookup", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 2, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 3, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "pair", name: "data", level: 3, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "string", name: "first", level: 4, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 5, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 6, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "char", name: "data", level: 6, byteSize: 1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "second", level: 4, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "TypelessData", name: "Payload", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "UInt8", name: "data", level: 2, byteSize: 1, metaFlags: 0)
        ]

        var data = Data()
        appendLE(Int32(1), to: &data)
        appendAlignedString("all", to: &data)
        appendLE(Int32(42), to: &data)
        appendLE(Int32(3), to: &data)
        data.append(contentsOf: [0x10, 0x20, 0x30])

        let value = try AoVUnityTypeTreeReader.read(nodes: nodes, data: data, littleEndian: true)
        let root = try XCTUnwrap(value.objectValue)
        guard case .map(let entries)? = root["Lookup"] else {
            return XCTFail("Lookup should decode as a map")
        }
        XCTAssertEqual(entries.count, 1)
        XCTAssertEqual(entries.first?.key.stringValue, "all")
        XCTAssertEqual(entries.first?.value.intValue, 42)
        if case .bytes(let bytes)? = root["Payload"] {
            XCTAssertEqual(bytes, Data([0x10, 0x20, 0x30]))
        } else {
            XCTFail("Payload should decode as TypelessData")
        }

        let rewritten = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: value,
            littleEndian: true
        )
        XCTAssertEqual(rewritten, data)
        let roundTripValue = try AoVUnityTypeTreeReader.read(
            nodes: nodes,
            data: rewritten,
            littleEndian: true
        )
        guard case .map(let roundTripEntries)? = roundTripValue.objectValue?["Lookup"] else {
            return XCTFail("Lookup map did not survive TypeTree writer round-trip.")
        }
        XCTAssertEqual(roundTripEntries.count, 1)
        if case .bytes(let roundTripBytes)? = roundTripValue.objectValue?["Payload"] {
            XCTAssertEqual(roundTripBytes, Data([0x10, 0x20, 0x30]))
        } else {
            XCTFail("TypelessData did not survive TypeTree writer round-trip.")
        }
    }

    func testReadsBigEndianValuesAndAlignedString() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "Root", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "string", name: "m_Name", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 2, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 3, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "char", name: "data", level: 3, byteSize: 1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "UInt32", name: "Checksum", level: 1, byteSize: 4, metaFlags: 0)
        ]
        var data = Data()
        appendAlignedString("verify", to: &data, littleEndian: false)
        appendBE(UInt32(0xA1B2C3D4), to: &data)

        let value = try AoVUnityTypeTreeReader.read(nodes: nodes, data: data, littleEndian: false)
        let root = try XCTUnwrap(value.objectValue)
        XCTAssertEqual(root["m_Name"]?.stringValue, "verify")
        guard case .unsigned(let checksum)? = root["Checksum"] else {
            return XCTFail("Checksum should be unsigned")
        }
        XCTAssertEqual(checksum, 0xA1B2C3D4)
    }

    func testTypeTreeWriterRoundTripsNestedPPtrsArraysAndAlignedStrings() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "MonoBehaviour", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<GameObject>", name: "m_GameObject", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 2, byteSize: 8, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "bool", name: "m_Enabled", level: 1, byteSize: 1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<MonoScript>", name: "m_Script", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 2, byteSize: 8, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "string", name: "m_Name", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "vector", name: "m_Pointers", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 2, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 3, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<Object>", name: "data", level: 3, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 4, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 4, byteSize: 8, metaFlags: 0)
        ]
        let original: AoVUnityTypeTreeValue = .object([
            "m_GameObject": .object([
                "m_FileID": .signed(0),
                "m_PathID": .signed(1001)
            ]),
            "m_Enabled": .boolean(true),
            "m_Script": .object([
                "m_FileID": .signed(1),
                "m_PathID": .signed(2002)
            ]),
            "m_Name": .string("AKI creator skin"),
            "m_Pointers": .array([
                .object(["m_FileID": .signed(0), "m_PathID": .signed(3003)]),
                .object(["m_FileID": .signed(2), "m_PathID": .signed(4004)])
            ])
        ])

        let encoded = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: original,
            littleEndian: true
        )
        let decoded = try AoVUnityTypeTreeReader.read(
            nodes: nodes,
            data: encoded,
            littleEndian: true
        )
        XCTAssertEqual(encoded, try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: decoded,
            littleEndian: true
        ))

        guard case .object(var fields) = decoded,
              case .object(var gameObject) = fields["m_GameObject"],
              case .array(var pointers) = fields["m_Pointers"],
              case .object(var firstPointer) = pointers.first else {
            return XCTFail("Decoded TypeTree object does not match the expected structured PPtr layout.")
        }
        gameObject["m_PathID"] = .signed(9001)
        fields["m_GameObject"] = .object(gameObject)
        firstPointer["m_PathID"] = .signed(9002)
        pointers[0] = .object(firstPointer)
        fields["m_Pointers"] = .array(pointers)
        let changed: AoVUnityTypeTreeValue = .object(fields)

        let rewritten = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: changed,
            littleEndian: true
        )
        let verified = try AoVUnityTypeTreeReader.read(
            nodes: nodes,
            data: rewritten,
            littleEndian: true
        )
        guard case .object(let verifiedFields) = verified,
              case .object(let verifiedGameObject)? = verifiedFields["m_GameObject"],
              case .signed(9001)? = verifiedGameObject["m_PathID"],
              case .array(let verifiedPointers)? = verifiedFields["m_Pointers"],
              case .object(let verifiedFirstPointer)? = verifiedPointers.first,
              case .signed(9002)? = verifiedFirstPointer["m_PathID"] else {
            return XCTFail("Updated PPtr pathIDs were not preserved by TypeTree round-trip.")
        }
    }

    func testTypeTreePointerRemapperAuditsAndRemapsLocalExternalAndArrayPointers() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "Root", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<GameObject>", name: "m_GameObject", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 2, byteSize: 8, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<Material>", name: "m_Material", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 2, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 2, byteSize: 8, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "vector", name: "m_References", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 2, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 3, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "PPtr<Object>", name: "data", level: 3, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "m_FileID", level: 4, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "SInt64", name: "m_PathID", level: 4, byteSize: 8, metaFlags: 0)
        ]
        let original: AoVUnityTypeTreeValue = .object([
            "m_GameObject": .object([
                "m_FileID": .signed(0),
                "m_PathID": .signed(1001)
            ]),
            "m_Material": .object([
                "m_FileID": .signed(1),
                "m_PathID": .signed(2002)
            ]),
            "m_References": .array([
                .object(["m_FileID": .signed(0), "m_PathID": .signed(3003)]),
                .object(["m_FileID": .signed(2), "m_PathID": .signed(4004)])
            ])
        ])

        let report = try AoVUnityTypeTreePointerRemapper.remap(
            nodes: nodes,
            value: original,
            localPathIDMap: [1001: 9001, 3003: 9003],
            availableLocalPathIDs: [9001, 9003],
            externalFileIDMap: [1: 3, 2: 4],
            externalPathIDMaps: [
                1: [2002: 8002],
                2: [4004: 8004]
            ],
            availableExternalTargetPathIDs: [
                3: [8002],
                4: [8004]
            ]
        )

        XCTAssertEqual(report.remappedLocalPointers, 2)
        XCTAssertEqual(report.remappedExternalPointers, 2)
        XCTAssertEqual(report.auditedExternalPointers, 2)
        guard case .object(let fields) = report.value,
              case .object(let gameObject)? = fields["m_GameObject"],
              case .object(let material)? = fields["m_Material"],
              case .array(let references)? = fields["m_References"],
              case .object(let firstReference)? = references.first,
              case .object(let secondReference)? = references.last else {
            return XCTFail("Remapped TypeTree PPtr structure is invalid.")
        }
        XCTAssertEqual(gameObject["m_FileID"]?.intValue, 0)
        XCTAssertEqual(gameObject["m_PathID"]?.intValue, 9001)
        XCTAssertEqual(material["m_FileID"]?.intValue, 3)
        XCTAssertEqual(material["m_PathID"]?.intValue, 8002)
        XCTAssertEqual(firstReference["m_PathID"]?.intValue, 9003)
        XCTAssertEqual(secondReference["m_FileID"]?.intValue, 4)
        XCTAssertEqual(secondReference["m_PathID"]?.intValue, 8004)

        XCTAssertThrowsError(
            try AoVUnityTypeTreePointerRemapper.remap(
                nodes: nodes,
                value: original,
                localPathIDMap: [1001: 9001, 3003: 9003],
                availableLocalPathIDs: [9001, 9003],
                externalFileIDMap: [1: 3, 2: 4],
                externalPathIDMaps: [1: [2002: 8002], 2: [4004: 8004]],
                availableExternalTargetPathIDs: [3: [8002], 4: [9999]]
            )
        ) { error in
            guard let remapError = error as? AoVUnityTypeTreePointerRemapError,
                  case .unresolvedExternalTarget = remapError else {
                return XCTFail("Expected unresolved external pointer rejection, got \(error)")
            }
        }
    }

    func testTypeTreeWriterRoundTripsBigEndianPrimitivesAndRejectsMissingFields() throws {
        let nodes = [
            AoVUnityTypeTreeNode(type: "Root", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "string", name: "m_Name", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "UInt32", name: "Checksum", level: 1, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "float", name: "Weight", level: 1, byteSize: 4, metaFlags: 0)
        ]
        let value: AoVUnityTypeTreeValue = .object([
            "m_Name": .string("big endian tree"),
            "Checksum": .unsigned(0xA1B2C3D4),
            "Weight": .floating(0.75)
        ])
        let encoded = try AoVUnityTypeTreeWriter.write(
            nodes: nodes,
            value: value,
            littleEndian: false
        )
        let decoded = try AoVUnityTypeTreeReader.read(
            nodes: nodes,
            data: encoded,
            littleEndian: false
        )
        XCTAssertEqual(decoded.objectValue?["m_Name"]?.stringValue, "big endian tree")
        XCTAssertEqual(decoded.objectValue?["Checksum"]?.intValue, Int64(0xA1B2C3D4))
        XCTAssertThrowsError(
            try AoVUnityTypeTreeWriter.write(
                nodes: nodes,
                value: .object(["m_Name": .string("missing fields")]),
                littleEndian: false
            )
        )
    }

    func testRejectsTruncatedArrayAndInvalidTreeLevels() {
        let nodes = [
            AoVUnityTypeTreeNode(type: "Root", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "vector", name: "Rows", level: 1, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "Array", name: "Array", level: 2, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "size", level: 3, byteSize: 4, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "data", level: 3, byteSize: 4, metaFlags: 0)
        ]
        XCTAssertThrowsError(
            try AoVUnityTypeTreeReader.read(nodes: nodes, data: Data([1, 0, 0, 0]), littleEndian: true)
        )

        let invalid = [
            AoVUnityTypeTreeNode(type: "Root", name: "Base", level: 0, byteSize: -1, metaFlags: 0),
            AoVUnityTypeTreeNode(type: "int", name: "bad", level: 2, byteSize: 4, metaFlags: 0)
        ]
        XCTAssertThrowsError(
            try AoVUnityTypeTreeReader.read(nodes: invalid, data: Data(repeating: 0, count: 4), littleEndian: true)
        )
    }

    private func appendAlignedString(_ value: String, to data: inout Data, littleEndian: Bool = true) {
        let bytes = Data(value.utf8)
        if littleEndian {
            appendLE(Int32(bytes.count), to: &data)
        } else {
            appendBE(UInt32(bytes.count), to: &data)
        }
        data.append(bytes)
        while data.count % 4 != 0 { data.append(0) }
    }

    private func appendLE(_ value: Int32, to data: inout Data) {
        let bits = UInt32(bitPattern: value)
        data.append(UInt8(bits & 0xFF))
        data.append(UInt8((bits >> 8) & 0xFF))
        data.append(UInt8((bits >> 16) & 0xFF))
        data.append(UInt8((bits >> 24) & 0xFF))
    }

    private func appendLE(_ value: UInt32, to data: inout Data) {
        data.append(UInt8(value & 0xFF))
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8((value >> 16) & 0xFF))
        data.append(UInt8((value >> 24) & 0xFF))
    }

    private func appendBE(_ value: UInt32, to data: inout Data) {
        data.append(UInt8((value >> 24) & 0xFF))
        data.append(UInt8((value >> 16) & 0xFF))
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
    }
}
