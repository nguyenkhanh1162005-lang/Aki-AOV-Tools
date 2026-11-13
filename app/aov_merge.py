from __future__ import annotations

import hashlib

from .aov_bytes import AOVBytesError, decode_aov_resource, encode_aov_resource
from .aov_mses import MSESError, build_mses, merge_mses_rows, parse_mses


class AOVResourceMergeError(ValueError):
    pass


_HUANHUA_TABLE_KEYS = {
    "Databin/Client/Huanhua/ResBillboardCfg.bytes": {0x00000253},
    "Databin/Client/Huanhua/ResBillboardSkinCfg.bytes": {0x00000253},
    "Databin/Client/Huanhua/ResKillBillboardCfg.bytes": {0x00000253},
}
_SKILLCOMBINE_KEYS = {0x000915A7, 0x000915A8, 0x000915A9, 0x000915AA, 0x000915AB}
_CHARACTER_COMPONENT_PATH = "Databin/Client/Character/ResCharacterComponent.bytes"

_SUPPORTED_MSES_TABLES = {
    _CHARACTER_COMPONENT_PATH,
    "Databin/Client/Sound/BattleBank.bytes",
    "Databin/Client/Sound/LobbyBank.bytes",
    "Databin/Client/Skill/skillcombine.bytes",
    "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes",
    *_HUANHUA_TABLE_KEYS,
}
_LEGACY_MSES_TABLES = set(_SUPPORTED_MSES_TABLES)

_P9_P10_ACTOR_OWNED_MSES = {
    "Databin/Client/Actor/heroSkin.bytes": {
        "field_count": 631, "row_count": 3072, "actor_divisor": 100,
        "conflict_count": 339,
        "p9_sha256": "a3721e2fc409ccab59f11fe8a50f1794a7f53b62f2db87b93f97c59c67ea4f7b",
        "p10_sha256": "8401822bd6a41c1cc648c7d965973da21cb140a00cf10e759b46ea099584f8fa",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },
    "Databin/Client/Motion/ResSkinMotionBaseCfg.bytes": {
        "field_count": 58, "row_count": 411, "actor_divisor": 10000,
        "conflict_count": 60,
        "p9_sha256": "711596da3a9a1453f32b585f72d3d299fc766ce9df280d3a931dfd9213b7a374",
        "p10_sha256": "159fd5ad6153846d8faafa338acd64d1470d4806fdc21d8c3dd0734994d8e995",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },
    "Databin/Client/Shop/HeroSkinShop.bytes": {
        "field_count": 273, "row_count": 1347, "actor_divisor": 100,
        "conflict_count": 208,
        "p9_sha256": "099bc89ceb79ac9a39d202c7cf11770cb7c389b506adc2f1b5afccd877e4cf95",
        "p10_sha256": "6a1e1fbb9d7c1e3ea094198e0c9cb39ba76655414e279dd20241fadeccadaa99",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },
    "Databin/Client/Skill/skillmark.bytes": {
        "field_count": 194, "row_count": 188, "actor_divisor": 100,
        "conflict_count": 3,
        "p9_sha256": "06585bd44510621b97a349e61f90df2a47b80a95470b16d61afe5db62efc0a71",
        "p10_sha256": "a452c192f73cbe81762512a1443e06b8412ed0fc2aa11985bfd3f0107d2f6b4d",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },

    "Databin/Client/Sound/BattleBank.bytes": {
        "field_count": 59, "row_count": 1932, "actor_id_rule": "bank_10000",
        "conflict_count": 352,
        "p9_sha256": "e350c1093ed73a3ffd06bfcdaa0485213eef630f49ace02bbd7ebf5d6c659ee1",
        "p10_sha256": "edcce2276905a12e39c3422754ac7c1429f95b2c43282cece11d3d02d49c6472",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },
    "Databin/Client/Sound/LobbyBank.bytes": {
        "field_count": 61, "row_count": 888, "actor_id_rule": "bank_10000",
        "conflict_count": 177,
        "p9_sha256": "0af679c8fea70399eef53830735ab1925b1502e8af11e03b0b67ad840747b3c6",
        "p10_sha256": "ef681b2e8a1041020249f7f7093869432623e960ad093c2fe5fefe82da6b11ad",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },
    "Databin/Client/Sound/LobbySound.bytes": {
        "field_count": 69, "row_count": 959, "actor_id_rule": "sound_1000",
        "conflict_count": 183,
        "p9_sha256": "0ef6ff11497eaa28ba9bb2c1dbf3f23f7ce3ac03be9d6a67b2ce8040586df8af",
        "p10_sha256": "4cb175546942bd5f5dda76e82419bf665108735d75821e506bab5db0aaa8eab5",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },
    "Databin/Client/Sound/ChatSound.bytes": {
        "field_count": 65, "row_count": 1270, "actor_id_rule": "sound_1000",
        "conflict_count": 171,
        "p9_sha256": "41fe532898207cfc08b4886e485d93b6c2333c1f92341aa6f5c766464bc0db99",
        "p10_sha256": "f356c99cbf752c0d3e471c2c7f7fef247517bf3901e26e69fee304a648e1d581",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },
    "Databin/Client/Sound/CoupleSound.bytes": {
        "field_count": 80, "row_count": 577, "actor_id_rule": "sound_1000",
        "conflict_count": 114,
        "p9_sha256": "3954faa22b4223e47dd5a9bb6a7fa2d3badc9fc5b681e850afa7d887cf49aff7",
        "p10_sha256": "31ecde02feca2329ca4cfab9acf13c140d8a1700de25aa77531223bef4cabbbd",
        "p9_actor_ids": {107, 130, 137, 163, 509, 510, 518, 540, 597},
        "p10_actor_ids": {111, 132, 150, 157, 167, 190, 524, 567, 598, 599},
    },
}

