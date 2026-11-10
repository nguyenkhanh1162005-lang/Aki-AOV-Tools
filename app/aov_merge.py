from __future__ import annotations

from .aov_bytes import AOVBytesError, decode_aov_resource, encode_aov_resource
from .aov_mses import MSESError, build_mses, merge_mses_rows, parse_mses


class AOVResourceMergeError(ValueError):
    pass


def merge_aov_mses_resource(
    base_resource: bytes,
    overlay_resource: bytes,
    resource_name: str,
    *,
    allow_invalid_base_checksum: bool = False,
) -> tuple[bytes, dict]:
    """Merge the known MSES v7 AoV tables while retaining the base package's schema.

    The sound-bank tables receive VTK's Hero_LiXin_Skin_C rows by key. Other
    overlay-only rows (the 595 skill-combine rows and senior-label row) are
    appended only after the MSES merger confirms that every shared row is
    byte-identical.
    """
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
    if resource_name.replace("\\", "/").endswith(("/BattleBank.bytes", "/LobbyBank.bytes")):
        for row in overlay_table.rows:
            if b"Hero_LiXin_Skin_C" in row:
                override_keys.add(int.from_bytes(row[:4], "little"))
        if not override_keys:
            raise AOVResourceMergeError(
                f"{resource_name}: overlay không có bản ghi Hero_LiXin_Skin_C để ghép."
            )

    try:
        merged_decoded, report = merge_mses_rows(
            base_decoded,
            overlay_decoded,
            override_keys=override_keys,
            allow_schema_mismatch=True,
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
