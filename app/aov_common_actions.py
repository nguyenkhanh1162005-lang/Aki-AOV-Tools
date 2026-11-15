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


def _is_special_track(track: ET.Element, special_ids: set[str]) -> bool:
    return any(
        node.tag == "SkinOrAvatarList" and node.attrib.get("id") in special_ids
        for node in track.iter()
    )


def _normalized_track_name(track: ET.Element) -> str:
    return re.sub(r"^\d+_", "", track.attrib.get("trackName", ""))


def _track_signature(track: ET.Element) -> str:
    clone = copy.deepcopy(track)
    clone.set("trackName", _normalized_track_name(clone))
    clone.attrib.pop("guid", None)
    clone.attrib.pop("useRefParam", None)
    clone.attrib.pop("refParamName", None)
    for node in clone.iter():
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None
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


_SKIN_CONDITION_EVENT_TYPES = {"CheckSkinIdTick", "CheckSkinIdVirtualTick"}


def _condition_skin_info(
    track: ET.Element,
    actor_prefixes: set[str] | None,
) -> dict | None:
    """Recognize the direct scalar/array skin-ID condition forms used by AoV XML.

    Only a single direct Event with a matching eventType and a direct targetId
    TemplateObject is accepted. Unknown/nested condition layouts fail closed.
    """
    event_type = track.attrib.get("eventType", "")
    if event_type not in _SKIN_CONDITION_EVENT_TYPES:
        return None
    if any(node.tag == "SkinOrAvatarList" for node in track.iter()):
        return None
    events = track.findall("Event")
    if len(events) != 1:
        raise CommonActionsMergeError(
            f"Track {_normalized_track_name(track)}: {event_type} phải có đúng một Event trực tiếp."
        )
    event = events[0]
    if event.attrib.get("eventName") != event_type:
        raise CommonActionsMergeError(
            f"Track {_normalized_track_name(track)}: eventName không khớp eventType {event_type}."
        )
    target_nodes = [
        node for node in list(event)
        if node.tag == "TemplateObject" and node.attrib.get("name") == "targetId"
    ]
    if len(target_nodes) != 1 or target_nodes[0].attrib.get("objectName") not in {"self", "target"}:
        raise CommonActionsMergeError(
            f"Track {_normalized_track_name(track)}: targetId không thuộc layout đã hỗ trợ."
        )

    scalar_nodes = [
        node for node in list(event)
        if node.tag == "int" and node.attrib.get("name") == "skinId"
    ]
    array_nodes = [
        node for node in list(event)
        if node.tag == "Array" and node.attrib.get("name") == "skinIdArray"
    ]
    if len(scalar_nodes) > 1 or len(array_nodes) > 1 or (not scalar_nodes and not array_nodes):
        raise CommonActionsMergeError(
            f"Track {_normalized_track_name(track)}: skinId/skinIdArray bị thiếu hoặc lặp."
        )

    scalar_id = None
    if scalar_nodes:
        scalar_id = scalar_nodes[0].attrib.get("value", "")
        if not scalar_id.isdigit():
            raise CommonActionsMergeError(
                f"Track {_normalized_track_name(track)}: skinId không phải số."
            )

    array_values: list[str] = []
    array_node = array_nodes[0] if array_nodes else None
    if array_node is not None:
        if array_node.attrib.get("type") != "int":
            raise CommonActionsMergeError(
                f"Track {_normalized_track_name(track)}: skinIdArray không phải Array<int>."
            )
        children = list(array_node)
        if not children or any(node.tag != "int" for node in children):
            raise CommonActionsMergeError(
                f"Track {_normalized_track_name(track)}: skinIdArray có child không hỗ trợ."
            )
        array_values = [node.attrib.get("value", "") for node in children]
        if any(not value.isdigit() for value in array_values):
            raise CommonActionsMergeError(
                f"Track {_normalized_track_name(track)}: skinIdArray có phần tử không phải số."
            )
        if len(array_values) != len(set(array_values)):
            raise CommonActionsMergeError(
                f"Track {_normalized_track_name(track)}: skinIdArray có skin ID trùng."
            )
        negate_nodes = [
            node for node in list(event)
            if node.tag == "bool" and node.attrib.get("name") == "useNegateValue"
        ]
        if len(negate_nodes) > 1 or (
            negate_nodes and negate_nodes[0].attrib.get("value") not in {"true", "false"}
        ):
            raise CommonActionsMergeError(
                f"Track {_normalized_track_name(track)}: useNegateValue không hợp lệ."
            )

    def is_owned(value: str) -> bool:
        return actor_prefixes is None or value in actor_prefixes or value[:3] in actor_prefixes

    owned_ids = {
        value for value in ([scalar_id] if scalar_id else []) + array_values
        if is_owned(value)
    }
    if not owned_ids:
        return None

    # An array-based condition can be safely unioned only when its scalar
    # discriminator is not itself actor-owned. The combined owned-scalar +
    # array variant is held back because its conjunction semantics need a
    # separate schema fixture.
    if array_node is not None and scalar_id and actor_prefixes is not None and is_owned(scalar_id):
        raise CommonActionsMergeError(
            f"Track {_normalized_track_name(track)}: kết hợp scalar skinId và skinIdArray "
            "cùng thuộc Actor chưa có quy tắc ghép đã xác minh."
        )

    return {
        "kind": "array" if array_node is not None else "scalar",
        "event_type": event_type,
        "scalar_id": scalar_id,
        "array_values": array_values,
        "owned_ids": owned_ids,
        "array_node": array_node,
    }


