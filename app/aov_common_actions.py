from __future__ import annotations

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


def merge_action_xml(
    base_bytes: bytes,
    overlay_bytes: bytes,
    resource_name: str,
) -> tuple[bytes, dict]:
    """Merge only overlay tracks explicitly gated to VTK skin IDs 59500/59501."""
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
    index: dict[tuple[str, str], list[ET.Element]] = {}
    for track in base_tracks:
        key = (_normalized_track_name(track), track.attrib.get("eventType", ""))
        index.setdefault(key, []).append(track)

    replaced = []
    appended = []
    already_present = []
    for source_track in overlay_tracks:
        if not _is_595_track(source_track):
            continue
        name = _normalized_track_name(source_track)
        event_type = source_track.attrib.get("eventType", "")
        key = (name, event_type)
        candidates = index.get(key, [])
        target = next((item for item in candidates if not _is_595_track(item)), None)
        if target is not None:
            replacement = ET.fromstring(ET.tostring(source_track, encoding="utf-8"))
            replacement.attrib["trackName"] = name
            for attr in ("guid", "useRefParam", "refParamName"):
                if attr in target.attrib and attr not in replacement.attrib:
                    replacement.attrib[attr] = target.attrib[attr]
            target_position = list(base_action).index(target)
            base_action.remove(target)
            base_action.insert(target_position, replacement)
            # The original node is gone; keep the index coherent for any repeated overlay name.
            index[key] = [replacement if item is target else item for item in candidates]
            replaced.append(name)
        elif any(_is_595_track(item) for item in candidates):
            already_present.append(name)
        else:
            addition = ET.fromstring(ET.tostring(source_track, encoding="utf-8"))
            addition.attrib["trackName"] = name
            addition.attrib.setdefault(
                "guid",
                str(uuid.uuid5(_UUID_NAMESPACE, f"{resource_name}:{name}:{event_type}")),
            )
            addition.attrib.setdefault("useRefParam", "false")
            addition.attrib.setdefault("refParamName", "")
            base_action.append(addition)
            index.setdefault(key, []).append(addition)
            appended.append(name)

    if not replaced and not appended and not already_present:
        raise CommonActionsMergeError(
            f"{resource_name}: overlay không có track tham chiếu SkinOrAvatarList 59500/59501."
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
