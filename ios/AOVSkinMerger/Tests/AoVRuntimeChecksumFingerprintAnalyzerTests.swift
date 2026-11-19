import Foundation
import XCTest
@testable import AOVSkinMerger

final class AoVRuntimeChecksumFingerprintAnalyzerTests: XCTestCase {
    func testUnityFSUncompressedFingerprintIsPartialWhenCatalogPathsAreMissing() {
        let mask: UInt32 = 0xCAFEBABE
        let samples = (0..<8).map { index in
            let crc = UInt32(index * 31 + 7)
            return AoVRuntimeChecksumFingerprintSample(
                path: "Packer/\(index)",
                storedChecksum: Int64(crc ^ mask),
                computedCRC32: UInt32(index * 19 + 11),
                computedUnityFSUncompressedCRC32: crc
            )
        }
        let finding = AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: "Packer",
            totalCatalogRows: 9,
            samples: samples,
            missingPaths: 1
        )

        XCTAssertEqual(finding.unityFSUncompressedStatus, .partialConsistentSample)
        XCTAssertEqual(finding.constantUnityFSUncompressedXORMask, mask)
    }

    func testCRC64WEMatchesKnownVector() {
        XCTAssertEqual(
            AoVRuntimeChecksumFingerprintAnalyzer.crc64WE(Data("123456789".utf8)),
            0x62EC59E3F1A4F00A
        )
    }

    func testCRC64WEFingerprintRequiresEightConsistentSamplesAndFindsExactMatches() {
        let samples = (0..<8).map { index -> AoVRuntimeChecksumCRC64FingerprintSample in
            let computed = AoVRuntimeChecksumFingerprintAnalyzer.crc64WE(
                Data("AoV-ZIP-entry-\\(index)".utf8)
            )
            return AoVRuntimeChecksumCRC64FingerprintSample(
                path: "Packages/\\(index).pkg.bytes",
                storedChecksum: computed,
                computedCRC64WE: computed
            )
        }
        let finding = AoVRuntimeChecksumFingerprintAnalyzer.evaluateCRC64WE(
            title: "Verification ZIP CRC64/WE",
            totalCatalogRows: samples.count,
            samples: samples
        )
        XCTAssertEqual(finding.status, .consistentSample)
        XCTAssertEqual(finding.evaluatedSamples, 8)
        XCTAssertEqual(finding.constantXORMask, 0)
        XCTAssertEqual(finding.exactCRC64WEMatches, 8)

        let insufficient = AoVRuntimeChecksumFingerprintAnalyzer.evaluateCRC64WE(
            title: "Verification ZIP CRC64/WE",
            totalCatalogRows: 7,
            samples: Array(samples.prefix(7))
        )
        XCTAssertEqual(insufficient.status, .insufficientSamples)
        XCTAssertNil(insufficient.constantXORMask)
    }

    func testCRC32MatchesIEEEKnownVector() {
        XCTAssertEqual(
            AoVRuntimeChecksumFingerprintAnalyzer.crc32(Data("123456789".utf8)),
            0xCBF43926
        )
    }

    func testFingerprintFindsConsistentXORMaskAcrossMinimumSamples() {
        let mask: UInt32 = 0xA5C31F27
        let samples = (0..<8).map { index in
            let computed = UInt32(index) &* 0x1020304 &+ 0xABCDEF
            return AoVRuntimeChecksumFingerprintSample(
                path: "Databin/\(index).bytes",
                storedChecksum: Int64(computed ^ mask),
                computedCRC32: computed
            )
        }

        let finding = AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: "Verification Databin",
            totalCatalogRows: samples.count,
            samples: samples
        )

        XCTAssertEqual(finding.status, .consistentSample)
        XCTAssertEqual(finding.evaluatedSamples, 8)
        XCTAssertEqual(finding.distinctXORMaskCount, 1)
        XCTAssertEqual(finding.constantXORMask, mask)
        XCTAssertEqual(finding.exactCRC32Matches, 0)
    }

    func testFingerprintFindsSeparateDecompressedUnityFSXORMask() {
        let mask: UInt32 = 0xD00DFEED
        let samples = (0..<8).map { index in
            let computedUnityFS = UInt32(index) &* 0x1F123BB5 &+ 0x1020304
            return AoVRuntimeChecksumFingerprintSample(
                path: "assetbundle/actor-\(index).assetbundle",
                storedChecksum: Int64(computedUnityFS ^ mask),
                computedCRC32: UInt32(index * 17),
                computedUnityFSUncompressedCRC32: computedUnityFS
            )
        }

        let finding = AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: "Verification ZIP",
            totalCatalogRows: samples.count,
            samples: samples
        )

        XCTAssertEqual(finding.unityFSUncompressedEvaluatedSamples, 8)
        XCTAssertEqual(finding.distinctUnityFSUncompressedXORMaskCount, 1)
        XCTAssertEqual(finding.constantUnityFSUncompressedXORMask, mask)
        XCTAssertEqual(finding.exactUnityFSUncompressedCRC32Matches, 0)
    }

    func testFingerprintRejectsAChangingXORMask() {
        let samples = (0..<10).map { index in
            let computed = UInt32(index) &* 0x98765 &+ 0x1234
            let mask: UInt32 = index == 7 ? 0x11111111 : 0x22222222
            return AoVRuntimeChecksumFingerprintSample(
                path: "assetbundle/\(index).assetbundle",
                storedChecksum: Int64(computed ^ mask),
                computedCRC32: computed
            )
        }

        let finding = AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: "Verification ZIP",
            totalCatalogRows: samples.count,
            samples: samples
        )

        XCTAssertEqual(finding.status, .inconsistent)
        XCTAssertNil(finding.constantXORMask)
        XCTAssertEqual(finding.distinctXORMaskCount, 2)
    }

    func testFingerprintKeepsConsistentButIncompleteEvidenceAsPartial() {
        let mask: UInt32 = 0x12345678
        let samples = (0..<8).map { index in
            let computed = UInt32(index) &* 0x110011 + 0x55
            return AoVRuntimeChecksumFingerprintSample(
                path: "Databin/\(index).bytes",
                storedChecksum: Int64(computed ^ mask),
                computedCRC32: computed
            )
        }

        let finding = AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: "Verification Databin",
            totalCatalogRows: 10,
            samples: samples,
            missingPaths: 2
        )

        XCTAssertEqual(finding.status, .partialConsistentSample)
        XCTAssertEqual(finding.constantXORMask, mask)
        XCTAssertEqual(finding.missingPaths, 2)
    }

    func testFingerprintDoesNotInferMaskFromTooFewSamples() {
        let samples = (0..<7).map { index in
            let computed = UInt32(index * 100)
            return AoVRuntimeChecksumFingerprintSample(
                path: "file/\(index)",
                storedChecksum: Int64(computed),
                computedCRC32: computed
            )
        }

        let finding = AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: "Verification ZIP",
            totalCatalogRows: samples.count,
            samples: samples
        )

        XCTAssertEqual(finding.status, .insufficientSamples)
        XCTAssertNil(finding.constantXORMask)
    }
}
