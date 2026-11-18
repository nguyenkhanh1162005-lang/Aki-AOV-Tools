import Foundation
import XCTest
@testable import AOVSkinMerger

final class AoVModRemakeRESExportReadinessAnalyzerTests: XCTestCase {
    func testBuildsPackerAndVerificationChecksumCandidatesButNeverMarksExportSafe() throws {
        let packerMask: UInt32 = 0x2468ACE0
        let verificationMask: UInt32 = 0xA5A5F00D
        let packerFinding = makeFinding(title: "Packer", mask: packerMask)
        let verificationFinding = makeFinding(title: "Verification ZIP", mask: verificationMask)
        let databinFinding = makeFinding(title: "Verification Databin", mask: 0x12345678)
        let fingerprint = AoVRuntimeChecksumFingerprintReport(
            baseRESName: "base-res.zip",
            packerAssetBundles: packerFinding,
            zipVerification: verificationFinding,
            databinVerification: databinFinding,
            warning: "synthetic"
        )
        let payload = Data("merged-assetbundle-payload".utf8)
        let result = makeMergeResult(payload: payload)
        let packerRow = AoVRuntimeResourcePackInfo(
            pathInIFS: "assetbundle/actor-731.assetbundle",
            pkgType: 3,
            crcXor: 111,
            tag: "Hero",
            flags: 512,
            resourceInfos: [
                AoVRuntimeResourceItem(hashCode: 731123, extensionName: "assetbundle")
            ]
        )
        let catalog = makePackerCatalog(rows: [packerRow])

        let report = try AoVModRemakeRESExportReadinessAnalyzer.buildProposals(
            baseRESName: "base-res.zip",
            assetBundleMergeResults: [result],
            legacyPackerCatalogs: [(source: "legacy-a.zip", catalog: catalog)],
            checksumFingerprint: fingerprint
        )

        let proposal = try XCTUnwrap(report.proposals.first)
        let rawCRC = AoVRuntimeChecksumFingerprintAnalyzer.crc32(payload)
        XCTAssertEqual(proposal.status, .requiresExternalValidation)
        XCTAssertEqual(proposal.rawPayloadCRC32, rawCRC)
        XCTAssertEqual(proposal.proposedPackerEntry?.crcXor, Int64(rawCRC ^ packerMask))
        XCTAssertEqual(proposal.proposedVerificationZIPChecksum, Int64(rawCRC ^ verificationMask))
        XCTAssertEqual(proposal.proposedPackerEntry?.tag, "Hero")
        XCTAssertEqual(proposal.proposedPackerEntry?.resourceInfos, packerRow.resourceInfos)
        XCTAssertFalse(report.safeToExport)
        XCTAssertTrue(report.allChecksumsHaveCandidates)
    }

    func testUsesBaseRESPackerMetadataWhenLegacyMODOmitsTheEntry() throws {
        let packerMask: UInt32 = 0xABCD1234
        let fingerprint = makeReport(
            packer: makeFinding(title: "Packer", mask: packerMask),
            zip: makeFinding(title: "ZIP", mask: 0x10293847)
        )
        let baseRow = AoVRuntimeResourcePackInfo(
            pathInIFS: "assetbundle/actor-731.assetbundle",
            pkgType: 4,
            crcXor: 777,
            tag: "BaseHero",
            flags: 256,
            resourceInfos: [AoVRuntimeResourceItem(hashCode: 731, extensionName: "assetbundle")]
        )

        let report = try AoVModRemakeRESExportReadinessAnalyzer.buildProposals(
            baseRESName: "base-res.zip",
            assetBundleMergeResults: [makeMergeResult(payload: Data("merged-cab".utf8))],
            legacyPackerCatalogs: [(source: "RES nền", catalog: makePackerCatalog(rows: [baseRow]))],
            checksumFingerprint: fingerprint
        )

        let proposal = try XCTUnwrap(report.proposals.first)
        XCTAssertEqual(proposal.status, .requiresExternalValidation)
        XCTAssertEqual(proposal.sourceArchives, ["RES nền"])
        XCTAssertEqual(proposal.proposedPackerEntry?.tag, "BaseHero")
        XCTAssertEqual(proposal.proposedPackerEntry?.pkgType, 4)
    }

