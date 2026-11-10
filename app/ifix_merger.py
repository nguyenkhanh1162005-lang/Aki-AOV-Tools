from __future__ import annotations

import copy
import io
import struct
from typing import Any


class IFixMergeError(ValueError):
    """Raised when two IFix patches cannot be safely combined."""


# Opcode names for the IFix/Unity IL opcode values used by supported AoV patches.
_OPCODE_NAMES = {
    0: "Conv_I1", 1: "Ldelem_I", 2: "Cgt", 3: "Mul", 5: "Stobj",
    6: "Ldftn", 7: "Conv_I8", 8: "Ldloca", 9: "Shr", 11: "Bgt",
    12: "Callvirtvirt", 13: "Ldarga", 15: "Ldobj", 16: "Ldobj",
    17: "Bgt_Un", 19: "Conv_Ovf_I4", 20: "Shl", 21: "Neg",
    22: "Box", 23: "Stelem_Ref", 24: "Isinst", 25: "Newarr_Prim",
    27: "Ldelem_I1", 28: "Ldc_I8", 29: "Conv_U4", 30: "Bge_Un",
    33: "Ble_Un", 34: "Ldflda", 36: "Stloc", 37: "Conv_I8",
    39: "Ldstr", 43: "Beq", 45: "Call", 47: "Blt_Un",
    50: "Ldelem_Ref", 60: "Ldsfld", 62: "Starg", 63: "Conv_I2",
    65: "Rethrow", 67: "Newanon", 69: "Conv_Ovf_I1", 72: "Ldloc",
    73: "Calli", 75: "Stfld", 79: "Or", 80: "Add", 85: "Stsfld",
    87: "Throw", 93: "Br", 94: "Ldtoken", 96: "Stobj",
    100: "Ldc_R8", 103: "Ret", 104: "Ldelem_Ref", 110: "Bne_Un",
    111: "Blt_Un", 117: "Initobj", 122: "Callvirt", 123: "Ldelem_I1",
    126: "Sub", 127: "Brtrue", 128: "Pop", 129: "Stelem_Ref",
    132: "Stelem_I8", 134: "Brfalse", 138: "Calli", 139: "Ldlen",
    140: "Conv_U4", 141: "Ldc_I4", 143: "Ldc_I4", 144: "Dup",
    146: "StackSpace", 148: "And", 149: "Switch", 151: "Newobj",
    156: "Ldelem_U4", 161: "Box", 164: "Ldarg", 166: "Ceq",
    167: "Ldfld", 169: "Shr_Un", 173: "Ret", 176: "Ble",
}

_INLINE_INT = {"Ldc_I4", "Ldc_R4", "Ldc_I4_S"}
_INLINE_STRING = {"Ldstr"}
_INLINE_8BYTE = {"Ldc_I8", "Ldc_R8"}
_INLINE_VAR = {"Ldarg", "Ldarga", "Ldloc", "Ldloca", "Stloc", "Starg"}
_INLINE_BRANCH = {
    "Br", "Brtrue", "Brfalse", "Beq", "Bne_Un", "Bge", "Bgt", "Ble",
    "Blt", "Bge_Un", "Bgt_Un", "Ble_Un", "Blt_Un", "Leave",
}
_INLINE_SWITCH = {"Switch"}
_INLINE_METHOD = {"Call", "Callvirt", "Ldftn", "Ldvirtftn", "Callvirtvirt", "Calli"}
_INLINE_FIELD = {"Ldfld", "Ldflda", "Stfld", "Ldsfld", "Stsfld"}
_INLINE_TYPE = {
    "Box", "Unbox", "Isinst", "Castclass", "Newarr", "Ldobj", "Stobj",
    "Initobj", "Newanon", "Ldtype", "Unbox_Any", "Newobj",
}
_INLINE_TOKEN = {"Ldtoken"}


