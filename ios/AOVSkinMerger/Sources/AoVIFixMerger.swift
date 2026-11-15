import Foundation

enum AoVIFixError: LocalizedError {
    case truncated
    case invalidCount(String)
    case invalidUTF8
    case trailingBytes
    case unknownOpcode(Int32)
    case invalidReference(String)
    case metadataMismatch(String)
    case duplicateFix
    case invalidActorScoped(String)
    case actorMappingConflict(String)
    case roundTripFailure

    var errorDescription: String? {
        switch self {
        case .truncated: return "IFix patch bị cắt cụt hoặc không đúng cấu trúc."
        case .invalidCount(let label): return "Số lượng \(label) trong IFix không hợp lệ."
        case .invalidUTF8: return "Chuỗi IFix không phải UTF-8 hợp lệ."
        case .trailingBytes: return "IFix patch có dữ liệu dư hoặc không tương thích."
        case .unknownOpcode(let opcode): return "Opcode IFix chưa hỗ trợ: \(opcode)."
        case .invalidReference(let detail): return "IFix có tham chiếu không hợp lệ: \(detail)"
        case .metadataMismatch(let key): return "Không thể ghép IFix: trường \(key) không khớp."
        case .duplicateFix: return "Hai IFix patch có FixInfo trùng nhau; dừng để tránh ghi đè."
        case .invalidActorScoped(let detail): return "IFix actor-scoped không đúng mẫu đã xác minh: \(detail)"
        case .actorMappingConflict(let detail): return "IFix có mapping Actor xung đột: \(detail)"
        case .roundTripFailure: return "IFix sau serialize/mã hóa không round-trip chính xác."
        }
    }
}

enum AoVIFixParameter: Equatable, Hashable, Sendable {
    case string(String)
    case index(Int32)
}

struct AoVIFixInstruction: Equatable, Sendable {
    var opcode: Int32
    var operand: Int32
}

struct AoVIFixExceptionHandler: Equatable, Sendable {
    var values: [Int32]
}

struct AoVIFixMethod: Equatable, Sendable {
    var instructions: [AoVIFixInstruction]
    var handlers: [AoVIFixExceptionHandler]
}

struct AoVIFixMethodReference: Equatable, Sendable {
    var isGeneric: Bool
    var typeID: Int32
    var name: String
    var genericArguments: [Int32]
    var parameters: [AoVIFixParameter]
}

struct AoVIFixField: Equatable, Sendable {
    var isNew: Bool
    var typeID: Int32
    var name: String
    var extra: Data
}

struct AoVIFixStaticField: Equatable, Sendable {
    var typeID: Int32
    var constructorIndex: Int32
}

struct AoVIFixAnonymousClass: Equatable, Sendable {
    var fields: [Int32]
    var constructor: Int32
    var constructorParameterCount: Int32
    var interfaces: [Int32]
    var vtable: [Int32]
}

struct AoVIFixFixInfo: Equatable, Sendable {
    var isGeneric: Bool
    var typeID: Int32
    var name: String
    var genericArguments: [Int32]
    var parameters: [AoVIFixParameter]
    var patchIndex: Int32
}

struct AoVIFixPatch: Equatable, Sendable {
    var magic: UInt64
    var bridge: String
    var types: [String]
    var methods: [AoVIFixMethod]
    var externalMethods: [AoVIFixMethodReference]
    var strings: [String]
    var fields: [AoVIFixField]
    var staticFields: [AoVIFixStaticField]
    var anonymousClasses: [AoVIFixAnonymousClass]
    var wrappers: String
    var assembly: String
    var fixes: [AoVIFixFixInfo]
    var newClasses: [String]
}

private struct AoVIFixReader {
    let data: Data
    var offset = 0

    mutating func read(_ count: Int) throws -> Data {
        guard count >= 0, offset <= data.count, count <= data.count - offset else {
            throw AoVIFixError.truncated
        }
        defer { offset += count }
        return data.subdata(in: offset..<(offset + count))
    }

    mutating func byte() throws -> UInt8 { try read(1)[0] }
    mutating func boolean() throws -> Bool { try byte() != 0 }

    mutating func int32() throws -> Int32 {
        let b = try read(4)
        let value = UInt32(b[0]) | (UInt32(b[1]) << 8) |
            (UInt32(b[2]) << 16) | (UInt32(b[3]) << 24)
        return Int32(bitPattern: value)
    }

    mutating func uint64() throws -> UInt64 {
        let b = try read(8)
        var result: UInt64 = 0
        for index in 0..<8 { result |= UInt64(b[index]) << (index * 8) }
        return result
    }

    mutating func count(_ label: String, maximum: Int = 2_000_000) throws -> Int {
        let value = try int32()
        guard value >= 0, Int(value) <= maximum else { throw AoVIFixError.invalidCount(label) }
        return Int(value)
    }

    mutating func string() throws -> String {
        var length = 0
        var shift = 0
        while true {
            let value = Int(try byte())
            if shift >= 35 { throw AoVIFixError.invalidCount("độ dài chuỗi") }
            length |= (value & 0x7F) << shift
            if value & 0x80 == 0 { break }
            shift += 7
        }
        guard length <= 32 * 1024 * 1024 else { throw AoVIFixError.invalidCount("độ dài chuỗi") }
        let bytes = try read(length)
        guard let value = String(data: bytes, encoding: .utf8) else { throw AoVIFixError.invalidUTF8 }
        return value
    }

