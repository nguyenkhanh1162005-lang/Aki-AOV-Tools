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
        appendLE(Int32(bytes.count), to: &data)
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
