import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVRuntimeCatalogComparatorTests: XCTestCase {
    func testComparesPackerAndVerificationRuntimeLists() {
        let common = pack("assetbundle/common.assetbundle", crc: 10)
        let heroCurrent = pack("assetbundle/hero.assetbundle", crc: 20)
        let heroLegacy = pack("assetbundle/hero.assetbundle", crc: 99)
        let oldOnly = pack("assetbundle/old.assetbundle", crc: 30)

        let current = inventory(
            name: "current-res.zip",
            packer: packer(
                version: "1.64.2.1",
                entries: [common, heroCurrent],
                links: [AoVPackInfoMapItem(parentIdx: 0, childIdx: 1)]
            ),
            verification: verification(
                version: "1.64.2.1",
                zip: [verify("CommonActions.pkg.bytes", crc: 111)],
                databin: [verify("Databin/Client/Hero.bytes", crc: 222)]
            )
        )
        let legacy = inventory(
            name: "legacy-mod.zip",
            packer: packer(
                version: "1.64.1.12",
                entries: [common, heroLegacy, oldOnly],
                links: [
                    AoVPackInfoMapItem(parentIdx: 0, childIdx: 1),
                    AoVPackInfoMapItem(parentIdx: 0, childIdx: 2)
                ]
            ),
            verification: verification(
                version: "1.64.1.12",
                zip: [
                    verify("CommonActions.pkg.bytes", crc: 999),
                    verify("old/Legacy.pkg.bytes", crc: 333)
                ],
                databin: [
                    verify("Databin/Client/Hero.bytes", crc: 222),
                    verify("Databin/Client/OldHero.bytes", crc: 444)
                ]
            )
        )

        let report = AoVRuntimeCatalogComparator.compare(current: current, legacy: [legacy])

        XCTAssertEqual(report.packerResources.currentEntryCount, 2)
        XCTAssertEqual(report.packerResources.legacyEntryCount, 3)
        XCTAssertEqual(report.packerResources.identicalEntryCount, 1)
        XCTAssertEqual(report.packerResources.changedEntryCount, 1)
        XCTAssertEqual(report.packerResources.legacyOnlyEntryCount, 1)
        XCTAssertEqual(report.packerResources.findings.first(where: {
            $0.path == "assetbundle/hero.assetbundle"
        })?.difference, .changed)
        XCTAssertEqual(report.packerResources.findings.first(where: {
            $0.path == "assetbundle/old.assetbundle"
        })?.difference, .legacyOnly)

        XCTAssertEqual(report.packerParentChildLinks.identicalEntryCount, 1)
        XCTAssertEqual(report.packerParentChildLinks.legacyOnlyEntryCount, 1)

        XCTAssertEqual(report.verificationZIP.changedEntryCount, 1)
        XCTAssertEqual(report.verificationZIP.legacyOnlyEntryCount, 1)
        XCTAssertEqual(report.verificationDatabin.identicalEntryCount, 1)
        XCTAssertEqual(report.verificationDatabin.legacyOnlyEntryCount, 1)

        XCTAssertTrue(report.packerResources.legacyCatalogVersions.contains {
            $0.contains("1.64.1.12")
        })
        XCTAssertTrue(report.packerResources.currentCatalogVersions.contains {
            $0.contains("1.64.2.1")
        })
    }

    func testFlagsDuplicateCurrentAndConflictingLegacyRuntimeEntries() {
        let duplicateCurrentA = pack("assetbundle/dup.assetbundle", crc: 1)
        let duplicateCurrentB = pack("assetbundle/dup.assetbundle", crc: 2)
        let legacyA = pack("assetbundle/conflict.assetbundle", crc: 3)
        let legacyB = pack("assetbundle/conflict.assetbundle", crc: 4)

        let current = inventory(
            name: "current.zip",
            packer: packer(version: "1.0", entries: [duplicateCurrentA, duplicateCurrentB]),
            verification: verification(version: "1.0", zip: [], databin: [])
        )
        let legacy = inventory(
            name: "legacy.zip",
            packer: packer(version: "0.9", entries: [legacyA, legacyB]),
            verification: verification(version: "0.9", zip: [], databin: [])
        )

        let report = AoVRuntimeCatalogComparator.compare(current: current, legacy: [legacy])
        XCTAssertEqual(report.packerResources.duplicateCurrentPathCount, 1)
        XCTAssertEqual(report.packerResources.conflictingLegacyPathCount, 1)
        XCTAssertEqual(report.packerResources.findings.first(where: {
            $0.path == "assetbundle/dup.assetbundle"
        })?.difference, .duplicateCurrent)
        XCTAssertEqual(report.packerResources.findings.first(where: {
            $0.path == "assetbundle/conflict.assetbundle"
        })?.difference, .conflictingLegacy)
    }

    func testCountsInvalidPackerParentChildIndexes() {
        let current = inventory(
            name: "current.zip",
            packer: packer(
                version: "1.0",
                entries: [pack("assetbundle/one.assetbundle", crc: 1)],
                links: [AoVPackInfoMapItem(parentIdx: 0, childIdx: 9)]
            ),
            verification: verification(version: "1.0", zip: [], databin: [])
        )
        let report = AoVRuntimeCatalogComparator.compare(current: current, legacy: [])
        XCTAssertEqual(report.packerParentChildLinks.invalidReferenceCount, 1)
    }

    private func pack(
        _ path: String,
        crc: Int64
    ) -> AoVRuntimeResourcePackInfo {
        AoVRuntimeResourcePackInfo(
            pathInIFS: path,
            pkgType: 0,
            crcXor: crc,
            tag: "test",
            flags: 0,
            resourceInfos: [
                AoVRuntimeResourceItem(hashCode: crc, extensionName: "asset")
            ]
        )
    }

    private func verify(_ path: String, crc: Int64) -> AoVRuntimeVerificationEntry {
        AoVRuntimeVerificationEntry(pathInIFS: path, crcXor: crc)
    }

    private func packer(
        version: String,
        entries: [AoVRuntimeResourcePackInfo],
        links: [AoVPackInfoMapItem] = []
    ) -> AoVRuntimeResourcePackerCatalog {
        AoVRuntimeResourcePackerCatalog(
            name: "ResourcePackerInfoSetXML",
            version: version,
            svnRevision: "test-svn",
            setName: "ResourcePackerInfoSetXML",
            allResourcePackInfo: entries,
            allParentIndexToChild: links,
            trailingData: Data()
        )
    }

    private func verification(
        version: String,
        zip: [AoVRuntimeVerificationEntry],
        databin: [AoVRuntimeVerificationEntry]
    ) -> AoVRuntimeResourceVerificationCatalog {
        AoVRuntimeResourceVerificationCatalog(
            name: "ResourceVerificationInfoSetXML",
            version: version,
            svnRevision: "test-svn",
            allZipVerificationInfo: zip,
            allDatabinVerificationInfo: databin,
            trailingData: Data()
        )
    }

    private func inventory(
        name: String,
        packer: AoVRuntimeResourcePackerCatalog,
        verification: AoVRuntimeResourceVerificationCatalog
    ) -> ArchiveInventory {
        let serialized = AoVSerializedFileSummary(
            formatVersion: 22,
            unityVersion: "2022.3.5f1",
            targetPlatform: 9,
            fileSize: 0,
            dataOffset: 0,
            littleEndian: true,
            fileSizeFieldOffset: 0,
            dataOffsetFieldOffset: 0,
            metadataSizeFieldOffset: 0,
            metadataStartOffset: 0,
            metadataEndOffset: 0,
            objectCountFieldOffset: 0,
            objectTableStartOffset: 0,
            objectTableEndOffset: 0,
            typeCount: 0,
            serializedTypes: [],
            objectCount: 0,
            serializedObjects: [],
            serializedObjectsByPathID: [:],
            serializedObjectPathIDs: [],
            assetBundleObjects: [],
            externalReferences: [],
            assetBundleRecords: [],
            resourcePackerCatalog: packer,
            resourceVerificationCatalog: verification
        )
        let info = AoVUnityFSBundleInfo(
            formatVersion: 8,
            playerVersion: "5.0.0",
            engineVersion: "2022.3.5f1",
            totalFileSize: 1,
            flags: 0x40,
            blockInfoCompression: "none",
            blockInfoLayout: .arenaOfValor,
            blockInfoAtEnd: false,
            usesAoVEncryption: false,
            blockPaddingAtStart: false,
            blocks: [],
            nodes: [],
            totalUncompressedBytes: 0,
            totalCompressedBlockBytes: 0,
            availableBlockDataBytes: 0,
            blockDataOffset: 0
        )
        let packerBundle = AoVUnityFSScanResult(
            archiveName: name,
            path: "assetbundle/resourcepackerinfosetall.assetbundle",
            info: info,
            catalogExtractionStatus: "test",
            catalogNodeSamples: [
                AoVUnityFSCatalogNodeSample(
                    path: "CAB-packer",
                    size: 0,
                    prefixHex: "",
                    stringHints: [],
                    serializedFileSummary: "test",
                    serializedFile: serialized
                )
            ]
        )
        let verificationBundle = AoVUnityFSScanResult(
            archiveName: name,
            path: "assetbundle/resourceverificationinfosetall.assetbundle",
            info: info,
            catalogExtractionStatus: "test",
            catalogNodeSamples: [
                AoVUnityFSCatalogNodeSample(
                    path: "CAB-verification",
                    size: 0,
                    prefixHex: "",
                    stringHints: [],
                    serializedFileSummary: "test",
                    serializedFile: serialized
                )
            ]
        )
        return ArchiveInventory(
            id: UUID(),
            fileName: name,
            fileSize: 1,
            fileCount: 2,
            uncompressedBytes: 0,
            paths: [],
            pathDigests: [],
            unityFSBundles: [packerBundle, verificationBundle],
            unityFSUnrecognizedCount: 0,
            unityFSTooLargeCount: 0,
            unityFSInspectionFailures: []
        )
    }
}