    func testAcceptsSameStaticPackerMetadataWhenLegacyChecksumsDiffer() throws {
        let mask: UInt32 = 0x11223344
        let fingerprint = makeReport(
            packer: makeFinding(title: "Packer", mask: mask),
            zip: makeFinding(title: "ZIP", mask: 0x55667788)
        )
        let first = AoVRuntimeResourcePackInfo(
            pathInIFS: "assetbundle/actor-731.assetbundle",
            pkgType: 2,
            crcXor: 0x01010101,
            tag: "Hero",
            flags: 512,
            resourceInfos: [AoVRuntimeResourceItem(hashCode: 731, extensionName: "assetbundle")]
        )
        let second = AoVRuntimeResourcePackInfo(
            pathInIFS: first.pathInIFS,
            pkgType: first.pkgType,
            crcXor: 0xFEFEFEFE,
            tag: first.tag,
            flags: first.flags,
            resourceInfos: first.resourceInfos
        )

        let report = try AoVModRemakeRESExportReadinessAnalyzer.buildProposals(
            baseRESName: "base-res.zip",
            assetBundleMergeResults: [makeMergeResult(payload: Data("candidate".utf8))],
            legacyPackerCatalogs: [
                (source: "legacy-a.zip", catalog: makePackerCatalog(rows: [first])),
                (source: "legacy-b.zip", catalog: makePackerCatalog(rows: [second]))
            ],
            checksumFingerprint: fingerprint
        )

        let proposal = try XCTUnwrap(report.proposals.first)
        XCTAssertEqual(proposal.status, .requiresExternalValidation)
        XCTAssertEqual(proposal.proposedPackerEntry?.tag, "Hero")
        XCTAssertEqual(proposal.sourceArchives, ["legacy-a.zip", "legacy-b.zip"])
    }

    func testBlocksConflictingPackerStaticMetadataAcrossMODs() throws {
        let mask: UInt32 = 0x55667788
        let finding = makeFinding(title: "Packer", mask: mask)
        let fingerprint = makeReport(packer: finding, zip: makeFinding(title: "ZIP", mask: mask))
        let path = "assetbundle/actor-731.assetbundle"
        let first = AoVRuntimeResourcePackInfo(
            pathInIFS: path,
            pkgType: 0,
            crcXor: 1,
            tag: "HeroA",
            flags: 0,
            resourceInfos: []
        )
        let second = AoVRuntimeResourcePackInfo(
            pathInIFS: path,
            pkgType: 0,
            crcXor: 2,
            tag: "HeroB",
            flags: 0,
            resourceInfos: []
        )

        let report = try AoVModRemakeRESExportReadinessAnalyzer.buildProposals(
            baseRESName: "base-res.zip",
            assetBundleMergeResults: [makeMergeResult(payload: Data("candidate".utf8))],
            legacyPackerCatalogs: [
                (source: "legacy-a.zip", catalog: makePackerCatalog(rows: [first])),
                (source: "legacy-b.zip", catalog: makePackerCatalog(rows: [second]))
            ],
            checksumFingerprint: fingerprint
        )

        let proposal = try XCTUnwrap(report.proposals.first)
        XCTAssertEqual(proposal.status, .blocked)
        XCTAssertNil(proposal.proposedPackerEntry)
        XCTAssertNil(proposal.proposedVerificationZIPChecksum)
        XCTAssertTrue(proposal.reasons.contains {
            $0.contains("không thống nhất pkgType/tag/flags/resourceInfos")
        })
    }

    func testBlocksWhenEitherChecksumFingerprintIsOnlyPartial() throws {
        let stableMask: UInt32 = 0x01020304
        let stable = makeFinding(title: "Packer", mask: stableMask)
        let samples = (0..<8).map { index in
            AoVRuntimeChecksumFingerprintSample(
                path: "bundle/\(index)",
                storedChecksum: Int64(UInt32(index) ^ stableMask),
                computedCRC32: UInt32(index)
            )
        }
        let partial = AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: "Verification ZIP",
            totalCatalogRows: 10,
            samples: samples,
            missingPaths: 2
        )
        let fingerprint = makeReport(packer: stable, zip: partial)

        let row = AoVRuntimeResourcePackInfo(
            pathInIFS: "assetbundle/actor-731.assetbundle",
            pkgType: 0,
            crcXor: 99,
            tag: "Hero",
            flags: 0,
            resourceInfos: []
        )
        let report = try AoVModRemakeRESExportReadinessAnalyzer.buildProposals(
            baseRESName: "base-res.zip",
            assetBundleMergeResults: [makeMergeResult(payload: Data("candidate".utf8))],
            legacyPackerCatalogs: [(source: "legacy.zip", catalog: makePackerCatalog(rows: [row]))],
            checksumFingerprint: fingerprint
        )

