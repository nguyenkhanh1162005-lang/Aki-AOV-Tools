import io
import unittest
import zipfile
from xml.etree import ElementTree as ET

from app.aov_bytes import decode_aov_resource, encode_aov_resource
from app.aov_common_actions import (
    _inspect_common_actions_overlay,
    merge_action_xml,
    merge_common_actions_packages,
)


def condition_xml(
    track_name,
    event_type,
    *,
    scalar_id=None,
    array_ids=None,
    negate=None,
    skin_list_ids=None,
):
    root = ET.Element("Project")
    action = ET.SubElement(root, "Action")
    track = ET.SubElement(action, "Track", {
        "trackName": track_name,
        "eventType": event_type,
        "enabled": "true",
    })
    event = ET.SubElement(track, "Event", {
        "eventName": event_type,
        "time": "0.000",
        "isDuration": "false",
    })
    ET.SubElement(event, "TemplateObject", {
        "name": "targetId",
        "objectName": "self",
        "id": "0",
        "isTemp": "false",
    })
    if scalar_id is not None:
        ET.SubElement(event, "int", {"name": "skinId", "value": str(scalar_id)})
    if negate is not None:
        ET.SubElement(event, "bool", {
            "name": "useNegateValue",
            "value": str(negate).lower(),
        })
    if array_ids is not None:
        array = ET.SubElement(event, "Array", {
            "name": "skinIdArray",
            "type": "int",
        })
        for value in array_ids:
            ET.SubElement(array, "int", {"value": str(value)})
    for value in skin_list_ids or ():
        ET.SubElement(track, "SkinOrAvatarList", {"id": str(value)})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def package(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class CommonActionsConditionMergeTests(unittest.TestCase):
    def test_inspector_recognizes_actor_owned_scalar_skin_conditions(self):
        path = "commonresource/HasteE1.xml"
        raw = condition_xml(
            "CheckSkinIdTick2",
            "CheckSkinIdTick",
            scalar_id="59300",
        )
        payload = encode_aov_resource(raw, path, "zstd")
        overlay = package({path: payload})

        skin_ids, report = _inspect_common_actions_overlay(
            "Actor593.zip", overlay, {"593"}
        )

        self.assertEqual(skin_ids, {"59300"})
        self.assertEqual(report["actor_gated_track_count"], 1)
        self.assertEqual(report["actor_gate_condition_count"], 1)

    def test_merges_actor_owned_scalar_check_skin_id_track_only(self):
        path = "commonresource/HasteE1.xml"
        base = encode_aov_resource(
            b'<?xml version="1.0"?><Project><Action /></Project>', path, "zstd"
        )
        overlay_root = ET.Element("Project")
        action = ET.SubElement(overlay_root, "Action")
        for skin_id in ("59300", "59500"):
            parsed = ET.fromstring(condition_xml(
                "CheckSkinIdTick2",
                "CheckSkinIdTick",
                scalar_id=skin_id,
            ))
            action.append(parsed.find("Action").find("Track"))
        overlay = encode_aov_resource(
            ET.tostring(overlay_root, encoding="utf-8", xml_declaration=True),
            path,
            "zstd",
        )

        merged, report = merge_action_xml(
            base, overlay, path, special_ids={"59300"}
        )
        decoded, _ = decode_aov_resource(merged, path)
        tracks = ET.fromstring(decoded).find("Action").findall("Track")
        scalar_ids = {
            node.attrib["value"]
            for track in tracks
            for node in track.iter("int")
            if node.attrib.get("name") == "skinId"
        }
        self.assertEqual(scalar_ids, {"59300"})
        self.assertEqual(report["condition_appended_tracks"], ["CheckSkinIdTick2"])
        self.assertTrue(report["verified"])

    def test_array_merge_unions_only_skin_ids_owned_by_overlay(self):
        path = "commonresource/HasteE1.xml"
        base = encode_aov_resource(
            condition_xml(
                "CheckSkinIdVirtualTick0",
                "CheckSkinIdVirtualTick",
                array_ids=["51015", "11620"],
                negate=True,
            ),
            path,
            "zstd",
        )
        overlay = encode_aov_resource(
            condition_xml(
                "0_CheckSkinIdVirtualTick0",
                "CheckSkinIdVirtualTick",
                array_ids=["51015", "11620", "52414", "59300"],
                negate=True,
            ),
            path,
            "zstd",
        )

        merged, report = merge_action_xml(
            base, overlay, path, special_ids={"52414"}
        )
        decoded, _ = decode_aov_resource(merged, path)
        track = ET.fromstring(decoded).find("Action").find("Track")
        values = [
            node.attrib["value"]
            for node in track.find("Event").find("Array")
            if node.tag == "int"
        ]
        self.assertEqual(values, ["51015", "11620", "52414"])
        self.assertEqual(report["condition_merged_tracks"], ["CheckSkinIdVirtualTick0"])
        self.assertTrue(report["verified"])

    def test_multi_overlay_unions_skin_id_array_actor_values(self):
        path = "commonresource/HasteE1.xml"
        base = package({path: encode_aov_resource(
            condition_xml(
                "CheckSkinIdVirtualTick0",
                "CheckSkinIdVirtualTick",
                array_ids=["51015"],
                negate=True,
            ),
            path,
            "zstd",
        )})
        overlay_524 = package({path: encode_aov_resource(
            condition_xml(
                "0_CheckSkinIdVirtualTick0",
                "CheckSkinIdVirtualTick",
                array_ids=["51015", "52414"],
                negate=True,
            ),
            path,
            "zstd",
        )})
        overlay_593 = package({path: encode_aov_resource(
            condition_xml(
                "1_CheckSkinIdVirtualTick0",
                "CheckSkinIdVirtualTick",
                array_ids=["51015", "59300"],
                negate=True,
            ),
            path,
            "zstd",
        )})

        merged, report = merge_common_actions_packages(
            base,
            [(overlay_524, {"52414"}), (overlay_593, {"59300"})],
        )
        with zipfile.ZipFile(io.BytesIO(merged)) as archive:
            decoded, _ = decode_aov_resource(archive.read(path), path)
        track = ET.fromstring(decoded).find("Action").find("Track")
        values = [node.attrib["value"] for node in track.find("Event").find("Array")]
        self.assertEqual(values, ["51015", "52414", "59300"])
        self.assertTrue(report["verified"])

    def test_mixed_skin_list_and_check_skin_scalar_track_stays_intact(self):
        path = "commonresource/Back.xml"
        base = encode_aov_resource(
            b'<?xml version="1.0"?><Project><Action /></Project>', path, "zstd"
        )
        overlay = encode_aov_resource(
            condition_xml(
                "0_CheckSkinIdTick0",
                "CheckSkinIdTick",
                scalar_id="59300",
                skin_list_ids=["59300"],
            ),
            path,
            "zstd",
        )

        merged, report = merge_action_xml(
            base, overlay, path, special_ids={"59300"}
        )
        decoded, _ = decode_aov_resource(merged, path)
        track = next(
            item for item in ET.fromstring(decoded).find("Action").findall("Track")
            if item.attrib.get("eventType") == "CheckSkinIdTick"
        )
        self.assertEqual(track.find("Event").find("int").attrib["value"], "59300")
        self.assertEqual(track.find("SkinOrAvatarList").attrib["id"], "59300")
        self.assertTrue(report["verified"])


if __name__ == "__main__":
    unittest.main()
