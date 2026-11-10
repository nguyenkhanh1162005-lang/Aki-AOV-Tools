from __future__ import annotations

from pathlib import Path

import pyzstd
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .aov_zstd_dict import ZSTD_DICT

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
ZSTD_HEADER = b"\x22\x4a\x00\xef"
AES_HEADER = b"\x22\x4a\x67"
_AES_BASE_KEY = bytes.fromhex("99 64 b1 b0 6b 03 8d 7f b7 7d b6 a7 54 90 8b 73")
_AES_IV = bytes(16)
_ZSTD_DICT = pyzstd.ZstdDict(ZSTD_DICT, is_raw=True)


class AOVBytesError(ValueError):
    pass


def _resource_stem(resource_name: str) -> str:
    return Path(resource_name.replace("\\", "/")).stem


def _aes_key(resource_name: str) -> bytes:
    name = _resource_stem(resource_name).upper()
    value = 0
    for char in name:
        value = ((value * 31) + ord(char)) & 0xFFFFFFFF
    hash_bytes = (
        value & 0xFF,
        (value >> 8) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 24) & 0xFF,
    )
    return bytes(byte ^ hash_bytes[index & 3] for index, byte in enumerate(_AES_BASE_KEY))


def decrypt_aes_resource(data: bytes, resource_name: str, *, verify_size: bool = True) -> bytes:
    if len(data) < 24 or data[:3] != AES_HEADER:
        raise AOVBytesError("Không nhận diện được header AES 22 4A 67.")
    ciphertext = data[8:]
    if not ciphertext or len(ciphertext) % AES.block_size:
        raise AOVBytesError("Độ dài dữ liệu AES không hợp lệ.")
    try:
        decryptor = AES.new(_aes_key(resource_name), AES.MODE_CBC, _AES_IV)
        decoded = unpad(decryptor.decrypt(ciphertext), AES.block_size)
    except ValueError as exc:
        raise AOVBytesError("Giải mã AES thất bại: sai tên tài nguyên hoặc dữ liệu hỏng.") from exc
    expected_size = int.from_bytes(data[4:8], "little")
    if verify_size and decoded.find(ZSTD_MAGIC) < 0 and len(decoded) != expected_size:
        raise AOVBytesError(
            f"Kích thước AES sau giải mã không khớp header: {len(decoded)} != {expected_size}."
        )
    return decoded


def encrypt_aes_resource(
    data: bytes,
    resource_name: str,
    *,
    expected_size: int | None = None,
    marker: int = 0,
) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("data phải là bytes.")
    encryptor = AES.new(_aes_key(resource_name), AES.MODE_CBC, _AES_IV)
    ciphertext = encryptor.encrypt(pad(data, AES.block_size))
    size = len(data) if expected_size is None else expected_size
    return AES_HEADER + bytes([marker]) + size.to_bytes(4, "little") + ciphertext


def _decompress_zstd_frame(frame: bytes, expected_size: int | None = None) -> bytes:
    offset = frame.find(ZSTD_MAGIC)
    if offset < 0:
        raise AOVBytesError("Không tìm thấy khung Zstandard trong tài nguyên AoV.")
    try:
        decoded = pyzstd.decompress(frame[offset:], _ZSTD_DICT)
    except pyzstd.ZstdError as exc:
        raise AOVBytesError("Giải nén Zstandard thất bại: sai dictionary hoặc dữ liệu hỏng.") from exc
    if expected_size is not None and expected_size and len(decoded) != expected_size:
        raise AOVBytesError(
            f"Kích thước Zstandard sau giải nén không khớp header: {len(decoded)} != {expected_size}."
        )
    return decoded


def decompress_aov_bytes(data: bytes) -> bytes:
    """Decode an AoV Zstandard-wrapped resource (22 4A 00 EF)."""
    if len(data) < 8 or data[:2] != b"\x22\x4a":
        raise AOVBytesError("Không nhận diện được header tài nguyên AoV.")
    expected_size = int.from_bytes(data[4:8], "little")
    return _decompress_zstd_frame(data, expected_size)


def compress_aov_bytes(data: bytes, level: int = 17) -> bytes:
    """Encode a decoded payload using the AoV Zstandard wrapper."""
    if not isinstance(data, bytes):
        raise TypeError("data phải là bytes.")
    compressed = pyzstd.compress(data, level, _ZSTD_DICT)
    return ZSTD_HEADER + len(data).to_bytes(4, "little") + compressed


def decode_aov_resource(data: bytes, resource_name: str) -> tuple[bytes, str]:
    """Decode AES, Zstandard, or AES-wrapped Zstandard AoV resource bytes."""
    if data[:3] == AES_HEADER:
        expected_size = int.from_bytes(data[4:8], "little")
        intermediate = decrypt_aes_resource(data, resource_name, verify_size=False)
        if intermediate.find(ZSTD_MAGIC) >= 0:
            return _decompress_zstd_frame(intermediate, expected_size), "aes+zstd"
        if len(intermediate) != expected_size:
            raise AOVBytesError(
                f"Kích thước AES sau giải mã không khớp header: {len(intermediate)} != {expected_size}."
            )
        return intermediate, "aes"
    if data[:2] == b"\x22\x4a" and data.find(ZSTD_MAGIC) >= 0:
        return decompress_aov_bytes(data), "zstd"
    raise AOVBytesError("Định dạng .bytes chưa được hỗ trợ hoặc dữ liệu không hợp lệ.")


def encode_aov_resource(data: bytes, resource_name: str, mode: str, level: int = 17) -> bytes:
    if mode == "aes":
        return encrypt_aes_resource(data, resource_name)
    if mode == "aes+zstd":
        compressed = pyzstd.compress(data, level, _ZSTD_DICT)
        return encrypt_aes_resource(compressed, resource_name, expected_size=len(data), marker=0xEF)
    if mode == "zstd":
        compressed = pyzstd.compress(data, level, _ZSTD_DICT)
        return ZSTD_HEADER + len(data).to_bytes(4, "little") + compressed
    raise AOVBytesError(f"Kiểu mã hóa không được hỗ trợ: {mode}.")
