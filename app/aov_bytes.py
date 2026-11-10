from __future__ import annotations

from pathlib import Path

import pyzstd
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .aov_zstd_dict import ZSTD_DICT

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
ZSTD_HEADER = b"\x22\x4a\x00\xef"
AES_HEADER = b"\x22\x4a\x67\x00"
AES_ZSTD_HEADER = b"\x22\x4a\x67\xef"
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


def _decompress_frame(frame: bytes) -> bytes:
    try:
        return pyzstd.decompress(frame, _ZSTD_DICT)
    except pyzstd.ZstdError as exc:
        raise AOVBytesError("Giải nén Zstandard thất bại: sai dictionary hoặc dữ liệu hỏng.") from exc


def _compress_frame(data: bytes, level: int = 17) -> bytes:
    return pyzstd.compress(data, level, _ZSTD_DICT)


def decrypt_aes_resource(data: bytes, resource_name: str) -> bytes:
    if len(data) < 24 or data[:3] != b"\x22\x4a\x67":
        raise AOVBytesError("Không nhận diện được header AES 22 4A 67.")
    flag = data[3]
    if flag not in (0x00, 0xEF):
        raise AOVBytesError(f"Flag AES AoV chưa hỗ trợ: 0x{flag:02X}.")
    ciphertext = data[8:]
    if not ciphertext or len(ciphertext) % AES.block_size:
        raise AOVBytesError("Độ dài dữ liệu AES không hợp lệ.")
    try:
        decryptor = AES.new(_aes_key(resource_name), AES.MODE_CBC, _AES_IV)
        decrypted = unpad(decryptor.decrypt(ciphertext), AES.block_size)
    except ValueError as exc:
        raise AOVBytesError("Giải mã AES thất bại: sai tên tài nguyên hoặc dữ liệu hỏng.") from exc

    expected_size = int.from_bytes(data[4:8], "little")
    if flag == 0xEF:
        if not decrypted.startswith(ZSTD_MAGIC):
            raise AOVBytesError("Payload AES-Zstandard không có magic Zstandard hợp lệ.")
        decoded = _decompress_frame(decrypted)
    else:
        decoded = decrypted

    if len(decoded) != expected_size:
        raise AOVBytesError(
            f"Kích thước sau giải mã không khớp header: {len(decoded)} != {expected_size}."
        )
    return decoded


def _encrypt_aes_payload(payload: bytes, expected_size: int, resource_name: str, header: bytes) -> bytes:
    encryptor = AES.new(_aes_key(resource_name), AES.MODE_CBC, _AES_IV)
    ciphertext = encryptor.encrypt(pad(payload, AES.block_size))
    return header + expected_size.to_bytes(4, "little") + ciphertext


def encrypt_aes_resource(data: bytes, resource_name: str) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("data phải là bytes.")
    return _encrypt_aes_payload(data, len(data), resource_name, AES_HEADER)


def encrypt_aes_zstd_resource(data: bytes, resource_name: str, level: int = 17) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("data phải là bytes.")
    compressed = _compress_frame(data, level)
    return _encrypt_aes_payload(compressed, len(data), resource_name, AES_ZSTD_HEADER)


def decompress_aov_bytes(data: bytes) -> bytes:
    """Decode an AoV Zstandard-wrapped resource (22 4A 00 EF)."""
    if len(data) < 8 or data[:2] != b"\x22\x4a":
        raise AOVBytesError("Không nhận diện được header tài nguyên AoV.")
    frame_offset = data.find(ZSTD_MAGIC)
    if frame_offset < 0:
        raise AOVBytesError("Tệp không chứa khung Zstandard AoV đã nhận diện.")
    expected_size = int.from_bytes(data[4:8], "little")
    decoded = _decompress_frame(data[frame_offset:])
    if expected_size and len(decoded) != expected_size:
        raise AOVBytesError(
            f"Kích thước Zstandard sau giải nén không khớp header: {len(decoded)} != {expected_size}."
        )
    return decoded


def compress_aov_bytes(data: bytes, level: int = 17) -> bytes:
    """Encode a decoded payload using the AoV Zstandard wrapper."""
    if not isinstance(data, bytes):
        raise TypeError("data phải là bytes.")
    return ZSTD_HEADER + len(data).to_bytes(4, "little") + _compress_frame(data, level)


def decode_aov_resource(data: bytes, resource_name: str) -> tuple[bytes, str]:
    """Decode AoV Zstandard, AES, or AES+Zstandard resource bytes."""
    if data[:3] == b"\x22\x4a\x67":
        mode = "aes-zstd" if data[3] == 0xEF else "aes"
        return decrypt_aes_resource(data, resource_name), mode
    if data[:2] == b"\x22\x4a" and data.find(ZSTD_MAGIC) >= 0:
        return decompress_aov_bytes(data), "zstd"
    raise AOVBytesError("Định dạng .bytes chưa được hỗ trợ hoặc dữ liệu không hợp lệ.")


def encode_aov_resource(data: bytes, resource_name: str, mode: str) -> bytes:
    if mode == "aes":
        return encrypt_aes_resource(data, resource_name)
    if mode == "aes-zstd":
        return encrypt_aes_zstd_resource(data, resource_name)
    if mode == "zstd":
        return compress_aov_bytes(data)
    raise AOVBytesError(f"Kiểu mã hóa không được hỗ trợ: {mode}.")