_SUPPORTED_MSES_TABLES.update(_P9_P10_ACTOR_OWNED_MSES)


def _normalise_actor_ids(values: set[int | str], label: str) -> set[int]:
    result = set()
    for value in values:
        try:
            actor_id = int(value)
        except (TypeError, ValueError) as exc:
            raise MSESError(f"{label}: Actor ID không hợp lệ: {value!r}.") from exc
        if not 0 < actor_id <= 999:
            raise MSESError(f"{label}: Actor ID ngoài phạm vi hỗ trợ: {actor_id}.")
        result.add(actor_id)
    return result


def _index_actor_owned_rows(rows: tuple[bytes, ...], label: str) -> tuple[dict[int, bytes], list[int]]:
    indexed = {}
    order = []
    for row in rows:
        if len(row) < 4:
            raise MSESError(f"{label}: bản ghi quá ngắn để đọc khóa.")
        key = int.from_bytes(row[:4], "little")
        if key in indexed:
            raise MSESError(f"{label}: khóa MSES trùng 0x{key:08x}.")
        indexed[key] = row
        order.append(key)
    return indexed, order


def _actor_id_from_mses_key(key: int, spec: dict) -> int | None:
    """Decode the source-validated Actor ID prefix used by a specific MSES table."""
    rule = spec.get("actor_id_rule")
    if rule == "bank_10000":
        encoded = key // 10000
        return encoded if encoded <= 999 else encoded // 100
    if rule == "sound_1000":
        encoded = key // 1000
        return encoded if encoded <= 999 else encoded // 100
    if rule == "actor_divisor" or rule is None:
        divisor = spec.get("actor_divisor")
        return key // divisor if isinstance(divisor, int) and divisor > 0 else None
    return None


