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
        XCTAssertEqual(summary.assetBundleRecords.first?.preloadReferences.first?.fileID, 0)
        XCTAssertEqual(summary.assetBundleRecords.first?.preloadReferences.first?.pathID, 7001)
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.count, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.key, "assets/hero.prefab")
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.preloadIndex, 0)
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.preloadSize, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.asset.pathID, 7002)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAsset.pathID, 7003)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAssetPreloadIndex, 0)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAssetPreloadSize, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.classCompatibilityIDs, [28])
        XCTAssertEqual(summary.assetBundleRecords.first?.classCompatibilityEntries.first?.info.asset.pathID, 7004)
        XCTAssertEqual(summary.assetBundleRecords.first?.classCompatibilityEntries.first?.info.preloadSize, 1)
        XCTAssertEqual(summary.assetBundleRecords.first?.trailingBytes, 0)
    }



    func testParsesUnity2020ExtendedSerializedFileHeader() throws {
        let data = makeSerializedFile(formatVersion: 22, littleEndian: false)
        let summary = try AoVSerializedFileParser.inspect(data)

        XCTAssertEqual(summary.formatVersion, 22)
        XCTAssertEqual(summary.fileSize, UInt64(data.count))
        XCTAssertEqual(summary.assetBundleRecords.first?.assetBundleName, "resource-packer")
        XCTAssertEqual(summary.assetBundleRecords.first?.dependencies, ["common", "actor/1"])
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.key, "assets/hero.prefab")
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.asset.pathID, 7002)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAsset.pathID, 7003)
    }

    func testParsesBigEndianSerializedFileAndAssetBundleObject() throws {
        let data = makeSerializedFile(littleEndian: false)
        let summary = try AoVSerializedFileParser.inspect(data)

        XCTAssertEqual(summary.formatVersion, 17)
        XCTAssertEqual(summary.targetPlatform, 13)
        XCTAssertEqual(summary.assetBundleRecords.first?.name, "catalog")
        XCTAssertEqual(summary.assetBundleRecords.first?.assetBundleName, "resource-packer")
        XCTAssertEqual(summary.assetBundleRecords.first?.dependencies, ["common", "actor/1"])
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.key, "assets/hero.prefab")
        XCTAssertEqual(summary.assetBundleRecords.first?.containerEntries.first?.asset.pathID, 7002)
        XCTAssertEqual(summary.assetBundleRecords.first?.mainAsset.pathID, 7003)
    }

    func testParsesUnity54ScriptCompatibilityEntries() throws {
        let data = makeSerializedFile(unityVersion: "5.4.0f3")
        let summary = try AoVSerializedFileParser.inspect(data)
        let record = try XCTUnwrap(summary.assetBundleRecords.first)

        XCTAssertEqual(record.scriptCompatibilityEntries.count, 1)
        XCTAssertEqual(record.scriptCompatibilityEntries.first?.first, 111)
        XCTAssertEqual(record.scriptCompatibilityEntries.first?.second, 222)
        XCTAssertEqual(record.classCompatibilityEntries.first?.info.asset.pathID, 7004)
        XCTAssertEqual(record.assetBundleName, "resource-packer")
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
        objectPayload: Data? = nil,
        littleEndian: Bool = true,
        unityVersion: String = "2020.3.34f1"
    ) -> Data {
        let payload = objectPayload ?? makeAssetBundleObject(
            littleEndian: littleEndian,
            unityVersion: unityVersion
        )
        var metadata = Data()
        metadata.append(Data((unityVersion + "\0").utf8))
        appendSerialized(targetPlatform, to: &metadata, littleEndian: littleEndian)
        metadata.append(0)
        appendSerialized(Int32(1), to: &metadata, littleEndian: littleEndian)
        appendSerialized(Int32(142), to: &metadata, littleEndian: littleEndian)
        metadata.append(0)
        appendSerialized(Int16(-1), to: &metadata, littleEndian: littleEndian)
        metadata.append(Data(repeating: 0, count: 16))
        if formatVersion >= 21 {
            appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian) // type dependencies
        }
        appendSerialized(Int32(1), to: &metadata, littleEndian: littleEndian)
        align4(&metadata)
        appendSerialized(Int64(1234), to: &metadata, littleEndian: littleEndian)
        if formatVersion >= 22 {
            appendSerialized(Int64(Int64(objectByteStart)), to: &metadata, littleEndian: littleEndian)
        } else {
            appendSerialized(objectByteStart, to: &metadata, littleEndian: littleEndian)
        }
        appendSerialized(UInt32(payload.count), to: &metadata, littleEndian: littleEndian)
        appendSerialized(objectTypeIndex, to: &metadata, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian) // script count
        appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian) // external count
        if formatVersion >= 20 {
            appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian) // reference types
        }
        metadata.append(0) // user information

        let headerSize = formatVersion >= 22 ? 48 : 20
        let dataOffset = ((headerSize + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + payload.count

        var file = Data()
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt32(fileSize), to: &file)
        appendBE(formatVersion, to: &file)
        appendBE(UInt32(dataOffset), to: &file)
        file.append(littleEndian ? 0 : 1)
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

    private func makeAssetBundleObject(
        littleEndian: Bool,
        unityVersion: String
    ) -> Data {
        var object = Data()
        appendAlignedString("catalog", to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // preload count
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int64(7001), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // container count
        appendAlignedString("assets/hero.prefab", to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian) // preload index
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // preload size
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int64(7002), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian) // main preload index
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // main preload size
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int64(7003), to: &object, littleEndian: littleEndian)
        if unityVersion.hasPrefix("5.4.") {
            appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
            appendSerialized(Int32(111), to: &object, littleEndian: littleEndian)
            appendSerialized(Int32(222), to: &object, littleEndian: littleEndian)
        }
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian) // class compatibility count
        appendSerialized(Int32(28), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int64(7004), to: &object, littleEndian: littleEndian)
        appendSerialized(UInt32(0), to: &object, littleEndian: littleEndian)
        appendAlignedString("resource-packer", to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(2), to: &object, littleEndian: littleEndian)
        appendAlignedString("common", to: &object, littleEndian: littleEndian)
        appendAlignedString("actor/1", to: &object, littleEndian: littleEndian)
        object.append(0)
        return object
    }

    private func appendAlignedString(_ value: String, to data: inout Data, littleEndian: Bool) {
        let bytes = Data(value.utf8)
        appendSerialized(Int32(bytes.count), to: &data, littleEndian: littleEndian)
        data.append(bytes)
        align4(&data)
    }

    private func appendSerialized(_ value: Int16, to data: inout Data, littleEndian: Bool) {
        let bits = UInt16(bitPattern: value)
        if littleEndian {
            data.append(UInt8(bits & 0xFF))
            data.append(UInt8((bits >> 8) & 0xFF))
        } else {
            data.append(UInt8((bits >> 8) & 0xFF))
            data.append(UInt8(bits & 0xFF))
        }
    }

    private func appendSerialized(_ value: Int32, to data: inout Data, littleEndian: Bool) {
        appendSerialized(UInt32(bitPattern: value), to: &data, littleEndian: littleEndian)
    }

    private func appendSerialized(_ value: UInt32, to data: inout Data, littleEndian: Bool) {
        if littleEndian {
            data.append(UInt8(value & 0xFF))
            data.append(UInt8((value >> 8) & 0xFF))
            data.append(UInt8((value >> 16) & 0xFF))
            data.append(UInt8((value >> 24) & 0xFF))
        } else {
            appendBE(value, to: &data)
        }
    }

    private func appendSerialized(_ value: Int64, to data: inout Data, littleEndian: Bool) {
        let bits = UInt64(bitPattern: value)
        if littleEndian {
            for shift in stride(from: 0, through: 56, by: 8) {
                data.append(UInt8((bits >> UInt64(shift)) & 0xFF))
            }
        } else {
            appendBE(bits, to: &data)
        }
    }

    private func align4(_ data: inout Data) {
        while data.count % 4 != 0 { data.append(0) }
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