        let proposal = try XCTUnwrap(report.proposals.first)
        XCTAssertEqual(proposal.status, .blocked)
        XCTAssertTrue(proposal.reasons.contains {
            $0.contains("Verification ZIP CRC32/XOR fingerprint")
        })
    }

    func testRejectsFingerprintFromAnotherRES() {
        let fingerprint = makeReport(
            packer: makeFinding(title: "Packer", mask: 1),
            zip: makeFinding(title: "ZIP", mask: 2),
            name: "different-res.zip"
        )
        XCTAssertThrowsError(
            try AoVModRemakeRESExportReadinessAnalyzer.buildProposals(
                baseRESName: "base-res.zip",
                assetBundleMergeResults: [makeMergeResult(payload: Data([1, 2, 3]))],
                legacyPackerCatalogs: [],
                checksumFingerprint: fingerprint
            )
        )
    }

    func testFingerprintIdentityRejectsDifferentRESBytesWithSameFilename() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-RES-Fingerprint-Identity-Test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let firstDirectory = root.appendingPathComponent("first", isDirectory: true)
        let secondDirectory = root.appendingPathComponent("second", isDirectory: true)
        try FileManager.default.createDirectory(at: firstDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: secondDirectory, withIntermediateDirectories: true)
        let firstRES = firstDirectory.appendingPathComponent("base-res.zip")
        let secondRES = secondDirectory.appendingPathComponent("base-res.zip")
        try Data("first-res-content".utf8).write(to: firstRES)
        try Data("different-res-content".utf8).write(to: secondRES)

        let fingerprint = makeReport(
            packer: makeFinding(title: "Packer", mask: 1),
            zip: makeFinding(title: "ZIP", mask: 2),
            fingerprint: try AoVRuntimeChecksumFingerprintAnalyzer.sha256File(at: firstRES)
        )

        XCTAssertNoThrow(
            try AoVModRemakeRESExportReadinessAnalyzer.validateFingerprintIdentity(
                fingerprint,
                baseRESURL: firstRES
            )
        )
        XCTAssertThrowsError(
            try AoVModRemakeRESExportReadinessAnalyzer.validateFingerprintIdentity(
                fingerprint,
                baseRESURL: secondRES
            )
        ) { error in
            guard case AoVModRemakeRESExportReadinessError.fingerprintContentMismatch = error else {
                return XCTFail("Expected a content fingerprint mismatch, got \(error)")
            }
        }
    }

    func testFingerprintIdentityIsRequiredBeforeRESReadinessAnalysis() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-RES-Fingerprint-Missing-Test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let baseRES = root.appendingPathComponent("base-res.zip")
        try Data("base-res".utf8).write(to: baseRES)

        let fingerprint = makeReport(
            packer: makeFinding(title: "Packer", mask: 1),
            zip: makeFinding(title: "ZIP", mask: 2)
        )
        XCTAssertThrowsError(
            try AoVModRemakeRESExportReadinessAnalyzer.validateFingerprintIdentity(
                fingerprint,
                baseRESURL: baseRES
            )
        ) { error in
            guard case AoVModRemakeRESExportReadinessError.missingRESFingerprintIdentity = error else {
                return XCTFail("Expected a missing fingerprint identity, got \(error)")
            }
        }
    }

    private func makeFinding(title: String, mask: UInt32) -> AoVRuntimeChecksumFingerprintFinding {
        let samples = (0..<8).map { index in
            let crc = UInt32(index * 1_000 + 19)
            return AoVRuntimeChecksumFingerprintSample(
                path: "\(title)/\(index)",
                storedChecksum: Int64(crc ^ mask),
                computedCRC32: crc
            )
        }
        return AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: title,
            totalCatalogRows: samples.count,
            samples: samples
        )
    }

    private func makeReport(
        packer: AoVRuntimeChecksumFingerprintFinding,
        zip: AoVRuntimeChecksumFingerprintFinding,
        name: String = "base-res.zip",
        fingerprint: String? = nil
    ) -> AoVRuntimeChecksumFingerprintReport {
        AoVRuntimeChecksumFingerprintReport(
            baseRESName: name,
            baseRESFingerprint: fingerprint,
            packerAssetBundles: packer,
            zipVerification: zip,
            databinVerification: makeFinding(title: "Databin", mask: 3),
            warning: "synthetic"
        )
    }

    private func makeMergeResult(payload: Data) -> AoVModRemakeAssetBundleMergeResult {
        AoVModRemakeAssetBundleMergeResult(
            resourcePath: "1.64.1.12/assetbundle/actor-731.assetbundle",
            resourcePayload: AoVModRemakeResourcePayload(
                archivePath: "1.64.1.12/assetbundle/actor-731.assetbundle",
                ifsPath: "assetbundle/actor-731.assetbundle",
                data: payload
            ),
            importedObjectCount: 1,
            importedPathIDs: [987654],
            roundTripVerified: true
        )
    }

    private func makePackerCatalog(
        rows: [AoVRuntimeResourcePackInfo]
    ) -> AoVRuntimeResourcePackerCatalog {
        AoVRuntimeResourcePackerCatalog(
            name: "ResourcePackerInfoSetXML",
            version: "1.64.1.12",
            svnRevision: "test",
            setName: "ResourcePackerInfoSetXML",
            allResourcePackInfo: rows,
            allParentIndexToChild: [],
            trailingData: Data()
        )
    }
}
