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
    allow_new_rows_across_schema_mismatch: bool = False,
    allowed_new_keys: set[int] | None = None,
    allow_retained_conflict_keys: set[int] | None = None,
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
    overrides = set(override_keys or ())
    if any(type(key) is not int or not 0 <= key <= 0xFFFFFFFF for key in overrides):
        raise MSESError("Khóa ghi đè MSES phải là uint32 hợp lệ.")
    missing_override_keys = overrides - set(overlay_map)
    if missing_override_keys:
        formatted = ", ".join(f"0x{key:08x}" for key in sorted(missing_override_keys))
        raise MSESError(
            f"Khóa ghi đè MSES không tồn tại trong overlay: {formatted}."
        )
    retained_overrides = set(allow_retained_conflict_keys or ())
    if any(
        type(key) is not int or not 0 <= key <= 0xFFFFFFFF
        for key in retained_overrides
    ):
        raise MSESError("Danh sách khóa xung đột giữ bản nền phải là uint32 hợp lệ.")
    missing_retained_keys = retained_overrides - common
    if missing_retained_keys:
        formatted = ", ".join(
            f"0x{key:08x}" for key in sorted(missing_retained_keys)
        )
        raise MSESError(
            f"Khóa xung đột giữ bản nền không phải bản ghi chung: {formatted}."
        )
    overlap_retained_keys = retained_overrides & overrides
    if overlap_retained_keys:
        formatted = ", ".join(
            f"0x{key:08x}" for key in sorted(overlap_retained_keys)
        )
        raise MSESError(
            f"Khóa không thể vừa ghi đè vừa giữ bản nền: {formatted}."
        )

    new_keys = set(overlay_map) - set(base_map)
    if allowed_new_keys is not None and any(
        type(key) is not int or not 0 <= key <= 0xFFFFFFFF for key in allowed_new_keys
    ):
        raise MSESError("Danh sách khóa MSES mới cho phép phải là uint32 hợp lệ.")
    if new_keys:
        if allowed_new_keys is None:
            if base.field_count != overlay.field_count:
                raise MSESError(
                    "Schema MSES khác nhau và overlay có bản ghi mới chưa được "
                    "chứng minh tương thích; cần adapter theo từng bảng."
                )
            raise MSESError(
                "Overlay có khóa mới nhưng chưa có allowlist theo bảng."
            )
        if not new_keys.issubset(allowed_new_keys):
            unexpected = sorted(new_keys - allowed_new_keys)
            formatted = ", ".join(f"0x{key:08x}" for key in unexpected)
            raise MSESError(
                "Overlay có khóa mới ngoài allowlist theo bảng: " + formatted + "."
            )
        if base.field_count != overlay.field_count and not allow_new_rows_across_schema_mismatch:
            raise MSESError(
                "Schema MSES khác nhau và bản ghi mới chưa được bật cho phép "
                "theo adapter bảng cụ thể."
            )

    if base.field_count != overlay.field_count:
        unsafe_differing_common = differing_common - overrides - retained_overrides
        if unsafe_differing_common:
            raise MSESError(
                "Schema MSES khác nhau và có bản ghi chung không được cho phép ghi đè; "
                "từ chối ghép an toàn."
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


def repair_mses_declared_row_count(
    data: bytes,
    *,
    expected_missing_keys: set[int],
) -> tuple[bytes, dict]:
    """Repair a stale row-count header only when the complete payload is row-aligned.

    The caller must supply the exact expected key gaps, verified against another
    source/version of the same table. No row payload is synthesized or discarded.
    """
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise MSESError("Không nhận diện được header MSES để sửa row count.")
    version, field_count, declared_count = struct.unpack_from("<III", data, 4)
    if version != 7 or not 0 < field_count <= 10000:
        raise MSESError("Header MSES không thuộc schema hỗ trợ.")
    rows: list[bytes] = []
    offset = HEADER_SIZE
    while offset < len(data):
        if offset + 4 > len(data):
            raise MSESError("Payload MSES kết thúc giữa trường độ dài bản ghi.")
        row_size = struct.unpack_from("<I", data, offset)[0]
        start = offset + 4
        end = start + row_size
        if row_size < 4 or row_size > MAX_MSES_ROW_BYTES or end > len(data):
            raise MSESError("Payload MSES có bản ghi cuối bị thiếu hoặc sai độ dài.")
        rows.append(data[start:end])
        offset = end
    if offset != len(data) or len(rows) >= declared_count:
        raise MSESError("Row count không phải trường hợp stale-count có thể sửa an toàn.")
    indexed: dict[int, bytes] = {}
    for row in rows:
        key = struct.unpack_from("<I", row)[0]
        if key in indexed:
            raise MSESError(f"Khóa MSES trùng khi sửa row count: 0x{key:08x}.")
        indexed[key] = row
    if not indexed:
        raise MSESError("Không thể sửa bảng MSES rỗng bằng row-count repair.")
    gaps = set(range(min(indexed), max(indexed) + 1)) - set(indexed)
    expected = set(expected_missing_keys)
    if any(type(key) is not int or not 0 <= key <= 0xFFFFFFFF for key in expected):
        raise MSESError("Expected missing keys phải là uint32 hợp lệ.")
    if gaps != expected:
        raise MSESError(
            "Các khóa thiếu trong MSES không khớp allowlist đã xác minh; từ chối sửa."
        )
    table = MSESTable(
        version=version,
        field_count=field_count,
        rows=tuple(rows),
        header=data[:HEADER_SIZE],
        checksum_valid=(
            data[CHECKSUM_START:CHECKSUM_END]
            == hashlib.md5(data[HEADER_SIZE:]).hexdigest().encode("ascii")
        ),
    )
    repaired = build_mses(table, rows)
    verified = parse_mses(repaired)
    return repaired, {
        "declared_row_count": declared_count,
        "repaired_row_count": len(verified.rows),
        "expected_missing_keys": [f"0x{key:08x}" for key in sorted(expected)],
        "source_checksum_valid": table.checksum_valid,
        "output_checksum_valid": verified.checksum_valid,
        "payload_complete": True,
        "rows_synthesized": 0,
    }