class _Reader:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)

    def read(self, size: int) -> bytes:
        data = self.stream.read(size)
        if len(data) != size:
            raise IFixMergeError("IFix patch bị cắt cụt hoặc không đúng cấu trúc.")
        return data

    def byte(self) -> int:
        return self.read(1)[0]

    def boolean(self) -> bool:
        return self.byte() != 0

    def int32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def uint64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def count(self, label: str, *, maximum: int = 2_000_000) -> int:
        count = self.int32()
        if count < 0 or count > maximum:
            raise IFixMergeError(f"Số lượng {label} trong IFix không hợp lệ.")
        return count

    def string(self) -> str:
        length = 0
        shift = 0
        while True:
            value = self.byte()
            length |= (value & 0x7F) << shift
            if not value & 0x80:
                break
            shift += 7
            if shift > 35:
                raise IFixMergeError("Độ dài chuỗi IFix không hợp lệ.")
        raw = self.read(length) if length else b""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IFixMergeError("Chuỗi IFix không phải UTF-8 hợp lệ.") from exc

    def int_array(self) -> list[int]:
        return [self.int32() for _ in range(self.count("phần tử"))]

    def tell(self) -> int:
        return self.stream.tell()


class _Writer:
    def __init__(self):
        self.stream = io.BytesIO()

    def write(self, data: bytes) -> None:
        self.stream.write(data)

    def byte(self, value: int) -> None:
        self.write(bytes((value & 0xFF,)))

    def boolean(self, value: bool) -> None:
        self.byte(1 if value else 0)

    def int32(self, value: int) -> None:
        self.write(struct.pack("<i", value))

    def uint64(self, value: int) -> None:
        self.write(struct.pack("<Q", value))

    def string(self, value: str) -> None:
        data = value.encode("utf-8")
        length = len(data)
        while length >= 0x80:
            self.byte((length & 0x7F) | 0x80)
            length >>= 7
        self.byte(length)
        self.write(data)

    def int_array(self, values: list[int]) -> None:
        self.int32(len(values))
        for value in values:
            self.int32(value)

    def result(self) -> bytes:
        return self.stream.getvalue()


def parse_ifix_patch(data: bytes) -> dict[str, Any]:
    """Parse an IFix patch while retaining every index-bearing metadata table."""
    reader = _Reader(data)
    patch: dict[str, Any] = {
        "magic": reader.uint64(),
        "bridge": reader.string(),
        "types": [],
        "methods": [],
        "extern_methods": [],
        "strings": [],
        "fields": [],
        "static_fields": [],
        "anon": [],
        "fixes": [],
        "new_classes": [],
    }
    patch["types"] = [reader.string() for _ in range(reader.count("bản ghi"))]

    for _ in range(reader.count("bản ghi")):
        instructions = [
            struct.unpack("<ii", reader.read(8))
            for _ in range(reader.count("bản ghi"))
        ]
        handlers = [
            struct.unpack("<iiiiii", reader.read(24))
            for _ in range(reader.count("bản ghi"))
        ]
        patch["methods"].append({"inst": instructions, "eh": handlers})

    for _ in range(reader.count("bản ghi")):
        is_generic = reader.boolean()
        type_id = reader.int32()
        name = reader.string()
        generic_args: list[int] = []
        params: list[tuple[str, Any]] = []
        if is_generic:
            generic_args = reader.int_array()
            for _ in range(reader.count("bản ghi")):
                if reader.boolean():
                    params.append(("s", reader.string()))
                else:
                    params.append(("i", reader.int32()))
        else:
            params = [("i", value) for value in reader.int_array()]
        patch["extern_methods"].append({
            "gen": is_generic, "type": type_id, "name": name,
            "g": generic_args, "params": params,
        })

    patch["strings"] = [reader.string() for _ in range(reader.count("bản ghi"))]

    for _ in range(reader.count("bản ghi")):
        is_new = reader.boolean()
        type_id = reader.int32()
        name = reader.string()
        extra = reader.read(8) if is_new else b""
        patch["fields"].append({
            "new": is_new, "type": type_id, "name": name, "extra": extra,
        })

    patch["static_fields"] = [
        (reader.int32(), reader.int32()) for _ in range(reader.count("bản ghi"))
    ]

    for _ in range(reader.count("bản ghi")):
        patch["anon"].append({
            "fields": reader.int_array(),
            "ctor": reader.int32(),
            "ctor_p": reader.int32(),
            "itfs": reader.int_array(),
            "vtable": reader.int_array(),
        })

    patch["wrappers"] = reader.string()
    patch["assembly"] = reader.string()

    for _ in range(reader.count("bản ghi")):
        is_generic = reader.boolean()
        type_id = reader.int32()
        name = reader.string()
        generic_args: list[int] = []
        params: list[tuple[str, Any]] = []
        if is_generic:
            generic_args = reader.int_array()
            for _ in range(reader.count("bản ghi")):
                if reader.boolean():
                    params.append(("s", reader.string()))
                else:
                    params.append(("i", reader.int32()))
        else:
            params = [("i", value) for value in reader.int_array()]
        patch["fixes"].append({
            "gen": is_generic, "type": type_id, "name": name,
            "g": generic_args, "params": params, "patch": reader.int32(),
        })

    patch["new_classes"] = [reader.string() for _ in range(reader.count("bản ghi"))]
    if reader.tell() != len(data):
        raise IFixMergeError("IFix patch có dữ liệu dư hoặc không tương thích.")
    return patch


