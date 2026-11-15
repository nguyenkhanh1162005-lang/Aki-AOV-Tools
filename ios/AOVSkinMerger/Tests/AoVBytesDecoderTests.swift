import XCTest
import Foundation
import ZIPFoundation
@testable import AOVSkinMerger

final class AoVBytesDecoderTests: XCTestCase {
    func testDecryptsKnownRealAoVAESFixture() throws {
        let hex = "224a6700cd0200008791646bd02a34c49fa5c166879dec16dc4de77e247574d27d9e6319c432b8faad1861c5a9f0d7bf09fba0bff12227640b76ac4990bbaac0a1b3b63b951728e06fe8a65d3ddc365932672f8b06f14ecd4fadc930e53653edede161b3506d26c85c27fbc232422703660a7c48df48261e0d1ceee130d3d2af26e3672621c96e197ba4c873f613c7c9fe2bcfab9e272beeac45097685002abf229754f9effb6122b8c94e40c2fc4b4e45870ec9827bea068ff97fc593ce6510b2af71d966a29ab0059632d5f1f72d7123d387576268ec859a39ae9d045af2588ae4489e5c15b68ccf4e0dde023ea30c2bdc457870286f8f1f327e7ccf0dd1a7728211f2128281f8817a4cd6c78a18b3e51dadf966567770ad0d029d625316ca695c832c47752d89fcb09e7798e1c336084288f85deefdc01b58798c042b44e8359ac66697f057746f4f1ece5eb91521a5892fb26a7fa199e2878cf624682c1bf39fe53751e0a2d3af3a8d889d59dff0c709ab0a34bd6ae9e52abccf093f60b55ac25b917d1ddac542268206e977c08be18a0353f7956a26c9f74d914e5538d5e00ecdd86de4dc12e4c3039d47543a8f005dae07880c24eef8cde91229bbde31b41f12202e08627310581dfc38c80793720f0e5b109d7b2573351f30fb95b15fff0efa1c6420b578d12e781c8277615a957cb52afa16454d0816d34a56bc04d6e00788c262a4a9acf0767b2363d21dbf39c4cdd157c479c22c4207bc89a904744bcba0fc5c4b9b210b56be3186fbab58044f0932a1fabc214461ad26f9b99e684ffde2518707936061e49712689779e0f95da15969b75ffbf27e6c02cadfd717ea1bac1dbfd6be59823c08b64993df57aef0b3240fe5b9a154a567e9559c048b67e0cc1a0b71e26294aa9552ee846c22efeff0ac613f9c5c607d05a8327d3d7468eacceb510d6dcf998601c2d358175440d9959a87abb3d065b0dbf442afba593a1cd4e722f2b0bb47d64f1e7823ff568d364fbc904efdfc"
        let bytes = stride(from: 0, to: hex.count, by: 2).map { offset -> UInt8 in
            let start = hex.index(hex.startIndex, offsetBy: offset)
            let end = hex.index(start, offsetBy: 2)
            return UInt8(hex[start..<end], radix: 16)!
        }
        let decoded = try AoVBytesDecoder.decodeResource(
            Data(bytes),
            resourceName: "ResSkinSeniorLabelCfg.bytes"
        )
        XCTAssertEqual(decoded.wrapperMode, "aes")
        XCTAssertEqual(decoded.data.count, 717)
        let rebuilt = try AoVBytesDecoder.encodeResource(
            decoded.data,
            resourceName: "ResSkinSeniorLabelCfg.bytes",
            wrapperMode: "aes"
        )
        XCTAssertEqual(
            try AoVBytesDecoder.decodeResource(rebuilt, resourceName: "ResSkinSeniorLabelCfg.bytes").data,
            decoded.data
        )
    }

