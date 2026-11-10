from __future__ import annotations

import pyzstd

from .aov_zstd_dict import ZSTD_DICT

MAGIC = b"\x28\xb5\x2f\xfd"
HEADER = b"\x22\x4a\x00\xef"
_ZSTD_DICT = pyzstd.ZstdDict(ZSTD_DICT, True)


class AOVBytesError(ValueError):
    pass


def decompress_aov_bytes(data: bytes) -> bytes:
    """Decode one AoV 22 4A wrapped Zstandard payload."""
    if len(data) < 8 or data[:2] != b"\x22\x4a":
        raise AOVBytesError("Không nhận diện được header tài nguyên AoV.")
    frame_offset = data.find(MAGIC)
    if frame_offset < 0:
        raise AOVBytesError("Tệp không chứa khung Zstandard AoV đã nhận diện.")
    expected_size = int.from_bytes(data[4:8], "little")
    try:
        decoded = pyzstd.decompress(data[frame_offset:], _ZSTD_DICT)
    except pyzstd.ZstdError as exc:
        raise AOVBytesError("Giải nén Zstandard thất bại: sai dictionary hoặc dữ liệu hỏng.") from exc
    if expected_size and len(decoded) != expected_size:
        raise AOVBytesError(
            f"Kích thước sau giải nén không khớp header: {len(decoded)} != {expected_size}."
        )
    return decoded


def compress_aov_bytes(data: bytes, level: int = 17) -> bytes:
    """Encode a decoded payload using the AoV Zstandard wrapper."""
    if not isinstance(data, bytes):
        raise TypeError("data phải là bytes.")
    compressed = pyzstd.compress(data, level, _ZSTD_DICT)
    return HEADER + len(data).to_bytes(4, "little") + compressed