def serialize_ifix_patch(patch: dict[str, Any]) -> bytes:
    writer = _Writer()
    writer.uint64(patch["magic"])
    writer.string(patch["bridge"])
    writer.int32(len(patch["types"]))
    for value in patch["types"]:
        writer.string(value)

    writer.int32(len(patch["methods"]))
    for method in patch["methods"]:
        writer.int32(len(method["inst"]))
        for instruction in method["inst"]:
            writer.write(struct.pack("<ii", *instruction))
        writer.int32(len(method["eh"]))
        for handler in method["eh"]:
            writer.write(struct.pack("<iiiiii", *handler))

    writer.int32(len(patch["extern_methods"]))
    for method in patch["extern_methods"]:
        writer.boolean(method["gen"])
        writer.int32(method["type"])
        writer.string(method["name"])
        if method["gen"]:
            writer.int_array(method["g"])
            writer.int32(len(method["params"]))
            for kind, value in method["params"]:
                writer.boolean(kind == "s")
                writer.string(value) if kind == "s" else writer.int32(value)
        else:
            writer.int_array([value for _, value in method["params"]])

    writer.int32(len(patch["strings"]))
    for value in patch["strings"]:
        writer.string(value)

    writer.int32(len(patch["fields"]))
    for field in patch["fields"]:
        writer.boolean(field["new"])
        writer.int32(field["type"])
        writer.string(field["name"])
        if field["new"]:
            writer.write(field["extra"])

    writer.int32(len(patch["static_fields"]))
    for type_id, cctor_id in patch["static_fields"]:
        writer.int32(type_id)
        writer.int32(cctor_id)

    writer.int32(len(patch["anon"]))
    for item in patch["anon"]:
        writer.int_array(item["fields"])
        writer.int32(item["ctor"])
        writer.int32(item["ctor_p"])
        writer.int_array(item["itfs"])
        writer.int_array(item["vtable"])

    writer.string(patch["wrappers"])
    writer.string(patch["assembly"])

    writer.int32(len(patch["fixes"]))
    for fix in patch["fixes"]:
        writer.boolean(fix["gen"])
        writer.int32(fix["type"])
        writer.string(fix["name"])
        if fix["gen"]:
            writer.int_array(fix["g"])
            writer.int32(len(fix["params"]))
            for kind, value in fix["params"]:
                writer.boolean(kind == "s")
                writer.string(value) if kind == "s" else writer.int32(value)
        else:
            writer.int_array([value for _, value in fix["params"]])
        writer.int32(fix["patch"])

    writer.int32(len(patch["new_classes"]))
    for value in patch["new_classes"]:
        writer.string(value)
    return writer.result()