    func testDecryptsKnownRealAoVAESZstdFixtureWithDictionary() throws {
        let hex = "224a67ef9d080000b1f3b6f8a17c99bc6c67d9499bc699e35496ac788a398d001f7f4122ce1876b7e170dcbcadda01472f7c720eaa470f5520cd4cc5a077b8a5290cdea811c999ab893f62c12dcc88d10fbd41e241de9dcb05827ea22f806654d4a2e11ea84ac28d678920e8966eb023c96edd87b42aa8523d152b7d225bfba0a8d64417ace4b75bbb10788c26bdae4cee27469334f996a03576137fa36cb65960268710ec830a9625d3204ffc24423b140545015590b5164f2cf10a9d02d0a3900338a6ce83234b84bbff63c80e5b599767abbef17936d7f42666cc0d0e2c099b85b068137f1ab465611f3c7c9815da6e5758dc7310026ef79ccf7d6acaacfb19c83f608f9457b9d1c18ad5cb158b3be3463e08d07d75a11f8b8f9dff23ae2b7ba4632ef8ee9d5bb40497cd3745c8f8f1f996fc6cd1538e2063c4530ef7cd57b5957f0d381c72dfe4801a6a63f0aa0ada609c01d2223a1efb3cbd5eed1c64178c6c6d1cc7f6ee17db98b2c4f7ddf2d6a937b1eb237d51deafc5be2007091a365687037bd50e672a19390e0cd8ce4f9d967c1473fdcdedd6c008a80bbbcd6c3148322fb335ca0bb5a5b604add9615a3ee355ba222abf85a4457a616eeac7dc65ceccac51d72cd12e05e261944ec5a068bf03457caa7a832eaead39b694c29d5b8c7c392aaeeed4f0b0ca9ae5549cf27c4229101ebe86e0411c639872a77925739d9ecbc5dc5ccae1448945dc59aa04c40b9288580939f49c1118fbbe1ebdbec73146544cba045f85eb54f0224f1af91a07e86a28872156a28ee24f822888c144a414137a0b0c097961b8613fbcecadc332b16120ee70895233dac9a7cf01c0730a130069094f7a9d1439ab5fb006b2ac8c2fd6daa6e0de67d37f9fb93904d9d6aec85cb5e91a754a57ce9f021410e35cdb99b5d532484d8c3f89236b2f8d903acd6c42e30d3fc9960b1bfb736b745bdf3fb20ef37dd016e41b7bf99aa5e9e2d74b396b9dd8691bcc79ab2565d42d72b48130fbcd1758accffe0d7f379c53427cc0694f8cce575441e317e44f5f79bfe2b25431840c9f27e37873c0a271e272cd244015d48fe187d2b5d6377b98c15cd6ba04f596731229ab4115dde36bfe8258e2e329fdf6b459cd81f79c0be1b1662e972c522845496950bb9081e6ec5f45d607cc69d3c41363edca20303ff164eb77040627527c016c157fe703707749b8c20c00e3e95f832adbf572cca96179acac6b703d0a2d229a0c1baf5aac15bc39dfefbdb97eea04d4cbfdc3a6486b6b5637d00b64da03907720c32e056c5a81d721004e6bde71e3ace13ea0397aad5eed8ef280f2ad0c6a69ce3019c9de5606209e4b424122f78422a4b703fb65ded324caf8cf7f836bfc99cfbabde40a0aa35c1f9b348d9679c462ee5ee46a3eca145d87032662b92daf113f004050a9ffdd2e2e7e5aad67c6cdf3f7194e3e3cc66414379d89ba34fde34e7a4b8803d0a5745c2a96211bac813cd6247c0178d7334b3b3d4e9686f48b9a3e0a66e25d4c43df2b4c0619557e0e8fc320"
        let bytes = stride(from: 0, to: hex.count, by: 2).map { offset -> UInt8 in
            let start = hex.index(hex.startIndex, offsetBy: offset)
            let end = hex.index(start, offsetBy: 2)
            return UInt8(hex[start..<end], radix: 16)!
        }
        let decoded = try AoVBytesDecoder.decodeResource(
            Data(bytes),
            resourceName: "Independent_lua.bytes"
        )
        XCTAssertEqual(decoded.wrapperMode, "aes+zstd")
        XCTAssertEqual(decoded.data.count, 2205)
        let rebuilt = try AoVBytesDecoder.encodeResource(
            decoded.data,
            resourceName: "Independent_lua.bytes",
            wrapperMode: "aes+zstd"
        )
        let roundTrip = try AoVBytesDecoder.decodeResource(rebuilt, resourceName: "Independent_lua.bytes")
        XCTAssertEqual(roundTrip.wrapperMode, "aes+zstd")
        XCTAssertEqual(roundTrip.data, decoded.data)
    }

