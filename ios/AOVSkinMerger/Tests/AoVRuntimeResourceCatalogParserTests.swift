import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVRuntimeResourceCatalogParserTests: XCTestCase {
    func testParsesResourcePackerMonoBehaviourCatalog() throws {
        var writer = AoVCatalogTestWriter()
        writer.appendBytes(Data(repeating: 0, count: 12))
        writer.appendByte(1)
        writer.align(4)
        writer.appendBytes(Data(repeating: 0, count: 12))
        writer.appendAlignedString("ResourcePackerInfoSetXML")
        writer.appendAlignedString("1.64.1.12")
        writer.appendAlignedString("1793129")
        writer.appendAlignedString("ResourcePackerInfoSetXML")
        writer.appendInt32(1)
        writer.appendAlignedString("assetbundle/test.assetbundle")
        writer.appendInt32(0)
        writer.appendInt64(123456)
        writer.appendAlignedString("CharBattle")
        writer.appendUInt16(512)
        writer.align(4)
        writer.appendInt32(2)
        writer.appendInt64(987654)
        writer.appendAlignedString("")
        writer.appendInt64(-123456)
        writer.appendAlignedString("mp4")
        writer.appendInt32(1)
        writer.appendInt32(0)
        writer.appendInt32(0)

        let result = try AoVRuntimeResourceCatalogParser.inspectMonoBehaviour(
            writer.data,
            serializedFormatVersion: 22,
            unityVersion: "2022.3.5f1",
            littleEndian: true
        )

        guard case .packer(let catalog)? = result else {
            return XCTFail("Expected ResourcePackerInfoSetXML catalog")
        }
        XCTAssertEqual(catalog.version, "1.64.1.12")
        XCTAssertEqual(catalog.svnRevision, "1793129")
        XCTAssertEqual(catalog.setName, "ResourcePackerInfoSetXML")
        XCTAssertEqual(catalog.allResourcePackInfo.count, 1)
        XCTAssertEqual(catalog.allResourcePackInfo[0].pathInIFS, "assetbundle/test.assetbundle")
        XCTAssertEqual(catalog.allResourcePackInfo[0].pkgType, 0)
        XCTAssertEqual(catalog.allResourcePackInfo[0].crcXor, 123456)
        XCTAssertEqual(catalog.allResourcePackInfo[0].tag, "CharBattle")
        XCTAssertEqual(catalog.allResourcePackInfo[0].flags, 512)
        XCTAssertEqual(catalog.allResourcePackInfo[0].resourceInfos.map(\.hashCode), [987654, -123456])
        XCTAssertEqual(catalog.allResourcePackInfo[0].resourceInfos.map(\.extensionName), ["", "mp4"])
        XCTAssertEqual(catalog.allParentIndexToChild, [AoVPackInfoMapItem(parentIdx: 0, childIdx: 0)])
        XCTAssertTrue(catalog.trailingData.isEmpty)
    }

    func testParsesResourceVerificationMonoBehaviourCatalog() throws {
        var writer = AoVCatalogTestWriter()
        writer.appendBytes(Data(repeating: 0, count: 12))
        writer.appendByte(1)
        writer.align(4)
        writer.appendBytes(Data(repeating: 0, count: 12))
        writer.appendAlignedString("ResourceVerificationInfoSetXML")
        writer.appendAlignedString("1.64.1.12")
        writer.appendAlignedString("1793129")
        writer.appendInt32(1)
        writer.appendAlignedString("CommonActions.pkg.bytes")
        writer.appendInt64(9_876_543_210)
        writer.appendInt32(1)
        writer.appendAlignedString("Databin/Client/Hero.bytes")
        writer.appendInt64(-987654321)

        let result = try AoVRuntimeResourceCatalogParser.inspectMonoBehaviour(
            writer.data,
            serializedFormatVersion: 22,
            unityVersion: "2022.3.5f1",
            littleEndian: true
        )

        guard case .verification(let catalog)? = result else {
            return XCTFail("Expected ResourceVerificationInfoSetXML catalog")
        }
        XCTAssertEqual(catalog.version, "1.64.1.12")
        XCTAssertEqual(catalog.svnRevision, "1793129")
        XCTAssertEqual(catalog.allZipVerificationInfo, [
            AoVRuntimeVerificationEntry(pathInIFS: "CommonActions.pkg.bytes", crcXor: 9_876_543_210)
        ])
        XCTAssertEqual(catalog.allDatabinVerificationInfo, [
            AoVRuntimeVerificationEntry(pathInIFS: "Databin/Client/Hero.bytes", crcXor: -987654321)
        ])
        XCTAssertTrue(catalog.trailingData.isEmpty)
    }

    func testIgnoresUnrelatedMonoBehaviourAndUnsupportedUnityLayout() throws {
        var writer = AoVCatalogTestWriter()
        writer.appendBytes(Data(repeating: 0, count: 12))
        writer.appendByte(1)
        writer.align(4)
        writer.appendBytes(Data(repeating: 0, count: 12))
        writer.appendAlignedString("SomeOtherGameBehaviour")

        XCTAssertNil(try AoVRuntimeResourceCatalogParser.inspectMonoBehaviour(
            writer.data,
            serializedFormatVersion: 22,
            unityVersion: "2022.3.5f1",
            littleEndian: true
        ))
        XCTAssertNil(try AoVRuntimeResourceCatalogParser.inspectMonoBehaviour(
            writer.data,
            serializedFormatVersion: 21,
            unityVersion: "2022.3.5f1",
            littleEndian: true
        ))
    }

    func testKnownCatalogWithTruncatedPayloadFailsClosed() throws {
        var writer = AoVCatalogTestWriter()
        writer.appendBytes(Data(repeating: 0, count: 12))
        writer.appendByte(1)
        writer.align(4)
        writer.appendBytes(Data(repeating: 0, count: 12))
        writer.appendAlignedString("ResourceVerificationInfoSetXML")
        writer.appendAlignedString("1.64.1.12")

        XCTAssertThrowsError(try AoVRuntimeResourceCatalogParser.inspectMonoBehaviour(
            writer.data,
            serializedFormatVersion: 22,
            unityVersion: "2022.3.5f1",
            littleEndian: true
        ))
    }
}

private struct AoVCatalogTestWriter {
    var data = Data()

    mutating func appendByte(_ value: UInt8) {
        data.append(value)
    }

    mutating func appendBytes(_ value: Data) {
        data.append(value)
    }

    mutating func align(_ boundary: Int) {
        while data.count % boundary != 0 { data.append(0) }
    }

    mutating func appendUInt16(_ value: UInt16) {
        data.append(UInt8(truncatingIfNeeded: value))
        data.append(UInt8(truncatingIfNeeded: value >> 8))
    }

    mutating func appendInt32(_ value: Int32) {
        let bits = UInt32(bitPattern: value)
        for shift in stride(from: 0, through: 24, by: 8) {
            data.append(UInt8(truncatingIfNeeded: bits >> shift))
        }
    }

    mutating func appendInt64(_ value: Int64) {
        let bits = UInt64(bitPattern: value)
        for shift in stride(from: 0, through: 56, by: 8) {
            data.append(UInt8(truncatingIfNeeded: bits >> shift))
        }
    }

    mutating func appendAlignedString(_ value: String) {
        let bytes = Data(value.utf8)
        appendInt32(Int32(bytes.count))
        data.append(bytes)
        align(4)
    }
}
