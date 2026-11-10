from __future__ import annotations

import copy
import hashlib
import io
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET

from .aov_bytes import AOVBytesError, decode_aov_resource, encode_aov_resource


SPECIAL_IDS = {"59500", "59501"}
_UUID_NAMESPACE = uuid.UUID("c4f8b1a2-57c4-4f6b-9e45-98e0d4a7a2a1")


class CommonActionsMergeError(ValueError):
    pass


def _is_595_track(track: ET.Element) -> bool:
    return any(
        node.tag == "SkinOrAvatarList" and node.attrib.get("id") in SPECIAL_IDS
        for node in track.iter()
    )


def _normalized_track_name(track: ET.Element) -> str:
    return re.sub(r"^\d+_", "", track.attrib.get("trackName", ""))


def _track_signature(track: ET.Element) -> str:
    clone = copy.deepcopy(track)
    clone.set("trackName", _normalized_track_name(clone))
    clone.attrib.pop("guid", None)
    for node in clone.iter():
        for attr in list(node.attrib):
            if attr.lower() in {"guid", "m_guid"}:
                node.attrib.pop(attr, None)
        for child in list(node):
            if child.tag == "SkinOrAvatarList":
                node.remove(child)
        if node.attrib:
            ordered = sorted(node.attrib.items())
            node.attrib.clear()
            node.attrib.update(ordered)
    return hashlib.sha256(ET.tostring(clone, encoding="utf-8")).hexdigest()


def _track_key(track: ET.Element) -> tuple[str, str, str]:
    return (
        _normalized_track_name(track),
        track.attrib.get("eventType", ""),
        _track_signature(track),
    )


def _unique_track_name(original: str, used: set[str], next_index: list[int]) -> str:
    if original and original not in used:
        return original
    suffix = re.sub(r"^\d+_", "", original)
    while True:
        candidate = f"{next_index[0]}_{suffix or 'VTKTrack'}"
        next_index[0] += 1
        if candidate not in used:
            return candidate


def merge_action_xml(
    base_bytes: bytes,
    overlay_bytes: bytes,
    resource_name: str,
) -> tuple[bytes, dict]:
    """Merge VTK-gated tracks by full event signature without dropping same-name variants."""
    try:
        base_decoded, base_mode = decode_aov_resource(base_bytes, resource_name)
        overlay_decoded, overlay_mode = decode_aov_resource(overlay_bytes, resource_name)
        base_root = ET.fromstring(base_decoded)
        overlay_root = ET.fromstring(overlay_decoded)
    except (AOVBytesError, ET.ParseError) as exc:
        raise CommonActionsMergeError(f"{resource_name}: XML/codec không hợp lệ: {exc}") from exc

    base_action = base_root.find("Action")
    overlay_action = overlay_root.find("Action")
    if base_action is None or overlay_action is None:
        raise CommonActionsMergeError(f"{resource_name}: thiếu node Action.")

    base_tracks = [node for node in list(base_action) if node.tag == "Track"]
    overlay_tracks = [node for node in list(overlay_action) if node.tag == "Track"]
    index: dict[tuple[str, str, str], list[ET.Element]] = {}
    for track in base_tracks:
        index.setdefault(_track_key(track), []).append(track)

    used_names = {
        track.attrib.get("trackName", "")
        for track in base_tracks
        if track.attrib.get("trackName")
    }
    numeric_prefixes = []
    for track in base_tracks + overlay_tracks:
        match = re.match(r"^(\d+)_", track.attrib.get("trackName", ""))
        if match:
            numeric_prefixes.append(int(match.group(1)))
    next_index = [max(numeric_prefixes, default=-1) + 1]

    replaced = []
    appended = []
    already_present = []
    merged_into_existing = []

    for source_track in overlay_tracks:
        if not _is_595_track(source_track):
            continue
        name = _normalized_track_name(source_track)
        key = _track_key(source_track)
        candidates = index.get(key, [])
        if candidates:
            target = candidates[0]
            existing_nodes = {
                node.attrib.get("id"): node
                for node in target.findall("SkinOrAvatarList")
            }
            changed = False
            for source_node in source_track.findall("SkinOrAvatarList"):
                skin_id = source_node.attrib.get("id")
                current = existing_nodes.get(skin_id)
                cloned = copy.deepcopy(source_node)
                if current is None:
                    target.append(cloned)
                    existing_nodes[skin_id] = cloned
                    changed = True
                elif current.attrib != cloned.attrib:
                    position = list(target).index(current)
                    target.remove(current)
                    target.insert(position, cloned)
                    existing_nodes[skin_id] = cloned
                    changed = True

            if changed:
                if _is_595_track(target):
                    merged_into_existing.append(name)
                else:
                    replaced.append(name)
            else:
                already_present.append(name)
            continue

        addition = copy.deepcopy(source_track)
        original_name = addition.attrib.get("trackName", "")
        addition.attrib["trackName"] = _unique_track_name(original_name, used_names, next_index)
        addition.attrib.setdefault(
            "guid",
            str(uuid.uuid5(_UUID_NAMESPACE, f"{resource_name}:{key[2]}")),
        )
        addition.attrib.setdefault("useRefParam", "false")
        addition.attrib.setdefault("refParamName", "")
        base_action.append(addition)
        used_names.add(addition.attrib["trackName"])
        index.setdefault(key, []).append(addition)
        appended.append(name)

    if not replaced and not appended and not already_present and not merged_into_existing:
        raise CommonActionsMergeError(
            f"{resource_name}: overlay không có track tham chiếu SkinOrAvatarList 59500/59501."
        )

    # Every source event variant and every special skin ID must remain represented.
    output_by_key: dict[tuple[str, str, str], set[str]] = {}
    for track in base_action.iter("Track"):
        key = _track_key(track)
        output_by_key.setdefault(key, set()).update(
            node.attrib.get("id")
            for node in track.iter("SkinOrAvatarList")
            if node.attrib.get("id") in SPECIAL_IDS
        )
    for source_track in overlay_action.iter("Track"):
        if not _is_595_track(source_track):
            continue
        key = _track_key(source_track)
        source_ids = {
            node.attrib.get("id")
            for node in source_track.iter("SkinOrAvatarList")
            if node.attrib.get("id") in SPECIAL_IDS
        }
        if not source_ids.issubset(output_by_key.get(key, set())):
            raise CommonActionsMergeError(
                f"{resource_name}: thiếu skin IDs {sorted(source_ids - output_by_key.get(key, set()))} "
                f"trong track {key[0]}."
            )

    output_xml = ET.tostring(base_root, encoding="utf-8", xml_declaration=True)
    try:
        output = encode_aov_resource(output_xml, resource_name, base_mode)
        verify_decoded, verify_mode = decode_aov_resource(output, resource_name)
        ET.fromstring(verify_decoded)
    except (AOVBytesError, ET.ParseError) as exc:
        raise CommonActionsMergeError(f"{resource_name}: không xác minh được XML sau ghép: {exc}") from exc

    return output, {
        "base_encoding": base_mode,
        "overlay_encoding": overlay_mode,
        "output_encoding": verify_mode,
        "replaced_tracks": replaced,
        "appended_tracks": appended,
        "already_present_tracks": already_present,
        "merged_into_existing_tracks": merged_into_existing,
        "verified": True,
    }