def _merge_p9_p10_actor_owned_rows(
    base_decoded: bytes,
    overlay_decoded: bytes,
    resource_name: str,
    base_actor_action_ids: set[int | str],
    overlay_actor_action_ids: set[int | str],
) -> tuple[bytes, dict]:
    """Merge only the exact observed P9/P10 table pair using Actor action-package ownership."""
    normalized_name = resource_name.replace(chr(92), "/").lstrip("/")
    spec = _P9_P10_ACTOR_OWNED_MSES.get(normalized_name)
    if spec is None:
        raise MSESError("Không có quy tắc Actor-owner cho bảng này.")

    base_table = parse_mses(base_decoded)
    overlay_table = parse_mses(overlay_decoded)
    expected_shape = (spec["field_count"], spec["row_count"])
    if (base_table.field_count, len(base_table.rows)) != expected_shape or (
        overlay_table.field_count, len(overlay_table.rows)
    ) != expected_shape:
        raise MSESError(
            f"{resource_name}: schema/row count khác cặp P9/P10 đã kiểm chứng "
            f"(cần {expected_shape}, nhận "
            f"{(base_table.field_count, len(base_table.rows))}/"
            f"{(overlay_table.field_count, len(overlay_table.rows))})."
        )

    base_hash = hashlib.sha256(base_decoded).hexdigest()
    overlay_hash = hashlib.sha256(overlay_decoded).hexdigest()
    if (base_hash, overlay_hash) == (spec["p9_sha256"], spec["p10_sha256"]):
        base_source, overlay_source = "P9", "P10"
    elif (base_hash, overlay_hash) == (spec["p10_sha256"], spec["p9_sha256"]):
        base_source, overlay_source = "P10", "P9"
    else:
        raise MSESError(
            f"{resource_name}: payload không khớp chính xác mẫu P9/P10 đã kiểm chứng; "
            "từ chối áp dụng quy tắc theo Actor ID."
        )

    base_ids = _normalise_actor_ids(base_actor_action_ids, "base")
    overlay_ids = _normalise_actor_ids(overlay_actor_action_ids, "overlay")
    expected_base_ids = spec[f"{base_source.lower()}_actor_ids"]
    expected_overlay_ids = spec[f"{overlay_source.lower()}_actor_ids"]
    if base_ids != expected_base_ids or overlay_ids != expected_overlay_ids:
        raise MSESError(
            f"{resource_name}: Actor_Action inventory không khớp nguồn {base_source}/{overlay_source}; "
            "không thể xác định nguồn sở hữu từng hàng."
        )
    if base_ids & overlay_ids:
        raise MSESError(
            f"{resource_name}: Actor_Action inventory bị giao nhau; quyền sở hữu không duy nhất."
        )

    base_map, base_order = _index_actor_owned_rows(base_table.rows, "base")
    overlay_map, _ = _index_actor_owned_rows(overlay_table.rows, "overlay")
    if set(base_map) != set(overlay_map):
        raise MSESError(
            f"{resource_name}: tập khóa P9/P10 thay đổi so với fixture đã kiểm chứng."
        )
    differing = {
        key for key in base_map
        if base_map[key] != overlay_map[key]
    }
    if len(differing) != spec["conflict_count"]:
        raise MSESError(
            f"{resource_name}: số hàng khác nội dung thay đổi "
            f"({len(differing)} != {spec['conflict_count']}); từ chối ghép."
        )

    merged = dict(base_map)
    selected_base = 0
    selected_overlay = 0
    for key in differing:
        actor_id = _actor_id_from_mses_key(key, spec)
        if actor_id is None:
            raise MSESError(
                f"{resource_name}: không có quy tắc Actor ID cho khóa 0x{key:08x}."
            )
        owned_by_base = actor_id in base_ids
        owned_by_overlay = actor_id in overlay_ids
        if owned_by_base == owned_by_overlay:
            raise MSESError(
                f"{resource_name}: không xác định được duy nhất chủ sở hữu hàng "
                f"0x{key:08x} (Actor {actor_id})."
            )
        if owned_by_base:
            merged[key] = base_map[key]
            selected_base += 1
        else:
            merged[key] = overlay_map[key]
            selected_overlay += 1

    output = build_mses(base_table, [merged[key] for key in base_order])
    verified = parse_mses(output)
    if len(verified.rows) != spec["row_count"]:
        raise MSESError("Số hàng MSES sau ghép không khớp fixture.")
    report = {
        "adapter": "p9_p10_actor_owned_mses_exact_source_pair",
        "resource": normalized_name,
        "base_source": base_source,
        "overlay_source": overlay_source,
        "base_fields": base_table.field_count,
        "overlay_fields": overlay_table.field_count,
        "output_fields": verified.field_count,
        "base_rows": len(base_table.rows),
        "overlay_rows": len(overlay_table.rows),
        "output_rows": len(verified.rows),
        "differing_shared_rows": len(differing),
        "selected_base_owned_rows": selected_base,
        "selected_overlay_owned_rows": selected_overlay,
        "base_checksum_valid": base_table.checksum_valid,
        "overlay_checksum_valid": overlay_table.checksum_valid,
        "output_checksum_valid": verified.checksum_valid,
        "source_hashes_pinned": True,
        "actor_inventory_pinned": True,
        "verified": True,
    }
    return output, report