def _operand_kind(opcode: int) -> str:
    name = _OPCODE_NAMES.get(opcode)
    if name is None:
        raise IFixMergeError(f"Opcode IFix chưa hỗ trợ: {opcode}.")
    if name in _INLINE_INT:
        return "int"
    if name in _INLINE_STRING:
        return "string"
    if name in _INLINE_8BYTE:
        return "8byte"
    if name in _INLINE_VAR:
        return "var"
    if name in _INLINE_BRANCH:
        return "branch"
    if name in _INLINE_SWITCH:
        return "switch"
    if name in _INLINE_METHOD:
        return "method"
    if name in _INLINE_FIELD:
        return "field"
    if name in _INLINE_TYPE:
        return "type"
    if name in _INLINE_TOKEN:
        # Ldtoken in the supported Project_d patches references an extern type.
        return "token"
    if name == "StackSpace":
        return "stack"
    return "none"


def validate_ifix_patch(patch: dict[str, Any]) -> None:
    type_count = len(patch["types"])
    method_count = len(patch["methods"])
    ext_count = len(patch["extern_methods"])
    field_count = len(patch["fields"])
    string_count = len(patch["strings"])

    for fix in patch["fixes"]:
        if not 0 <= fix["type"] < type_count or not 0 <= fix["patch"] < method_count:
            raise IFixMergeError("FixInfo trỏ tới type hoặc method không tồn tại.")
    for method in patch["extern_methods"]:
        if not 0 <= method["type"] < type_count:
            raise IFixMergeError("External method trỏ tới type không tồn tại.")
        if any(not 0 <= value < type_count for value in method["g"]):
            raise IFixMergeError("Generic argument trỏ tới type không tồn tại.")
        if any(kind == "i" and not 0 <= value < type_count for kind, value in method["params"]):
            raise IFixMergeError("External method parameter trỏ tới type không tồn tại.")
    for field in patch["fields"]:
        if not 0 <= field["type"] < type_count:
            raise IFixMergeError("Field trỏ tới type không tồn tại.")
    for type_id, cctor_id in patch["static_fields"]:
        if not 0 <= type_id < type_count or (cctor_id >= 0 and cctor_id >= method_count):
            raise IFixMergeError("Static field trỏ tới type hoặc method không tồn tại.")
    for item in patch["anon"]:
        if any(value >= type_count for value in item["fields"] if value >= 0):
            raise IFixMergeError("Anonymous-storey field trỏ tới type không tồn tại.")
        if item["ctor"] >= method_count:
            raise IFixMergeError("Anonymous-storey constructor không tồn tại.")
        if any(value >= type_count for value in item["itfs"] if value >= 0):
            raise IFixMergeError("Anonymous-storey interface không tồn tại.")
        if any(value >= method_count for value in item["vtable"] if value >= 0):
            raise IFixMergeError("Anonymous-storey vtable method không tồn tại.")

    for method in patch["methods"]:
        index = 0
        instructions = method["inst"]
        while index < len(instructions):
            opcode, operand = instructions[index]
            kind = _operand_kind(opcode)
            if kind == "string" and not 0 <= operand < string_count:
                raise IFixMergeError("Instruction trỏ tới chuỗi không tồn tại.")
            if kind in {"type", "token"} and operand >= type_count:
                raise IFixMergeError("Instruction trỏ tới type không tồn tại.")
            if kind == "field" and operand >= field_count:
                raise IFixMergeError("Instruction trỏ tới field không tồn tại.")
            if kind == "method" and not 0 <= (operand & 0xFFFF) < ext_count:
                raise IFixMergeError("Instruction trỏ tới external method không tồn tại.")
            if kind == "8byte":
                index += 1
            elif kind == "switch":
                index += (operand + 1) // 2
            index += 1


