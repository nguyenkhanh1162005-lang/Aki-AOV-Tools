"""MSES v7 length-prefixed row parser and skin-scoped table merger."""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from .aov_crypto import AES_MAGIC, ZSTD_WRAPPER_MAGIC, AoVResourceError, decode_aov_resource, encrypt_aov_resource
from .aov_zstd import AoVZstdError, encode_aov_bytes

MSES_MAGIC = b"MSES"
FIXED_HEADER_SIZE = 140
MAX_ROWS = 10_000_000
MAX_ROW_BYTES = 64 * 1024 * 1024


class MSESError(ValueError):
    """Invalid or unsupported MSES table."""


@dataclass(frozen=True)
class MSESTable:
    version: int
    field_count: int
    row_count: int
    data_offset: int
    header: bytes
    rows: tuple[bytes, ...]


@dataclass(frozen=True)
class MSESMergeReport:
    rows_added: int
    rows_replaced: int
    rows_unchanged: int
    final_rows: int
    target_prefixes: tuple[str, ...]


def parse_mses(data: bytes) -> MSESTable:
    """Parse the MSES v7 header and length-prefixed row payloads."""
    if len(data) < FIXED_HEADER_SIZE or data[:4] != MSES_MAGIC:
        raise MSESError("Không phải bảng MSES hợp lệ.")
    version, fields, row_count = struct.unpack_from("<III", data, 4)
    if version != 7:
        raise MSESError(f"Chỉ hỗ trợ MSES v7, nhận được v{version}.")
    if not 1 <= fields <= 10000 or row_count > MAX_ROWS:
        raise MSESError("Field count hoặc row count bất thường.")
    data_offset = struct.unpack_from("<I", data, 132)[0]
    if data_offset < FIXED_HEADER_SIZE or data_offset > len(data):
        raise MSESError("Offset dữ liệu MSES không hợp lệ.")

    rows: list[bytes] = []
    keys: set[int] = set()
    position = data_offset
    for index in range(row_count):
        if position + 4 > len(data):
            raise MSESError(f"Thiếu độ dài record MSES tại dòng {index}.")
        row_length = struct.unpack_from("<I", data, position)[0]
        position += 4
        if row_length < 4 or row_length > MAX_ROW_BYTES or row_length > len(data) - position:
            raise MSESError(f"Độ dài record MSES không hợp lệ tại dòng {index}.")
        row = data[position:position + row_length]
        position += row_length
        key = struct.unpack_from("<I", row, 0)[0]
        if key in keys:
            raise MSESError(f"Trùng khóa record MSES: {key}.")
        keys.add(key)
        rows.append(row)

    if position != len(data):
        raise MSESError(
            f"Còn {len(data) - position} byte dư sau {row_count} record MSES."
        )
    return MSESTable(
        version=version,
        field_count=fields,
        row_count=row_count,
        data_offset=data_offset,
        header=data[:data_offset],
        rows=tuple(rows),
    )


def _matches_prefix(key: int, prefixes: tuple[str, ...]) -> bool:
    text = str(key)
    return any(text.startswith(prefix) for prefix in prefixes)


def merge_mses(
    base_data: bytes,
    incoming_data: bytes,
    *,
    target_prefixes: tuple[str, ...] | list[str],
) -> tuple[bytes, MSESMergeReport]:
    """Merge only rows belonging to target hero-ID prefixes.

    Existing non-target rows remain byte-for-byte from the base table. Matching
    target rows are replaced by the incoming version; new target rows are appended.
    """
    prefixes = tuple(sorted({str(prefix).strip() for prefix in target_prefixes if str(prefix).strip()}))
    if not prefixes or any(not prefix.isdigit() or len(prefix) < 3 for prefix in prefixes):
        raise MSESError("Cần ít nhất một tiền tố ID tướng dạng số, tối thiểu 3 chữ số.")

    base = parse_mses(base_data)
    incoming = parse_mses(incoming_data)
    if base.version != incoming.version:
        raise MSESError("Hai bảng MSES khác phiên bản.")
    if base.data_offset != incoming.data_offset:
        raise MSESError("Hai bảng MSES có offset dữ liệu khác nhau.")

    rows = list(base.rows)
    index = {struct.unpack_from("<I", row, 0)[0]: position for position, row in enumerate(rows)}
    added = replaced = unchanged = 0

    for row in incoming.rows:
        key = struct.unpack_from("<I", row, 0)[0]
        if not _matches_prefix(key, prefixes):
            continue
        if key in index:
            row_index = index[key]
            if rows[row_index] == row:
                unchanged += 1
            else:
                rows[row_index] = row
                replaced += 1
        else:
            index[key] = len(rows)
            rows.append(row)
            added += 1

    serialized_rows = b"".join(
        struct.pack("<I", len(row)) + row for row in rows
    )
    header = bytearray(base.header)
    struct.pack_into("<I", header, 12, len(rows))
    if len(header) < 128:
        raise MSESError("Header MSES quá ngắn để cập nhật checksum.")
    header[96:128] = hashlib.md5(serialized_rows).hexdigest().encode("ascii")
    result = bytes(header) + serialized_rows

    # Read-back validation: row count, keys, bounds and checksum must all agree.
    verified = parse_mses(result)
    if len(verified.rows) != len(rows):
        raise MSESError("Kiểm tra lại MSES sau ghép không khớp số record.")
    if hashlib.md5(result[verified.data_offset:]).hexdigest().encode("ascii") != result[96:128]:
        raise MSESError("Checksum MSES sau ghép không khớp.")

    return result, MSESMergeReport(
        rows_added=added,
        rows_replaced=replaced,
        rows_unchanged=unchanged,
        final_rows=len(rows),
        target_prefixes=prefixes,
    )


def merge_aov_mses_resource(
    resource_name: str,
    base_payload: bytes,
    incoming_payload: bytes,
    *,
    target_prefixes: tuple[str, ...] | list[str],
) -> tuple[bytes, MSESMergeReport]:
    """Decode, merge and re-wrap an AoV MSES resource using the base wrapper type."""
    try:
        base_plain = decode_aov_resource(resource_name, base_payload)
        incoming_plain = decode_aov_resource(resource_name, incoming_payload)
    except AoVResourceError as exc:
        raise MSESError(f"Không giải mã được tài nguyên {resource_name}: {exc}") from exc

    merged_plain, report = merge_mses(
        base_plain,
        incoming_plain,
        target_prefixes=target_prefixes,
    )
    try:
        if base_payload.startswith(AES_MAGIC):
            marker = base_payload[3:4]
            if marker == bytes.fromhex("ef"):
                return encrypt_aov_resource(
                    resource_name,
                    encode_aov_bytes(merged_plain),
                    marker=marker,
                ), report
            return encrypt_aov_resource(
                resource_name, merged_plain, marker=marker
            ), report
        if base_payload.startswith(ZSTD_WRAPPER_MAGIC):
            return encode_aov_bytes(merged_plain), report
        if base_payload.startswith(MSES_MAGIC):
            return merged_plain, report
    except (AoVResourceError, AoVZstdError) as exc:
        raise MSESError(f"Không đóng gói lại được {resource_name}: {exc}") from exc
    raise MSESError(f"Wrapper gốc không được hỗ trợ: {resource_name}")
