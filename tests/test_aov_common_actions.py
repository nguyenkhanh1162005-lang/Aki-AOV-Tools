import io
import unittest
import zipfile
from xml.etree import ElementTree as ET

from app.aov_bytes import encode_aov_resource, decode_aov_resource
from app.aov_common_actions import merge_action_xml, merge_common_actions_pkg


def action_xml(track_name, special=False):
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
        ET.SubElement(track, "SkinOrAvatarList", {"id": "59500"})
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


if __name__ == "__main__":
    unittest.main()