    mutating func intArray() throws -> [Int32] {
        try (0..<count("phần tử")).map { _ in try int32() }
    }
}

private struct AoVIFixWriter {
    var data = Data()

    mutating func byte(_ value: UInt8) { data.append(value) }
    mutating func boolean(_ value: Bool) { byte(value ? 1 : 0) }

    mutating func int32(_ value: Int32) {
        let bits = UInt32(bitPattern: value)
        data.append(contentsOf: [
            UInt8(bits & 0xFF), UInt8((bits >> 8) & 0xFF),
            UInt8((bits >> 16) & 0xFF), UInt8((bits >> 24) & 0xFF)
        ])
    }

    mutating func uint64(_ value: UInt64) {
        for index in 0..<8 { byte(UInt8((value >> (index * 8)) & 0xFF)) }
    }

    mutating func string(_ value: String) {
        let bytes = Data(value.utf8)
        var length = bytes.count
        while length >= 0x80 {
            byte(UInt8((length & 0x7F) | 0x80))
            length >>= 7
        }
        byte(UInt8(length))
        data.append(bytes)
    }

    mutating func intArray(_ values: [Int32]) {
        int32(Int32(values.count))
        values.forEach { int32($0) }
    }
}

struct AoVIFixReport: Sendable {
    let route: String
    let typeCount: Int
    let methodCount: Int
    let fixCount: Int
    let verified: Bool
}

enum AoVIFixMerger {
    private static let operandKinds: [Int32: String] = [
        0: "none",
        1: "none",
        2: "none",
        3: "none",
        5: "type",
        6: "method",
        7: "none",
        8: "var",
        9: "none",
        11: "branch",
        12: "method",
        13: "var",
        15: "type",
        16: "type",
        17: "branch",
        19: "none",
        20: "none",
        21: "none",
        22: "type",
        23: "none",
        24: "type",
        25: "none",
        27: "none",
        28: "8byte",
        29: "none",
        30: "branch",
        33: "branch",
        34: "field",
        35: "none",
        36: "var",
        37: "none",
        39: "string",
        40: "none",
        41: "none",
        42: "none",
        43: "branch",
        44: "none",
        45: "method",
        46: "none",
        47: "branch",
        48: "none",
        49: "none",
        51: "none",
        52: "none",
        53: "type",
        55: "none",
        56: "type",
        57: "none",
        58: "none",
        59: "none",
        60: "field",
        61: "none",
        62: "var",
        63: "none",
        64: "none",
        65: "none",
        66: "none",
        67: "type",
        68: "none",
        69: "none",
        71: "none",
        72: "var",
        73: "method",
        74: "none",
        75: "field",
        76: "none",
        77: "type",
        78: "none",
        79: "none",
        80: "none",
        81: "type",
        82: "none",
        83: "type",
        84: "none",
        85: "field",
        86: "type",
        87: "none",
        88: "type",
        89: "type",
        90: "none",
        91: "none",
        93: "branch",
        94: "token",
        95: "type",
        96: "type",
        97: "method",
        98: "none",
        99: "none",
        100: "8byte",
        101: "none",
        103: "none",
        105: "none",
        108: "none",
        109: "branch",
        110: "branch",
        111: "branch",
        112: "none",
        113: "none",
        115: "none",
        116: "branch",
        117: "type",
        119: "none",
        120: "none",
        121: "none",
        122: "method",
        123: "none",
        124: "none",
        125: "branch",
        126: "none",
        127: "branch",
        128: "none",
        129: "none",
        130: "none",
        131: "none",
        132: "none",
        133: "type",
        134: "branch",
        135: "type",
        136: "none",
        137: "type",
        138: "method",
        139: "none",
        140: "none",
        141: "int",
        143: "int",
        144: "none",
        145: "none",
        147: "none",
        148: "none",
        149: "switch",
        150: "none",
        151: "type",
        153: "branch",
        155: "type",
        156: "none",
        158: "none",
        159: "none",
        160: "none",
        161: "type",
        162: "method",
        163: "none",
        164: "var",
        165: "none",
        166: "none",
        167: "field",
        168: "type",
        169: "none",
        170: "none",
        171: "none",
        172: "type",
        173: "none",
        174: "none",
        175: "none",
        176: "branch",
        177: "none",
        178: "none",
        179: "none"
    ]