def merge_ifix_patches(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge patches without mutating caller-owned inputs, remapping every table index."""
    validate_ifix_patch(base)
    validate_ifix_patch(incoming)
    base = copy.deepcopy(base)
    incoming = copy.deepcopy(incoming)

    for key in ("magic", "bridge", "wrappers", "assembly"):
        if base[key] != incoming[key]:
            raise IFixMergeError(f"Không thể ghép IFix: trường {key} không khớp.")

    base_fix_keys = {(x["type"], x["name"], tuple(x["params"])) for x in base["fixes"]}
    incoming_keys = {(x["type"], x["name"], tuple(x["params"])) for x in incoming["fixes"]}
    if base_fix_keys & incoming_keys:
        raise IFixMergeError("Hai IFix patch có FixInfo trùng nhau; dừng để tránh ghi đè.")

    type_offset = len(base["types"])
    method_offset = len(base["methods"])
    ext_offset = len(base["extern_methods"])
    string_offset = len(base["strings"])
    field_offset = len(base["fields"])

    type_map = {i: type_offset + i for i in range(len(incoming["types"]))}
    method_map = {i: method_offset + i for i in range(len(incoming["methods"]))}
    ext_map = {i: ext_offset + i for i in range(len(incoming["extern_methods"]))}
    string_map = {i: string_offset + i for i in range(len(incoming["strings"]))}
    field_map = {i: field_offset + i for i in range(len(incoming["fields"]))}

    for item in incoming["extern_methods"]:
        item["type"] = type_map[item["type"]]
        item["g"] = [type_map[value] for value in item["g"]]
        item["params"] = [
            (kind, type_map[value] if kind == "i" else value)
            for kind, value in item["params"]
        ]
    for item in incoming["fields"]:
        item["type"] = type_map[item["type"]]
    incoming["static_fields"] = [
        (type_map[type_id], method_map.get(cctor, cctor) if cctor >= 0 else cctor)
        for type_id, cctor in incoming["static_fields"]
    ]
    for item in incoming["anon"]:
        item["fields"] = [type_map[value] if value >= 0 else value for value in item["fields"]]
        if item["ctor"] >= 0:
            item["ctor"] = method_map[item["ctor"]]
        item["itfs"] = [type_map[value] if value >= 0 else value for value in item["itfs"]]
        item["vtable"] = [method_map[value] if value >= 0 else value for value in item["vtable"]]

    for method in incoming["methods"]:
        instructions = method["inst"]
        index = 0
        while index < len(instructions):
            opcode, operand = instructions[index]
            kind = _operand_kind(opcode)
            if kind == "string":
                if operand not in string_map:
                    raise IFixMergeError("IFix patch tham chiếu chuỗi ngoài phạm vi.")
                instructions[index] = (opcode, string_map[operand])
            elif kind == "type":
                if operand >= 0:
                    if operand not in type_map:
                        raise IFixMergeError("IFix patch tham chiếu type ngoài phạm vi.")
                    instructions[index] = (opcode, type_map[operand])
            elif kind == "token":
                if operand >= 0:
                    if operand not in type_map:
                        raise IFixMergeError("IFix Ldtoken tham chiếu type ngoài phạm vi.")
                    instructions[index] = (opcode, type_map[operand])
            elif kind == "field":
                if operand >= 0:
                    if operand not in field_map:
                        raise IFixMergeError("IFix patch tham chiếu field ngoài phạm vi.")
                    instructions[index] = (opcode, field_map[operand])
            elif kind == "method":
                old_id = operand & 0xFFFF
                arg_count = (operand >> 16) & 0xFFFF
                if old_id not in ext_map:
                    raise IFixMergeError("IFix patch tham chiếu external method ngoài phạm vi.")
                instructions[index] = (opcode, (arg_count << 16) | ext_map[old_id])
            if kind == "8byte":
                index += 1
            elif kind == "switch":
                index += (operand + 1) // 2
            index += 1

    for fix in incoming["fixes"]:
        fix["type"] = type_map[fix["type"]]
        fix["g"] = [type_map[value] for value in fix["g"]]
        fix["params"] = [
            (kind, type_map[value] if kind == "i" else value)
            for kind, value in fix["params"]
        ]
        fix["patch"] = method_map[fix["patch"]]

    base["types"].extend(incoming["types"])
    base["methods"].extend(incoming["methods"])
    base["extern_methods"].extend(incoming["extern_methods"])
    base["strings"].extend(incoming["strings"])
    base["fields"].extend(incoming["fields"])
    base["static_fields"].extend(incoming["static_fields"])
    base["anon"].extend(incoming["anon"])
    base["fixes"].extend(incoming["fixes"])
    base["new_classes"].extend(incoming["new_classes"])
    validate_ifix_patch(base)
    return base