    func testDecodesKnownRealAoVStandaloneZstdResource() throws {
        let hex = "224a00ef440d000028b52ffd63e755430b440c8d080063cd2b77a6f0df8879dc03ac97ad2a45a729c92d9187de81758034878b83bda15e6a679534ce961ce1eccb16562dd486f9640d91869633d817aa0bb485c9825c21aad05938a670fb041851b026d4122809730429420ca141e00f6063b957ac955a85529953a407514a47612890517852c375629bd41dca64eac89c88d337346839002c8016d6225406d3360c2868d2c2f908096193962a72396144438c369396ed6d59bb4ab38b6d10d698711d86dd8abd048093a811a037b4ff06c09532cc01821082866008212817be1174fcfe1924f7a43e65d5dfe210613fd391e5b9ffa86f67efb035cd103d3925ad4f3ff03fd998e6934f57463e3f421595544a5c397608a7f5b62402b7e67fa7a00133c1eb7407"
        let bytes = stride(from: 0, to: hex.count, by: 2).map { offset -> UInt8 in
            let start = hex.index(hex.startIndex, offsetBy: offset)
            let end = hex.index(start, offsetBy: 2)
            return UInt8(hex[start..<end], radix: 16)!
        }
        let decoded = try AoVBytesDecoder.decodeResource(
            Data(bytes),
            resourceName: "ResBillboard.bytes"
        )
        XCTAssertEqual(decoded.wrapperMode, "zstd")
        XCTAssertEqual(decoded.data.count, 3396)
        let rebuilt = try AoVBytesDecoder.encodeResource(
            decoded.data,
            resourceName: "ResBillboard.bytes",
            wrapperMode: "zstd"
        )
        let roundTrip = try AoVBytesDecoder.decodeResource(rebuilt, resourceName: "ResBillboard.bytes")
        XCTAssertEqual(roundTrip.wrapperMode, "zstd")
        XCTAssertEqual(roundTrip.data, decoded.data)
    }

    func testRejectsUnknownAESFlag() {
        var data = Data([0x22, 0x4A, 0x67, 0x01, 0x01, 0, 0, 0])
        data.append(Data(repeating: 0, count: 16))
        XCTAssertThrowsError(
            try AoVBytesDecoder.decodeResource(data, resourceName: "Unknown.bytes")
        ) { error in
            XCTAssertEqual(error as? AoVBytesDecodeError, .unsupportedAESFlag(0x01))
        }
    }

    func testSamePathPayloadsAreClassifiedBySHA256() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AOVSkinMergerTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let sameA = try makeArchive(root: root, name: "same-a.zip", path: "Resources/asset.bytes", payload: Data("same".utf8))
        let sameB = try makeArchive(root: root, name: "same-b.zip", path: "Resources/asset.bytes", payload: Data("same".utf8))
        let sameReport = try ArchiveScanner.analyze([sameA, sameB])
        XCTAssertEqual(sameReport.identicalDuplicatePaths, 1)
        XCTAssertEqual(sameReport.conflictingPaths, 0)
        XCTAssertEqual(sameReport.crcValidatedFiles, 2)

