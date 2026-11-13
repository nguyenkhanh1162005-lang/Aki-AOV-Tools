from __future__ import annotations

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


def merge_aov_mses_resource(
    base_resource: bytes,
    overlay_resource: bytes,
    resource_name: str,
    *,
    allow_invalid_base_checksum: bool = False,
) -> tuple[bytes, dict]:
    """Merge the known MSES v7 AoV tables while retaining the base package's schema.

    New opaque rows are admitted only by an exact table-specific key allowlist.
    Sound banks admit only rows carrying the VTK Hero_LiXin_Skin_C marker;
    ResSkinSeniorLabelCfg admits only the observed 59500 row. ResCharacterComponent
    allows only a same-schema union when every shared row is byte-identical.
    skillcombine admits only its source-verified Actor 595 keys.
    """
    normalized_name = resource_name.replace("\\", "/").lstrip("/")
    if normalized_name not in _SUPPORTED_MSES_TABLES:
        raise AOVResourceMergeError(
            f"{resource_name}: không có adapter MSES theo bảng đã xác minh."
        )

    try:
        base_decoded, base_mode = decode_aov_resource(base_resource, resource_name)
        overlay_decoded, overlay_mode = decode_aov_resource(overlay_resource, resource_name)
        base_checksum_repaired = False
        if allow_invalid_base_checksum:
            if not resource_name.replace("\\", "/").endswith("/ResSkinSeniorLabelCfg.bytes"):
                raise MSESError("Chỉ cho phép sửa checksum nền cho ResSkinSeniorLabelCfg.")
            base_table = parse_mses(base_decoded, verify_checksum=False)
            if base_table.field_count != 69 or len(base_table.rows) != 53:
                raise MSESError("ResSkinSeniorLabelCfg nền không đúng cấu trúc đã kiểm tra.")
            base_decoded = build_mses(base_table, base_table.rows)
            base_table = parse_mses(base_decoded)
            base_checksum_repaired = True
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
            verify_base_checksum=True,
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
    })
    return merged_resource, report