def merge_common_actions_pkg(base_package: bytes, overlay_package: bytes) -> tuple[bytes, dict]:
    """Retain the base package and selectively merge VTK-gated common action tracks."""
    try:
        base_zip = zipfile.ZipFile(io.BytesIO(base_package), "r")
        overlay_zip = zipfile.ZipFile(io.BytesIO(overlay_package), "r")
    except zipfile.BadZipFile as exc:
        raise CommonActionsMergeError("CommonActions.pkg.bytes không phải ZIP hợp lệ.") from exc

    merge_candidates = {
        "commonresource/Back.xml",
        "commonresource/BackFast.xml",
        "commonresource/HasteE1.xml",
        "commonresource/HasteE1_super.xml",
        "commonresource/HasteE2.xml",
        "commonresource/HasteE3.xml",
    }
    base_entries = {item.filename: item for item in base_zip.infolist() if not item.is_dir()}
    overlay_entries = {item.filename: item for item in overlay_zip.infolist() if not item.is_dir()}
    output_buffer = io.BytesIO()
    merged_files = {}
    appended_files = []

    try:
        with zipfile.ZipFile(output_buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as out:
            for name, info in base_entries.items():
                base_data = base_zip.read(info)
                if name in merge_candidates and name in overlay_entries:
                    merged, report = merge_action_xml(
                        base_data,
                        overlay_zip.read(overlay_entries[name]),
                        name,
                    )
                    base_data = merged
                    merged_files[name] = report
                target = zipfile.ZipInfo(name, date_time=info.date_time)
                target.compress_type = zipfile.ZIP_DEFLATED
                target.external_attr = info.external_attr
                target.create_system = info.create_system
                out.writestr(target, base_data)

            for name, info in overlay_entries.items():
                if name in base_entries:
                    continue
                target = zipfile.ZipInfo(name, date_time=info.date_time)
                target.compress_type = zipfile.ZIP_DEFLATED
                target.external_attr = info.external_attr
                target.create_system = info.create_system
                out.writestr(target, overlay_zip.read(info))
                appended_files.append(name)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise CommonActionsMergeError(f"Không thể ghép CommonActions: {exc}") from exc
    finally:
        base_zip.close()
        overlay_zip.close()

    result = output_buffer.getvalue()
    try:
        with zipfile.ZipFile(io.BytesIO(result), "r") as verify:
            bad = verify.testzip()
            if bad:
                raise CommonActionsMergeError(f"ZIP CommonActions lỗi CRC tại {bad}.")
    except zipfile.BadZipFile as exc:
        raise CommonActionsMergeError("ZIP CommonActions sau ghép không hợp lệ.") from exc

    return result, {
        "base_entries": len(base_entries),
        "overlay_entries": len(overlay_entries),
        "output_entries": len(base_entries) + len(appended_files),
        "merged_files": merged_files,
        "appended_files": appended_files,
        "verified": True,
    }
