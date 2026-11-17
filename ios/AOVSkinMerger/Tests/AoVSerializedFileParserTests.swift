import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVSerializedFileParserTests: XCTestCase {
    func testInspectsMinimalUnity2020SerializedFileAndFindsAssetBundleObject() throws {
        let data = makeSerializedFile()
        let summary = try AoVSerializedFileParser.inspect(data)

        XCTAssertEqual(summary.formatVersion, 17)
        XCTAssertEqual(summary.unityVersion, "2020.3.34f1")
        XCTAssertEqual(summary.targetPlatform, 13)
        XCTAssertEqual(summary.typeCount, 1)
        XCTAssertEqual(summary.objectCount, 1)
        XCTAssertEqual(summary.assetBundleObjectCount, 1)
        XCTAssertEqual(summary.assetBundleObjects.first?.pathID, 1234)
        XCTAssertEqual(summary.assetBundleObjects.first?.classID, 142)
        XCTAssertGreaterThan(summary.assetBundleObjects.first?.byteSize ?? 0, 4)
        XCTAssertEqual(summary.assetBundleRecords.count, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.name, "catalog")
        XCTAssertEqual(summary.assetBundleRecords.first?.assetBundleName, "resource-packer")
        XCTAssertEqual(summary.assetBundleRecords.first?.dependencies, ["common", "actor/1"])
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.count, 0)
    }

    func testRejectsTruncatedAssetBundleObject() {
        let data = makeSerializedFile(objectPayload: Data([0x01, 0x02]))
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data))
    }

    func testRefusesUnverifiedNoTargetBaseLayout() {
        let data = makeSerializedFile(targetPlatform: -2)
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data)) { error in
            guard let parserError = error as? AoVSerializedFileError,
                  case .unsupportedTargetPlatform(-2) = parserError else {
                return XCTFail("Expected unsupported target platform, got \(error)")
            }
        }
    }

    func testRejectsInvalidObjectRange() {
        let data = makeSerializedFile(objectByteStart: UInt32.max)
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data))
    }

    func testRejectsInvalidTypeIndex() {
        let data = makeSerializedFile(objectTypeIndex: 3)
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data))
    }

    func testRejectsUnknownFormatVersion() {
        let data = makeSerializedFile(formatVersion: 31)
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(data))
    }

    func testRejectsTruncatedInput() {
        XCTAssertThrowsError(try AoVSerializedFileParser.inspect(Data(repeating: 0, count: 19)))
    }

    private func makeSerializedFile(
        formatVersion: UInt32 = 17,
        objectByteStart: UInt32 = 0,
        objectTypeIndex: Int32 = 0,
        targetPlatform: Int32 = 13,
        objectPayload: Data? = nil
    ) -> Data {
        let payload = objectPayload ?? makeAssetBundleObject()
        var metadata = Data()
        metadata.append(Data("2020.3.34f1\0".utf8))
        appendLE(targetPlatform, to: &metadata)
        metadata.append(0) // no embedded type tree
        appendLE(Int32(1), to: &metadata) // type count
        appendLE(Int32(142), to: &metadata) // AssetBundle class ID
        metadata.append(0) // stripped type
        appendLE(Int16(-1), to: &metadata) // script type index
        metadata.append(Data(repeating: 0, count: 16)) // old type hash
        appendLE(Int32(1), to: &metadata) // object count
        align4(&metadata)
        appendLE(Int64(1234), to: &metadata) // path ID
        appendLE(objectByteStart, to: &metadata)
        appendLE(UInt32(payload.count), to: &metadata) // object size
        appendLE(objectTypeIndex, to: &metadata)
        appendLE(Int32(0), to: &metadata) // script count
        appendLE(Int32(0), to: &metadata) // external count
        metadata.append(0) // user information CString

        let headerSize = formatVersion >= 22 ? 48 : 20
        let dataOffset = ((headerSize + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + payload.count

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
        file.append(payload)
        return file
    }

    private func makeAssetBundleObject() -> Data {
        var object = Data()
        appendAlignedString("catalog", to: &object)
        appendLE(Int32(0), to: &object) // preload count
        appendLE(Int32(0), to: &object) // container count
        appendLE(Int32(0), to: &object) // main asset preload index
        appendLE(Int32(0), to: &object) // main asset preload size
        appendLE(Int32(0), to: &object) // main asset file ID
        appendLE(Int64(0), to: &object) // main asset path ID
        appendLE(Int32(0), to: &object) // class compatibility count
        appendLE(UInt32(0), to: &object) // runtime compatibility
        appendAlignedString("resource-packer", to: &object)
        appendLE(Int32(2), to: &object) // dependencies count
        appendAlignedString("common", to: &object)
        appendAlignedString("actor/1", to: &object)
        object.append(0) // streamed scene bundle false
        return object
    }

    private func appendAlignedString(_ value: String, to data: inout Data) {
        let bytes = Data(value.utf8)
        appendLE(Int32(bytes.count), to: &data)
        data.append(bytes)
        align4(&data)
    }

    private func align4(_ data: inout Data) {
        while data.count % 4 != 0 { data.append(0) }
    }

    private func appendLE(_ value: Int16, to data: inout Data) {
        let bits = UInt16(bitPattern: value)
        data.append(UInt8(bits & 0xFF))
        data.append(UInt8((bits >> 8) & 0xFF))
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

    private func appendLE(_ value: Int64, to data: inout Data) {
        let bits = UInt64(bitPattern: value)
        appendLE(bits, to: &data)
    }

    private func appendLE(_ value: UInt64, to data: inout Data) {
        for shift in stride(from: 0, through: 56, by: 8) {
            data.append(UInt8((value >> UInt64(shift)) & 0xFF))
        }
    }

    private func appendBE(_ value: UInt32, to data: inout Data) {
        data.append(UInt8((value >> 24) & 0xFF))
        data.append(UInt8((value >> 16) & 0xFF))
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
    }

    private func appendBE(_ value: UInt64, to data: inout Data) {
        appendBE(UInt32((value >> 32) & 0xFFFF_FFFF), to: &data)
        appendBE(UInt32(value & 0xFFFF_FFFF), to: &data)
    }
}
