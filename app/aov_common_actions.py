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
    """Merge only explicitly actor-gated CommonActions tracks.

    This parser supports direct-child SkinOrAvatarList gates only. Condition-index
    based CheckSkinIdTick logic used by some MOD packs is a separate format and
    must not be treated as equivalent to these explicit skin-list gates.
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

    for source_track in overlay_tracks:
        source_skin_nodes = list(source_track.iter("SkinOrAvatarList"))
        if not source_skin_nodes:
            continue
        if not _is_special_track(source_track, target_ids):
            continue
        # The supported track schema keeps SkinOrAvatarList as direct children.
        # Refuse an unknown nested layout instead of moving nodes to a different
        # event location during the merge.
        direct_skin_nodes = source_track.findall("SkinOrAvatarList")
        if len(direct_skin_nodes) != len(source_skin_nodes):
            raise CommonActionsMergeError(
                f"{resource_name}: track {_normalized_track_name(source_track)} "
                "có SkinOrAvatarList lồng ở vị trí chưa được hỗ trợ."
            )
        # Only import IDs explicitly assigned to this overlay. Some packs can
        # contain shared/common track metadata for other actors as well.
        filtered_track = copy.deepcopy(source_track)
        for node in list(filtered_track.findall("SkinOrAvatarList")):
            if node.attrib.get("id") not in target_ids:
                filtered_track.remove(node)
        if not filtered_track.findall("SkinOrAvatarList"):
            continue
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

        addition = filtered_track
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
            if node.attrib.get("id") in target_ids
        )
    for source_track in overlay_action.iter("Track"):
        if not _is_special_track(source_track, target_ids):
            continue
        key = _track_key(source_track)
        source_ids = {
            node.attrib.get("id")
            for node in source_track.iter("SkinOrAvatarList")
            if node.attrib.get("id") in target_ids
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
        verified_root = ET.fromstring(verify_decoded)
        verified_action = verified_root.find("Action")
        if verified_action is None:
            raise CommonActionsMergeError(
                f"{resource_name}: Action node biến mất sau encode/decode."
            )
        verified_by_key: dict[tuple[str, str, str], set[str]] = {}
        for track in verified_action.iter("Track"):
            key = _track_key(track)
            verified_by_key.setdefault(key, set()).update(
                node.attrib.get("id")
                for node in track.iter("SkinOrAvatarList")
                if node.attrib.get("id") in target_ids
            )
        for source_track in overlay_action.iter("Track"):
            source_ids = {
                node.attrib.get("id")
                for node in source_track.iter("SkinOrAvatarList")
                if node.attrib.get("id") in target_ids
            }
            if not source_ids:
                continue
            key = _track_key(source_track)
            if not source_ids.issubset(verified_by_key.get(key, set())):
                raise CommonActionsMergeError(
                    f"{resource_name}: IDs {sorted(source_ids - verified_by_key.get(key, set()))} "
                    f"không còn sau round-trip tại track {key[0]}."
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
        "verified": True,
    }


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
    if strict_conflicts:
        for name in set(base_entries) & set(overlay_entries):
            if name in merge_candidates:
                continue
            if base_zip.read(base_entries[name]) != overlay_zip.read(overlay_entries[name]):
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
                    merged, report = merge_action_xml(
                        base_data,
                        overlay_zip.read(overlay_entries[name]),
                        name,
                        special_ids=special_ids,
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
) -> dict[tuple[str, tuple[str, str, str], str], str]:
    """Fingerprint actor-gated XML conditions contributed by one overlay.

    The fingerprint intentionally keeps the full SkinOrAvatarList element,
    while the track key normalizes generated names/GUIDs. This lets the
    multi-overlay path detect two sources trying to define the same actor
    condition differently without confusing distinct event variants.
    """
    merge_candidates = {
        "commonresource/Back.xml",
        "commonresource/BackFast.xml",
        "commonresource/HasteE1.xml",
        "commonresource/HasteE1_super.xml",
        "commonresource/HasteE2.xml",
        "commonresource/HasteE3.xml",
    }
    try:
        archive = zipfile.ZipFile(io.BytesIO(overlay_package), "r")
    except zipfile.BadZipFile as exc:
        raise CommonActionsMergeError(
            "CommonActions.pkg.bytes không phải ZIP hợp lệ."
        ) from exc

    signatures: dict[tuple[str, tuple[str, str, str], str], str] = {}
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
                target_skin_nodes = [
                    node for node in all_skin_nodes
                    if node.attrib.get("id") in special_ids
                ]
                if not target_skin_nodes:
                    continue
                direct_target_nodes = [
                    node for node in track.findall("SkinOrAvatarList")
                    if node.attrib.get("id") in special_ids
                ]
                if len(direct_target_nodes) != len(target_skin_nodes):
                    raise CommonActionsMergeError(
                        f"{name}: track {_normalized_track_name(track)} có "
                        "SkinOrAvatarList của actor mục tiêu lồng ở vị trí "
                        "chưa được hỗ trợ."
                    )
                track_key = _track_key(track)
                for node in direct_target_nodes:
                    skin_id = node.attrib.get("id")
                    key = (name, track_key, skin_id)
                    digest = hashlib.sha256(
                        ET.tostring(node, encoding="utf-8")
                    ).hexdigest()
                    previous = signatures.get(key)
                    if previous is not None and previous != digest:
                        raise CommonActionsMergeError(
                            f"{name}: một overlay tự khai báo nhiều điều kiện "
                            f"khác nhau cho skin ID {skin_id} tại track "
                            f"{track_key[0]}."
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
    "commonresource/HasteE1.xml",
    "commonresource/HasteE1_super.xml",
    "commonresource/HasteE2.xml",
    "commonresource/HasteE3.xml",
}


def _inspect_common_actions_overlay(
    source_name: str,
    package_bytes: bytes,
) -> tuple[set[str], dict]:
    """Read explicit actor skin gates from the supported CommonActions XML files."""
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
                if not skin_nodes:
                    continue
                direct_nodes = track.findall("SkinOrAvatarList")
                if len(direct_nodes) != len(skin_nodes):
                    raise CommonActionsMergeError(
                        f"{source_name}: {name}, track "
                        f"{_normalized_track_name(track)} có SkinOrAvatarList lồng "
                        "ở vị trí chưa được hỗ trợ."
                    )
                gated_tracks += 1
                for node in direct_nodes:
                    skin_id = node.attrib.get("id", "")
                    if not skin_id.isdigit():
                        raise CommonActionsMergeError(
                            f"{source_name}: {name}, track "
                            f"{_normalized_track_name(track)} có skin ID không phải số."
                        )
                    skin_ids.add(skin_id)
                    gated_conditions += 1
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
    overlays: list[tuple[str, bytes]],
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
        for source_name, package_bytes in overlays:
            skin_ids, report = _inspect_common_actions_overlay(
                source_name, package_bytes
            )
            package_reports.append(report)
            if skin_ids:
                parser_overlays.append((package_bytes, skin_ids))

        if not parser_overlays:
            return {
                "available": True,
                "state": "NO_ACTOR_GATED_TRACKS",
                "overlay_count": 0,
                "source_package_count": len(overlays),
                "packages": package_reports,
                "merge_authorized": False,
                "full_merge_authorized": False,
                "output_discarded": True,
                "game_tested": False,
                "note": (
                    "Không tìm thấy track có SkinOrAvatarList trực tiếp trong sáu XML "
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
            "parser_verified": bool(dry_run_report["verified"]),
            "merge_authorized": False,
            "full_merge_authorized": False,
            "output_discarded": True,
            "game_tested": False,
            "note": (
                "Dry-run chỉ kiểm tra sáu XML CommonActions và các điều kiện actor. "
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
            "reason": str(exc),
            "merge_authorized": False,
            "full_merge_authorized": False,
            "output_discarded": True,
            "game_tested": False,
        }
