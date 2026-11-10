"""AoV resource wrapper crypto helpers.

Supports the documented resource-name-derived AES-CBC wrapper (22 4A 67 xx)
and the existing AoV Zstandard wrapper decoder. This is a format decoder, not
a semantic merger for MSES tables or Unity asset bundles.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AES_MAGIC = b"\x22\x4a\x67"
AES_BASE_KEY = bytes.fromhex(
    "99 64 b1 b0 6b 03 8d 7f b7 7d b6 a7 54 90 8b 73"
)
ZERO_IV = b"\x00" * 16
MSES_MAGIC = b"MSES"
ZSTD_WRAPPER_MAGIC = b"\x22\x4a\x00\xef"
MAX_DECODE_BYTES = 128 * 1024 * 1024


class AoVResourceError(ValueError):
    """Unsupported or invalid AoV resource wrapper."""


@dataclass(frozen=True)
class MSESHeader:
    version: int
    field_count: int
    row_count: int


def resource_key(resource_name: str) -> bytes:
    """Derive the 16-byte AoV AES key from the resource basename."""
    stem = resource_name.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    value = 0
    for character in stem.upper():
        value = ((value * 31) + ord(character)) & 0xFFFFFFFF
    mixed = bytearray(AES_BASE_KEY)
    for index in range(len(mixed)):
        mixed[index] ^= (value >> ((index & 3) * 8)) & 0xFF
    return bytes(mixed)


def _aes_cbc_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    if not ciphertext or len(ciphertext) % 16:
        raise AoVResourceError("AES-CBC ciphertext không đủ khối 16 byte.")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(ZERO_IV)).decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    padding = plaintext[-1]
    if padding < 1 or padding > 16 or plaintext[-padding:] != bytes([padding]) * padding:
        raise AoVResourceError("AES-CBC sai khóa hoặc padding không hợp lệ.")
    return plaintext[:-padding]


def _aes_cbc_encrypt(plaintext: bytes, key: bytes) -> bytes:
    padding = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(key), modes.CBC(ZERO_IV)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_aov_resource(resource_name: str, data: bytes) -> tuple[bytes, bytes]:
    """Return (plaintext, original 4th magic byte) for 22 4A 67 wrappers."""
    if len(data) < 24 or not data.startswith(AES_MAGIC):
        raise AoVResourceError("Không phải tài nguyên AES AoV 22 4A 67.")
    marker = data[3:4]
    expected_size = struct.unpack("<I", data[4:8])[0]
    plaintext = _aes_cbc_decrypt(data[8:], resource_key(resource_name))
    if expected_size and len(plaintext) != expected_size:
        raise AoVResourceError(
            f"Kích thước giải mã không khớp: header={expected_size}, "
            f"thực tế={len(plaintext)}."
        )
    return plaintext, marker


def encrypt_aov_resource(
    resource_name: str, plaintext: bytes, *, marker: bytes = b"\x00"
) -> bytes:
    """Encrypt plaintext using the AoV resource-name AES-CBC wrapper."""
    if len(marker) != 1:
        raise ValueError("marker phải đúng 1 byte.")
    ciphertext = _aes_cbc_encrypt(plaintext, resource_key(resource_name))
    return AES_MAGIC + marker + struct.pack("<I", len(plaintext)) + ciphertext


def parse_mses_header(data: bytes) -> MSESHeader:
    """Read the fixed MSES v7 header; does not parse table records."""
    if len(data) < 16 or data[:4] != MSES_MAGIC:
        raise AoVResourceError("Không tìm thấy header MSES.")
    version, fields, rows = struct.unpack_from("<III", data, 4)
    if version != 7:
        raise AoVResourceError(f"MSES version chưa hỗ trợ: {version}.")
    if fields == 0 or fields > 10000 or rows > 10_000_000:
        raise AoVResourceError("Số field/record trong header MSES bất thường.")
    return MSESHeader(version=version, field_count=fields, row_count=rows)


def peek_mses_header(resource_name: str, prefix: bytes) -> MSESHeader | None:
    """Inspect a table header without reading/decrypting the entire resource."""
    if prefix.startswith(MSES_MAGIC):
        return parse_mses_header(prefix)
    if prefix.startswith(AES_MAGIC) and len(prefix) >= 24:
        marker = prefix[3:4]
        if marker != b"\x00":
            return None
        block = prefix[8:24]
        decryptor = Cipher(
            algorithms.AES(resource_key(resource_name)), modes.CBC(ZERO_IV)
        ).decryptor()
        first_plain_block = decryptor.update(block) + decryptor.finalize()
        if first_plain_block.startswith(MSES_MAGIC):
            return parse_mses_header(first_plain_block)
    return None


def decode_aov_resource(resource_name: str, data: bytes) -> bytes:
    """Decode supported AES and Zstandard wrappers, preserving raw assets."""
    if len(data) > MAX_DECODE_BYTES:
        raise AoVResourceError("Tài nguyên vượt quá giới hạn giải mã 128 MB.")
    if data.startswith(AES_MAGIC):
        plaintext, marker = decrypt_aov_resource(resource_name, data)
        if marker == b"\xef":
            data = plaintext
        else:
            return plaintext
    if data.startswith(ZSTD_WRAPPER_MAGIC):
        from .aov_zstd import AoVZstdError, decode_aov_bytes

        try:
            return decode_aov_bytes(data)
        except AoVZstdError as exc:
            raise AoVResourceError(str(exc)) from exc
    return data


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