def merge_aov_mses_resource(
    base_resource: bytes,
    overlay_resource: bytes,
    resource_name: str,
    *,
    allow_invalid_base_checksum: bool = False,
    allow_p9_p10_label_union: bool = False,
    base_actor_action_ids: set[int | str] | None = None,
    overlay_actor_action_ids: set[int | str] | None = None,
) -> tuple[bytes, dict]:
    """Merge the known MSES v7 AoV tables while retaining the base package's schema.

    New opaque rows are admitted only by an exact table-specific key allowlist.
    Sound banks admit only rows carrying the VTK Hero_LiXin_Skin_C marker;
    ResSkinSeniorLabelCfg normally admits only the observed 59500 row. Its separate
    P9/P10 mode is pinned to the observed 69-field 8-row/98-row pair with stale
    source checksums and byte-identical shared rows. ResCharacterComponent allows
    only a same-schema union when every shared row is byte-identical.
    skillcombine admits only its source-verified Actor 595 keys.
    """
    normalized_name = resource_name.replace("\\", "/").lstrip("/")
    if normalized_name not in _SUPPORTED_MSES_TABLES:
        raise AOVResourceMergeError(
            f"{resource_name}: không có adapter MSES theo bảng đã xác minh."
        )

    actor_owner_requested = (
        base_actor_action_ids is not None or overlay_actor_action_ids is not None
    )
    if normalized_name in _P9_P10_ACTOR_OWNED_MSES:
        if base_actor_action_ids is not None and overlay_actor_action_ids is not None:
            try:
                base_decoded, base_mode = decode_aov_resource(base_resource, resource_name)
                overlay_decoded, overlay_mode = decode_aov_resource(overlay_resource, resource_name)
                merged_decoded, report = _merge_p9_p10_actor_owned_rows(
                    base_decoded,
                    overlay_decoded,
                    normalized_name,
                    base_actor_action_ids,
                    overlay_actor_action_ids,
                )
                merged_resource = encode_aov_resource(merged_decoded, resource_name, base_mode)
                verify_decoded, verify_mode = decode_aov_resource(merged_resource, resource_name)
                parse_mses(verify_decoded)
                if verify_decoded != merged_decoded:
                    raise MSESError("Payload sau mã hóa/giải mã round-trip không trùng.")
                report.update({
                    "base_encoding": base_mode,
                    "overlay_encoding": overlay_mode,
                    "output_encoding": verify_mode,
                    "output_bytes": len(merged_resource),
                })
                return merged_resource, report
            except (AOVBytesError, MSESError) as exc:
                raise AOVResourceMergeError(
                    f"{resource_name}: không thể ghép theo Actor-owner: {exc}"
                ) from exc
        if actor_owner_requested:
            raise AOVResourceMergeError(
                f"{resource_name}: phải có Actor_Action inventory của cả hai nguồn."
            )
        if normalized_name not in _LEGACY_MSES_TABLES:
            raise AOVResourceMergeError(
                f"{resource_name}: cần Actor_Action inventory P9/P10 để dùng adapter mới."
            )
    if actor_owner_requested:
        raise AOVResourceMergeError(
            f"{resource_name}: Actor_Action inventory chỉ dùng cho bốn bảng P9/P10 đã ghim."
        )

    try:
        base_decoded, base_mode = decode_aov_resource(base_resource, resource_name)
        overlay_decoded, overlay_mode = decode_aov_resource(overlay_resource, resource_name)
        base_checksum_repaired = False
        overlay_checksum_repaired = False
        if allow_invalid_base_checksum and allow_p9_p10_label_union:
            raise MSESError("Không thể bật đồng thời hai chế độ sửa checksum MSES.")
        if allow_invalid_base_checksum:
            if not resource_name.replace("\\", "/").endswith("/ResSkinSeniorLabelCfg.bytes"):
                raise MSESError("Chỉ cho phép sửa checksum nền cho ResSkinSeniorLabelCfg.")
            base_table = parse_mses(base_decoded, verify_checksum=False)
            if base_table.field_count != 69 or len(base_table.rows) != 53:
                raise MSESError("ResSkinSeniorLabelCfg nền không đúng cấu trúc đã kiểm tra.")
            base_decoded = build_mses(base_table, base_table.rows)
            base_table = parse_mses(base_decoded)
            base_checksum_repaired = True
            overlay_table = parse_mses(overlay_decoded)
        elif allow_p9_p10_label_union:
            if normalized_name != "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes":
                raise MSESError(
                    "Chế độ P9/P10 chỉ áp dụng cho ResSkinSeniorLabelCfg.bytes."
                )
            base_table = parse_mses(base_decoded, verify_checksum=False)
            overlay_table = parse_mses(overlay_decoded, verify_checksum=False)
            expected_stale_checksum = b"6679e51360ee25c838a5ee74af96a364"
            if (
                base_table.checksum_valid
                or overlay_table.checksum_valid
                or base_decoded[0x60:0x80] != expected_stale_checksum
                or overlay_decoded[0x60:0x80] != expected_stale_checksum
            ):
                raise MSESError(
                    "Adapter P9/P10 chỉ nhận đúng chuỗi checksum cũ đã xác minh ở cả hai bảng."
                )
            if (
                base_table.field_count != 69
                or overlay_table.field_count != 69
                or sorted((len(base_table.rows), len(overlay_table.rows))) != [8, 98]
            ):
                raise MSESError(
                    "Adapter P9/P10 chỉ xác minh schema 69 trường với cặp 8/98 hàng."
                )

            def _index_label_rows(rows, label):
                indexed = {}
                for row in rows:
                    if len(row) < 4:
                        raise MSESError(
                            f"ResSkinSeniorLabelCfg: hàng {label} quá ngắn."
                        )
                    key = int.from_bytes(row[:4], "little")
                    if key in indexed:
                        raise MSESError(
                            f"ResSkinSeniorLabelCfg: khóa trùng trong {label}: 0x{key:08x}."
                        )
                    indexed[key] = row
                return indexed

            base_rows_by_key = _index_label_rows(base_table.rows, "base")
            overlay_rows_by_key = _index_label_rows(overlay_table.rows, "overlay")
            smaller = (
                base_rows_by_key
                if len(base_rows_by_key) == 8
                else overlay_rows_by_key
            )
            larger = (
                overlay_rows_by_key
                if len(overlay_rows_by_key) == 98
                else base_rows_by_key
            )
            if not set(smaller).issubset(larger):
                raise MSESError(
                    "Bảng P9/P10 8 hàng không phải tập con của bảng 98 hàng."
                )
            differing = [
                key for key in smaller
                if smaller[key] != larger[key]
            ]
            if differing:
                raise MSESError(
                    "Bảng P9/P10 có hàng chung khác nội dung: "
                    + ", ".join(f"0x{key:08x}" for key in sorted(differing)[:20])
                )
            base_checksum_repaired = True
            overlay_checksum_repaired = True
        else:
            base_table = parse_mses(base_decoded)
            overlay_table = parse_mses(overlay_decoded)
    except (AOVBytesError, MSESError) as exc:
        raise AOVResourceMergeError(f"{resource_name}: {exc}") from exc

    override_keys: set[int] = set()
    allowed_new_keys: set[int] = set()
    is_sound_bank = normalized_name in {
        "Databin/Client/Sound/BattleBank.bytes",
        "Databin/Client/Sound/LobbyBank.bytes",
    }
    if is_sound_bank:
        marker_rows = [
            row for row in overlay_table.rows
            if b"Hero_LiXin_Skin_C" in row
        ]
        if not marker_rows:
            raise AOVResourceMergeError(
                f"{resource_name}: overlay không có bản ghi Hero_LiXin_Skin_C để ghép."
            )
        override_keys = {int.from_bytes(row[:4], "little") for row in marker_rows}
        allowed_new_keys = override_keys
    elif normalized_name in _HUANHUA_TABLE_KEYS:
        allowed_new_keys = set(_HUANHUA_TABLE_KEYS[normalized_name])
        overlay_keys = {int.from_bytes(row[:4], "little") for row in overlay_table.rows}
        base_rows_by_key = {
            int.from_bytes(row[:4], "little"): row for row in base_table.rows
        }
        overlay_rows_by_key = {
            int.from_bytes(row[:4], "little"): row for row in overlay_table.rows
        }
        if not allowed_new_keys.issubset(overlay_rows_by_key):
            raise AOVResourceMergeError(
                f"{resource_name}: overlay thiếu khóa billboard đã xác minh "
                + ", ".join(f"0x{key:08x}" for key in sorted(allowed_new_keys - set(overlay_rows_by_key)))
                + "."
            )
        for key in allowed_new_keys & set(base_rows_by_key):
            if base_rows_by_key[key] != overlay_rows_by_key[key]:
                raise AOVResourceMergeError(
                    f"{resource_name}: khóa billboard 0x{key:08x} đã tồn tại "
                    "nhưng payload khác; chưa có quy tắc ghi đè an toàn."
                )
        unexpected = overlay_keys - set(base_rows_by_key) - allowed_new_keys
        if unexpected:
            raise AOVResourceMergeError(
                f"{resource_name}: overlay có khóa mới ngoài allowlist billboard: "
                + ", ".join(f"0x{key:08x}" for key in sorted(unexpected))
                + "."
            )
    elif normalized_name == "Databin/Client/Skill/skillcombine.bytes":
        allowed_new_keys = set(_SKILLCOMBINE_KEYS)
        overlay_rows_by_key = {
            int.from_bytes(row[:4], "little"): row for row in overlay_table.rows
        }
        base_rows_by_key = {
            int.from_bytes(row[:4], "little"): row for row in base_table.rows
        }
        missing = allowed_new_keys - set(overlay_rows_by_key)
        if missing:
            raise AOVResourceMergeError(
                f"{resource_name}: overlay thiếu key skillcombine đã xác minh: "
                + ", ".join(f"0x{key:08x}" for key in sorted(missing))
                + "."
            )
        conflicts = {
            key for key in allowed_new_keys & set(base_rows_by_key)
            if base_rows_by_key[key] != overlay_rows_by_key[key]
        }
        if conflicts:
            raise AOVResourceMergeError(
                f"{resource_name}: key skillcombine đã tồn tại nhưng payload khác: "
                + ", ".join(f"0x{key:08x}" for key in sorted(conflicts))
                + "."
            )
        unexpected = {
            int.from_bytes(row[:4], "little") for row in overlay_table.rows
        } - set(base_rows_by_key) - allowed_new_keys
        if unexpected:
            raise AOVResourceMergeError(
                f"{resource_name}: overlay có key skillcombine ngoài allowlist: "
                + ", ".join(f"0x{key:08x}" for key in sorted(unexpected))
                + "."
            )
    elif normalized_name == _CHARACTER_COMPONENT_PATH:
        if (
            base_table.field_count != 188
            or overlay_table.field_count != 188
        ):
            raise AOVResourceMergeError(
                f"{resource_name}: số trường khác chuẩn 188 hoặc schema không hợp lệ; "
                f"nhận {base_table.field_count}/{overlay_table.field_count}; chặn ghép."
            )

        def rows_by_key(rows, label):
            indexed = {}
            for row in rows:
                if len(row) < 4:
                    raise AOVResourceMergeError(
                        f"{resource_name}: bản ghi {label} quá ngắn."
                    )
                key = int.from_bytes(row[:4], "little")
                if key in indexed:
                    raise AOVResourceMergeError(
                        f"{resource_name}: khóa ResCharacterComponent trùng "
                        f"trong {label}: 0x{key:08x}."
                    )
                indexed[key] = row
            return indexed

        base_rows_by_key = rows_by_key(base_table.rows, "base")
        overlay_rows_by_key = rows_by_key(overlay_table.rows, "overlay")
        common_keys = set(base_rows_by_key) & set(overlay_rows_by_key)
        differing_keys = {
            key for key in common_keys
            if base_rows_by_key[key] != overlay_rows_by_key[key]
        }
        if differing_keys:
            raise AOVResourceMergeError(
                f"{resource_name}: hàng chung khác nội dung, chưa có quy tắc ghi đè: "
                + ", ".join(f"0x{key:08x}" for key in sorted(differing_keys)[:20])
                + "."
            )
        allowed_new_keys = set(overlay_rows_by_key) - set(base_rows_by_key)
    elif (
        normalized_name == "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes"
        and allow_p9_p10_label_union
    ):
        base_keys = {
            int.from_bytes(row[:4], "little") for row in base_table.rows
        }
        overlay_keys = {
            int.from_bytes(row[:4], "little") for row in overlay_table.rows
        }
        allowed_new_keys = overlay_keys - base_keys
    elif normalized_name == "Databin/Client/Actor/ResSkinSeniorLabelCfg.bytes":
        allowed_new_keys = {59500}
        base_rows_by_key = {
            int.from_bytes(row[:4], "little"): row for row in base_table.rows
        }
        overlay_rows_by_key = {
            int.from_bytes(row[:4], "little"): row for row in overlay_table.rows
        }
        if 59500 not in overlay_rows_by_key:
            raise AOVResourceMergeError(
                f"{resource_name}: overlay thiếu bản ghi skin 59500 đã xác minh."
            )
        # This adapter has evidence for adding this key, not replacing an
        # existing opaque row. Never report success while silently retaining
        # a conflicting base record for the target skin.
        if (
            59500 in base_rows_by_key
            and base_rows_by_key[59500] != overlay_rows_by_key[59500]
        ):
            raise AOVResourceMergeError(
                f"{resource_name}: key 59500 đã tồn tại nhưng payload khác; "
                "chưa có quy tắc ghi đè an toàn."
            )
    # New opaque rows are admitted only for the exact source-observed keys above;
    # all other overlay-only keys remain rejected by merge_mses_rows.

    try:
        merged_decoded, report = merge_mses_rows(
            base_decoded,
            overlay_decoded,
            override_keys=override_keys,
            allow_schema_mismatch=True,
            allow_new_rows_across_schema_mismatch=bool(allowed_new_keys),
            allowed_new_keys=allowed_new_keys,
            verify_base_checksum=not allow_p9_p10_label_union,
            verify_overlay_checksum=not allow_p9_p10_label_union,
        )
        merged_resource = encode_aov_resource(merged_decoded, resource_name, base_mode)
        # Decode and parse once more so a successful return means the wrapper,
        # encryption and MSES checksum all survive a round trip.
        verify_decoded, verify_mode = decode_aov_resource(merged_resource, resource_name)
        verify_table = parse_mses(verify_decoded)
    except (AOVBytesError, MSESError) as exc:
        raise AOVResourceMergeError(f"{resource_name}: không thể xác minh bản ghép: {exc}") from exc

    report.update({
        "base_encoding": base_mode,
        "overlay_encoding": overlay_mode,
        "output_encoding": verify_mode,
        "output_bytes": len(merged_resource),
        "verified": True,
        "base_checksum_repaired": base_checksum_repaired,
        "overlay_checksum_repaired": overlay_checksum_repaired,
    })
    return merged_resource, report