    static func parse(_ data: Data) throws -> AoVIFixPatch {
        var reader = AoVIFixReader(data: data)
        let magic = try reader.uint64()
        let bridge = try reader.string()
        let types = try (0..<reader.count("bản ghi")).map { _ in try reader.string() }

        var methods: [AoVIFixMethod] = []
        for _ in 0..<reader.count("bản ghi") {
            let instructions = try (0..<reader.count("bản ghi")).map { _ in
                AoVIFixInstruction(opcode: try reader.int32(), operand: try reader.int32())
            }
            let handlers = try (0..<reader.count("bản ghi")).map { _ in
                AoVIFixExceptionHandler(values: try (0..<6).map { _ in try reader.int32() })
            }
            methods.append(AoVIFixMethod(instructions: instructions, handlers: handlers))
        }

        func readReference(_ reader: inout AoVIFixReader, hasPatchIndex: Bool) throws -> AoVIFixMethodReference {
            let isGeneric = try reader.boolean()
            let typeID = try reader.int32()
            let name = try reader.string()
            var genericArguments: [Int32] = []
            var parameters: [AoVIFixParameter] = []
            if isGeneric {
                genericArguments = try reader.intArray()
                for _ in 0..<reader.count("bản ghi") {
                    if try reader.boolean() {
                        parameters.append(.string(try reader.string()))
                    } else {
                        parameters.append(.index(try reader.int32()))
                    }
                }
            } else {
                parameters = try reader.intArray().map { .index($0) }
            }
            if hasPatchIndex {
                _ = try reader.int32()
            }
            return AoVIFixMethodReference(
                isGeneric: isGeneric, typeID: typeID, name: name,
                genericArguments: genericArguments, parameters: parameters
            )
        }

        var externalMethods: [AoVIFixMethodReference] = []
        for _ in 0..<reader.count("bản ghi") {
            externalMethods.append(try readReference(&reader, hasPatchIndex: false))
        }
        let strings = try (0..<reader.count("bản ghi")).map { _ in try reader.string() }

        var fields: [AoVIFixField] = []
        for _ in 0..<reader.count("bản ghi") {
            let isNew = try reader.boolean()
            let typeID = try reader.int32()
            let name = try reader.string()
            fields.append(AoVIFixField(
                isNew: isNew, typeID: typeID, name: name,
                extra: isNew ? try reader.read(8) : Data()
            ))
        }

        let staticFields = try (0..<reader.count("bản ghi")).map { _ in
            AoVIFixStaticField(typeID: try reader.int32(), constructorIndex: try reader.int32())
        }
        var anonymousClasses: [AoVIFixAnonymousClass] = []
        for _ in 0..<reader.count("bản ghi") {
            anonymousClasses.append(AoVIFixAnonymousClass(
                fields: try reader.intArray(),
                constructor: try reader.int32(),
                constructorParameterCount: try reader.int32(),
                interfaces: try reader.intArray(),
                vtable: try reader.intArray()
            ))
        }
        let wrappers = try reader.string()
        let assembly = try reader.string()

        var fixes: [AoVIFixFixInfo] = []
        for _ in 0..<reader.count("bản ghi") {
            let isGeneric = try reader.boolean()
            let typeID = try reader.int32()
            let name = try reader.string()
            var genericArguments: [Int32] = []
            var parameters: [AoVIFixParameter] = []
            if isGeneric {
                genericArguments = try reader.intArray()
                for _ in 0..<reader.count("bản ghi") {
                    if try reader.boolean() {
                        parameters.append(.string(try reader.string()))
                    } else {
                        parameters.append(.index(try reader.int32()))
                    }
                }
            } else {
                parameters = try reader.intArray().map { .index($0) }
            }
            fixes.append(AoVIFixFixInfo(
                isGeneric: isGeneric, typeID: typeID, name: name,
                genericArguments: genericArguments, parameters: parameters,
                patchIndex: try reader.int32()
            ))
        }
        let newClasses = try (0..<reader.count("bản ghi")).map { _ in try reader.string() }
        guard reader.offset == data.count else { throw AoVIFixError.trailingBytes }

        let patch = AoVIFixPatch(
            magic: magic, bridge: bridge, types: types, methods: methods,
            externalMethods: externalMethods, strings: strings, fields: fields,
            staticFields: staticFields, anonymousClasses: anonymousClasses,
            wrappers: wrappers, assembly: assembly, fixes: fixes, newClasses: newClasses
        )
        try validate(patch)
        return patch
    }

    static func serialize(_ patch: AoVIFixPatch) throws -> Data {
        try validate(patch)
        var writer = AoVIFixWriter()
        writer.uint64(patch.magic)
        writer.string(patch.bridge)
        writer.int32(Int32(patch.types.count))
        patch.types.forEach { writer.string($0) }

        writer.int32(Int32(patch.methods.count))
        for method in patch.methods {
            writer.int32(Int32(method.instructions.count))
            for instruction in method.instructions {
                writer.int32(instruction.opcode)
                writer.int32(instruction.operand)
            }
            writer.int32(Int32(method.handlers.count))
            for handler in method.handlers {
                for value in handler.values { writer.int32(value) }
            }
        }

        func writeReference(_ item: AoVIFixMethodReference, writer: inout AoVIFixWriter) {
            writer.boolean(item.isGeneric)
            writer.int32(item.typeID)
            writer.string(item.name)
            if item.isGeneric {
                writer.intArray(item.genericArguments)
                writer.int32(Int32(item.parameters.count))
                for parameter in item.parameters {
                    switch parameter {
                    case .string(let value):
                        writer.boolean(true)
                        writer.string(value)
                    case .index(let value):
                        writer.boolean(false)
                        writer.int32(value)
                    }
                }
            } else {
                writer.intArray(item.parameters.map {
                    if case .index(let value) = $0 { return value }
                    return 0
                })
            }
        }

        writer.int32(Int32(patch.externalMethods.count))
        patch.externalMethods.forEach { writeReference($0, writer: &writer) }
        writer.int32(Int32(patch.strings.count))
        patch.strings.forEach { writer.string($0) }

        writer.int32(Int32(patch.fields.count))
        for field in patch.fields {
            writer.boolean(field.isNew)
            writer.int32(field.typeID)
            writer.string(field.name)
            if field.isNew { writer.data.append(field.extra) }
        }
        writer.int32(Int32(patch.staticFields.count))
        for item in patch.staticFields {
            writer.int32(item.typeID)
            writer.int32(item.constructorIndex)
        }
        writer.int32(Int32(patch.anonymousClasses.count))
        for item in patch.anonymousClasses {
            writer.intArray(item.fields)
            writer.int32(item.constructor)
            writer.int32(item.constructorParameterCount)
            writer.intArray(item.interfaces)
            writer.intArray(item.vtable)
        }
        writer.string(patch.wrappers)
        writer.string(patch.assembly)

        writer.int32(Int32(patch.fixes.count))
        for fix in patch.fixes {
            writer.boolean(fix.isGeneric)
            writer.int32(fix.typeID)
            writer.string(fix.name)
            if fix.isGeneric {
                writer.intArray(fix.genericArguments)
                writer.int32(Int32(fix.parameters.count))
                for parameter in fix.parameters {
                    switch parameter {
                    case .string(let value):
                        writer.boolean(true)
                        writer.string(value)
                    case .index(let value):
                        writer.boolean(false)
                        writer.int32(value)
                    }
                }
            } else {
                writer.intArray(fix.parameters.map {
                    if case .index(let value) = $0 { return value }
                    return 0
                })
            }
            writer.int32(fix.patchIndex)
        }
        writer.int32(Int32(patch.newClasses.count))
        patch.newClasses.forEach { writer.string($0) }
        return writer.data
    }

