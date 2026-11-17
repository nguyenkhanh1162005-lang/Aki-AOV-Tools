import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVSerializedFileRewriterTests: XCTestCase {
    func testReplacesAssetBundleAndRebuildsObjectSizeAndFileSize() throws {
        let original = makeSerializedFile(formatVersion: 17, littleEndian: true)
        let originalSummary = try AoVSerializedFileParser.inspect(original)
        let record = try XCTUnwrap(originalSummary.assetBundleRecords.first)
        let replacement = replacing(record, name: "catalog-with-a-longer-name", dependencies: ["common", "actor/1", "new/shader"])

        let report = try AoVSerializedFileRewriter.replaceAssetBundleRecord(
            in: original,
            pathID: record.pathID,
            with: replacement
        )

        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(report.replacedPathIDs, [record.pathID])
        XCTAssertGreaterThan(report.rewrittenFileSize, report.originalFileSize)

        let output = try AoVSerializedFileParser.inspect(report.data)
        let rewritten = try XCTUnwrap(output.assetBundleRecords.first)
        XCTAssertEqual(rewritten, replacement)
        XCTAssertEqual(output.fileSize, UInt64(report.data.count))
        XCTAssertEqual(output.serializedObjects.first?.byteSize, report.rewrittenObjectSizes[record.pathID])
    }

    func testReplacesBigEndianAssetBundleObject() throws {
        let original = makeSerializedFile(formatVersion: 17, littleEndian: false)
        let record = try XCTUnwrap(AoVSerializedFileParser.inspect(original).assetBundleRecords.first)
        let replacement = replacing(record, name: "catalog-be", dependencies: ["common", "fx/new"])

        let report = try AoVSerializedFileRewriter.replaceAssetBundleRecord(
            in: original,
            pathID: record.pathID,
            with: replacement
        )
        let output = try AoVSerializedFileParser.inspect(report.data)

        XCTAssertFalse(output.littleEndian)
        XCTAssertEqual(output.assetBundleRecords.first, replacement)
    }

    func testReplacesUnityExtendedHeaderSerializedFile() throws {
        let original = makeSerializedFile(formatVersion: 22, littleEndian: true)
        let record = try XCTUnwrap(AoVSerializedFileParser.inspect(original).assetBundleRecords.first)
        let replacement = replacing(record, name: "catalog-unity-2020", dependencies: ["common", "new/dep"])

        let report = try AoVSerializedFileRewriter.replaceAssetBundleRecord(
            in: original,
            pathID: record.pathID,
            with: replacement
        )
        let output = try AoVSerializedFileParser.inspect(report.data)

        XCTAssertEqual(output.formatVersion, 22)
        XCTAssertEqual(output.assetBundleRecords.first, replacement)
    }

    func testRejectsMissingAssetBundleObjectReplacement() throws {
        let original = makeSerializedFile()
        let record = try XCTUnwrap(AoVSerializedFileParser.inspect(original).assetBundleRecords.first)
        let replacement = replacing(record, name: "missing-target", dependencies: ["common"])

        XCTAssertThrowsError(
            try AoVSerializedFileRewriter.replaceAssetBundleRecord(
                in: original,
                pathID: 999,
                with: replacement
            )
        )
    }

    private func replacing(
        _ record: AoVSerializedAssetBundleRecord,
        name: String,
        dependencies: [String]
    ) -> AoVSerializedAssetBundleRecord {
        // The minimal fixture contains only the AssetBundle object itself.
        // Null PPtrs are valid and keep this writer test focused on serialization,
        // size/offset patching and round-trip preservation rather than graph import.
        let nullPointer = AoVSerializedAssetPointer(fileID: 0, pathID: 0)
        return AoVSerializedAssetBundleRecord(
            pathID: record.pathID,
            name: name,
            preloadReferences: record.preloadReferences.map { _ in nullPointer },
            containerEntries: record.containerEntries.map {
                AoVSerializedAssetBundleEntry(
                    key: $0.key,
                    preloadIndex: $0.preloadIndex,
                    preloadSize: $0.preloadSize,
                    asset: nullPointer
                )
            },
            mainAsset: nullPointer,
            mainAssetPreloadIndex: record.mainAssetPreloadIndex,
            mainAssetPreloadSize: record.mainAssetPreloadSize,
            scriptCompatibilityEntries: record.scriptCompatibilityEntries,
            classCompatibilityIDs: record.classCompatibilityIDs,
            classCompatibilityEntries: record.classCompatibilityEntries.map {
                AoVSerializedClassCompatibilityEntry(
                    classID: $0.classID,
                    info: AoVSerializedAssetInfo(
                        preloadIndex: $0.info.preloadIndex,
                        preloadSize: $0.info.preloadSize,
                        asset: nullPointer
                    )
                )
            },
            runtimeCompatibility: record.runtimeCompatibility,
            assetBundleName: record.assetBundleName + "-updated",
            dependencies: dependencies,
            isStreamedSceneAssetBundle: record.isStreamedSceneAssetBundle,
            trailingBytes: record.trailingBytes,
            trailingData: record.trailingData
        )
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
            appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian)
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
        appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian)
        if formatVersion >= 20 {
            appendSerialized(Int32(0), to: &metadata, littleEndian: littleEndian)
        }
        metadata.append(0)

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
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int64(7001), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
        appendAlignedString("assets/hero.prefab", to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int64(7002), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
        appendSerialized(Int32(0), to: &object, littleEndian: littleEndian)
        appendSerialized(Int64(7003), to: &object, littleEndian: littleEndian)
        if unityVersion.hasPrefix("5.4.") {
            appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
            appendSerialized(Int32(111), to: &object, littleEndian: littleEndian)
            appendSerialized(Int32(222), to: &object, littleEndian: littleEndian)
        }
        appendSerialized(Int32(1), to: &object, littleEndian: littleEndian)
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
