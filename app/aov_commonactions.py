"""Format-aware merge for AoV CommonActions.pkg.bytes.

Only merges XML action tracks that explicitly reference the selected skin IDs.
All other base package entries are retained unchanged; incoming non-target tracks
are not copied over the base package.
"""
from __future__ import annotations

import copy
import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from .aov_zstd import AoVZstdError, WRAPPER_MAGIC, decode_aov_bytes, encode_aov_bytes

MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 10000
DEFAULT_HERO_PREFIXES = ("595",)


class CommonActionsMergeError(ValueError):
    """CommonActions package is invalid or cannot be safely merged."""


@dataclass(frozen=True)
class CommonActionsMergeReport:
    changed_files: tuple[str, ...]
    tracks_added: int
    skipped_conflicting_files: tuple[str, ...]


def _safe_member_name(name: str) -> None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise CommonActionsMergeError(f"Đường dẫn PKG không an toàn: {name}")


def _read_package(data: bytes) -> tuple[zipfile.ZipFile, io.BytesIO]:
    if len(data) > MAX_PACKAGE_BYTES:
        raise CommonActionsMergeError("CommonActions PKG vượt quá 64 MB.")
    buffer = io.BytesIO(data)
    try:
        archive = zipfile.ZipFile(buffer, "r")
        if len(archive.infolist()) > MAX_PACKAGE_ENTRIES:
            archive.close()
            raise CommonActionsMergeError("CommonActions PKG có quá nhiều mục.")
        return archive, buffer
    except (OSError, zipfile.BadZipFile) as exc:
        raise CommonActionsMergeError("CommonActions.pkg.bytes không phải ZIP hợp lệ.") from exc


def _merge_xml(base_payload: bytes, incoming_payload: bytes, member_name: str, hero_prefixes: tuple[str, ...]) -> tuple[bytes, int]:
    if not base_payload.startswith(WRAPPER_MAGIC) or not incoming_payload.startswith(WRAPPER_MAGIC):
        raise CommonActionsMergeError(
            f"Không nhận diện được wrapper Zstandard của XML: {member_name}"
        )
    try:
        base_xml = decode_aov_bytes(base_payload)
        incoming_xml = decode_aov_bytes(incoming_payload)
        base_root = ET.fromstring(base_xml)
        incoming_root = ET.fromstring(incoming_xml)
    except (AoVZstdError, ET.ParseError) as exc:
        raise CommonActionsMergeError(f"Không giải mã/đọc được XML {member_name}: {exc}") from exc

    base_action = base_root.find("Action")
    incoming_action = incoming_root.find("Action")
    if base_action is None or incoming_action is None:
        raise CommonActionsMergeError(f"XML thiếu node Action: {member_name}")

    existing_names = {
        track.attrib.get("trackName")
        for track in base_action.findall("Track")
        if track.attrib.get("trackName")
    }
    pattern = re.compile(
        rb"(?<![0-9])(?:" + b"|".join(
            re.escape(prefix.encode("ascii")) + rb"\\d{2}"
            for prefix in hero_prefixes
        ) + rb")(?![0-9])"
    )
    added = 0
    for incoming_track in incoming_action.findall("Track"):
        serialized_track = ET.tostring(incoming_track, encoding="utf-8")
        if not pattern.search(serialized_track):
            continue
        track_name = incoming_track.attrib.get("trackName")
        if not track_name or track_name in existing_names:
            continue

        # Some AoV track records omit these attributes. Add the required defaults
        # and a fresh GUID instead of reusing a GUID from another package.
        if not incoming_track.attrib.get("guid"):
            incoming_track.set("guid", str(uuid.uuid4()))
        incoming_track.set("useRefParam", incoming_track.attrib.get("useRefParam", "false"))
        incoming_track.set("refParamName", incoming_track.attrib.get("refParamName", ""))

        base_action.append(copy.deepcopy(incoming_track))
        existing_names.add(track_name)
        added += 1

    if not added:
        return base_payload, 0

    serialized = ET.tostring(
        base_root,
        encoding="utf-8",
        xml_declaration=True,
    )
    try:
        return encode_aov_bytes(serialized), added
    except AoVZstdError as exc:
        raise CommonActionsMergeError(f"Không nén lại được XML {member_name}: {exc}") from exc


def merge_common_actions(
    base_package: bytes,
    incoming_package: bytes,
    *,
    hero_prefixes: tuple[str, ...] | list[str] = DEFAULT_HERO_PREFIXES,
) -> tuple[bytes, CommonActionsMergeReport]:
    """Append incoming tracks referencing skin IDs belonging to target hero prefixes."""
    prefixes = tuple(sorted({str(p).strip() for p in hero_prefixes if str(p).strip()}))
    if not prefixes or any(not p.isdigit() or len(p) < 3 for p in prefixes):
        raise CommonActionsMergeError("Cần tiền tố ID tướng dạng số, tối thiểu 3 chữ số.")
    base_archive, base_buffer = _read_package(base_package)
    incoming_archive, incoming_buffer = _read_package(incoming_package)
    try:
        base_infos = [info for info in base_archive.infolist() if not info.is_dir()]
        incoming_info_list = [
            info for info in incoming_archive.infolist() if not info.is_dir()
        ]
        if len({info.filename for info in base_infos}) != len(base_infos):
            raise CommonActionsMergeError("Base CommonActions PKG có đường dẫn lặp.")
        if len({info.filename for info in incoming_info_list}) != len(incoming_info_list):
            raise CommonActionsMergeError("PKG đầu vào có đường dẫn lặp.")
        incoming_infos = {info.filename: info for info in incoming_info_list}
        base_payloads: dict[str, bytes] = {}
        output_infos = list(base_infos)
        changed: list[str] = []
        skipped: list[str] = []
        total_tracks = 0

        for info in base_infos:
            _safe_member_name(info.filename)
            base_payloads[info.filename] = base_archive.read(info)

        for name, incoming_info in incoming_infos.items():
            _safe_member_name(name)
            if name not in base_payloads:
                base_payloads[name] = incoming_archive.read(incoming_info)
                output_infos.append(copy.copy(incoming_info))
                continue
            base_payload = base_payloads[name]
            incoming_payload = incoming_archive.read(incoming_info)
            if base_payload == incoming_payload:
                continue
            if not name.lower().endswith(".xml"):
                skipped.append(name)
                continue
            if not (
                base_payload.startswith(WRAPPER_MAGIC)
                and incoming_payload.startswith(WRAPPER_MAGIC)
            ):
                skipped.append(name)
                continue
            merged_payload, added = _merge_xml(base_payload, incoming_payload, name, prefixes)
            if added:
                base_payloads[name] = merged_payload
                changed.append(name)
                total_tracks += added
            else:
                skipped.append(name)

        output_buffer = io.BytesIO()
        with zipfile.ZipFile(output_buffer, "w", allowZip64=True) as output:
            output.comment = base_archive.comment
            for info in output_infos:
                output.writestr(copy.copy(info), base_payloads[info.filename])

        return output_buffer.getvalue(), CommonActionsMergeReport(
            changed_files=tuple(changed),
            tracks_added=total_tracks,
            skipped_conflicting_files=tuple(sorted(set(skipped))),
        )
    finally:
        base_archive.close()
        incoming_archive.close()
        base_buffer.close()
        incoming_buffer.close()