    static func validate(_ patch: AoVIFixPatch) throws {
        let typeCount = patch.types.count
        let methodCount = patch.methods.count
        let externalCount = patch.externalMethods.count
        let fieldCount = patch.fields.count
        let stringCount = patch.strings.count

        func validType(_ value: Int32) -> Bool { value >= 0 && Int(value) < typeCount }
        func validMethod(_ value: Int32) -> Bool { value >= 0 && Int(value) < methodCount }
        func validateParameters(_ parameters: [AoVIFixParameter], context: String) throws {
            for parameter in parameters {
                if case .index(let value) = parameter, !validType(value) {
                    throw AoVIFixError.invalidReference("\(context) parameter")
                }
            }
        }

        for fix in patch.fixes {
            guard validType(fix.typeID), validMethod(fix.patchIndex) else {
                throw AoVIFixError.invalidReference("FixInfo type/method")
            }
            guard fix.genericArguments.allSatisfy(validType) else {
                throw AoVIFixError.invalidReference("FixInfo generic argument")
            }
            try validateParameters(fix.parameters, context: "FixInfo")
        }
        for method in patch.externalMethods {
            guard validType(method.typeID), method.genericArguments.allSatisfy(validType) else {
                throw AoVIFixError.invalidReference("External method type/generic")
            }
            try validateParameters(method.parameters, context: "External method")
        }
        for field in patch.fields where !validType(field.typeID) {
            throw AoVIFixError.invalidReference("Field type")
        }
        for item in patch.staticFields {
            guard validType(item.typeID), item.constructorIndex < 0 || validMethod(item.constructorIndex) else {
                throw AoVIFixError.invalidReference("Static field")
            }
        }
        for item in patch.anonymousClasses {
            if item.fields.contains(where: { $0 >= 0 && !validType($0) }) ||
                (item.constructor >= 0 && !validMethod(item.constructor)) ||
                item.interfaces.contains(where: { $0 >= 0 && !validType($0) }) ||
                item.vtable.contains(where: { $0 >= 0 && !validMethod($0) }) {
                throw AoVIFixError.invalidReference("Anonymous class")
            }
        }

        for method in patch.methods {
            var index = 0
            while index < method.instructions.count {
                let instruction = method.instructions[index]
                guard let kind = operandKinds[instruction.opcode] else {
                    throw AoVIFixError.unknownOpcode(instruction.opcode)
                }
                switch kind {
                case "string":
                    guard instruction.operand >= 0, Int(instruction.operand) < stringCount else {
                        throw AoVIFixError.invalidReference("instruction string")
                    }
                case "type", "token":
                    if instruction.operand >= 0, !validType(instruction.operand) {
                        throw AoVIFixError.invalidReference("instruction type/token")
                    }
                case "field":
                    if instruction.operand >= 0, Int(instruction.operand) >= fieldCount {
                        throw AoVIFixError.invalidReference("instruction field")
                    }
                case "method":
                    let raw = UInt32(bitPattern: instruction.operand)
                    let methodIndex = Int(raw & 0xFFFF)
                    guard methodIndex < externalCount else {
                        throw AoVIFixError.invalidReference("instruction external method")
                    }
                case "8byte":
                    guard index + 1 < method.instructions.count else {
                        throw AoVIFixError.invalidReference("truncated 8-byte operand")
                    }
                    index += 1
                case "switch":
                    guard instruction.operand >= 0 else {
                        throw AoVIFixError.invalidReference("negative switch size")
                    }
                    let skip = (Int(instruction.operand) + 1) / 2
                    guard index + skip < method.instructions.count else {
                        throw AoVIFixError.invalidReference("truncated switch operand")
                    }
                    index += skip
                default:
                    break
                }
                index += 1
            }
        }
    }

