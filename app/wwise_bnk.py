from __future__ import annotations

import hashlib
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
            media_payload = data[audio_data.offset + media_offset:audio_data.offset + end]
            media.append({
                "id": media_id,
                "offset": media_offset,
                "size": media_size,
                "sha256": hashlib.sha256(media_payload).hexdigest(),
            })
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
        # Wwise bank versions <= 48 encode HIRC object type as u32;
        # newer versions use u8. The size field and body follow that type.
        object_type_width = 4 if version <= 48 else 1
        object_header_size = object_type_width + 4
        for index in range(count):
            if cursor + object_header_size > end:
                raise WwiseBNKError(f"HIRC object {index} bị cắt cụt.")
            if object_type_width == 4:
                object_type = _u32(data, cursor)
            else:
                object_type = data[cursor]
            object_size = _u32(data, cursor + object_type_width)
            body_start = cursor + object_header_size
            body_end = body_start + object_size
            if object_size < 4 or body_end > end:
                raise WwiseBNKError(f"Kích thước HIRC object {index} không hợp lệ.")
            object_id = _u32(data, body_start)
            if object_id in seen_objects:
                raise WwiseBNKError(f"HIRC có object ID trùng: {object_id}.")
            seen_objects.add(object_id)
            hirc_objects.append({
                "type": object_type, "id": object_id, "size": object_size,
                "sha256": hashlib.sha256(data[cursor:body_end]).hexdigest(),
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


def compare_wwise_bnk(base_data: bytes, overlay_data: bytes) -> dict[str, Any]:
    """Compare BNK identities and payload collisions without rewriting either bank.

    HIRC object references and event/action semantics are not decoded here, so this
    report is strictly advisory and can never authorize a SoundBank merge.
    """
    base = inspect_wwise_bnk(base_data)
    overlay = inspect_wwise_bnk(overlay_data)
    base_media = {item["id"]: item for item in base["media"]}
    overlay_media = {item["id"]: item for item in overlay["media"]}
    shared_media = sorted(set(base_media) & set(overlay_media))
    identical_media = [
        media_id for media_id in shared_media
        if base_media[media_id]["sha256"] == overlay_media[media_id]["sha256"]
    ]
    changed_media = [
        media_id for media_id in shared_media
        if base_media[media_id]["sha256"] != overlay_media[media_id]["sha256"]
    ]

    base_objects = {item["id"]: item for item in base["hirc_objects"]}
    overlay_objects = {item["id"]: item for item in overlay["hirc_objects"]}
    shared_objects = sorted(set(base_objects) & set(overlay_objects))
    identical_objects = [
        object_id for object_id in shared_objects
        if base_objects[object_id]["sha256"] == overlay_objects[object_id]["sha256"]
    ]
    changed_objects = [
        object_id for object_id in shared_objects
        if base_objects[object_id]["sha256"] != overlay_objects[object_id]["sha256"]
    ]
    object_type_conflicts = [
        object_id for object_id in shared_objects
        if base_objects[object_id]["type"] != overlay_objects[object_id]["type"]
    ]

    version_match = base["version"] == overlay["version"]
    bank_id_match = base["bank_id"] == overlay["bank_id"]
    base_sha256 = hashlib.sha256(base_data).hexdigest()
    overlay_sha256 = hashlib.sha256(overlay_data).hexdigest()
    whole_bank_identical = base_sha256 == overlay_sha256
    new_media_ids = sorted(set(overlay_media) - set(base_media))
    new_hirc_ids = sorted(set(overlay_objects) - set(base_objects))
    if not version_match:
        status = "VERSION_CONFLICT"
        reasons = ["Wwise SoundBank có version khác nhau."]
    elif not bank_id_match:
        status = "BANK_ID_CONFLICT"
        reasons = ["Bank ID khác nhau; đây là hai SoundBank identity khác nhau, không được xem là cùng bank để thay thế trực tiếp."]
    elif whole_bank_identical:
        status = "IDENTICAL_BANK_PAYLOAD"
        reasons = ["Toàn bộ BNK có SHA-256 giống nhau."]
    else:
        status = "NEEDS_RULE"
        if changed_media or changed_objects or object_type_conflicts:
            reasons = ["Có media ID hoặc HIRC object ID trùng nhưng payload khác nhau; cần remap và kiểm tra tham chiếu."]
        elif new_media_ids or new_hirc_ids:
            reasons = ["Có media ID hoặc HIRC object ID mới; cần giải quyết dependency và tham chiếu trước khi ghép."]
        else:
            reasons = ["Toàn bộ BNK khác SHA-256 nhưng phần khác biệt nằm ngoài các trường đã kiểm kê; cần adapter/kiểm tra sâu hơn."]

    return {
        "format": "Wwise BNK comparison",
        "status": status,
        "bank_versions_match": version_match,
        "bank_id_match": bank_id_match,
        "bank_payload_identical": whole_bank_identical,
        "base_bank_sha256": base_sha256,
        "overlay_bank_sha256": overlay_sha256,
        "bank_ids": {"base": base["bank_id"], "overlay": overlay["bank_id"]},
        "base_chunk_ids": [item["id"] for item in base["chunks"]],
        "overlay_chunk_ids": [item["id"] for item in overlay["chunks"]],
        "media": {
            "base_count": len(base_media),
            "overlay_count": len(overlay_media),
            "shared_ids": shared_media[:200],
            "identical_payload_ids": identical_media[:200],
            "changed_payload_ids": changed_media[:200],
            "new_overlay_ids": new_media_ids[:200],
        },
        "hirc": {
            "base_count": len(base_objects),
            "overlay_count": len(overlay_objects),
            "shared_ids": shared_objects[:200],
            "identical_payload_ids": identical_objects[:200],
            "changed_payload_ids": changed_objects[:200],
            "object_type_conflict_ids": object_type_conflicts[:200],
            "new_overlay_ids": new_hirc_ids[:200],
        },
        "advisory_only": True,
        "merge_authorized": False,
        "scope": "BKHD version/bank ID, DIDX media payload hashes and HIRC object payload hashes only; no HIRC reference graph, event semantics, serializer or in-game validation",
        "reasons": reasons,
    }

_LANGUAGE_SEGMENTS = {
    "arabic", "chinese", "chinesesimplified", "chinesetraditional",
    "dutch", "english", "englishus", "englishuk", "french", "german",
    "greek", "hindi", "indonesian", "italian", "japanese", "korean",
    "malay", "polish", "portuguese", "portuguesebrazil", "russian",
    "spanish", "thai", "turkish", "ukrainian", "vietnamese",
}


def _wwise_identity_scope(resource_path: str) -> str:
    """Partition IDs by platform and explicitly named localization directory.

    A global sound bank overlaps every platform/locale. A platform-local bank
    overlaps only that platform, and a localized bank overlaps only that
    platform + locale. Unknown directory names are not guessed to be locales.
    """
    parts = [part for part in str(resource_path).replace("\\", "/").split("/") if part]
    lowered = [part.casefold() for part in parts]
    for index, part in enumerate(lowered):
        if part != "sound" or index + 2 >= len(parts):
            continue
        platform = lowered[index + 1]
        if platform not in {"ios", "android"}:
            continue
        possible_locale = parts[index + 2]
        normalized_locale = "".join(
            character for character in possible_locale.casefold()
            if character.isalnum()
        )
        is_language = (
            normalized_locale in _LANGUAGE_SEGMENTS
            or bool(__import__("re").fullmatch(
                r"[a-z]{2,3}(?:[-_][a-z0-9]{2,8})?",
                possible_locale.casefold(),
            ))
        )
        has_bank_below_locale = index + 3 < len(parts)
        if is_language and has_bank_below_locale:
            return f"{platform}|{normalized_locale}"
        return f"{platform}|default"
    return "global"


def _wwise_scopes_overlap(left: str, right: str) -> bool:
    return left == "global" or right == "global" or left == right


def assess_wwise_bnk_overlay_set(
    base_banks: list[tuple[str, bytes]],
    overlay_banks: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """Authorize append-only BNK files only when their IDs need no remapping.

    This function never edits a BNK. It verifies Bank IDs, Media IDs and HIRC
    object IDs across every namespace that can be loaded together. Platform and
    explicitly named language directories are treated as mutually exclusive
    namespaces; global banks overlap all of them. Unknown chunks and external
    WEM references remain blocked because their IDs/references are not decoded.
    """
    if not overlay_banks:
        raise WwiseBNKError("Cần ít nhất một Wwise BNK overlay.")

    inventories: list[dict[str, Any]] = []
    seen_paths: dict[str, str] = {}
    all_inputs = [("base", item) for item in base_banks] + [
        ("overlay", item) for item in overlay_banks
    ]

    for role, (raw_path, payload) in all_inputs:
        path = str(raw_path).replace("\\", "/").lstrip("/")
        if not path or not isinstance(payload, bytes) or not payload:
            raise WwiseBNKError("Đường dẫn hoặc payload Wwise BNK rỗng/không hợp lệ.")
        digest = hashlib.sha256(payload).hexdigest()
        previous_path_digest = seen_paths.get(path)
        if previous_path_digest is not None:
            if previous_path_digest != digest:
                raise WwiseBNKError(
                    f"{path}: cùng đường dẫn Wwise BNK nhưng payload khác nhau."
                )
            raise WwiseBNKError(
                f"{path}: Wwise BNK bị đưa vào kiểm tra nhiều lần."
            )
        seen_paths[path] = digest
        try:
            report = inspect_wwise_bnk(payload)
        except WwiseBNKError as exc:
            raise WwiseBNKError(f"{path}: {exc}") from exc
        if report["unknown_chunks"]:
            raise WwiseBNKError(
                f"{path}: có chunk Wwise chưa được giải nghĩa "
                f"({', '.join(report['unknown_chunks'])}); không thể xác nhận ID namespace."
            )
        external_media = [
            item["id"] for item in report["media"] if item["size"] == 0
        ]
        if external_media:
            raise WwiseBNKError(
                f"{path}: có {len(external_media)} external media ID cần WEM riêng; "
                "luồng append-only hiện chưa xác minh cặp BNK/WEM."
            )
        inventories.append({
            "role": role,
            "path": path,
            "sha256": digest,
            "scope": _wwise_identity_scope(path),
            "report": report,
        })

    for index, current in enumerate(inventories):
        current_report = current["report"]
        for previous in inventories[:index]:
            if not _wwise_scopes_overlap(current["scope"], previous["scope"]):
                continue
            previous_report = previous["report"]
            if current_report["version"] != previous_report["version"]:
                raise WwiseBNKError(
                    f"{current['path']}: Wwise version {current_report['version']} "
                    f"không khớp {previous['path']} ({previous_report['version']})."
                )
            if current_report["bank_id"] == previous_report["bank_id"]:
                raise WwiseBNKError(
                    f"{current['path']}: Bank ID {current_report['bank_id']} "
                    f"đã được dùng bởi {previous['path']} trong namespace có thể tải cùng nhau."
                )

            previous_media = {
                item["id"]: item for item in previous_report["media"]
            }
            for item in current_report["media"]:
                old = previous_media.get(item["id"])
                if old and old["sha256"] != item["sha256"]:
                    raise WwiseBNKError(
                        f"{current['path']}: Media ID {item['id']} trùng nhưng payload khác "
                        f"với {previous['path']}."
                    )

            previous_objects = {
                item["id"]: item for item in previous_report["hirc_objects"]
            }
            for item in current_report["hirc_objects"]:
                old = previous_objects.get(item["id"])
                if old and (
                    old["type"] != item["type"]
                    or old["sha256"] != item["sha256"]
                ):
                    raise WwiseBNKError(
                        f"{current['path']}: HIRC object ID {item['id']} trùng nhưng "
                        f"payload/type khác với {previous['path']}."
                    )

    overlay_reports = [
        {
            "path": item["path"],
            "scope": item["scope"],
            "bank_id": item["report"]["bank_id"],
            "version": item["report"]["version"],
            "media_count": item["report"]["media_count"],
            "hirc_object_count": item["report"]["hirc_object_count"],
            "sha256": item["sha256"],
            "append_only": True,
        }
        for item in inventories if item["role"] == "overlay"
    ]
    return {
        "adapter": "wwise_bnk_append_only_disjoint_id_namespaces",
        "base_bank_count": sum(item["role"] == "base" for item in inventories),
        "overlay_bank_count": len(overlay_reports),
        "overlay_banks": overlay_reports,
        "identity_checks": [
            "Wwise version",
            "Bank ID",
            "Media ID payload SHA-256",
            "HIRC object ID/type/payload SHA-256",
            "platform and explicit locale namespace",
        ],
        "no_id_remapping_required": True,
        "unknown_chunks_allowed": False,
        "external_wem_allowed": False,
        "verified": True,
        "game_tested": False,
    }

