from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

MAGIC = b"MSES"
HEADER_SIZE = 0x8C
CHECKSUM_START = 0x60
CHECKSUM_END = 0x80
MAX_MSES_ROW_BYTES = 64 * 1024 * 1024


class MSESError(ValueError):
    pass


@dataclass(frozen=True)
class MSESTable:
    version: int
    field_count: int
    rows: tuple[bytes, ...]
    header: bytes
    checksum_valid: bool


def parse_mses(data: bytes, *, verify_checksum: bool = True) -> MSESTable:
    """Parse the length-prefixed MSES v7 table envelope without interpreting row fields."""
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise MSESError("Không nhận diện được header MSES.")
    version, field_count, row_count = struct.unpack_from("<III", data, 4)
    if version != 7:
        raise MSESError(f"Chỉ hỗ trợ MSES version 7, nhận được {version}.")
    if not 0 < field_count <= 10000 or not 0 <= row_count <= 2_000_000:
        raise MSESError("Số trường hoặc số bản ghi MSES không hợp lệ.")
    stored = data[CHECKSUM_START:CHECKSUM_END]
    actual = hashlib.md5(data[HEADER_SIZE:]).hexdigest().encode("ascii")
    checksum_valid = stored == actual
    if verify_checksum and not checksum_valid:
        raise MSESError("MD5 phần dữ liệu MSES không khớp header.")
    rows: list[bytes] = []
    offset = HEADER_SIZE
    for index in range(row_count):
        if offset + 4 > len(data):
            raise MSESError(f"Thiếu độ dài bản ghi MSES thứ {index}.")
        row_size = struct.unpack_from("<I", data, offset)[0]
        start = offset + 4
        end = start + row_size
        if row_size < 4 or row_size > MAX_MSES_ROW_BYTES or end > len(data):
            raise MSESError(f"Độ dài bản ghi MSES thứ {index} không hợp lệ.")
        rows.append(data[start:end])
        offset = end
    if offset != len(data):
        raise MSESError(f"MSES còn {len(data) - offset} byte dư sau bản ghi cuối.")
    return MSESTable(version, field_count, tuple(rows), data[:HEADER_SIZE], checksum_valid)


def build_mses(table: MSESTable, rows: list[bytes] | tuple[bytes, ...], *, field_count: int | None = None) -> bytes:
    """Rebuild an MSES v7 table and refresh row count and body MD5."""
    selected_fields = table.field_count if field_count is None else field_count
    if not 0 < selected_fields <= 10000:
        raise MSESError("Số trường MSES mới không hợp lệ.")
    body = bytearray()
    for row in rows:
        if not isinstance(row, bytes) or len(row) < 4 or len(row) > MAX_MSES_ROW_BYTES:
            raise MSESError("Bản ghi MSES có kích thước không hợp lệ.")
        body.extend(struct.pack("<I", len(row)))
        body.extend(row)
    header = bytearray(table.header)
    struct.pack_into("<III", header, 4, table.version, selected_fields, len(rows))
    header[CHECKSUM_START:CHECKSUM_END] = hashlib.md5(body).hexdigest().encode("ascii")
    return bytes(header + body)


def merge_mses_rows(
    base_data: bytes,
    overlay_data: bytes,
    *,
    override_keys: set[int] | None = None,
    allow_schema_mismatch: bool = False,
    verify_base_checksum: bool = True,
    verify_overlay_checksum: bool = True,
) -> tuple[bytes, dict]:
    """Merge rows by their leading uint32 key, retaining base rows unless explicitly overridden.

    Schema mismatches are accepted only when every shared key has byte-identical row data.
    This lets a newer base table retain its schema while adding proven-compatible rows from
    an older/specialized overlay.
    """
    base = parse_mses(base_data, verify_checksum=verify_base_checksum)
    overlay = parse_mses(overlay_data, verify_checksum=verify_overlay_checksum)
    if base.version != overlay.version:
        raise MSESError("Không thể ghép hai phiên bản MSES khác nhau.")
    if base.field_count != overlay.field_count and not allow_schema_mismatch:
        raise MSESError(
            f"Schema MSES khác nhau: base={base.field_count}, overlay={overlay.field_count}."
        )

    def index_rows(rows: tuple[bytes, ...], label: str) -> tuple[dict[int, bytes], list[int]]:
        indexed: dict[int, bytes] = {}
        order: list[int] = []
        for row in rows:
            if len(row) < 4:
                raise MSESError(f"Bản ghi {label} quá ngắn để đọc khóa.")
            key = struct.unpack_from("<I", row)[0]
            if key in indexed:
                raise MSESError(f"Khóa MSES trùng trong {label}: 0x{key:08x}.")
            indexed[key] = row
            order.append(key)
        return indexed, order

    base_map, base_order = index_rows(base.rows, "base")
    overlay_map, overlay_order = index_rows(overlay.rows, "overlay")
    common = set(base_map).intersection(overlay_map)
    differing_common = {key for key in common if base_map[key] != overlay_map[key]}
    if base.field_count != overlay.field_count and differing_common:
        raise MSESError(
            "Schema MSES khác nhau và có bản ghi chung khác nội dung; từ chối ghép an toàn."
        )

    overrides = set(override_keys or ())
    if overrides:
        if base.field_count != overlay.field_count:
            raise MSESError(
                "Không thể ghi đè khóa MSES khi schema giữa base và overlay khác nhau."
            )
        missing_override_keys = overrides - common
        if missing_override_keys:
            formatted = ", ".join(f"0x{key:08x}" for key in sorted(missing_override_keys))
            raise MSESError(
                f"Khóa ghi đè MSES không tồn tại ở cả base và overlay: {formatted}."
            )

    merged = dict(base_map)
    replaced = []
    appended = []
    retained_conflicts = []
    for key in overlay_order:
        candidate = overlay_map[key]
        if key not in merged:
            merged[key] = candidate
            appended.append(key)
        elif key in overrides:
            merged[key] = candidate
            if base_map[key] != candidate:
                replaced.append(key)
        elif base_map[key] != candidate:
            retained_conflicts.append(key)

    ordered_keys = [key for key in base_order]
    ordered_keys.extend(key for key in overlay_order if key not in base_map)
    result_rows = [merged[key] for key in ordered_keys]
    output = build_mses(base, result_rows, field_count=base.field_count)
    # Verify the generated table before returning it.
    verified = parse_mses(output)
    report = {
        "base_fields": base.field_count,
        "overlay_fields": overlay.field_count,
        "output_fields": verified.field_count,
        "base_rows": len(base.rows),
        "overlay_rows": len(overlay.rows),
        "output_rows": len(verified.rows),
        "appended_keys": [f"0x{x:08x}" for x in appended],
        "replaced_keys": [f"0x{x:08x}" for x in replaced],
        "retained_base_conflicts": [f"0x{x:08x}" for x in retained_conflicts],
        "shared_rows_identical": len(common) - len(differing_common),
        "base_checksum_valid": base.checksum_valid,
        "overlay_checksum_valid": overlay.checksum_valid,
    }
    return output, report