    static func merge(_ baseInput: AoVIFixPatch, _ overlayInput: AoVIFixPatch) throws -> AoVIFixPatch {
        try validate(baseInput)
        try validate(overlayInput)
        var base = baseInput
        var incoming = overlayInput
        guard base.magic == incoming.magic else { throw AoVIFixError.metadataMismatch("magic") }
        guard base.bridge == incoming.bridge else { throw AoVIFixError.metadataMismatch("bridge") }
        guard base.wrappers == incoming.wrappers else { throw AoVIFixError.metadataMismatch("wrappers") }
        guard base.assembly == incoming.assembly else { throw AoVIFixError.metadataMismatch("assembly") }

        let baseKeys = Set(base.fixes.map { FixSignature(type: $0.typeID, name: $0.name, parameters: $0.parameters) })
        let overlayKeys = Set(incoming.fixes.map { FixSignature(type: $0.typeID, name: $0.name, parameters: $0.parameters) })
        guard baseKeys.isDisjoint(with: overlayKeys) else { throw AoVIFixError.duplicateFix }

        let typeOffset = Int32(base.types.count)
        let methodOffset = Int32(base.methods.count)
        let externalOffset = Int32(base.externalMethods.count)
        let stringOffset = Int32(base.strings.count)
        let fieldOffset = Int32(base.fields.count)

        let typeMap = Dictionary(uniqueKeysWithValues: incoming.types.indices.map { (Int32($0), Int32($0) + typeOffset) })
        let methodMap = Dictionary(uniqueKeysWithValues: incoming.methods.indices.map { (Int32($0), Int32($0) + methodOffset) })
        let externalMap = Dictionary(uniqueKeysWithValues: incoming.externalMethods.indices.map { (Int32($0), Int32($0) + externalOffset) })
        let stringMap = Dictionary(uniqueKeysWithValues: incoming.strings.indices.map { (Int32($0), Int32($0) + stringOffset) })
        let fieldMap = Dictionary(uniqueKeysWithValues: incoming.fields.indices.map { (Int32($0), Int32($0) + fieldOffset) })

        for index in incoming.externalMethods.indices {
            var item = incoming.externalMethods[index]
            guard let mappedType = typeMap[item.typeID] else { throw AoVIFixError.invalidReference("external type") }
            item.typeID = mappedType
            item.genericArguments = try item.genericArguments.map { value in
                guard let mapped = typeMap[value] else { throw AoVIFixError.invalidReference("external generic") }
                return mapped
            }
            item.parameters = try item.parameters.map { parameter in
                if case .index(let value) = parameter {
                    guard let mapped = typeMap[value] else { throw AoVIFixError.invalidReference("external param") }
                    return .index(mapped)
                }
                return parameter
            }
            incoming.externalMethods[index] = item
        }
        for index in incoming.fields.indices {
            guard let mapped = typeMap[incoming.fields[index].typeID] else {
                throw AoVIFixError.invalidReference("field type")
            }
            incoming.fields[index].typeID = mapped
        }
        incoming.staticFields = try incoming.staticFields.map { item in
            guard let type = typeMap[item.typeID] else { throw AoVIFixError.invalidReference("static type") }
            let method = item.constructorIndex >= 0 ? (methodMap[item.constructorIndex] ?? item.constructorIndex) : item.constructorIndex
            return AoVIFixStaticField(typeID: type, constructorIndex: method)
        }
        for index in incoming.anonymousClasses.indices {
            var item = incoming.anonymousClasses[index]
            item.fields = try item.fields.map { value in
                value >= 0 ? (typeMap[value] ?? { throw AoVIFixError.invalidReference("anon field") }()) : value
            }
            if item.constructor >= 0 {
                guard let mapped = methodMap[item.constructor] else { throw AoVIFixError.invalidReference("anon ctor") }
                item.constructor = mapped
            }
            item.interfaces = try item.interfaces.map { value in
                value >= 0 ? (typeMap[value] ?? { throw AoVIFixError.invalidReference("anon interface") }()) : value
            }
            item.vtable = try item.vtable.map { value in
                value >= 0 ? (methodMap[value] ?? { throw AoVIFixError.invalidReference("anon vtable") }()) : value
            }
            incoming.anonymousClasses[index] = item
        }

        for methodIndex in incoming.methods.indices {
            var method = incoming.methods[methodIndex]
            var instructionIndex = 0
            while instructionIndex < method.instructions.count {
                var instruction = method.instructions[instructionIndex]
                guard let kind = operandKinds[instruction.opcode] else { throw AoVIFixError.unknownOpcode(instruction.opcode) }
                switch kind {
                case "string":
                    guard let mapped = stringMap[instruction.operand] else { throw AoVIFixError.invalidReference("ldstr") }
                    instruction.operand = mapped
                case "type", "token":
                    if instruction.operand >= 0 {
                        guard let mapped = typeMap[instruction.operand] else { throw AoVIFixError.invalidReference("type/token IL") }
                        instruction.operand = mapped
                    }
                case "field":
                    if instruction.operand >= 0 {
                        guard let mapped = fieldMap[instruction.operand] else { throw AoVIFixError.invalidReference("field IL") }
                        instruction.operand = mapped
                    }
                case "method":
                    let raw = UInt32(bitPattern: instruction.operand)
                    let oldIndex = Int32(raw & 0xFFFF)
                    let argCount = raw >> 16
                    guard let newIndex = externalMap[oldIndex] else { throw AoVIFixError.invalidReference("external method IL") }
                    instruction.operand = Int32(bitPattern: (argCount << 16) | UInt32(bitPattern: newIndex))
                default:
                    break
                }
                method.instructions[instructionIndex] = instruction
                if kind == "8byte" { instructionIndex += 1 }
                else if kind == "switch" { instructionIndex += (Int(instruction.operand) + 1) / 2 }
                instructionIndex += 1
            }
            incoming.methods[methodIndex] = method
        }

        for index in incoming.fixes.indices {
            var fix = incoming.fixes[index]
            guard let type = typeMap[fix.typeID], let method = methodMap[fix.patchIndex] else {
                throw AoVIFixError.invalidReference("fix type/method")
            }
            fix.typeID = type
            fix.patchIndex = method
            fix.genericArguments = try fix.genericArguments.map { value in
                guard let mapped = typeMap[value] else { throw AoVIFixError.invalidReference("fix generic") }
                return mapped
            }
            fix.parameters = try fix.parameters.map { parameter in
                if case .index(let value) = parameter {
                    guard let mapped = typeMap[value] else { throw AoVIFixError.invalidReference("fix param") }
                    return .index(mapped)
                }
                return parameter
            }
            incoming.fixes[index] = fix
        }

        base.types.append(contentsOf: incoming.types)
        base.methods.append(contentsOf: incoming.methods)
        base.externalMethods.append(contentsOf: incoming.externalMethods)
        base.strings.append(contentsOf: incoming.strings)
        base.fields.append(contentsOf: incoming.fields)
        base.staticFields.append(contentsOf: incoming.staticFields)
        base.anonymousClasses.append(contentsOf: incoming.anonymousClasses)
        base.fixes.append(contentsOf: incoming.fixes)
        base.newClasses.append(contentsOf: incoming.newClasses)
        try validate(base)
        return base
    }

