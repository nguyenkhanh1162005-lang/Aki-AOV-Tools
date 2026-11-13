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




def make_actor_skin_ifix(kind, notify_ids=None, open_ids=None, buttons=None, add_hero=None):
    is_plugins = kind == "HeroSkin_2"
    type_count = 9 if is_plugins else 168
    field_count = 4 if is_plugins else 81
    fields = [
        {"new": False, "type": 0, "name": f"field_{index}", "extra": b""}
        for index in range(field_count)
    ]

    def notify_method(actor_ids):
        actor_ids = sorted(actor_ids)
        out = [(146, 2), (164, 1), (167, 80)]
        true_return = 3 + 3 + 5 * (len(actor_ids) - 1) + 2
        branch_index = len(out) + 2
        out.extend([(143, actor_ids[0]), (166, 0), (127, true_return - branch_index)])
        for actor_id in actor_ids[1:]:
            branch_index = len(out) + 4
            out.extend([(164, 1), (167, 80), (143, actor_id), (166, 0), (127, true_return - branch_index)])
        out.extend([(143, 0), (173, 1), (143, 1), (173, 1)])
        return out

    def open_method(actor_ids):
        actor_ids = sorted(actor_ids)
        prefix = [(0, 0)] * 55
        prefix[0] = (146, 196611)
        prefix[5] = prefix[13] = prefix[18] = prefix[26] = (134, 0)
        prefix[54] = (72, 0)
        false_return = 55 + 3 + 4 * (len(actor_ids) - 1)
        true_return = false_return + 2
        for index in (5, 13, 18, 26):
            prefix[index] = (134, false_return - index)
        out = prefix
        branch_index = len(out) + 2
        out.extend([(143, actor_ids[0]), (166, 0), (127, true_return - branch_index)])
        for actor_id in actor_ids[1:]:
            branch_index = len(out) + 3
            out.extend([(72, 0), (143, actor_id), (166, 0), (127, true_return - branch_index)])
        out.extend([(143, 0), (173, 1), (143, 1), (173, 1)])
        return out

    def button_method(mapping):
        mapping = dict(sorted(mapping.items()))
        prefix = [(0, 0)] * 55
        prefix[0] = (146, 196611)
        prefix[5] = prefix[13] = prefix[18] = prefix[26] = (134, 0)
        prefix[54] = (72, 0)
        default_return = 55 + 5 + 6 * (len(mapping) - 1)
        for index in (5, 13, 18, 26):
            prefix[index] = (134, default_return - index)
        out = prefix
        actor_id = next(iter(mapping))
        out.extend([(143, actor_id), (166, 0), (134, 3), (143, mapping[actor_id]), (173, 1)])
        for actor_id, button_id in list(mapping.items())[1:]:
            out.extend([(72, 0), (143, actor_id), (166, 0), (134, 3), (143, button_id), (173, 1)])
        out.extend([(143, 0), (173, 1)])
        return out

    def add_hero_method(mapping):
        mapping = dict(sorted(mapping.items()))
        prefix = [(0, 0)] * 58
        prefix[0] = (146, 5)
        prefix[33] = prefix[41] = prefix[46] = prefix[54] = (134, 0)
        prefix[57] = (127, 1)
        blocks = []
        for actor_id, (field_three, field_two) in mapping.items():
            if field_two is None:
                blocks.extend([(164, 1), (143, actor_id), (166, 0), (134, 5), (164, 0), (143, field_three), (75, 3), (173, 0)])
            else:
                blocks.extend([(164, 1), (143, actor_id), (166, 0), (134, 8), (164, 0), (143, field_three), (75, 3), (164, 0), (143, field_two), (75, 2), (173, 0)])
        final_return = 58 + len(blocks)
        for index in (33, 41, 46, 54):
            prefix[index] = (134, final_return - index)
        return prefix + blocks + [(173, 0)]

    if is_plugins:
        method_list = [add_hero_method(add_hero or {})]
        fix_names = ["AddHero"]
        bridge = "IFix.ILFixInterfaceBridge, Project.Plugins_d"
        assembly = "Project.Plugins_d"
    else:
        method_list = [
            notify_method(notify_ids or []),
            open_method(open_ids or []),
            button_method(buttons or {}),
        ]
        fix_names = ["IsPlaySkinKillNotify", "IsOpen", "get_PersonalBtnId"]
        bridge = "IFix.ILFixInterfaceBridge, Project_d"
        assembly = "Project_d"

    return {
        "magic": 123456789,
        "bridge": bridge,
        "types": [f"Type{index}" for index in range(type_count)],
        "methods": [{"inst": method, "eh": []} for method in method_list],
        "extern_methods": [],
        "strings": [],
        "fields": fields,
        "static_fields": [],
        "anon": [],
        "wrappers": "Wrappers",
        "assembly": assembly,
        "fixes": [
            {"gen": False, "type": 0, "name": name, "g": [], "params": [], "patch": index}
            for index, name in enumerate(fix_names)
        ],
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
        self.assertEqual(_operand_kind(146), "stack")       # AoV custom StackSpace pseudo-op

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



    def test_merge_p9_p10_hero_skin_1_actor_methods(self):
        from app.ifix_merger import merge_ifix_actor_scoped_patches

        p9 = make_actor_skin_ifix(
            "HeroSkin_1",
            notify_ids={130, 137},
            open_ids={107, 130},
            buttons={107: 10714, 130: 13015},
        )
        p10 = make_actor_skin_ifix(
            "HeroSkin_1",
            notify_ids={111, 132},
            open_ids={111, 132},
            buttons={111: 11120, 132: 13215},
        )
        merged = merge_ifix_actor_scoped_patches(p9, p10)

        notify = merged["methods"][0]["inst"]
        self.assertIn((143, 111), notify)
        self.assertIn((143, 130), notify)
        self.assertIn((143, 132), notify)
        self.assertIn((143, 137), notify)

        open_method = merged["methods"][1]["inst"]
        self.assertIn((143, 107), open_method)
        self.assertIn((143, 111), open_method)
        self.assertIn((143, 130), open_method)
        self.assertIn((143, 132), open_method)

        buttons = merged["methods"][2]["inst"]
        self.assertIn((143, 10714), buttons)
        self.assertIn((143, 11120), buttons)
        self.assertIn((143, 13015), buttons)
        self.assertIn((143, 13215), buttons)
        encoded = serialize_ifix_patch(merged)
        self.assertEqual(serialize_ifix_patch(parse_ifix_patch(encoded)), encoded)

    def test_merge_p9_p10_hero_skin_2_add_hero_actor_mappings(self):
        from app.ifix_merger import merge_ifix_actor_scoped_patches

        p9 = make_actor_skin_ifix(
            "HeroSkin_2",
            add_hero={107: (36, None), 130: (12, 12)},
        )
        p10 = make_actor_skin_ifix(
            "HeroSkin_2",
            add_hero={111: (34, 33), 132: (76, 64)},
        )
        merged = merge_ifix_actor_scoped_patches(p9, p10)
        instructions = merged["methods"][0]["inst"]
        for value in (107, 111, 130, 132, 36, 34, 33, 12, 76, 64):
            self.assertIn((143, value), instructions)
        encoded = serialize_ifix_patch(merged)
        self.assertEqual(serialize_ifix_patch(parse_ifix_patch(encoded)), encoded)

    def test_p9_p10_ifix_union_rejects_conflicting_personal_button_mapping(self):
        from app.ifix_merger import merge_ifix_actor_scoped_patches

        p9 = make_actor_skin_ifix(
            "HeroSkin_1",
            notify_ids={130},
            open_ids={130},
            buttons={130: 13015},
        )
        p10 = make_actor_skin_ifix(
            "HeroSkin_1",
            notify_ids={130},
            open_ids={130},
            buttons={130: 13099},
        )
        with self.assertRaisesRegex(IFixMergeError, "mapping actor xung đột"):
            merge_ifix_actor_scoped_patches(p9, p10)


if __name__ == "__main__":
    unittest.main()
