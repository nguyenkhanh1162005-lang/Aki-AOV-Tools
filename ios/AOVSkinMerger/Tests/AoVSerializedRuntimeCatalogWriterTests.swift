import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVSerializedRuntimeCatalogWriterTests: XCTestCase {
    func testRewritesPackerMonoBehaviourAndSerializedFileRoundTrips() throws {
        let original = makeSerializedFile(with: makePackerCatalog())
        let summary = try AoVSerializedFileParser.inspect(original)
        let object = try XCTUnwrap(summary.serializedObjects.first(where: { $0.classID == 114 }))
        let oldCatalog = try XCTUnwrap(summary.resourcePackerCatalog)
        XCTAssertEqual(oldCatalog.version, "1.64.1.12")
        XCTAssertEqual(oldCatalog.allResourcePackInfo.count, 1)

        let replacement = AoVRuntimeResourcePackerCatalog(
            name: oldCatalog.name,
            version: "1.64.2.1",
            svnRevision: "1800000",
            setName: oldCatalog.setName,
            allResourcePackInfo: [
                AoVRuntimeResourcePackInfo(
                    pathInIFS: "assetbundle/current.assetbundle",
                    pkgType: 0,
                    crcXor: 987654,
                    tag: "Hero",
                    flags: 512,
                    resourceInfos: [
                        AoVRuntimeResourceItem(hashCode: 123456, extensionName: "assetbundle"),
                        AoVRuntimeResourceItem(hashCode: -9876, extensionName: "bytes")
                    ]
                ),
                AoVRuntimeResourcePackInfo(
                    pathInIFS: "assetbundle/new.assetbundle",
                    pkgType: 1,
                    crcXor: 111222,
                    tag: "BattleUI",
                    flags: 0,
                    resourceInfos: []
                )
            ],
            allParentIndexToChild: [
                AoVPackInfoMapItem(parentIdx: 0, childIdx: 1)
            ],
            trailingData: oldCatalog.trailingData
        )

        let report = try AoVSerializedRuntimeCatalogWriter.rewrite(
            serializedFile: original,
            objectPathID: object.pathID,
            with: .packer(replacement)
        )
        let verified = try AoVSerializedFileParser.inspect(report.data)
        let rewrittenObject = try XCTUnwrap(
            verified.serializedObjects.first(where: { $0.pathID == object.pathID })
        )

        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(report.catalogName, "ResourcePackerInfoSetXML")
        XCTAssertGreaterThan(report.rewrittenFileBytes, report.originalFileBytes)
        XCTAssertEqual(verified.fileSize, UInt64(report.data.count))
        XCTAssertEqual(verified.resourcePackerCatalog, replacement)
        XCTAssertEqual(verified.resourceVerificationCatalog, nil)
        XCTAssertEqual(rewrittenObject.classID, 114)
        XCTAssertGreaterThan(rewrittenObject.byteOffset, object.byteOffset)
    }

    func testRewritesVerificationMonoBehaviourAndKeepsTrailingBytes() throws {
        let oldCatalog = makeVerificationCatalog()
        let original = makeSerializedFile(with: oldCatalog)
        let summary = try AoVSerializedFileParser.inspect(original)
        let object = try XCTUnwrap(summary.serializedObjects.first(where: { $0.classID == 114 }))

        let replacement = AoVRuntimeResourceVerificationCatalog(
            name: oldCatalog.name,
            version: "1.64.2.1",
            svnRevision: "1800000",
            allZipVerificationInfo: [
                AoVRuntimeVerificationEntry(
                    pathInIFS: "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes",
                    crcXor: 999999
                ),
                AoVRuntimeVerificationEntry(
                    pathInIFS: "New/Legacy.pkg.bytes",
                    crcXor: 222333
                )
            ],
            allDatabinVerificationInfo: [
                AoVRuntimeVerificationEntry(
                    pathInIFS: "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes",
                    crcXor: -123456
                )
            ],
            trailingData: oldCatalog.trailingData
        )

        let report = try AoVSerializedRuntimeCatalogWriter.rewrite(
            serializedFile: original,
            objectPathID: object.pathID,
            with: .verification(replacement)
        )
        let verified = try AoVSerializedFileParser.inspect(report.data)

        XCTAssertTrue(report.roundTripVerified)
        XCTAssertEqual(verified.resourceVerificationCatalog, replacement)
        XCTAssertNil(verified.resourcePackerCatalog)
        XCTAssertEqual(verified.fileSize, UInt64(report.data.count))
    }

    func testRuntimeCatalogWriterRejectsWrongObjectAndInvalidIndexes() throws {
        let original = makeSerializedFile(with: makePackerCatalog())
        XCTAssertThrowsError(
            try AoVSerializedRuntimeCatalogWriter.rewrite(
                serializedFile: original,
                objectPathID: 987654,
                with: .packer(makePackerCatalog())
            )
        )

        let old = makePackerCatalog()
        let invalid = AoVRuntimeResourcePackerCatalog(
            name: old.name,
            version: old.version,
            svnRevision: old.svnRevision,
            setName: old.setName,
            allResourcePackInfo: old.allResourcePackInfo,
            allParentIndexToChild: [
                AoVPackInfoMapItem(parentIdx: 0, childIdx: 9)
            ],
            trailingData: old.trailingData
        )
        let summary = try AoVSerializedFileParser.inspect(original)
        let object = try XCTUnwrap(summary.serializedObjects.first(where: { $0.classID == 114 }))
        XCTAssertThrowsError(
            try AoVSerializedRuntimeCatalogWriter.rewrite(
                serializedFile: original,
                objectPathID: object.pathID,
                with: .packer(invalid)
            )
        )
    }

    private func makePackerCatalog() -> AoVRuntimeResourcePackerCatalog {
        AoVRuntimeResourcePackerCatalog(
            name: "ResourcePackerInfoSetXML",
            version: "1.64.1.12",
            svnRevision: "1793129",
            setName: "ResourcePackerInfoSetXML",
            allResourcePackInfo: [
                AoVRuntimeResourcePackInfo(
                    pathInIFS: "assetbundle/current.assetbundle",
                    pkgType: 0,
                    crcXor: 100,
                    tag: "Hero",
                    flags: 512,
                    resourceInfos: [
                        AoVRuntimeResourceItem(hashCode: 111, extensionName: "assetbundle")
                    ]
                )
            ],
            allParentIndexToChild: [
                AoVPackInfoMapItem(parentIdx: 0, childIdx: 0)
            ],
            trailingData: Data([0xAA, 0xBB, 0xCC])
        )
    }

    private func makeVerificationCatalog() -> AoVRuntimeResourceVerificationCatalog {
        AoVRuntimeResourceVerificationCatalog(
            name: "ResourceVerificationInfoSetXML",
            version: "1.64.1.12",
            svnRevision: "1793129",
            allZipVerificationInfo: [
                AoVRuntimeVerificationEntry(
                    pathInIFS: "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes",
                    crcXor: 123456
                )
            ],
            allDatabinVerificationInfo: [
                AoVRuntimeVerificationEntry(
                    pathInIFS: "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes",
                    crcXor: 654321
                )
            ],
            trailingData: Data([0x44, 0x55])
        )
    }

    private func makeSerializedFile(with catalog: AoVRuntimeResourceCatalog) -> Data {
        let name: String
        switch catalog {
        case .packer(let value): name = value.name
        case .verification(let value): name = value.name
        }
        let objectPayload = makeMonoBehaviour(name: name, catalog: catalog)
        var metadata = Data()
        appendCString("2022.3.5f1", to: &metadata)
        appendInt32(9, to: &metadata)
        metadata.append(0) // type tree disabled
        appendInt32(1, to: &metadata) // type count
        appendInt32(114, to: &metadata) // MonoBehaviour
        metadata.append(0) // stripped
        appendInt16(-1, to: &metadata) // script type index
        metadata.append(Data(repeating: 0, count: 16)) // script ID
        metadata.append(Data(repeating: 0, count: 16)) // old type hash
        appendInt32(0, to: &metadata) // type dependencies
        appendInt32(1, to: &metadata) // object count
        align4(&metadata)
        appendInt64(4321, to: &metadata) // path ID
        appendInt64(0, to: &metadata) // relative byte start
        appendUInt32(UInt32(objectPayload.count), to: &metadata)
        appendInt32(0, to: &metadata) // type ID
        appendInt32(0, to: &metadata) // script count
        appendInt32(0, to: &metadata) // external refs
        appendInt32(0, to: &metadata) // reference types
        appendCString("", to: &metadata) // user info

        let headerSize = 48
        let dataOffset = ((headerSize + metadata.count + 15) / 16) * 16
        let fileSize = dataOffset + objectPayload.count
        var file = Data()
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt32(fileSize), to: &file)
        appendBE(UInt32(22), to: &file)
        appendBE(UInt32(dataOffset), to: &file)
        file.append(0) // little-endian serialized data
        file.append(contentsOf: [0, 0, 0])
        appendBE(UInt32(metadata.count), to: &file)
        appendBE(UInt64(fileSize), to: &file)
        appendBE(UInt64(dataOffset), to: &file)
        appendBE(UInt64(0), to: &file)
        file.append(metadata)
        while file.count < dataOffset { file.append(0) }
        file.append(objectPayload)
        return file
    }

    private func makeMonoBehaviour(name: String, catalog: AoVRuntimeResourceCatalog) -> Data {
        var data = Data(repeating: 0, count: 12) // m_GameObject
        data.append(1) // m_Enabled
        align4(&data)
        data.append(Data(repeating: 0, count: 12)) // m_Script
        appendAlignedString(name, to: &data)
        switch catalog {
        case .packer(let value):
            appendAlignedString(value.version, to: &data)
            appendAlignedString(value.svnRevision, to: &data)
            appendAlignedString(value.setName, to: &data)
            appendInt32(Int32(value.allResourcePackInfo.count), to: &data)
            for item in value.allResourcePackInfo {
                appendAlignedString(item.pathInIFS, to: &data)
                appendInt32(item.pkgType, to: &data)
                appendInt64(item.crcXor, to: &data)
                appendAlignedString(item.tag, to: &data)
                appendUInt16(item.flags, to: &data)
                align4(&data)
                appendInt32(Int32(item.resourceInfos.count), to: &data)
                for resource in item.resourceInfos {
                    appendInt64(resource.hashCode, to: &data)
                    appendAlignedString(resource.extensionName, to: &data)
                }
            }
            appendInt32(Int32(value.allParentIndexToChild.count), to: &data)
            for link in value.allParentIndexToChild {
                appendInt32(link.parentIdx, to: &data)
                appendInt32(link.childIdx, to: &data)
            }
            data.append(value.trailingData)
        case .verification(let value):
            appendAlignedString(value.version, to: &data)
            appendAlignedString(value.svnRevision, to: &data)
            appendInt32(Int32(value.allZipVerificationInfo.count), to: &data)
            for item in value.allZipVerificationInfo {
                appendAlignedString(item.pathInIFS, to: &data)
                appendInt64(item.crcXor, to: &data)
            }
            appendInt32(Int32(value.allDatabinVerificationInfo.count), to: &data)
            for item in value.allDatabinVerificationInfo {
                appendAlignedString(item.pathInIFS, to: &data)
                appendInt64(item.crcXor, to: &data)
            }
            data.append(value.trailingData)
        }
        return data
    }

    private func appendCString(_ value: String, to data: inout Data) {
        data.append(Data((value + "\0").utf8))
    }

    private func appendAlignedString(_ value: String, to data: inout Data) {
        appendInt32(Int32(value.utf8.count), to: &data)
        data.append(Data(value.utf8))
        align4(&data)
    }

    private func appendInt16(_ value: Int16, to data: inout Data) {
        let bits = UInt16(bitPattern: value)
        data.append(UInt8(truncatingIfNeeded: bits))
        data.append(UInt8(truncatingIfNeeded: bits >> 8))
    }

    private func appendUInt16(_ value: UInt16, to data: inout Data) {
        data.append(UInt8(truncatingIfNeeded: value))
        data.append(UInt8(truncatingIfNeeded: value >> 8))
    }

    private func appendInt32(_ value: Int32, to data: inout Data) {
        let bits = UInt32(bitPattern: value)
        appendUInt32(bits, to: &data)
    }

    private func appendUInt32(_ value: UInt32, to data: inout Data) {
        for shift in stride(from: 0, through: 24, by: 8) {
            data.append(UInt8(truncatingIfNeeded: value >> shift))
        }
    }

    private func appendInt64(_ value: Int64, to data: inout Data) {
        let bits = UInt64(bitPattern: value)
        for shift in stride(from: 0, through: 56, by: 8) {
            data.append(UInt8(truncatingIfNeeded: bits >> shift))
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
