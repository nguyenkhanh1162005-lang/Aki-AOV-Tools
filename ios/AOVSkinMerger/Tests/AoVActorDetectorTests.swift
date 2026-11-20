import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVActorDetectorTests: XCTestCase {
    func testDetectsThreeAndFourDigitHeroIDsFromResourcePaths() {
        let evidence = AoVActorDetector.detect(paths: [
            "Resources/Actor_123_Actions.pkg.bytes",
            "Resources/AssetRefs/Hero/1234_AssetRef.bytes",
            "Resources/assetbundle/1234_hero.assetbundle",
            "Resources/Prefab_Hero/4567_Hero.bytes",
            "Resources/hero/3456/skin.prefab",
            "Resources/commercializenotify_blue_6789.bytes"
        ])

        XCTAssertTrue(evidence.allActorIDs.contains("123"))
        XCTAssertTrue(evidence.allActorIDs.contains("1234"))
        XCTAssertTrue(evidence.allActorIDs.contains("4567"))
        XCTAssertTrue(evidence.allActorIDs.contains("3456"))
        XCTAssertTrue(evidence.allActorIDs.contains("6789"))
    }

    func testSkinIDsMatchFourDigitHeroPrefix() {
        let ids = AoVActorDetector.skinIDs(
            in: "123401 123402 789012 45678",
            actorIDs: ["1234"]
        )

        XCTAssertEqual(ids, Set(["123401", "123402"]))
    }

    func testSkinIDsKeepThreeDigitHeroCompatibility() {
        let ids = AoVActorDetector.skinIDs(
            in: "12301 12302 45678",
            actorIDs: ["123"]
        )

        XCTAssertEqual(ids, Set(["12301", "12302"]))
    }
}