    static func mergeActorScoped(_ base: AoVIFixPatch, _ incoming: AoVIFixPatch) throws -> AoVIFixPatch {
        try validate(base)
        try validate(incoming)
        guard base.magic == incoming.magic, base.bridge == incoming.bridge,
              base.types == incoming.types, base.externalMethods == incoming.externalMethods,
              base.strings == incoming.strings, base.fields == incoming.fields,
              base.staticFields == incoming.staticFields,
              base.anonymousClasses == incoming.anonymousClasses,
              base.fixes == incoming.fixes, base.newClasses == incoming.newClasses,
              base.wrappers == incoming.wrappers, base.assembly == incoming.assembly else {
            throw AoVIFixError.metadataMismatch("actor-scoped metadata")
        }
        guard base.methods.count == incoming.methods.count else {
            throw AoVIFixError.metadataMismatch("method count")
        }
        let groups = Dictionary(grouping: base.fixes, by: \.name)
        let builders: [String]
        if ["IsPlaySkinKillNotify", "IsOpen", "get_PersonalBtnId"].allSatisfy({ groups[$0]?.count == 1 }) {
            builders = ["IsPlaySkinKillNotify", "IsOpen", "get_PersonalBtnId"]
        } else if groups["AddHero"]?.count == 1 {
            builders = ["AddHero"]
        } else {
            throw AoVIFixError.invalidActorScoped("FixInfo name")
        }
        let methodIndices = Set(builders.compactMap { groups[$0]?.first?.patchIndex }.map(Int.init))
        for index in base.methods.indices where !methodIndices.contains(index) {
            guard base.methods[index] == incoming.methods[index] else {
                throw AoVIFixError.invalidActorScoped("non-target method changed at \(index)")
            }
        }
        var result = base
        for name in builders {
            let methodIndex = Int(groups[name]!.first!.patchIndex)
            let first = base.methods[methodIndex].instructions
            let second = incoming.methods[methodIndex].instructions
            let merged: [AoVIFixInstruction]
            switch name {
            case "IsPlaySkinKillNotify": merged = try buildNotify(first, second)
            case "IsOpen": merged = try buildOpen(first, second)
            case "get_PersonalBtnId": merged = try buildPersonalButtons(first, second)
            case "AddHero": merged = try buildAddHero(first, second)
            default: throw AoVIFixError.invalidActorScoped(name)
            }
            result.methods[methodIndex].instructions = merged
        }
        try validate(result)
        return result
    }

    static func mergeResource(_ baseResource: Data, _ overlayResource: Data, resourcePath: String) throws -> (Data, AoVIFixReport) {
        let baseDecoded = try AoVBytesDecoder.decodeResource(baseResource, resourceName: resourcePath)
        let overlayDecoded = try AoVBytesDecoder.decodeResource(overlayResource, resourceName: resourcePath)
        let basePatch = try parse(baseDecoded.data)
        let overlayPatch = try parse(overlayDecoded.data)
        let mergedPatch: AoVIFixPatch
        let route: String
        do {
            mergedPatch = try merge(basePatch, overlayPatch)
            route = "generic_index_remap_union"
        } catch {
            mergedPatch = try mergeActorScoped(basePatch, overlayPatch)
            route = "actor_scoped_verified_union"
        }
        let bytes = try serialize(mergedPatch)
        let verify = try parse(bytes)
        guard try serialize(verify) == bytes else { throw AoVIFixError.roundTripFailure }
        let wrapped = try AoVBytesDecoder.encodeResource(bytes, resourceName: resourcePath, wrapperMode: baseDecoded.wrapperMode)
        let roundTrip = try AoVBytesDecoder.decodeResource(wrapped, resourceName: resourcePath)
        guard try parse(roundTrip.data) == verify else { throw AoVIFixError.roundTripFailure }
        return (wrapped, AoVIFixReport(
            route: route, typeCount: verify.types.count,
            methodCount: verify.methods.count, fixCount: verify.fixes.count,
            verified: true
        ))
    }

    private struct FixSignature: Hashable {
        let type: Int32
        let name: String
        let parameters: [AoVIFixParameter]
    }