def _condition_array_template_signature(track: ET.Element) -> str:
    clone = copy.deepcopy(track)
    for event in clone.findall("Event"):
        for array_node in list(event.findall("Array")):
            if array_node.attrib.get("name") == "skinIdArray":
                for child in list(array_node):
                    array_node.remove(child)
    return _track_signature(clone)


def _condition_array_key(track: ET.Element) -> tuple[str, str, str]:
    return (
        _normalized_track_name(track),
        track.attrib.get("eventType", ""),
        _condition_array_template_signature(track),
    )


def _track_actor_gate_ids(
    track: ET.Element,
    actor_prefixes: set[str] | None,
) -> set[str]:
    skin_nodes = list(track.iter("SkinOrAvatarList"))
    direct_nodes = track.findall("SkinOrAvatarList")
    if skin_nodes and len(skin_nodes) != len(direct_nodes):
        raise CommonActionsMergeError(
            f"Track {_normalized_track_name(track)} có SkinOrAvatarList lồng ở vị trí chưa hỗ trợ."
        )
    if skin_nodes:
        result = set()
        for node in direct_nodes:
            value = node.attrib.get("id", "")
            if not value.isdigit():
                raise CommonActionsMergeError(
                    f"Track {_normalized_track_name(track)} có SkinOrAvatarList ID không phải số."
                )
            if actor_prefixes is None or value in actor_prefixes or value[:3] in actor_prefixes:
                result.add(value)
        return result
    condition = _condition_skin_info(track, actor_prefixes)
    return set(condition["owned_ids"]) if condition else set()


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
    *,
    special_ids: set[str] | None = None,
) -> tuple[bytes, dict]:
    """Merge actor-gated skin lists and verified scalar/array skin conditions.

    Supported condition forms are direct CheckSkinIdTick/CheckSkinIdVirtualTick
    events with targetId plus scalar skinId, or a typed skinIdArray whose values
    can be unioned under an otherwise identical condition. Unknown layouts fail
    closed; nested condition-index structures are never rewritten.
    """
    target_ids = {str(value) for value in (SPECIAL_IDS if special_ids is None else special_ids)}
    if not target_ids or any(not value.isdigit() for value in target_ids):
        raise CommonActionsMergeError(
            f"{resource_name}: special skin IDs phải là tập không rỗng gồm chuỗi số."
        )
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
    condition_appended = []
    condition_merged = []

    def append_track(track: ET.Element, key: tuple[str, str, str], label: str) -> None:
        addition = copy.deepcopy(track)
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
        appended.append(label)

    for source_track in overlay_tracks:
        direct_skin_nodes = source_track.findall("SkinOrAvatarList")
        all_skin_nodes = list(source_track.iter("SkinOrAvatarList"))
        if all_skin_nodes and len(direct_skin_nodes) != len(all_skin_nodes):
            raise CommonActionsMergeError(
                f"{resource_name}: track {_normalized_track_name(source_track)} "
                "có SkinOrAvatarList lồng ở vị trí chưa được hỗ trợ."
            )
        condition = None if all_skin_nodes else _condition_skin_info(source_track, target_ids)

        if direct_skin_nodes:
            source_ids = {
                node.attrib.get("id") for node in direct_skin_nodes
                if node.attrib.get("id") in target_ids
            }
            if not source_ids:
                continue
            filtered_track = copy.deepcopy(source_track)
            for node in list(filtered_track.findall("SkinOrAvatarList")):
                if node.attrib.get("id") not in target_ids:
                    filtered_track.remove(node)
            name = _normalized_track_name(filtered_track)
            key = _track_key(filtered_track)
            candidates = index.get(key, [])
            if candidates:
                target = candidates[0]
                was_special = _is_special_track(target, target_ids)
                existing_nodes = {
                    node.attrib.get("id"): node
                    for node in target.findall("SkinOrAvatarList")
                }
                changed = False
                for source_node in filtered_track.findall("SkinOrAvatarList"):
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
                for attr in ("useRefParam", "refParamName"):
                    if attr not in target.attrib and attr in source_track.attrib:
                        target.attrib[attr] = source_track.attrib[attr]
                        changed = True
                if changed:
                    if was_special:
                        merged_into_existing.append(name)
                    else:
                        replaced.append(name)
                else:
                    already_present.append(name)
                continue
            append_track(filtered_track, key, name)
            continue

        if not condition:
            continue

        name = _normalized_track_name(source_track)
        if condition["kind"] == "scalar":
            scalar_id = condition["scalar_id"]
            scalar_key = (name, condition["event_type"], scalar_id)
            candidates = [
                track for track in list(base_action)
                if track.tag == "Track"
                and _track_key(track) == _track_key(source_track)
                and (info := _condition_skin_info(track, None)) is not None
                and info["kind"] == "scalar"
                and info["scalar_id"] == scalar_id
            ]
            if candidates:
                already_present.append(name)
                continue
            scalar_track_key = _track_key(source_track)
            append_track(source_track, scalar_track_key, name)
            condition_appended.append(name)
            continue

        # Array<int> skinIdArray: union only the IDs owned by this overlay.
        source_array = condition["array_node"]
        owned_array_values = [
            value for value in condition["array_values"]
            if value in target_ids or value[:3] in target_ids
        ]
        if not owned_array_values:
            continue
        array_key = _condition_array_key(source_track)
        candidates = []
        for target_track in list(base_action):
            if target_track.tag != "Track":
                continue
            if (
                _normalized_track_name(target_track),
                target_track.attrib.get("eventType", ""),
            ) == array_key[:2]:
                target_info = _condition_skin_info(target_track, None)
                if target_info and target_info["kind"] == "array" and _condition_array_key(target_track) == array_key:
                    candidates.append(target_track)
        if candidates:
            changed_any = False
            for target in candidates:
                target_info = _condition_skin_info(target, None)
                target_array = target_info["array_node"]
                current_values = [child.attrib["value"] for child in list(target_array)]
                merged_values = current_values + [
                    value for value in owned_array_values if value not in current_values
                ]
                if merged_values != current_values:
                    child_template = copy.deepcopy(list(target_array)[0])
                    for child in list(target_array):
                        target_array.remove(child)
                    for value in merged_values:
                        cloned = copy.deepcopy(child_template)
                        cloned.attrib["value"] = value
                        target_array.append(cloned)
                    changed_any = True
            if changed_any:
                condition_merged.append(name)
            else:
                already_present.append(name)
            continue
        filtered_track = copy.deepcopy(source_track)
        filtered_array = next(
            node for node in filtered_track.find("Event").findall("Array")
            if node.attrib.get("name") == "skinIdArray"
        )
        child_template = copy.deepcopy(list(filtered_array)[0])
        for child in list(filtered_array):
            filtered_array.remove(child)
        for value in owned_array_values:
            cloned = copy.deepcopy(child_template)
            cloned.attrib["value"] = value
            filtered_array.append(cloned)
        filtered_key = _track_key(filtered_track)
        append_track(filtered_track, filtered_key, name)
        condition_appended.append(name)

        )

    output_xml = ET.tostring(base_root, encoding="utf-8", xml_declaration=True)
    try:
        output = encode_aov_resource(output_xml, resource_name, base_mode)
        verify_decoded, verify_mode = decode_aov_resource(output, resource_name)
        verified_root = ET.fromstring(verify_decoded)
        verified_action = verified_root.find("Action")
        if verified_action is None:
            raise CommonActionsMergeError(
                f"{resource_name}: Action node biến mất sau encode/decode."
            )
        # Re-scan the serialized output and prove every overlay-owned gate survived.
        verified_tracks = [node for node in list(verified_action) if node.tag == "Track"]
        for source_track in overlay_tracks:
            source_gate = _track_actor_gate_ids(source_track, target_ids)
            if not source_gate:
                continue
            if (
                source_track.attrib.get("eventType") in _SKIN_CONDITION_EVENT_TYPES
                and not any(node.tag == "SkinOrAvatarList" for node in source_track.iter())
            ):
                source_info = _condition_skin_info(source_track, target_ids)
                if not source_info:
                    continue
                if source_info["kind"] == "scalar":
                    matches = []
                    for candidate in verified_tracks:
                        info = _condition_skin_info(candidate, target_ids)
                        if (
                            info and info["kind"] == "scalar"
                            and _normalized_track_name(candidate) == _normalized_track_name(source_track)
                            and candidate.attrib.get("eventType") == source_track.attrib.get("eventType")
                            and info["scalar_id"] == source_info["scalar_id"]
                            and _track_signature(candidate) == _track_signature(source_track)
                        ):
                            matches.append(candidate)
                    if not matches:
                        raise CommonActionsMergeError(
                            f"{resource_name}: điều kiện skinId {source_info['scalar_id']} "
                            f"không còn sau round-trip tại track {name}."
                        )
                else:
                    source_array_values = source_info["owned_ids"]
                    matches = []
                    for candidate in verified_tracks:
                        if any(node.tag == "SkinOrAvatarList" for node in candidate.iter()):
                            continue
                        info = _condition_skin_info(candidate, None)
                        if info and info["kind"] == "array" and _condition_array_key(candidate) == _condition_array_key(source_track):
                            matches.append(info)
                    present = set()
                    for info in matches:
                        present.update(info["array_values"])
                    if len(matches) != 1 or not source_array_values.issubset(present):
                        raise CommonActionsMergeError(
                            f"{resource_name}: thiếu skinIdArray {sorted(source_array_values - present)} "
                            f"sau round-trip tại track {_normalized_track_name(source_track)}."
                        )
            else:
                key = _track_key(source_track)
                output_by_key: dict[tuple[str, str, str], set[str]] = {}
                for candidate in verified_tracks:
                    candidate_key = _track_key(candidate)
                    output_by_key.setdefault(candidate_key, set()).update(
                        node.attrib.get("id")
                        for node in candidate.findall("SkinOrAvatarList")
                        if node.attrib.get("id") in target_ids
                    )
                source_ids = {
                    node.attrib.get("id") for node in source_track.findall("SkinOrAvatarList")
                    if node.attrib.get("id") in target_ids
                }
                if not source_ids.issubset(output_by_key.get(key, set())):
                    raise CommonActionsMergeError(
                        f"{resource_name}: thiếu SkinOrAvatarList IDs {sorted(source_ids - output_by_key.get(key, set()))} "
                        f"sau round-trip tại track {_normalized_track_name(source_track)}."
                    )
    except (AOVBytesError, ET.ParseError) as exc:
        raise CommonActionsMergeError(f"{resource_name}: không xác minh được XML sau ghép: {exc}") from exc

    return output, {
        "special_ids": sorted(target_ids),
        "base_encoding": base_mode,
        "overlay_encoding": overlay_mode,
        "output_encoding": verify_mode,
        "replaced_tracks": replaced,
        "appended_tracks": appended,
        "already_present_tracks": already_present,
        "merged_into_existing_tracks": merged_into_existing,
        "condition_appended_tracks": condition_appended,
        "condition_merged_tracks": condition_merged,
        "verified": True,
    }


