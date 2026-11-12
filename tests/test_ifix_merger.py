import unittest

from app.ifix_merger import (
    IFixMergeError,
    merge_ifix_patches,
    parse_ifix_patch,
    serialize_ifix_patch,
)


def make_patch(name, type_name, string_value, field_name, opcode=103):
    return {
        "magic": 123456789,
        "bridge": "Project_d",
        "types": [type_name],
        "methods": [{"inst": [(opcode, 0)], "eh": []}],
        "extern_methods": [{"gen": False, "type": 0, "name": name + "Call", "g": [], "params": []}],
        "strings": [string_value],
        "fields": [{"new": False, "type": 0, "name": field_name, "extra": b""}],
        "static_fields": [(0, 0)],
        "anon": [],
        "wrappers": "Wrappers",
        "assembly": "Project_d",
        "fixes": [{"gen": False, "type": 0, "name": name, "g": [], "params": [], "patch": 0}],
        "new_classes": [],
    }


class IFixMergerTests(unittest.TestCase):
    def test_parse_serialize_roundtrip(self):
        patch = make_patch("BaseFix", "BaseType", "base-string", "baseField")
        encoded = serialize_ifix_patch(patch)
        self.assertEqual(serialize_ifix_patch(parse_ifix_patch(encoded)), encoded)

    def test_canonical_ifix_opcode_operand_kinds(self):
        from app.ifix_merger import _operand_kind

        # Cross-check the two opcodes encountered in the pinned VTK patch:
        self.assertEqual(_operand_kind(89), "type")       # Newarr, InlineType
        self.assertEqual(_operand_kind(175), "none")      # Stelem_Ref, InlineNone
        self.assertEqual(_operand_kind(162), "method")    # Ldvirtftn, InlineMethod

    def test_opcode_89_newarr_type_operand_is_remapped_and_roundtrips(self):
        base = make_patch("BaseFix", "BaseType", "base-string", "baseField")
        incoming = make_patch("IncomingFix", "IncomingType", "incoming-string", "incomingField")
        incoming["methods"][0]["inst"] = [
            (89, 0),   # Newarr, InlineType operand (verified in tools/ifix_parse.py)
            (103, 0),  # Ret
        ]

        merged = merge_ifix_patches(base, incoming)
        self.assertEqual(merged["methods"][1]["inst"], [(89, 1), (103, 0)])

        encoded = serialize_ifix_patch(merged)
        reparsed = parse_ifix_patch(encoded)
        self.assertEqual(serialize_ifix_patch(reparsed), encoded)
        self.assertEqual(reparsed["methods"][1]["inst"], [(89, 1), (103, 0)])

    def test_merge_remaps_all_referenced_tables(self):
        base = make_patch("BaseFix", "BaseType", "base-string", "baseField")
        incoming = make_patch("IncomingFix", "IncomingType", "incoming-string", "incomingField")
        incoming["methods"][0]["inst"] = [
            (39, 0),   # Ldstr
            (45, 0),   # Call external method
            (5, 0),    # Stobj type
            (34, 0),   # Ldflda field
            (94, 0),   # Ldtoken type
            (103, 0),  # Ret
        ]
        incoming["anon"] = [{
            "fields": [0, -2], "ctor": 0, "ctor_p": 0,
            "itfs": [0], "vtable": [0, -1],
        }]
        merged = merge_ifix_patches(base, incoming)
        self.assertEqual(len(merged["types"]), 2)
        self.assertEqual(len(merged["methods"]), 2)
        self.assertEqual(len(merged["extern_methods"]), 2)
        self.assertEqual(len(merged["strings"]), 2)
        self.assertEqual(len(merged["fields"]), 2)
        self.assertEqual(merged["methods"][1]["inst"], [
            (39, 1), (45, 1), (5, 1), (34, 1), (94, 1), (103, 0)
        ])
        self.assertEqual(merged["fixes"][1]["type"], 1)
        self.assertEqual(merged["fixes"][1]["patch"], 1)
        self.assertEqual(merged["anon"][0]["fields"], [1, -2])
        self.assertEqual(merged["anon"][0]["ctor"], 1)
        self.assertEqual(merged["anon"][0]["itfs"], [1])
        self.assertEqual(merged["anon"][0]["vtable"], [1, -1])
        final_bytes = serialize_ifix_patch(merged)
        self.assertEqual(serialize_ifix_patch(parse_ifix_patch(final_bytes)), final_bytes)

    def test_merge_does_not_mutate_input_patches(self):
        import copy

        base = make_patch("BaseFix", "BaseType", "base-string", "baseField")
        incoming = make_patch("IncomingFix", "IncomingType", "incoming-string", "incomingField")
        base_before = copy.deepcopy(base)
        incoming_before = copy.deepcopy(incoming)
        merge_ifix_patches(base, incoming)
        self.assertEqual(base, base_before)
        self.assertEqual(incoming, incoming_before)

    def test_invalid_input_references_raise_ifix_error_before_remapping(self):
        base = make_patch("BaseFix", "BaseType", "base-string", "baseField")
        incoming = make_patch("IncomingFix", "IncomingType", "incoming-string", "incomingField")
        incoming["fixes"][0]["type"] = 99
        with self.assertRaisesRegex(IFixMergeError, "FixInfo trỏ tới type"):
            merge_ifix_patches(base, incoming)

    def test_negative_collection_count_is_rejected(self):
        import struct

        malformed = struct.pack("<Q", 0) + b"\x00" + struct.pack("<i", -1)
        with self.assertRaisesRegex(IFixMergeError, "Số lượng bản ghi trong IFix"):
            parse_ifix_patch(malformed)

    def test_invalid_utf8_string_is_rejected_as_ifix_error(self):
        import struct

        malformed = struct.pack("<Q", 0) + b"\x01\xff"
        with self.assertRaisesRegex(IFixMergeError, "không phải UTF-8"):
            parse_ifix_patch(malformed)

    def test_unknown_opcode_is_rejected(self):
        base = make_patch("BaseFix", "BaseType", "base-string", "baseField")
        incoming = make_patch("IncomingFix", "IncomingType", "incoming-string", "incomingField", opcode=999)
        with self.assertRaises(IFixMergeError):
            merge_ifix_patches(base, incoming)

    def test_truncated_patch_is_rejected(self):
        patch = make_patch("BaseFix", "BaseType", "base-string", "baseField")
        encoded = serialize_ifix_patch(patch)
        with self.assertRaises(IFixMergeError):
            parse_ifix_patch(encoded[:-1])


if __name__ == "__main__":
    unittest.main()