    private static func operandKind(_ opcode: Int32) throws -> String {
        guard let kind = operandKinds[opcode] else { throw AoVIFixError.unknownOpcode(opcode) }
        return kind
    }

    private static func prefix(_ first: [AoVIFixInstruction], _ second: [AoVIFixInstruction], length: Int, branchIndices: Set<Int>) throws -> [AoVIFixInstruction] {
        guard first.count >= length, second.count >= length else {
            throw AoVIFixError.invalidActorScoped("method prefix truncated")
        }
        var left = Array(first.prefix(length))
        var right = Array(second.prefix(length))
        for index in branchIndices {
            guard left.indices.contains(index), right.indices.contains(index) else {
                throw AoVIFixError.invalidActorScoped("branch index out of range")
            }
            left[index].operand = 0
            right[index].operand = 0
        }
        guard left == right else { throw AoVIFixError.invalidActorScoped("shared IL prefix differs") }
        return Array(first.prefix(length))
    }

    private static func buildNotify(_ first: [AoVIFixInstruction], _ second: [AoVIFixInstruction]) throws -> [AoVIFixInstruction] {
        func parse(_ input: [AoVIFixInstruction]) throws -> [Int32] {
            guard Array(input.prefix(3)) == [ins(146,2),ins(164,1),ins(167,80)],
                  Array(input.suffix(4)) == [ins(143,0),ins(173,1),ins(143,1),ins(173,1)] else {
                throw AoVIFixError.invalidActorScoped("IsPlaySkinKillNotify")
            }
            let body = Array(input.dropFirst(3).dropLast(4))
            guard body.count >= 3, (body.count - 3) % 5 == 0,
                  body[0].opcode == 143, body[1] == ins(166,0), body[2].opcode == 127 else {
                throw AoVIFixError.invalidActorScoped("notify condition")
            }
            var ids = [body[0].operand]
            var offset = 3
            while offset < body.count {
                let block = Array(body[offset..<offset+5])
                guard block[0] == ins(164,1), block[1] == ins(167,80),
                      block[2].opcode == 143, block[3] == ins(166,0), block[4].opcode == 127 else {
                    throw AoVIFixError.invalidActorScoped("notify actor block")
                }
                ids.append(block[2].operand)
                offset += 5
            }
            return ids
        }
        let ids = Set(try parse(first) + parse(second)).sorted()
        var output = [ins(146,2),ins(164,1),ins(167,80)]
        let trueReturn = 3 + 3 + 5 * (ids.count - 1) + 2
        output += [ins(143,ids[0]),ins(166,0),ins(127,Int32(trueReturn - (output.count + 2)))]
        for actor in ids.dropFirst() {
            let branch = output.count + 4
            output += [ins(164,1),ins(167,80),ins(143,actor),ins(166,0),ins(127,Int32(trueReturn-branch))]
        }
        output += [ins(143,0),ins(173,1),ins(143,1),ins(173,1)]
        return output
    }

    private static func buildOpen(_ first: [AoVIFixInstruction], _ second: [AoVIFixInstruction]) throws -> [AoVIFixInstruction] {
        let length = 55
        func parse(_ input: [AoVIFixInstruction]) throws -> [Int32] {
            guard input.count >= length + 7, input[0] == ins(146,196611), input[length-1] == ins(72,0),
                  Array(input.suffix(4)) == [ins(143,0),ins(173,1),ins(143,1),ins(173,1)] else {
                throw AoVIFixError.invalidActorScoped("IsOpen")
            }
            let body=Array(input[length..<input.count-4])
            guard body.count >= 3, (body.count-3)%4 == 0, body[0].opcode == 143,
                  body[1] == ins(166,0), body[2].opcode == 127 else {
                throw AoVIFixError.invalidActorScoped("IsOpen condition")
            }
            var ids=[body[0].operand]
            var offset=3
            while offset<body.count {
                let block=Array(body[offset..<offset+4])
                guard block[0] == ins(72,0), block[1].opcode == 143, block[2] == ins(166,0), block[3].opcode == 127 else {
                    throw AoVIFixError.invalidActorScoped("IsOpen actor block")
                }
                ids.append(block[1].operand); offset += 4
            }
            return ids
        }
        let a=try parse(first), b=try parse(second)
        let prefix=try self.prefix(first,second,length:length,branchIndices:[5,13,18,26])
        let ids=Set(a+b).sorted()
        let falseReturn=length+3+4*(ids.count-1), trueReturn=falseReturn+2
        var result=prefix
        for index in [5,13,18,26] { result[index].operand=Int32(falseReturn-index) }
        let branch=result.count+2
        result += [ins(143,ids[0]),ins(166,0),ins(127,Int32(trueReturn-branch))]
        for actor in ids.dropFirst() {
            let branch=result.count+3
            result += [ins(72,0),ins(143,actor),ins(166,0),ins(127,Int32(trueReturn-branch))]
        }
        result += [ins(143,0),ins(173,1),ins(143,1),ins(173,1)]
        return result
    }