def _canonical_xml_bytes(root: ET.Element) -> bytes:
    clone = copy.deepcopy(root)
    for node in clone.iter():
        if node.attrib:
            node.attrib = dict(sorted(node.attrib.items()))
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None
    return ET.tostring(clone, encoding="utf-8")


def _decode_xml_resource(data: bytes, resource_name: str) -> ET.Element:
    try:
        decoded, _ = decode_aov_resource(data, resource_name)
    except AOVBytesError:
        # Some CommonActions resources are stored as plain UTF-8 XML.
        decoded = data
    try:
        return ET.fromstring(decoded.lstrip(b"\xef\xbb\xbf"))
    except ET.ParseError as exc:
        raise CommonActionsMergeError(
            f"{resource_name}: XML không giải mã/parse được khi đối chiếu semantic."
        ) from exc


def _xml_resource_is_semantic_subset(
    base_data: bytes, overlay_data: bytes, resource_name: str
) -> bool:
    """Return true only when every overlay XML track is already present in base."""
    try:
        base_root = _decode_xml_resource(base_data, resource_name)
        overlay_root = _decode_xml_resource(overlay_data, resource_name)
    except CommonActionsMergeError:
        return False
    if base_root.tag != overlay_root.tag or base_root.attrib != overlay_root.attrib:
        return False
    base_action = base_root.find("Action")
    overlay_action = overlay_root.find("Action")
    if (base_action is None) != (overlay_action is None):
        return False
    base_non_action = [node for node in list(base_root) if node is not base_action]
    overlay_non_action = [node for node in list(overlay_root) if node is not overlay_action]
    if [_canonical_xml_bytes(node) for node in base_non_action] != [
        _canonical_xml_bytes(node) for node in overlay_non_action
    ]:
        return False
    if base_action is None:
        return _canonical_xml_bytes(base_root) == _canonical_xml_bytes(overlay_root)
    if base_action.attrib != overlay_action.attrib:
        return False
    base_other = [node for node in list(base_action) if node.tag != "Track"]
    overlay_other = [node for node in list(overlay_action) if node.tag != "Track"]
    if [_canonical_xml_bytes(node) for node in base_other] != [
        _canonical_xml_bytes(node) for node in overlay_other
    ]:
        return False
    from collections import Counter
    base_tracks = Counter(
        _canonical_xml_bytes(node)
        for node in list(base_action) if node.tag == "Track"
    )
    overlay_tracks = Counter(
        _canonical_xml_bytes(node)
        for node in list(overlay_action) if node.tag == "Track"
    )
    return all(base_tracks[key] >= count for key, count in overlay_tracks.items())


