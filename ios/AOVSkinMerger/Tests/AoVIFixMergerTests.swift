import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVIFixMergerTests: XCTestCase {
    private func makePatch(
        name: String,
        typeName: String,
        stringValue: String,
        fieldName: String,
        instructions: [AoVIFixInstruction] = [AoVIFixInstruction(opcode: 103, operand: 0)],
        anonymous: [AoVIFixAnonymousClass] = []
    ) -> AoVIFixPatch {
        AoVIFixPatch(
            magic: 123456789,
            bridge: "Project_d",
            types: [typeName],
            methods: [AoVIFixMethod(instructions: instructions, handlers: [])],
            externalMethods: [
                AoVIFixMethodReference(
                    isGeneric: false,
                    typeID: 0,
                    name: "\(name)Call",
                    genericArguments: [],
                    parameters: []
                )
            ],
            strings: [stringValue],
            fields: [AoVIFixField(isNew: false, typeID: 0, name: fieldName, extra: Data())],
            staticFields: [AoVIFixStaticField(typeID: 0, constructorIndex: 0)],
            anonymousClasses: anonymous,
            wrappers: "Wrappers",
            assembly: "Project_d",
            fixes: [
                AoVIFixFixInfo(
                    isGeneric: false,
                    typeID: 0,
                    name: name,
                    genericArguments: [],
                    parameters: [],
                    patchIndex: 0
                )
            ],
            newClasses: []
        )
    }

    func testIFixParseSerializeRoundTrip() throws {
        let patch = makePatch(
            name: "BaseFix",
            typeName: "BaseType",
            stringValue: "base-string",
            fieldName: "baseField"
        )
        let encoded = try AoVIFixMerger.serialize(patch)
        let decoded = try AoVIFixMerger.parse(encoded)
        XCTAssertEqual(try AoVIFixMerger.serialize(decoded), encoded)
        XCTAssertEqual(decoded.types, ["BaseType"])
        XCTAssertEqual(decoded.fixes.first?.name, "BaseFix")
    }

    func testGenericMergeRemapsAllReferencedIndexTables() throws {
        let base = makePatch(
            name: "BaseFix",
            typeName: "BaseType",
            stringValue: "base-string",
            fieldName: "baseField"
        )
        let incoming = makePatch(
            name: "IncomingFix",
            typeName: "IncomingType",
            stringValue: "incoming-string",
            fieldName: "incomingField",
            instructions: [
                AoVIFixInstruction(opcode: 39, operand: 0),   // ldstr
                AoVIFixInstruction(opcode: 45, operand: 0),   // call extern method
                AoVIFixInstruction(opcode: 5, operand: 0),    // stobj type
                AoVIFixInstruction(opcode: 34, operand: 0),   // ldflda field
                AoVIFixInstruction(opcode: 94, operand: 0),   // ldtoken type
                AoVIFixInstruction(opcode: 103, operand: 0)   // ret
            ],
            anonymous: [
                AoVIFixAnonymousClass(
                    fields: [0, -2],
                    constructor: 0,
                    constructorParameterCount: 0,
                    interfaces: [0],
                    vtable: [0, -1]
                )
            ]
        )

        let merged = try AoVIFixMerger.merge(base, incoming)
        XCTAssertEqual(merged.types.count, 2)
        XCTAssertEqual(merged.methods.count, 2)
        XCTAssertEqual(merged.externalMethods.count, 2)
        XCTAssertEqual(merged.strings.count, 2)
        XCTAssertEqual(merged.fields.count, 2)
        XCTAssertEqual(merged.methods[1].instructions, [
            AoVIFixInstruction(opcode: 39, operand: 1),
            AoVIFixInstruction(opcode: 45, operand: 1),
            AoVIFixInstruction(opcode: 5, operand: 1),
            AoVIFixInstruction(opcode: 34, operand: 1),
            AoVIFixInstruction(opcode: 94, operand: 1),
            AoVIFixInstruction(opcode: 103, operand: 0)
        ])
        XCTAssertEqual(merged.fixes[1].typeID, 1)
        XCTAssertEqual(merged.fixes[1].patchIndex, 1)
        XCTAssertEqual(merged.anonymousClasses[0].fields, [1, -2])
        XCTAssertEqual(merged.anonymousClasses[0].constructor, 1)
        XCTAssertEqual(merged.anonymousClasses[0].interfaces, [1])
        XCTAssertEqual(merged.anonymousClasses[0].vtable, [1, -1])

        let encoded = try AoVIFixMerger.serialize(merged)
        let reparsed = try AoVIFixMerger.parse(encoded)
        XCTAssertEqual(try AoVIFixMerger.serialize(reparsed), encoded)
    }

    func testMergeIFixResourcePreservesAoVWrapperAndRoundTrips() throws {
        let path = "Thanos/HeroSkin_1.bytes"
        let basePatch = makePatch(name: "BaseFix", typeName: "BaseType",
                                  stringValue: "base-string", fieldName: "baseField")
        let overlayPatch = makePatch(name: "OverlayFix", typeName: "OverlayType",
                                     stringValue: "overlay-string", fieldName: "overlayField")
        let base = try AoVBytesDecoder.encodeResource(
            AoVIFixMerger.serialize(basePatch),
            resourceName: path,
            wrapperMode: "aes"
        )
        let overlay = try AoVBytesDecoder.encodeResource(
            AoVIFixMerger.serialize(overlayPatch),
            resourceName: path,
            wrapperMode: "aes"
        )
        let (merged, report) = try AoVIFixMerger.mergeResource(base, overlay, resourcePath: path)
        let decoded = try AoVBytesDecoder.decodeResource(merged, resourceName: path)
        let patch = try AoVIFixMerger.parse(decoded.data)
        XCTAssertEqual(decoded.wrapperMode, "aes")
        XCTAssertEqual(patch.fixes.count, 2)
        XCTAssertEqual(report.route, "generic_index_remap_union")
        XCTAssertTrue(report.verified)
    }

    func testRejectsUnknownOpcodeBeforeMerge() throws {
        let base = makePatch(name: "BaseFix", typeName: "BaseType",
                             stringValue: "base-string", fieldName: "baseField")
        let overlay = makePatch(
            name: "IncomingFix",
            typeName: "IncomingType",
            stringValue: "incoming-string",
            fieldName: "incomingField",
            instructions: [AoVIFixInstruction(opcode: 999, operand: 0)]
        )
        XCTAssertThrowsError(try AoVIFixMerger.merge(base, overlay))
    }
}
