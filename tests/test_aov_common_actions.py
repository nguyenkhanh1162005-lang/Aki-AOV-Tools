import io
import unittest
import zipfile
from xml.etree import ElementTree as ET

from app.aov_bytes import encode_aov_resource, decode_aov_resource
from app.aov_common_actions import (
    CommonActionsMergeError,
    merge_action_xml,
    merge_common_actions_pkg,
    merge_common_actions_packages,
)


def action_xml(track_name, special=False, special_id="59500"):
    root = ET.Element("Project")
    ET.SubElement(root, "TemplateObjectList")
    ET.SubElement(root, "RefParamList")
    action = ET.SubElement(root, "Action", {"length": "1.000", "loop": "false"})
    track = ET.SubElement(action, "Track", {
        "trackName": track_name,
        "eventType": "TriggerParticleTick",
        "enabled": "true",
    })
    if special:
        ET.SubElement(track, "SkinOrAvatarList", {"id": special_id})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def package(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class CommonActionsMergeTests(unittest.TestCase):
    def test_merges_595_track_and_preserves_base_tracks(self):
        name = "commonresource/Back.xml"
        base = encode_aov_resource(action_xml("TriggerParticleTick0"), name, "zstd")
        overlay = encode_aov_resource(action_xml("0_TriggerParticleTick0", True), name, "zstd")
        output, report = merge_action_xml(base, overlay, name)
        decoded, mode = decode_aov_resource(output, name)
        root = ET.fromstring(decoded)
        tracks = root.find("Action").findall("Track")
        self.assertEqual(mode, "zstd")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].attrib["trackName"], "TriggerParticleTick0")
        self.assertEqual(tracks[0].find("SkinOrAvatarList").attrib["id"], "59500")
        self.assertEqual(report["replaced_tracks"], ["TriggerParticleTick0"])
        self.assertTrue(report["verified"])

    def test_appends_new_595_track(self):
        name = "commonresource/HasteE1.xml"
        base = encode_aov_resource(action_xml("ExistingTrack"), name, "zstd")
        overlay = encode_aov_resource(action_xml("1_PlayHeroSoundTick2", True), name, "zstd")
        output, report = merge_action_xml(base, overlay, name)
        root = ET.fromstring(decode_aov_resource(output, name)[0])
        tracks = root.find("Action").findall("Track")
        self.assertEqual(len(tracks), 2)
        self.assertTrue(tracks[-1].attrib.get("guid"))
        self.assertEqual(report["appended_tracks"], ["PlayHeroSoundTick2"])

    def test_merges_multiple_595_variants_for_same_track(self):
        name = "commonresource/Back.xml"
        root = ET.Element("Project")
        action = ET.SubElement(root, "Action")
        base_track = ET.SubElement(action, "Track", {
            "trackName": "TriggerParticleTick0",
            "eventType": "TriggerParticleTick",
            "guid": "base-guid",
        })
        ET.SubElement(base_track, "SkinOrAvatarList", {"id": "59500"})
        overlay_root = ET.Element("Project")
        overlay_action = ET.SubElement(overlay_root, "Action")
        for skin_id in ("59500", "59501"):
            track = ET.SubElement(overlay_action, "Track", {
                "trackName": "0_TriggerParticleTick0",
                "eventType": "TriggerParticleTick",
            })
            ET.SubElement(track, "SkinOrAvatarList", {"id": skin_id})
        base = encode_aov_resource(ET.tostring(root), name, "zstd")
        overlay = encode_aov_resource(ET.tostring(overlay_root), name, "zstd")
        output, report = merge_action_xml(base, overlay, name)
        merged_root = ET.fromstring(decode_aov_resource(output, name)[0])
        merged_track = merged_root.find("Action").find("Track")
        ids = {node.attrib["id"] for node in merged_track.findall("SkinOrAvatarList")}
        self.assertEqual(ids, {"59500", "59501"})
        self.assertTrue(report["merged_into_existing_tracks"])

    def test_keeps_same_name_tracks_with_different_event_flags(self):
        name = "commonresource/HasteE1.xml"
        base_root = ET.Element("Project")
        base_action = ET.SubElement(base_root, "Action")
        overlay_root = ET.Element("Project")
        overlay_action = ET.SubElement(overlay_root, "Action")
        for index, completed in enumerate(("false", "true")):
            track = ET.SubElement(overlay_action, "Track", {
                "trackName": f"{85 + index}_SetAnimationParamsTick0",
                "eventType": "SetAnimationParamsTick",
                "enabled": "true",
                "execOnActionCompleted": completed,
                "SkinAvatarFilterType": "9",
            })
            event = ET.SubElement(track, "Event", {
                "eventName": "SetAnimationParamsTick",
                "time": "0.000",
                "isDuration": "false",
            })
            ET.SubElement(event, "String", {"name": "variant", "value": completed})
            ET.SubElement(track, "SkinOrAvatarList", {"id": "59500"})
        base = encode_aov_resource(ET.tostring(base_root), name, "zstd")
        overlay = encode_aov_resource(ET.tostring(overlay_root), name, "zstd")
        output, report = merge_action_xml(base, overlay, name)
        decoded, _ = decode_aov_resource(output, name)
        result = ET.fromstring(decoded).find("Action").findall("Track")
        self.assertEqual(len(result), 2)
        self.assertEqual(
            {track.attrib["execOnActionCompleted"] for track in result},
            {"false", "true"},
        )
        self.assertEqual(len(report["appended_tracks"]), 2)




    def test_rejects_unknown_nested_skin_id_layout(self):
        name = "commonresource/Back.xml"
        base = encode_aov_resource(action_xml("BackTrack"), name, "zstd")
        overlay_root = ET.Element("Project")
        action = ET.SubElement(overlay_root, "Action")
        track = ET.SubElement(action, "Track", {
            "trackName": "0_BackTrack",
            "eventType": "TriggerParticleTick",
        })
        event = ET.SubElement(track, "Event", {
            "eventName": "TriggerParticleTick",
            "time": "0.000",
            "isDuration": "false",
        })
        ET.SubElement(event, "SkinOrAvatarList", {"id": "59300"})
        overlay = encode_aov_resource(ET.tostring(overlay_root), name, "zstd")
        with self.assertRaises(CommonActionsMergeError):
            merge_action_xml(base, overlay, name, special_ids={"59300"})

    def test_actor_overlay_does_not_import_unrequested_skin_ids(self):
        name = "commonresource/Back.xml"
        base = encode_aov_resource(action_xml("BackTrack"), name, "zstd")
        overlay_root = ET.Element("Project")
        overlay_action = ET.SubElement(overlay_root, "Action")
        track = ET.SubElement(overlay_action, "Track", {
            "trackName": "0_BackTrack",
            "eventType": "TriggerParticleTick",
            "enabled": "true",
        })
        ET.SubElement(track, "SkinOrAvatarList", {"id": "59300"})
        ET.SubElement(track, "SkinOrAvatarList", {"id": "59400"})
        overlay = encode_aov_resource(ET.tostring(overlay_root), name, "zstd")

        merged, report = merge_action_xml(
            base, overlay, name, special_ids={"59300"}
        )
        decoded, _ = decode_aov_resource(merged, name)
        result_track = ET.fromstring(decoded).find("Action").find("Track")
        ids = {
            node.attrib["id"]
            for node in result_track.findall("SkinOrAvatarList")
        }
        self.assertEqual(ids, {"59300"})
        self.assertEqual(report["special_ids"], ["59300"])

    def test_merges_multiple_actor_id_groups_without_losing_tracks(self):
        path = "commonresource/Back.xml"
        base = package({
            path: encode_aov_resource(action_xml("BaseBackTrack"), path, "zstd")
        })
        overlay_593 = package({
            path: encode_aov_resource(
                action_xml("0_BaseBackTrack", True, "59300"), path, "zstd"
            )
        })
        overlay_595 = package({
            path: encode_aov_resource(
                action_xml("1_BaseBackTrack", True, "59500"), path, "zstd"
            )
        })

        merged, report = merge_common_actions_packages(
            base,
            [(overlay_593, {"59300"}), (overlay_595, {"59500"})],
        )
        with zipfile.ZipFile(io.BytesIO(merged)) as archive:
            decoded, _ = decode_aov_resource(archive.read(path), path)
        action = ET.fromstring(decoded).find("Action")
        tracks = action.findall("Track")
        ids = {
            node.attrib["id"]
            for track in tracks
            for node in track.findall("SkinOrAvatarList")
        }
        self.assertEqual(ids, {"59300", "59500"})
        self.assertEqual(len(tracks), 1)
        self.assertEqual(report["overlay_count"], 2)
        self.assertTrue(report["verified"])

    def test_multi_overlay_merge_rejects_opaque_same_path_conflict(self):
        base = package({"commonresource/Other.xml": b"base"})
        overlay = package({"commonresource/Other.xml": b"overlay"})
        with self.assertRaises(CommonActionsMergeError):
            merge_common_actions_packages(base, [(overlay, {"59300"})])

    def test_package_merge_replaces_only_selected_xml(self):
        path = "commonresource/Back.xml"
        other = "commonresource/Unrelated.xml"
        base = package({
            path: encode_aov_resource(action_xml("BackTrack"), path, "zstd"),
            other: b"base",
        })
        overlay = package({
            path: encode_aov_resource(action_xml("0_BackTrack", True), path, "zstd"),
            other: b"overlay",
        })
        merged, report = merge_common_actions_pkg(base, overlay)
        with zipfile.ZipFile(io.BytesIO(merged)) as z:
            decoded, _ = decode_aov_resource(z.read(path), path)
            root = ET.fromstring(decoded)
            self.assertEqual(root.find("Action").find("Track").find("SkinOrAvatarList").attrib["id"], "59500")
            self.assertEqual(z.read(other), b"base")
        self.assertEqual(report["merged_files"][path]["replaced_tracks"], ["BackTrack"])
        self.assertTrue(report["verified"])


    def test_multi_overlay_merge_rejects_conflicting_same_actor_track_conditions(self):
        path = "commonresource/Back.xml"

        def actor_overlay(track_name, mode):
            root = ET.Element("Project")
            action = ET.SubElement(root, "Action")
            track = ET.SubElement(action, "Track", {
                "trackName": track_name,
                "eventType": "TriggerParticleTick",
                "enabled": "true",
            })
            ET.SubElement(track, "SkinOrAvatarList", {
                "id": "59300",
                "mode": mode,
            })
            return package({
                path: encode_aov_resource(ET.tostring(root), path, "zstd")
            })

        base = package({
            path: encode_aov_resource(action_xml("BaseBackTrack"), path, "zstd")
        })
        first = actor_overlay("0_BaseBackTrack", "recall-a")
        second = actor_overlay("1_BaseBackTrack", "recall-b")

        with self.assertRaisesRegex(
            CommonActionsMergeError,
            "nhiều overlay định nghĩa điều kiện khác nhau",
        ):
            merge_common_actions_packages(
                base,
                [(first, {"59300"}), (second, {"59300"})],
            )

    def test_multi_overlay_merge_keeps_identical_same_actor_condition_once(self):
        path = "commonresource/Back.xml"
        overlay_root = ET.Element("Project")
        action = ET.SubElement(overlay_root, "Action")
        track = ET.SubElement(action, "Track", {
            "trackName": "0_BaseBackTrack",
            "eventType": "TriggerParticleTick",
            "enabled": "true",
        })
        ET.SubElement(track, "SkinOrAvatarList", {
            "id": "59300",
            "mode": "recall-a",
        })
        overlay = package({
            path: encode_aov_resource(ET.tostring(overlay_root), path, "zstd")
        })
        base = package({
            path: encode_aov_resource(action_xml("BaseBackTrack"), path, "zstd")
        })

        merged, report = merge_common_actions_packages(
            base,
            [(overlay, {"59300"}), (overlay, {"59300"})],
        )
        with zipfile.ZipFile(io.BytesIO(merged)) as archive:
            decoded, _ = decode_aov_resource(archive.read(path), path)
        action_out = ET.fromstring(decoded).find("Action")
        matching = [
            node for node in action_out.iter("SkinOrAvatarList")
            if node.attrib.get("id") == "59300"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].attrib["mode"], "recall-a")
        self.assertEqual(report["overlay_count"], 2)
        self.assertTrue(report["verified"])

if __name__ == "__main__":
    unittest.main()