    private static func buildPersonalButtons(_ first: [AoVIFixInstruction], _ second: [AoVIFixInstruction]) throws -> [AoVIFixInstruction] {
        let length=55
        func parse(_ input:[AoVIFixInstruction]) throws -> [Int32:Int32] {
            guard input.count >= length+7,input[0]==ins(146,196611),input[length-1]==ins(72,0),
                  Array(input.suffix(2)) == [ins(143,0),ins(173,1)] else {
                throw AoVIFixError.invalidActorScoped("get_PersonalBtnId")
            }
            let body=Array(input[length..<input.count-2])
            guard body.count>=5,(body.count-5)%6==0,body[0].opcode==143,body[1]==ins(166,0),
                  body[2]==ins(134,3),body[3].opcode==143,body[4]==ins(173,1) else {
                throw AoVIFixError.invalidActorScoped("personal button first mapping")
            }
            var result=[body[0].operand:body[3].operand]
            var offset=5
            while offset<body.count {
                let block=Array(body[offset..<offset+6])
                guard block[0]==ins(72,0),block[1].opcode==143,block[2]==ins(166,0),
                      block[3]==ins(134,3),block[4].opcode==143,block[5]==ins(173,1) else {
                    throw AoVIFixError.invalidActorScoped("personal button block")
                }
                let actor=block[1].operand, button=block[4].operand
                if let old=result[actor],old != button { throw AoVIFixError.actorMappingConflict("personal button") }
                result[actor]=button; offset += 6
            }
            return result
        }
        var mapping=try parse(first)
        for (actor,button) in try parse(second) {
            if let old=mapping[actor],old != button { throw AoVIFixError.actorMappingConflict("personal button") }
            mapping[actor]=button
        }
        mapping=Dictionary(uniqueKeysWithValues:mapping.sorted(by:{$0.key<$1.key}))
        var result=try prefix(first,second,length:length,branchIndices:[5,13,18,26])
        let defaultReturn=length+5+6*(mapping.count-1)
        for index in [5,13,18,26] { result[index].operand=Int32(defaultReturn-index) }
        let firstActor=mapping.keys.sorted()[0]
        result += [ins(143,firstActor),ins(166,0),ins(134,3),ins(143,mapping[firstActor]!),ins(173,1)]
        for actor in mapping.keys.sorted().dropFirst() {
            result += [ins(72,0),ins(143,actor),ins(166,0),ins(134,3),ins(143,mapping[actor]!),ins(173,1)]
        }
        result += [ins(143,0),ins(173,1)]
        return result
    }

    private static func buildAddHero(_ first: [AoVIFixInstruction], _ second: [AoVIFixInstruction]) throws -> [AoVIFixInstruction] {
        let length=58
        func parse(_ input:[AoVIFixInstruction]) throws -> [Int32:(Int32,Int32?)] {
            guard input.count>=length+1,input[0]==ins(146,5),input.last==ins(173,0) else {
                throw AoVIFixError.invalidActorScoped("AddHero")
            }
            let body=Array(input[length..<input.count-1])
            var result=[Int32:(Int32,Int32?)]()
            var offset=0
            while offset<body.count {
                guard body.count-offset>=4,body[offset]==ins(164,1),body[offset+1].opcode==143,
                      body[offset+2]==ins(166,0),body[offset+3].opcode==134 else {
                    throw AoVIFixError.invalidActorScoped("AddHero block header")
                }
                let actor=body[offset+1].operand, branch=body[offset+3].operand
                let values:(Int32,Int32?), blockLength:Int
                if branch==5 {
                    guard body.count-offset>=8,body[offset+4].opcode==164,body[offset+5].opcode==143,
                          body[offset+6]==ins(75,3),body[offset+7]==ins(173,0) else {
                        throw AoVIFixError.invalidActorScoped("AddHero single field")
                    }
                    values=(body[offset+5].operand,nil); blockLength=8
                } else if branch==8 {
                    guard body.count-offset>=11,body[offset+4]==ins(164,0),body[offset+5].opcode==143,
                          body[offset+6]==ins(75,3),body[offset+7]==ins(164,0),
                          body[offset+8].opcode==143,body[offset+9]==ins(75,2),body[offset+10]==ins(173,0) else {
                        throw AoVIFixError.invalidActorScoped("AddHero double field")
                    }
                    values=(body[offset+5].operand,body[offset+8].operand); blockLength=11
                } else { throw AoVIFixError.invalidActorScoped("AddHero branch length") }
                if let old=result[actor],old.0 != values.0 || old.1 != values.1 {
                    throw AoVIFixError.actorMappingConflict("AddHero")
                }
                result[actor]=values; offset += blockLength
            }
            return result
        }
        var mapping=try parse(first)
        for (actor,values) in try parse(second) {
            if let old=mapping[actor],old.0 != values.0 || old.1 != values.1 {
                throw AoVIFixError.actorMappingConflict("AddHero")
            }
            mapping[actor]=values
        }
        var result=try prefix(first,second,length:length,branchIndices:[33,41,46,54])
        var blocks=[AoVIFixInstruction]()
        for actor in mapping.keys.sorted() {
            let values=mapping[actor]!
            if let secondField=values.1 {
                blocks += [ins(164,1),ins(143,actor),ins(166,0),ins(134,8),
                           ins(164,0),ins(143,values.0),ins(75,3),ins(164,0),
                           ins(143,secondField),ins(75,2),ins(173,0)]
            } else {
                blocks += [ins(164,1),ins(143,actor),ins(166,0),ins(134,5),
                           ins(164,0),ins(143,values.0),ins(75,3),ins(173,0)]
            }
        }
        let finalReturn=length+blocks.count
        for index in [33,41,46,54] { result[index].operand=Int32(finalReturn-index) }
        result += blocks
        result.append(ins(173,0))
        return result
    }

    private static func ins(_ opcode: Int32, _ operand: Int32) -> AoVIFixInstruction {
        AoVIFixInstruction(opcode: opcode, operand: operand)
    }
}
