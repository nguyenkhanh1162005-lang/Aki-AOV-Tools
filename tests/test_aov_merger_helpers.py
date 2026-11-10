import hashlib
import unittest

from app.aov_bytes import decode_aov_resource, encode_aov_resource
from app.aov_merger import (
    _merge_bank,
    _merge_common_action_xml,
    _merge_res_skin,
    _merge_skillcombine,
    _parse_label_rows,
)


def make_mses(row_count, schema, body):
    header = bytearray(140)
    header[:4] = b"MSES"
    header[8:12] = schema.to_bytes(4, "little")
    header[12:16] = row_count.to_bytes(4, "little")
    header[96:128] = hashlib.md5(body).hexdigest().encode("ascii")
    return bytes(header) + body


def label_row(key, value):
    raw = value.encode("utf-8")
    return (59).to_bytes(4, "little") + key.to_bytes(4, "little") + len(raw).to_bytes(4, "little") + (b"\0" * 20) + raw


class AOVMergerHelperTests(unittest.TestCase):
    def test_res_skin_appends_only_skin_59500_row(self):
        base_raw = make_mses(3, 69, label_row(1, "one") + label_row(2, "two") + label_row(3, "three"))
        vtk_raw = make_mses(2, 63, label_row(1, "one") + label_row(59500, "Vấn Tâm Kiếm"))
        base = encode_aov_resource(base_raw, "ResSkinSeniorLabelCfg", "aes")
        vtk = encode_aov_resource(vtk_raw, "ResSkinSeniorLabelCfg", "aes")
        merged = _merge_res_skin(base, vtk)
        raw, mode = decode_aov_resource(merged, "ResSkinSeniorLabelCfg")
        self.assertEqual(mode, "aes")
        self.assertEqual([key for key, _ in _parse_label_rows(raw)], [1, 2, 3, 59500])
        self.assertEqual(int.from_bytes(raw[8:12], "little"), 69)
        self.assertEqual(int.from_bytes(raw[12:16], "little"), 4)

    def test_skillcombine_keeps_newer_header_and_appends_vtk_tail(self):
        base_raw = make_mses(14608, 316, b"BASE-ROWS")
        vtk_raw = make_mses(14613, 304, b"BASE-ROWS" + b"FIVE-VTK-ROWS")
        base = encode_aov_resource(base_raw, "skillcombine", "aes")
        vtk = encode_aov_resource(vtk_raw, "skillcombine", "zstd")
        merged = _merge_skillcombine(base, vtk)
        raw, mode = decode_aov_resource(merged, "skillcombine")
        self.assertEqual(mode, "aes")
        self.assertEqual(int.from_bytes(raw[8:12], "little"), 316)
        self.assertEqual(int.from_bytes(raw[12:16], "little"), 14613)
        self.assertEqual(raw[140:], b"BASE-ROWSFIVE-VTK-ROWS")
        self.assertEqual(raw[96:128], hashlib.md5(raw[140:]).hexdigest().encode("ascii"))

    def test_bank_is_reencoded_in_base_format(self):
        base_raw = make_mses(1932, 59, b"Hero_LiXin_SFX Hero_LiXin_VO")
        vtk_raw = make_mses(1932, 59, b"Hero_LiXin_Skin_C_SFX Hero_LiXin_Skin_C_VO")
        base = encode_aov_resource(base_raw, "BattleBank", "aes")
        vtk = encode_aov_resource(vtk_raw, "BattleBank", "zstd")
        merged = _merge_bank(
            base, vtk, "BattleBank",
            (b"Hero_LiXin_Skin_C_SFX", b"Hero_LiXin_Skin_C_VO"),
        )
        raw, mode = decode_aov_resource(merged, "BattleBank")
        self.assertEqual(mode, "aes")
        self.assertEqual(raw, vtk_raw)

    def test_common_action_xml_adds_only_skin_59500_track(self):
        base = b'''<?xml version="1.0"?><Root><TemplateObjectList><TemplateObject id="1"/></TemplateObjectList><RefParamList><RefParam name="base"/></RefParamList><Action length="5"><Track trackName="baseTrack"><SkinOrAvatarList id="1"/></Track></Action></Root>'''
        vtk = b'''<?xml version="1.0"?><Root><TemplateObjectList><TemplateObject id="2"/></TemplateObjectList><RefParamList><RefParam name="vtk"/></RefParamList><Action length="6"><Track trackName="vtkTrack"><SkinOrAvatarList id="59500"/><SkinOrAvatarList id="123"/></Track></Action></Root>'''
        merged, names = _merge_common_action_xml(base, vtk)
        self.assertEqual(names, ["vtkTrack"])
        self.assertIn(b'id="59500"', merged)
        self.assertNotIn(b'id="123"', merged)
        self.assertIn(b'length="6.000"', merged)
        self.assertIn(b'id="2"', merged)
        self.assertIn(b'name="vtk"', merged)


if __name__ == "__main__":
    unittest.main()
