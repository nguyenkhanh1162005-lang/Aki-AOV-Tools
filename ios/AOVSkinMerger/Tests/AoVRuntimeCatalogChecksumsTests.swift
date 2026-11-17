import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVRuntimeCatalogChecksumsTests: XCTestCase {
    func testVerificationDatabinUsesStandardCRC32() {
        let data = Data("123456789".utf8)
        XCTAssertEqual(
            AoVRuntimeCatalogChecksum.verificationDatabinCRC32(data),
            Int64(Int32(bitPattern: 0xCBF4_3926))
        )
    }

    func testVerificationDatabinChecksumPreservesSignedInt32BitPattern() {
        let data = Data("123456789".utf8)
        let checksum = AoVRuntimeCatalogChecksum.verificationDatabinCRC32(data)
        XCTAssertLessThan(checksum, 0)
        XCTAssertTrue(AoVRuntimeCatalogChecksum.verifiesDatabin(data, catalogValue: checksum))
        XCTAssertFalse(AoVRuntimeCatalogChecksum.verifiesDatabin(data, catalogValue: checksum + 1))
    }

    func testEmptyPayloadMatchesStandardCRC32() {
        XCTAssertEqual(AoVRuntimeCatalogChecksum.verificationDatabinCRC32(Data()), 0)
    }
}