        let conflictB = try makeArchive(root: root, name: "different.zip", path: "Resources/asset.bytes", payload: Data("changed".utf8))
        let conflictReport = try ArchiveScanner.analyze([sameA, conflictB])
        XCTAssertEqual(conflictReport.identicalDuplicatePaths, 0)
        XCTAssertEqual(conflictReport.conflictingPaths, 1)
    }

    func testRecognizesRawAssetReferenceSetAndActorID() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AOVAssetRefTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        var payload = Data(repeating: 0, count: 512)
        let type = Data("TypeAssetRefAnalyser.AssetReferenceSet".utf8)
        let base = Data("baseSubset".utf8)
        let skin = Data("skinSubset".utf8)
        let reference = Data("VPrefabs/SkillEffects/TestEffect".utf8)
        payload.replaceSubrange(0..<5, with: Data("ROOTC".utf8))
        payload.replaceSubrange(16..<(16 + type.count), with: type)
        payload.replaceSubrange(120..<(120 + base.count), with: base)
        payload.replaceSubrange(160..<(160 + skin.count), with: skin)
        payload.replaceSubrange(220..<(220 + reference.count), with: reference)

        let zipURL = try makeArchive(
            root: root,
            name: "assetref.zip",
            path: "Resources/AssetRefs/Hero/593_AssetRef.bytes",
            payload: payload
        )
        let report = try ArchiveScanner.inspectAESResources([zipURL])
        XCTAssertEqual(report.assetReferenceSets.count, 1)
        XCTAssertEqual(report.assetReferenceSets.first?.actorID, "593")
        XCTAssertEqual(report.assetReferenceSets.first?.wrapperMode, "raw")
        XCTAssertTrue(report.assetReferenceSets.first?.hasBaseSubset ?? false)
        XCTAssertTrue(report.assetReferenceSets.first?.hasSkinSubset ?? false)
        XCTAssertEqual(report.assetReferenceSets.first?.referencePathCandidates, 1)
    }

    func testMergesDirectSkinOrAvatarListAndPreservesBaseTrackXML() throws {
        let path = "commonresource/Back.xml"
        let baseXML = Data("""
        <?xml version="1.0" encoding="utf-8"?>
        <Project><TemplateObjectList/><RefParamList/><Action length="1.000" loop="false">
          <Track trackName="TriggerParticleTick0" eventType="TriggerParticleTick" enabled="true">
            <Event eventName="TriggerParticleTick" time="0.000" isDuration="false"/>
          </Track>
        </Action></Project>
        """.utf8)
        let overlayXML = Data("""
        <?xml version="1.0" encoding="utf-8"?>
        <Project><TemplateObjectList/><RefParamList/><Action length="1.000" loop="false">
          <Track trackName="0_TriggerParticleTick0" eventType="TriggerParticleTick" enabled="true">
            <Event eventName="TriggerParticleTick" time="0.000" isDuration="false"/>
            <SkinOrAvatarList id="59500"/>
          </Track>
        </Action></Project>
        """.utf8)
        let base = try AoVBytesDecoder.encodeResource(baseXML, resourceName: path, wrapperMode: "zstd")
        let overlay = try AoVBytesDecoder.encodeResource(overlayXML, resourceName: path, wrapperMode: "zstd")

        let (merged, report) = try AoVCommonActionsMerger.mergeActionXML(
            baseBytes: base,
            overlayBytes: overlay,
            resourceName: path,
            specialSkinIDs: ["59500"]
        )
        let output = try AoVBytesDecoder.decodeResource(merged, resourceName: path)
        let text = String(data: output.data, encoding: .utf8) ?? ""
        XCTAssertTrue(text.contains("SkinOrAvatarList"))
        XCTAssertTrue(text.contains("59500"))
        XCTAssertEqual(report.wrapperMode, "zstd")
        XCTAssertTrue(report.verified)
        XCTAssertEqual(report.replacedTracks, ["TriggerParticleTick0"])
    }

    func testAppendsVerifiedScalarCheckSkinIdTickTrack() throws {
        let path = "commonresource/Back.xml"
        let baseXML = Data("""
        <Project><Action><Track trackName="Existing" eventType="TriggerParticleTick"/></Action></Project>
        """.utf8)
        let overlayXML = Data("""
        <Project><Action>
          <Track trackName="0_CheckSkin" eventType="CheckSkinIdTick">
            <Event eventName="CheckSkinIdTick">
              <TemplateObject name="targetId" objectName="self" id="0"/>
              <int name="skinId" value="59300"/>
            </Event>
          </Track>
        </Action></Project>
        """.utf8)
        let base = try AoVBytesDecoder.encodeResource(baseXML, resourceName: path, wrapperMode: "aes")
        let overlay = try AoVBytesDecoder.encodeResource(overlayXML, resourceName: path, wrapperMode: "aes")
        let (merged, report) = try AoVCommonActionsMerger.mergeActionXML(
            baseBytes: base, overlayBytes: overlay, resourceName: path, specialSkinIDs: ["59300"]
        )
        let output = try AoVBytesDecoder.decodeResource(merged, resourceName: path)
        let text = String(data: output.data, encoding: .utf8) ?? ""
        XCTAssertTrue(text.contains("name=\"skinId\" value=\"59300\""))
        XCTAssertEqual(report.conditionAppendedTracks, ["CheckSkin"])
        XCTAssertTrue(report.verified)
    }

    func testArrayCheckSkinIdTickKeepsOnlyActorOwnedSkinIDs() throws {
        let path = "commonresource/Back.xml"
        let baseXML = Data("<Project><Action/></Project>".utf8)
        let overlayXML = Data("""
        <Project><Action>
          <Track trackName="0_CheckArray" eventType="CheckSkinIdVirtualTick">
            <Event eventName="CheckSkinIdVirtualTick">
              <TemplateObject name="targetId" objectName="target" id="1"/>
              <Array name="skinIdArray" type="int">
                <int value="59300"/><int value="59500"/>
              </Array>
            </Event>
          </Track>
        </Action></Project>
        """.utf8)
        let base = try AoVBytesDecoder.encodeResource(baseXML, resourceName: path, wrapperMode: "aes+zstd")
        let overlay = try AoVBytesDecoder.encodeResource(overlayXML, resourceName: path, wrapperMode: "aes+zstd")
        let (merged, report) = try AoVCommonActionsMerger.mergeActionXML(
            baseBytes: base, overlayBytes: overlay, resourceName: path, specialSkinIDs: ["59300"]
        )
        let output = try AoVBytesDecoder.decodeResource(merged, resourceName: path)
        let text = String(data: output.data, encoding: .utf8) ?? ""
        XCTAssertTrue(text.contains("value=\"59300\""))
        XCTAssertFalse(text.contains("value=\"59500\""))
        XCTAssertEqual(report.conditionAppendedTracks, ["CheckArray"])
        XCTAssertTrue(report.verified)
    }

    func testRejectsNestedCommonActionsSkinListLayout() throws {
        let path = "commonresource/Back.xml"
        let base = try AoVBytesDecoder.encodeResource(
            Data("<Project><Action/></Project>".utf8),
            resourceName: path,
            wrapperMode: "zstd"
        )
        let overlayXML = Data("""
        <Project><Action><Track trackName="0_Nested" eventType="TriggerParticleTick">
          <Event><SkinOrAvatarList id="59300"/></Event>
        </Track></Action></Project>
        """.utf8)
        let overlay = try AoVBytesDecoder.encodeResource(overlayXML, resourceName: path, wrapperMode: "zstd")
        XCTAssertThrowsError(try AoVCommonActionsMerger.mergeActionXML(
            baseBytes: base, overlayBytes: overlay, resourceName: path, specialSkinIDs: ["59300"]
        ))
    }

    func testMergesCommonActionsPackageZipAndPreservesVerifiedXML() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AOVCommonActionsPackageTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let path = "commonresource/Back.xml"
        let baseXML = Data("""
        <Project><TemplateObjectList/><RefParamList/><Action length="1.000" loop="false">
          <Track trackName="TriggerParticleTick0" eventType="TriggerParticleTick" enabled="true">
            <Event eventName="TriggerParticleTick" time="0.000" isDuration="false"/>
          </Track>
        </Action></Project>
        """.utf8)
        let overlayXML = Data("""
        <Project><TemplateObjectList/><RefParamList/><Action length="1.000" loop="false">
          <Track trackName="0_TriggerParticleTick0" eventType="TriggerParticleTick" enabled="true">
            <Event eventName="TriggerParticleTick" time="0.000" isDuration="false"/>
            <SkinOrAvatarList id="59500"/>
          </Track>
        </Action></Project>
        """.utf8)
        let baseResource = try AoVBytesDecoder.encodeResource(baseXML, resourceName: path, wrapperMode: "zstd")
        let overlayResource = try AoVBytesDecoder.encodeResource(overlayXML, resourceName: path, wrapperMode: "zstd")
        let baseURL = try makeArchive(root: root, name: "base.pkg.zip", path: path, payload: baseResource)
        let overlayURL = try makeArchive(root: root, name: "overlay.pkg.zip", path: path, payload: overlayResource)

        let (mergedPackage, report) = try AoVCommonActionsMerger.mergeCommonActionsPackage(
            basePackage: Data(contentsOf: baseURL),
            overlayPackage: Data(contentsOf: overlayURL),
            specialSkinIDs: ["59500"]
        )
        XCTAssertTrue(report.verified)
        XCTAssertEqual(report.mergedFiles, [path])

        let outputURL = root.appendingPathComponent("merged.pkg.zip")
        try mergedPackage.write(to: outputURL)
        let outputArchive = try Archive(url: outputURL, accessMode: .read)
        guard let mergedEntry = outputArchive.first(where: { $0.path == path }) else {
            return XCTFail("Merged CommonActions XML entry missing")
        }
        var xml = Data()
        _ = try outputArchive.extract(mergedEntry, consumer: { xml.append($0) })
        let decoded = try AoVBytesDecoder.decodeResource(xml, resourceName: path)
        let outputText = String(data: decoded.data, encoding: .utf8) ?? ""
        XCTAssertTrue(outputText.contains("SkinOrAvatarList"))
        XCTAssertTrue(outputText.contains("59500"))
    }

    func testCommonActionsPackageFailsOnOpaqueSamePathConflict() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AOVCommonActionsConflictTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let baseURL = try makeArchive(root: root, name: "base.zip", path: "commonresource/Other.bytes", payload: Data("base".utf8))
        let overlayURL = try makeArchive(root: root, name: "overlay.zip", path: "commonresource/Other.bytes", payload: Data("overlay".utf8))
        XCTAssertThrowsError(try AoVCommonActionsMerger.mergeCommonActionsPackage(
            basePackage: Data(contentsOf: baseURL),
            overlayPackage: Data(contentsOf: overlayURL),
            specialSkinIDs: ["59300"]
        ))
    }

    func testOfflineModArchiveMergerBuildsValidatedOuterZIP() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-ModMerge-E2E-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let commonPath = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
        let backPath = "commonresource/Back.xml"
        let baseXML = Data("""
        <Project><Action><Track trackName="TriggerParticleTick0" eventType="TriggerParticleTick">
          <Event eventName="TriggerParticleTick" time="0.000"/>
        </Track></Action></Project>
        """.utf8)
        let overlayXML = Data("""
        <Project><Action><Track trackName="0_TriggerParticleTick0" eventType="TriggerParticleTick">
          <Event eventName="TriggerParticleTick" time="0.000"/>
          <SkinOrAvatarList id="59300"/>
        </Track></Action></Project>
        """.utf8)
        let baseBack = try AoVBytesDecoder.encodeResource(baseXML, resourceName: backPath, wrapperMode: "zstd")
        let overlayBack = try AoVBytesDecoder.encodeResource(overlayXML, resourceName: backPath, wrapperMode: "zstd")
        let baseCommon = try makeArchive(
            root: root,
            name: "base-common.zip",
            path: backPath,
            payload: baseBack
        )
        let overlayCommon = try makeArchive(
            root: root,
            name: "overlay-common.zip",
            path: backPath,
            payload: overlayBack
        )

        let packerPath = "assetbundle/resourcepackerinfosetall.assetbundle"
        let verificationPath = "assetbundle/resourceverificationinfosetall.assetbundle"
        let resSkinPath = "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        let ifixPath = "Thanos/HeroSkin_1.bytes"
        let baseIFix = try AoVBytesDecoder.encodeResource(
            AoVIFixMerger.serialize(makeIFixPatch(name: "BaseFix")),
            resourceName: ifixPath,
            wrapperMode: "aes"
        )
        let overlayIFix = try AoVBytesDecoder.encodeResource(
            AoVIFixMerger.serialize(makeIFixPatch(name: "OverlayFix")),
            resourceName: ifixPath,
            wrapperMode: "aes"
        )
        let baseResourcesURL = try makeArchive(
            root: root,
            name: "base-resources.zip",
            files: [
                "version.txt": Data("1.64.1.12".utf8),
                commonPath: Data(contentsOf: baseCommon),
                packerPath: Data("packer-placeholder".utf8),
                verificationPath: Data("verification-placeholder".utf8),
                resSkinPath: Data("base-res-skin".utf8),
                ifixPath: baseIFix
            ]
        )
        let overlayResourcesURL = try makeArchive(
            root: root,
            name: "overlay-resources.zip",
            files: [
                "version.txt": Data("1.64.1.12".utf8),
                commonPath: Data(contentsOf: overlayCommon),
                "Actor_593_Actions.pkg.bytes": Data("actor593".utf8),
                "Ages/Prefab_Characters/Prefab_Hero/Actor593/NewResource.bytes": Data("unique-overlay-resource".utf8),
                ifixPath: overlayIFix
            ]
        )

        let baseOuter = try makeArchive(
            root: root,
            name: "base.zip",
            files: [
                "Resources.zip": Data(contentsOf: baseResourcesURL),
                "Resources/1.64.1/LooseBase.bytes": Data("keep-this-loose-file".utf8),
                "DanhSachSkin.txt": Data("Base Skin\n".utf8)
            ]
        )
        let overlayOuter = try makeArchive(
            root: root,
            name: "overlay.zip",
            files: [
                "Resources.zip": Data(contentsOf: overlayResourcesURL),
                "DanhSachSkin.txt": Data("Creator Skin\n".utf8)
            ]
        )

        let report = try AoVModArchiveMerger.merge([baseOuter, overlayOuter])
        XCTAssertTrue(report.structurallyValidated)
        XCTAssertFalse(report.gameCompatibilityTested)
        XCTAssertEqual(report.baseSource, "base.zip")
        XCTAssertEqual(report.commonActionsFiles, [commonPath])
        XCTAssertEqual(report.mergedIFixPatches, [ifixPath])
        XCTAssertEqual(report.addedResources, 2)
        XCTAssertTrue(FileManager.default.fileExists(atPath: report.outputURL.path))

        let outer = try Archive(url: report.outputURL, accessMode: .read)
        guard let resourcesEntry = outer.first(where: {
            ($0.path as NSString).lastPathComponent == "Resources.zip"
        }), let skinEntry = outer.first(where: {
            ($0.path as NSString).lastPathComponent == "DanhSachSkin.txt"
        }) else {
            return XCTFail("Output wrapper is missing Resources.zip or DanhSachSkin.txt")
        }

        XCTAssertNotNil(outer.first(where: { $0.path == "Resources/1.64.1/LooseBase.bytes" }))
        let mergedResourcesURL = root.appendingPathComponent("output-resources.zip")
        _ = try outer.extract(resourcesEntry, to: mergedResourcesURL)
        var skinData = Data()
        _ = try outer.extract(skinEntry, consumer: { skinData.append($0) })
        let skinText = String(data: skinData, encoding: .utf8) ?? ""
        XCTAssertTrue(skinText.contains("Base Skin"))
        XCTAssertTrue(skinText.contains("Creator Skin"))

        let resources = try Archive(url: mergedResourcesURL, accessMode: .read)
        guard let commonEntry = resources.first(where: { $0.path == commonPath }),
              resources.first(where: { $0.path == "Ages/Prefab_Characters/Prefab_Hero/Actor593/NewResource.bytes" }) != nil else {
            return XCTFail("Output Resources.zip is missing merged/added resources")
        }
        var packageData = Data()
        _ = try resources.extract(commonEntry, consumer: { packageData.append($0) })
        let packageURL = root.appendingPathComponent("output-common.pkg.zip")
        try packageData.write(to: packageURL)
        let common = try Archive(url: packageURL, accessMode: .read)
        guard let backEntry = common.first(where: { $0.path == backPath }) else {
            return XCTFail("Merged CommonActions Back.xml is missing")
        }
        var wrappedXML = Data()
        _ = try common.extract(backEntry, consumer: { wrappedXML.append($0) })
        let decodedXML = try AoVBytesDecoder.decodeResource(wrappedXML, resourceName: backPath)
        let xmlText = String(data: decodedXML.data, encoding: .utf8) ?? ""
        XCTAssertTrue(xmlText.contains("SkinOrAvatarList"))
        XCTAssertTrue(xmlText.contains("59300"))
    }

    private func makeIFixPatch(name: String) -> AoVIFixPatch {
        AoVIFixPatch(
            magic: 123456789,
            bridge: "Project_d",
            types: ["\(name)Type"],
            methods: [AoVIFixMethod(instructions: [
                AoVIFixInstruction(opcode: 103, operand: 0)
            ], handlers: [])],
            externalMethods: [],
            strings: [],
            fields: [],
            staticFields: [],
            anonymousClasses: [],
            wrappers: "Wrappers",
            assembly: "Project_d",
            fixes: [AoVIFixFixInfo(
                isGeneric: false,
                typeID: 0,
                name: name,
                genericArguments: [],
                parameters: [],
                patchIndex: 0
            )],
            newClasses: []
        )
    }

    private func makeArchive(root: URL, name: String, files: [String: Data]) throws -> URL {
        let archiveURL = root.appendingPathComponent(name)
        let archive = try Archive(url: archiveURL, accessMode: .create)
        for (path, data) in files {
            let fileURL = root.appendingPathComponent(UUID().uuidString + ".tmp")
            try data.write(to: fileURL)
            defer { try? FileManager.default.removeItem(at: fileURL) }
            try archive.addEntry(with: path, fileURL: fileURL, compressionMethod: .deflate)
        }
        return archiveURL
    }

    private func makeArchive(root: URL, name: String, path: String, payload: Data) throws -> URL {
        let source = root.appendingPathComponent(UUID().uuidString + ".bin")
        try payload.write(to: source)
        let archiveURL = root.appendingPathComponent(name)
        let archive = try Archive(url: archiveURL, accessMode: .create)
        try archive.addEntry(with: path, fileURL: source, compressionMethod: .deflate)
        return archiveURL
    }
}
