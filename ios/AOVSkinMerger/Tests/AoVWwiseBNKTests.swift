import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVWwiseBNKTests: XCTestCase {
    private struct Object {
        let type: UInt32
        let id: UInt32
        var payload: [UInt8] = []
    }

    private func u32(_ value: UInt32) -> Data {
        Data([
            UInt8(value & 0xFF),
            UInt8((value >> 8) & 0xFF),
            UInt8((value >> 16) & 0xFF),
            UInt8((value >> 24) & 0xFF)
        ])
    }

    private func chunk(_ id: String, _ payload: Data) -> Data {
        Data(id.utf8) + u32(UInt32(payload.count)) + payload
    }

    private func bank(
        version: UInt32 = 135,
        bankID: UInt32,
        objects: [Object] = [],
        media: [(UInt32, Data)] = [],
        unknownChunk: Bool = false
    ) -> Data {
        var result = Data()
        result.append(chunk("BKHD", u32(version) + u32(bankID) + Data(repeating: 0, count: 4)))

        if !media.isEmpty {
            var didx = Data()
            var audio = Data()
            for (id, bytes) in media {
                didx.append(u32(id))
                didx.append(u32(UInt32(audio.count)))
                didx.append(u32(UInt32(bytes.count)))
                audio.append(bytes)
            }
            result.append(chunk("DIDX", didx))
            result.append(chunk("DATA", audio))
        }

        if !objects.isEmpty {
            var hirc = u32(UInt32(objects.count))
            for object in objects {
                let body = u32(object.id) + Data(object.payload)
                if version <= 48 {
                    hirc.append(u32(object.type))
                } else {
                    hirc.append(UInt8(truncatingIfNeeded: object.type))
                }
                hirc.append(u32(UInt32(body.count)))
                hirc.append(body)
            }
            result.append(chunk("HIRC", hirc))
        }

        if unknownChunk {
            result.append(chunk("ZZZZ", Data([1, 2, 3])))
        }
        return result
    }

    func testDisjointBanksAreApprovedWithoutRewriting() throws {
        let overlayData = bank(bankID: 20, objects: [Object(type: 2, id: 200, payload: [3, 4])])
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(bankID: 10, objects: [Object(type: 2, id: 100, payload: [1, 2])])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: overlayData
        )

        let report = try AoVWwiseBNK.validateAppendOnly(
            baseBanks: [base],
            overlayBanks: [overlay]
        )

        XCTAssertTrue(report.verified)
        XCTAssertEqual(report.baseBankCount, 1)
        XCTAssertEqual(report.checkedBankCount, 2)
        XCTAssertEqual(report.appendedBankPaths, ["Sound/iOS/English/skin.bnk"])
        XCTAssertEqual(overlay.data, overlayData)
    }

    func testConflictingHIRCObjectIDIsRejected() {
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(bankID: 10, objects: [Object(type: 2, id: 100, payload: [1])])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(bankID: 20, objects: [Object(type: 2, id: 100, payload: [9])])
        )
        XCTAssertThrowsError(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]))
    }

    func testConflictingBankIDIsRejectedEvenWhenObjectsDiffer() {
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(bankID: 10, objects: [Object(type: 2, id: 100)])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(bankID: 10, objects: [Object(type: 2, id: 200)])
        )
        XCTAssertThrowsError(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]))
    }

    func testExternalWemReferenceIsRejected() {
        let base = AoVWwiseBankInput(path: "Sound/iOS/English/base.bnk", data: bank(bankID: 10))
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(bankID: 20, media: [(44, Data())])
        )
        XCTAssertThrowsError(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]))
    }

    func testIdenticalMediaIDPayloadCanBeShared() throws {
        let mediaPayload = Data([1, 2, 3])
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(bankID: 10, media: [(44, mediaPayload)])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(bankID: 20, media: [(44, mediaPayload)])
        )
        XCTAssertTrue(try AoVWwiseBNK.validateAppendOnly(
            baseBanks: [base], overlayBanks: [overlay]
        ).verified)
    }

    func testGlobalBankOverlapsIOSLocalizedNamespace() {
        let base = AoVWwiseBankInput(
            path: "Sound/Shared/global.bnk",
            data: bank(bankID: 10, objects: [Object(type: 2, id: 100, payload: [1])])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(bankID: 20, objects: [Object(type: 2, id: 100, payload: [2])])
        )
        XCTAssertThrowsError(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]))
    }

    func testMismatchedWwiseVersionIsRejectedAcrossNamespaces() {
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(version: 135, bankID: 10)
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/Android/English/skin.bnk",
            data: bank(version: 136, bankID: 20)
        )
        XCTAssertThrowsError(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]))
    }

    func testUnknownChunkIsRejected() {
        let base = AoVWwiseBankInput(path: "Sound/iOS/English/base.bnk", data: bank(bankID: 10))
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(bankID: 20, unknownChunk: true)
        )
        XCTAssertThrowsError(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]))
    }

    func testWwiseVersion48UsesFourByteHIRCType() throws {
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(version: 48, bankID: 10, objects: [Object(type: 2, id: 100, payload: [1, 2])])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(version: 48, bankID: 20, objects: [Object(type: 3, id: 200, payload: [3, 4])])
        )
        XCTAssertTrue(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]).verified)
    }

    func testChangedMediaPayloadForSameIDIsRejected() {
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(bankID: 10, media: [(44, Data([1, 2, 3]))])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(bankID: 20, media: [(44, Data([9, 8, 7]))])
        )
        XCTAssertThrowsError(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]))
    }

    func testDuplicateBankPathIsRejected() {
        let samePath = "Sound/iOS/English/shared.bnk"
        let base = AoVWwiseBankInput(path: samePath, data: bank(bankID: 10))
        let overlay = AoVWwiseBankInput(path: samePath, data: bank(bankID: 20))
        XCTAssertThrowsError(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]))
    }

    func testIdenticalHIRCObjectCanBeShared() throws {
        let sharedObject = Object(type: 2, id: 100, payload: [1, 2, 3])
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(bankID: 10, objects: [sharedObject])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/iOS/English/skin.bnk",
            data: bank(bankID: 20, objects: [sharedObject])
        )
        XCTAssertTrue(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]).verified)
    }

    func testMutuallyExclusivePlatformScopesMayReuseIDs() throws {
        let base = AoVWwiseBankInput(
            path: "Sound/iOS/English/base.bnk",
            data: bank(bankID: 10, objects: [Object(type: 2, id: 100, payload: [1])])
        )
        let overlay = AoVWwiseBankInput(
            path: "Sound/Android/English/skin.bnk",
            data: bank(bankID: 10, objects: [Object(type: 2, id: 100, payload: [9])])
        )
        XCTAssertTrue(try AoVWwiseBNK.validateAppendOnly(baseBanks: [base], overlayBanks: [overlay]).verified)
    }

}
