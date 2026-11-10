from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any


MAX_BNK_BYTES = 512 * 1024 * 1024
MAX_BNK_CHUNKS = 4096
MAX_HIRC_OBJECTS = 2_000_000
KNOWN_CHUNKS = {
    "BKHD", "DIDX", "DATA", "HIRC", "STID", "INIT", "STMG", "PLAT",
    "ENVS", "FXPR", "STP ", "BANK", "BUSS", "USER", "UNKN",
}


class WwiseBNKError(ValueError):
    """Raised when a Wwise SoundBank has an invalid or unsupported structure."""


@dataclass(frozen=True)
class BNKChunk:
    chunk_id: str
    offset: int
    size: int


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise WwiseBNKError("Wwise BNK bị cắt cụt khi đọc số nguyên.")
    return struct.unpack_from("<I", data, offset)[0]


def _parse_chunks(data: bytes) -> list[BNKChunk]:
    if len(data) > MAX_BNK_BYTES:
        raise WwiseBNKError("Wwise BNK vượt quá giới hạn 512 MiB.")
    if len(data) < 8:
        raise WwiseBNKError("Wwise BNK quá ngắn để chứa chunk.")
    chunks: list[BNKChunk] = []
    offset = 0
    while offset < len(data):
        if len(chunks) >= MAX_BNK_CHUNKS:
            raise WwiseBNKError("Wwise BNK có quá nhiều chunk.")
        if offset + 8 > len(data):
            raise WwiseBNKError("Wwise BNK có header chunk bị cắt cụt.")
        try:
            chunk_id = data[offset:offset + 4].decode("ascii")
        except UnicodeDecodeError as exc:
            raise WwiseBNKError("Wwise BNK có mã chunk không phải ASCII.") from exc
        size = _u32(data, offset + 4)
        payload_offset = offset + 8
        end = payload_offset + size
        if end > len(data):
            raise WwiseBNKError(f"Chunk {chunk_id!r} vượt quá cuối file.")
        chunks.append(BNKChunk(chunk_id, payload_offset, size))
        offset = end
    return chunks


def inspect_wwise_bnk(data: bytes) -> dict[str, Any]:
    """Read-only structural inventory; does not rewrite or merge SoundBanks."""
    chunks = _parse_chunks(data)
    by_id: dict[str, list[BNKChunk]] = {}
    for chunk in chunks:
        by_id.setdefault(chunk.chunk_id, []).append(chunk)

    headers = by_id.get("BKHD", [])
    if len(headers) != 1:
        raise WwiseBNKError("Wwise BNK phải có đúng một chunk BKHD.")
    header = headers[0]
    if header.size < 8:
        raise WwiseBNKError("Chunk BKHD thiếu version hoặc bank ID.")
    version = _u32(data, header.offset)
    bank_id = _u32(data, header.offset + 4)

    didx_chunks = by_id.get("DIDX", [])
    data_chunks = by_id.get("DATA", [])
    if len(didx_chunks) > 1 or len(data_chunks) > 1:
        raise WwiseBNKError("Wwise BNK có nhiều chunk DIDX hoặc DATA không được hỗ trợ.")
    media: list[dict[str, int]] = []
    if didx_chunks:
        if not data_chunks:
            raise WwiseBNKError("Wwise BNK có DIDX nhưng thiếu DATA.")
        didx = didx_chunks[0]
        audio_data = data_chunks[0]
        if didx.size % 12:
            raise WwiseBNKError("Kích thước DIDX không chia hết cho bản ghi 12 byte.")
        intervals: list[tuple[int, int, int]] = []
        seen_media: set[int] = set()
        for pos in range(didx.offset, didx.offset + didx.size, 12):
            media_id, media_offset, media_size = struct.unpack_from("<III", data, pos)
            end = media_offset + media_size
            if media_id in seen_media:
                raise WwiseBNKError(f"DIDX có media ID trùng: {media_id}.")
            if end > audio_data.size:
                raise WwiseBNKError(f"Media ID {media_id} vượt quá kích thước DATA.")
            if media_size:
                intervals.append((media_offset, end, media_id))
            seen_media.add(media_id)
            media.append({"id": media_id, "offset": media_offset, "size": media_size})
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1]:
                raise WwiseBNKError(
                    f"DIDX có vùng media chồng lấn: {previous[2]} và {current[2]}."
                )

    hirc_chunks = by_id.get("HIRC", [])
    if len(hirc_chunks) > 1:
        raise WwiseBNKError("Wwise BNK có nhiều chunk HIRC không được hỗ trợ.")
    hirc_objects: list[dict[str, int]] = []
    if hirc_chunks:
        hirc = hirc_chunks[0]
        if hirc.size < 4:
            raise WwiseBNKError("Chunk HIRC thiếu số lượng object.")
        count = _u32(data, hirc.offset)
        if count > MAX_HIRC_OBJECTS:
            raise WwiseBNKError("HIRC có quá nhiều object.")
        cursor = hirc.offset + 4
        end = hirc.offset + hirc.size
        seen_objects: set[int] = set()
        for index in range(count):
            if cursor + 5 > end:
                raise WwiseBNKError(f"HIRC object {index} bị cắt cụt.")
            object_type = data[cursor]
            object_size = _u32(data, cursor + 1)
            body_start = cursor + 5
            body_end = body_start + object_size
            if object_size < 4 or body_end > end:
                raise WwiseBNKError(f"Kích thước HIRC object {index} không hợp lệ.")
            object_id = _u32(data, body_start)
            if object_id in seen_objects:
                raise WwiseBNKError(f"HIRC có object ID trùng: {object_id}.")
            seen_objects.add(object_id)
            hirc_objects.append({
                "type": object_type, "id": object_id, "size": object_size,
            })
            cursor = body_end
        if cursor != end:
            raise WwiseBNKError("HIRC có dữ liệu thừa sau object cuối cùng.")

    return {
        "format": "Wwise BNK",
        "version": version,
        "bank_id": bank_id,
        "chunks": [
            {"id": chunk.chunk_id, "size": chunk.size, "offset": chunk.offset}
            for chunk in chunks
        ],
        "unknown_chunks": sorted({chunk.chunk_id for chunk in chunks if chunk.chunk_id not in KNOWN_CHUNKS}),
        "media": media,
        "hirc_objects": hirc_objects,
        "media_count": len(media),
        "hirc_object_count": len(hirc_objects),
    }