def merge_common_actions_pkg(
    base_package: bytes,
    overlay_package: bytes,
    *,
    special_ids: set[str] | None = None,
    strict_conflicts: bool = False,
) -> tuple[bytes, dict]:
    """Retain the base package and selectively merge actor-gated common action tracks."""
    target_ids = {
        str(value)
        for value in (SPECIAL_IDS if special_ids is None else special_ids)
    }
    if not target_ids or any(not value.isdigit() for value in target_ids):
        raise CommonActionsMergeError(
            "special skin IDs phải là tập không rỗng gồm chuỗi số."
        )
    try:
        base_zip = zipfile.ZipFile(io.BytesIO(base_package), "r")
        overlay_zip = zipfile.ZipFile(io.BytesIO(overlay_package), "r")
    except zipfile.BadZipFile as exc:
        raise CommonActionsMergeError("CommonActions.pkg.bytes không phải ZIP hợp lệ.") from exc

    try:
        # This is the production path used by the version-locked Actor 595
        # adapter as well as the multi-overlay helper. Check the overlay itself
        # before any target XML can be modified.
        _overlay_actor_track_signatures(overlay_package, target_ids)
    except CommonActionsMergeError:
        base_zip.close()
        overlay_zip.close()
        raise

    merge_candidates = set(_COMMON_ACTIONS_MERGE_CANDIDATES)
    base_entries = {item.filename: item for item in base_zip.infolist() if not item.is_dir()}
    overlay_entries = {item.filename: item for item in overlay_zip.infolist() if not item.is_dir()}
    output_buffer = io.BytesIO()
    merged_files = {}
    appended_files = []
    if strict_conflicts:
        for name in set(base_entries) & set(overlay_entries):
            if name in merge_candidates:
                continue
            base_payload = base_zip.read(base_entries[name])
            overlay_payload = overlay_zip.read(overlay_entries[name])
            if base_payload == overlay_payload:
                continue
            if name.lower().endswith(".xml") and _xml_resource_is_semantic_subset(
                base_payload, overlay_payload, name
            ):
                continue
            base_zip.close()
            overlay_zip.close()
            raise CommonActionsMergeError(
                f"{name}: xung đột tệp CommonActions ngoài danh sách XML có parser; "
                "không tự chọn bản nền hoặc bản overlay."
            )

    try:
        with zipfile.ZipFile(output_buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as out:
            for name, info in base_entries.items():
                base_data = base_zip.read(info)
                if name in merge_candidates and name in overlay_entries:
                    overlay_data = overlay_zip.read(overlay_entries[name])
                    decoded_overlay, _ = decode_aov_resource(overlay_data, name)
                    overlay_root = ET.fromstring(decoded_overlay)
                    has_target_gate = any(
                        _track_actor_gate_ids(track, target_ids)
                        for track in overlay_root.iter("Track")
                    )
                    if has_target_gate:
                        merged, report = merge_action_xml(
                            base_data,
                            overlay_data,
                            name,
                            special_ids=target_ids,
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
        "special_ids": sorted({str(value) for value in (SPECIAL_IDS if special_ids is None else special_ids)}),
        "strict_conflicts": strict_conflicts,
        "verified": True,
    }


def _overlay_actor_track_signatures(
    overlay_package: bytes,
    special_ids: set[str],
) -> dict[tuple, str]:
    """Fingerprint direct list gates and supported scalar/array skin conditions."""
    merge_candidates = set(_COMMON_ACTIONS_MERGE_CANDIDATES)
    try:
        archive = zipfile.ZipFile(io.BytesIO(overlay_package), "r")
    except zipfile.BadZipFile as exc:
        raise CommonActionsMergeError(
            "CommonActions.pkg.bytes không phải ZIP hợp lệ."
        ) from exc

    signatures: dict[tuple, str] = {}
    try:
        entries: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.filename in entries:
                raise CommonActionsMergeError(
                    f"{info.filename}: CommonActions có nhiều entry trùng đường dẫn."
                )
            entries[info.filename] = info

        for name in merge_candidates & entries.keys():
            try:
                decoded, _ = decode_aov_resource(archive.read(entries[name]), name)
                root = ET.fromstring(decoded)
            except (AOVBytesError, ET.ParseError) as exc:
                raise CommonActionsMergeError(
                    f"{name}: không phân tích được XML khi đối chiếu các overlay: {exc}"
                ) from exc

            action = root.find("Action")
            if action is None:
                raise CommonActionsMergeError(f"{name}: thiếu node Action.")

            for track in action.iter("Track"):
                all_skin_nodes = list(track.iter("SkinOrAvatarList"))
                direct_skin_nodes = track.findall("SkinOrAvatarList")
                if all_skin_nodes:
                    target_nodes = [
                        node for node in direct_skin_nodes
                        if node.attrib.get("id") in special_ids
                    ]
                    if len(direct_skin_nodes) != len(all_skin_nodes) and any(
                        node.attrib.get("id") in special_ids for node in all_skin_nodes
                    ):
                        raise CommonActionsMergeError(
                            f"{name}: track {_normalized_track_name(track)} có "
                            "SkinOrAvatarList của actor mục tiêu lồng ở vị trí chưa hỗ trợ."
                        )
                    for node in target_nodes:
                        skin_id = node.attrib.get("id")
                        key = (name, "SkinOrAvatarList", _track_key(track), skin_id)
                        digest = hashlib.sha256(
                            ET.tostring(node, encoding="utf-8")
                        ).hexdigest()
                        previous = signatures.get(key)
                        if previous is not None and previous != digest:
                            raise CommonActionsMergeError(
                                f"{name}: một overlay tự khai báo nhiều điều kiện khác nhau "
                                f"cho skin ID {skin_id} tại track {_normalized_track_name(track)}."
                            )
                        signatures[key] = digest
                    continue

                condition = _condition_skin_info(track, special_ids)
                if condition is None:
                    continue
                if condition["kind"] == "scalar":
                    skin_id = condition["scalar_id"]
                    key = (name, "CheckSkinIdScalar", _track_key(track), skin_id)
                    digest = _track_signature(track)
                else:
                    key = (name, "CheckSkinIdArray", _condition_array_key(track))
                    digest = _condition_array_template_signature(track)
                previous = signatures.get(key)
                if previous is not None and previous != digest:
                    raise CommonActionsMergeError(
                        f"{name}: nhiều overlay định nghĩa điều kiện skin khác nhau "
                        f"cho track {_normalized_track_name(track)}."
                    )
                signatures[key] = digest
    finally:
        archive.close()

    return signatures


def merge_common_actions_packages(
    base_package: bytes,
    overlays: list[tuple[bytes, set[str]]],
) -> tuple[bytes, dict]:
    """Sequentially merge several actor-scoped overlays into one CommonActions package.

    Each overlay carries the exact SkinOrAvatarList IDs that its actor pack is
    allowed to contribute. Non-parser files that collide with an existing path
    must be byte-identical. This routine does not infer actor IDs from filenames.
    """
    if not overlays:
        raise CommonActionsMergeError("Cần ít nhất một overlay CommonActions.")

    # Preflight all overlays together before mutating the in-memory base.
    # Identical repeated actor conditions are harmless duplicates; differing
    # conditions for the same actor + XML track are ambiguous and must stop.
    seen_conditions: dict[
        tuple[str, tuple[str, str, str], str], tuple[str, int]
    ] = {}
    for index, (overlay_package, special_ids) in enumerate(overlays, start=1):
        normalized_ids = {str(value) for value in special_ids}
        if not normalized_ids or any(not value.isdigit() for value in normalized_ids):
            raise CommonActionsMergeError(
                f"Overlay {index}: special skin IDs phải là tập không rỗng gồm chuỗi số."
            )
        signatures = _overlay_actor_track_signatures(
            overlay_package, normalized_ids
        )
        for key, digest in signatures.items():
            previous = seen_conditions.get(key)
            if previous is not None and previous[0] != digest:
                name, track_key, skin_id = key
                raise CommonActionsMergeError(
                    f"{name}: nhiều overlay định nghĩa điều kiện khác nhau cho "
                    f"skin ID {skin_id} tại track {track_key[0]} "
                    f"(overlay {previous[1]} và {index}); không tự chọn bản thắng."
                )
            seen_conditions[key] = (digest, index)

    current = base_package
    reports = []
    for index, (overlay_package, special_ids) in enumerate(overlays, start=1):
        current, report = merge_common_actions_pkg(
            current,
            overlay_package,
            special_ids=set(special_ids),
            strict_conflicts=True,
        )
        reports.append({
            "overlay_index": index,
            "special_ids": sorted({str(value) for value in special_ids}),
            "report": report,
        })
    return current, {
        "overlay_count": len(overlays),
        "overlay_reports": reports,
        "verified": all(item["report"]["verified"] for item in reports),
    }


_COMMON_ACTIONS_MERGE_CANDIDATES = {
    "commonresource/Back.xml",
    "commonresource/BackFast.xml",
    "commonresource/Born.xml",
    "commonresource/Dance.xml",
    "commonresource/DanceBullet.xml",
    "commonresource/Dead_Born.xml",
    "commonresource/HasteE1.xml",
    "commonresource/HasteE1_leave.xml",
    "commonresource/HasteE1_super.xml",
    "commonresource/HasteE2.xml",
    "commonresource/HasteE3.xml",
    "commonresource/QuickBack.xml",
}


def _inspect_common_actions_overlay(
    source_name: str,
    package_bytes: bytes,
    actor_prefixes: set[str] | None = None,
) -> tuple[set[str], dict]:
    """Read actor-owned list and condition-based skin gates from supported CommonActions XML."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(package_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise CommonActionsMergeError(
            f"{source_name}: CommonActions.pkg.bytes không phải ZIP hợp lệ."
        ) from exc

    skin_ids: set[str] = set()
    gated_tracks = 0
    gated_conditions = 0
    xml_files = 0
    try:
        entries = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.filename in entries:
                raise CommonActionsMergeError(
                    f"{source_name}: trùng entry {info.filename} trong CommonActions."
                )
            entries[info.filename] = info

        bad = archive.testzip()
        if bad:
            raise CommonActionsMergeError(
                f"{source_name}: CommonActions lỗi CRC tại {bad}."
            )

        for name in sorted(_COMMON_ACTIONS_MERGE_CANDIDATES & entries.keys()):
            xml_files += 1
            try:
                decoded, _ = decode_aov_resource(archive.read(entries[name]), name)
                root = ET.fromstring(decoded)
            except (AOVBytesError, ET.ParseError) as exc:
                raise CommonActionsMergeError(
                    f"{source_name}: {name} không giải mã/parse được: {exc}"
                ) from exc

            action = root.find("Action")
            if action is None:
                raise CommonActionsMergeError(
                    f"{source_name}: {name} thiếu node Action."
                )
            for track in action.iter("Track"):
                skin_nodes = list(track.iter("SkinOrAvatarList"))
                direct_nodes = track.findall("SkinOrAvatarList")
                if skin_nodes and len(direct_nodes) != len(skin_nodes):
                    if any(
                        node.attrib.get("id", "")[:3] in (actor_prefixes or set())
                        or node.attrib.get("id") in (actor_prefixes or set())
                        for node in skin_nodes
                    ):
                        raise CommonActionsMergeError(
                            f"{source_name}: {name}, track "
                            f"{_normalized_track_name(track)} có SkinOrAvatarList lồng "
                            "ở vị trí chưa được hỗ trợ."
                        )
                condition = None if skin_nodes else _condition_skin_info(track, actor_prefixes)

                if skin_nodes:
                    owned_ids = set()
                    for node in direct_nodes:
                        skin_id = node.attrib.get("id", "")
                        if not skin_id.isdigit():
                            raise CommonActionsMergeError(
                                f"{source_name}: {name}, track "
                                f"{_normalized_track_name(track)} có skin ID không phải số."
                            )
                        if actor_prefixes is None or skin_id in actor_prefixes or skin_id[:3] in actor_prefixes:
                            owned_ids.add(skin_id)
                else:
                    owned_ids = set(condition["owned_ids"]) if condition else set()

                if not owned_ids:
                    continue
                gated_tracks += 1
                skin_ids.update(owned_ids)
                gated_conditions += len(owned_ids)
    finally:
        archive.close()

    return skin_ids, {
        "source": source_name,
        "xml_file_count": xml_files,
        "actor_gated_track_count": gated_tracks,
        "actor_gate_condition_count": gated_conditions,
        "skin_ids": sorted(skin_ids),
        "state": "ACTOR_GATED_TRACKS_FOUND" if skin_ids else "NO_ACTOR_GATED_TRACKS",
    }




def _common_actions_opaque_path_conflicts(
    overlays: list[tuple[str, bytes]],
) -> tuple[list[dict], int, list[str]]:
    """List unresolved opaque conflicts and recognize safe semantic XML subsets."""
    by_path: dict[str, list[dict]] = {}
    for overlay in overlays:
        source_name, package_bytes = overlay[:2]
        try:
            archive = zipfile.ZipFile(io.BytesIO(package_bytes), "r")
        except zipfile.BadZipFile as exc:
            raise CommonActionsMergeError(
                f"{source_name}: CommonActions.pkg.bytes không phải ZIP hợp lệ."
            ) from exc
        try:
            for info in archive.infolist():
                if info.is_dir() or info.filename in _COMMON_ACTIONS_MERGE_CANDIDATES:
                    continue
                payload = archive.read(info)
                by_path.setdefault(info.filename, []).append({
                    "source": source_name,
                    "size_bytes": info.file_size,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "_payload": payload,
                })
        finally:
            archive.close()

    conflicts = []
    semantic_subset_paths = []
    identical_duplicates = 0
    for path, contributors in sorted(by_path.items()):
        if len(contributors) < 2:
            continue
        unique_hashes = {item["sha256"] for item in contributors}
        if len(unique_hashes) == 1:
            identical_duplicates += 1
            continue
        payloads = [item["_payload"] for item in contributors]
        semantic_subset = (
            path.lower().endswith(".xml")
            and all(
                _xml_resource_is_semantic_subset(payloads[0], payload, path)
                for payload in payloads[1:]
            )
        )
        if semantic_subset:
            semantic_subset_paths.append(path)
            continue
        conflicts.append({
            "path": path,
            "source_count": len(contributors),
            "sources": [
                {key: value for key, value in item.items() if key != "_payload"}
                for item in contributors
            ],
        })
    return conflicts, identical_duplicates, semantic_subset_paths

def _empty_common_actions_package() -> bytes:
    """Build a parser-only baseline; its output is never used as a game resource."""
    root = ET.Element("Project")
    ET.SubElement(root, "TemplateObjectList")
    ET.SubElement(root, "RefParamList")
    ET.SubElement(root, "Action", {"length": "0.000", "loop": "false"})
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as archive:
        for name in sorted(_COMMON_ACTIONS_MERGE_CANDIDATES):
            archive.writestr(name, encode_aov_resource(xml, name, "zstd"))
    return buffer.getvalue()


def analyze_common_actions_overlay_set(
    overlays: list[tuple],
) -> dict:
    """Dry-run compatibility check for actor-gated CommonActions from many sources.

    This validates only the supported XML/track subset. It discards the synthetic
    merge output and never grants permission to merge the surrounding AoV archives.
    """
    if not overlays:
        return {
            "available": False,
            "state": "NOT_FOUND",
            "overlay_count": 0,
            "merge_authorized": False,
            "full_merge_authorized": False,
            "game_tested": False,
        }

    package_reports = []
    parser_overlays = []
    try:
        for overlay in overlays:
            if len(overlay) == 3:
                source_name, package_bytes, actor_prefixes = overlay
                actor_prefixes = {str(value) for value in actor_prefixes}
            else:
                source_name, package_bytes = overlay
                actor_prefixes = None
            skin_ids, report = _inspect_common_actions_overlay(
                source_name, package_bytes, actor_prefixes
            )
            report["actor_prefixes"] = sorted(actor_prefixes or ())
            package_reports.append(report)
            if skin_ids:
                parser_overlays.append((package_bytes, skin_ids))

        (
            opaque_conflicts,
            identical_duplicate_paths,
            semantic_subset_paths,
        ) = _common_actions_opaque_path_conflicts(overlays)

        if not parser_overlays:
            return {
                "available": True,
                "state": "BLOCKED" if opaque_conflicts else "NO_ACTOR_GATED_TRACKS",
                "overlay_count": 0,
                "source_package_count": len(overlays),
                "packages": package_reports,
                "unsupported_path_conflicts": opaque_conflicts,
                "unsupported_path_conflict_count": len(opaque_conflicts),
                "identical_duplicate_path_count": identical_duplicate_paths,
                "semantic_subset_paths": semantic_subset_paths,
                "reason": (
                    f"Có {len(opaque_conflicts)} đường dẫn CommonActions ngoài parser "
                    "bị khác nội dung giữa các nguồn."
                    if opaque_conflicts else None
                ),
                "merge_authorized": False,
                "full_merge_authorized": False,
                "output_discarded": True,
                "game_tested": False,
                "note": (
                    "Không tìm thấy track có SkinOrAvatarList trực tiếp trong mười hai XML "
                    "được parser hỗ trợ."
                ),
            }

        synthetic_base = _empty_common_actions_package()
        _, dry_run_report = merge_common_actions_packages(
            synthetic_base, parser_overlays
        )

        active_reports = [item for item in package_reports if item["skin_ids"]]
        for package_report, overlay_report in zip(
            active_reports, dry_run_report["overlay_reports"]
        ):
            merge_report = overlay_report["report"]
            package_report["parser_merged_xml_files"] = len(
                merge_report["merged_files"]
            )
            package_report["parser_appended_files"] = len(
                merge_report["appended_files"]
            )
            package_report["parser_actor_tracks"] = sum(
                len(item.get("replaced_tracks", []))
                + len(item.get("appended_tracks", []))
                for item in merge_report["merged_files"].values()
            )

        return {
            "available": True,
            "state": (
                "COMPATIBLE"
                if len(parser_overlays) > 1
                else "SINGLE_OVERLAY_VALIDATED"
            ),
            "source_package_count": len(overlays),
            "overlay_count": len(parser_overlays),
            "skin_ids": sorted({
                skin_id
                for _, ids in parser_overlays
                for skin_id in ids
            }),
            "actor_gated_track_count": sum(
                item["actor_gated_track_count"] for item in active_reports
            ),
            "actor_gate_condition_count": sum(
                item["actor_gate_condition_count"] for item in active_reports
            ),
            "packages": package_reports,
            "unsupported_path_conflicts": opaque_conflicts,
            "unsupported_path_conflict_count": len(opaque_conflicts),
            "identical_duplicate_path_count": identical_duplicate_paths,
            "semantic_subset_paths": semantic_subset_paths,
            "parser_verified": bool(dry_run_report["verified"]),
            "merge_authorized": False,
            "full_merge_authorized": False,
            "output_discarded": True,
            "game_tested": False,
            "note": (
                "Dry-run chỉ kiểm tra mười hai XML CommonActions và các điều kiện actor. "
                "ZIP mô phỏng đã bị loại bỏ; kết quả này không ghép MSES, IFix, "
                "AssetBundle, AssetRef, Packer/Verification hoặc Wwise."
            ),
        }
    except CommonActionsMergeError as exc:
        return {
            "available": True,
            "state": "BLOCKED",
            "source_package_count": len(overlays),
            "overlay_count": len(parser_overlays),
            "packages": package_reports,
            "unsupported_path_conflicts": opaque_conflicts
            if "opaque_conflicts" in locals() else [],
            "unsupported_path_conflict_count": len(opaque_conflicts)
            if "opaque_conflicts" in locals() else 0,
            "identical_duplicate_path_count": identical_duplicate_paths
            if "identical_duplicate_paths" in locals() else 0,
            "reason": str(exc),
            "merge_authorized": False,
            "full_merge_authorized": False,
            "output_discarded": True,
            "game_tested": False,
        }
