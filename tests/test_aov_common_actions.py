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
    analyze_common_actions_overlay_set,
    _xml_resource_is_semantic_subset,
    _common_actions_opaque_path_conflicts,
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



    def test_multi_overlay_analysis_reports_incompatible_actor_conditions(self):
        path = "commonresource/Back.xml"

        def overlay(mode):
            root = ET.Element("Project")
            action = ET.SubElement(root, "Action")
            track = ET.SubElement(action, "Track", {
                "trackName": "0_RecallTrack",
                "eventType": "TriggerParticleTick",
                "enabled": "true",
            })
            ET.SubElement(track, "SkinOrAvatarList", {
                "id": "59300",
                "mode": mode,
            })
            payload = encode_aov_resource(ET.tostring(root), path, "zstd")
            return package({path: payload})

        report = analyze_common_actions_overlay_set([
            ("Actor593-source-a.zip", overlay("recall-a")),
            ("Actor593-source-b.zip", overlay("recall-b")),
        ])

        self.assertEqual(report["state"], "BLOCKED")
        self.assertIn("nhiều overlay định nghĩa điều kiện khác nhau", report["reason"])
        self.assertFalse(report["merge_authorized"])
        self.assertFalse(report["full_merge_authorized"])
        self.assertTrue(report["output_discarded"])


    def test_multi_overlay_analysis_lists_opaque_path_conflicts(self):
        path = "commonresource/Back.xml"
        opaque = "commonresource/HasteE1_leave.xml"

        def overlay(skin_id, opaque_payload):
            root = ET.Element("Project")
            action = ET.SubElement(root, "Action")
            track = ET.SubElement(action, "Track", {
                "trackName": "0_RecallTrack",
                "eventType": "TriggerParticleTick",
                "enabled": "true",
            })
            ET.SubElement(track, "SkinOrAvatarList", {"id": skin_id})
            payload = encode_aov_resource(ET.tostring(root), path, "zstd")
            return package({path: payload, opaque: opaque_payload})

        report = analyze_common_actions_overlay_set([
            ("P9-source.zip", overlay("59300", b"opaque-a")),
            ("VTK-source.zip", overlay("59500", b"opaque-b")),
        ])

        self.assertEqual(report["state"], "BLOCKED")
        self.assertEqual(report["unsupported_path_conflict_count"], 1)
        self.assertEqual(
            report["unsupported_path_conflicts"][0]["path"], opaque
        )
        self.assertFalse(report["merge_authorized"])
        self.assertTrue(report["output_discarded"])

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

    def test_single_overlay_rejects_conflicting_duplicate_actor_conditions(self):
        path = "commonresource/Back.xml"
        base = package({
            path: encode_aov_resource(action_xml("BaseBackTrack"), path, "zstd")
        })
        root = ET.Element("Project")
        action = ET.SubElement(root, "Action")
        track = ET.SubElement(action, "Track", {
            "trackName": "0_BaseBackTrack",
            "eventType": "TriggerParticleTick",
            "enabled": "true",
        })
        ET.SubElement(track, "SkinOrAvatarList", {
            "id": "59500",
            "mode": "recall-a",
        })
        ET.SubElement(track, "SkinOrAvatarList", {
            "id": "59500",
            "mode": "recall-b",
        })
        overlay = package({
            path: encode_aov_resource(ET.tostring(root), path, "zstd")
        })

        with self.assertRaisesRegex(
            CommonActionsMergeError,
            "một overlay tự khai báo nhiều điều kiện khác nhau",
        ):
            merge_common_actions_pkg(
                base, overlay, special_ids={"59500"}
            )


    def test_semantic_xml_subset_accepts_raw_xml_and_zstd_encoding(self):
        name = "PassiveResource/junglemark.xml"

        def root_with_tracks(names):
            root = ET.Element("Project")
            ET.SubElement(root, "TemplateObjectList")
            ET.SubElement(root, "RefParamList")
            action = ET.SubElement(root, "Action", {"length": "1.000", "loop": "false"})
            for track_name in names:
                ET.SubElement(action, "Track", {
                    "trackName": track_name,
                    "eventType": "HitTriggerTick",
                    "enabled": "true",
                })
            return ET.tostring(root, encoding="utf-8", xml_declaration=True)

        base = root_with_tracks(["HitTriggerTick0", "SkillFuncDuration0", "SetCameraHeightDuration0"])
        overlay = root_with_tracks(["HitTriggerTick0", "SkillFuncDuration0"])
        encoded_overlay = encode_aov_resource(overlay, name, "zstd")

        self.assertTrue(_xml_resource_is_semantic_subset(base, encoded_overlay, name))
        self.assertFalse(_xml_resource_is_semantic_subset(encoded_overlay, base, name))

    def test_strict_merge_keeps_base_xml_superset_and_skips_unowned_candidate(self):
        back = "commonresource/Back.xml"
        quick_back = "commonresource/QuickBack.xml"
        unused = "commonresource/HasteE1_super.xml"
        passive = "PassiveResource/junglemark.xml"

        base_root = ET.Element("Project")
        base_action = ET.SubElement(base_root, "Action")
        ET.SubElement(base_action, "Track", {
            "trackName": "BaseBack",
            "eventType": "TriggerParticleTick",
        })
        base_quick = ET.Element("Project")
        base_quick_action = ET.SubElement(base_quick, "Action")
        ET.SubElement(base_quick_action, "Track", {
            "trackName": "BaseQuickBack",
            "eventType": "TriggerParticleTick",
        })
        passive_base = ET.Element("Project")
        passive_action = ET.SubElement(passive_base, "Action")
        for track_name in ("HitTriggerTick0", "SkillFuncDuration0", "SetCameraHeightDuration0"):
            ET.SubElement(passive_action, "Track", {
                "trackName": track_name,
                "eventType": "HitTriggerTick",
            })
        unused_base = encode_aov_resource(action_xml("BaseUnused"), unused, "zstd")
        base = package({
            back: encode_aov_resource(ET.tostring(base_root), back, "zstd"),
            quick_back: encode_aov_resource(ET.tostring(base_quick), quick_back, "zstd"),
            unused: unused_base,
            passive: ET.tostring(passive_base, encoding="utf-8", xml_declaration=True),
        })

        overlay_back = ET.Element("Project")
        overlay_action = ET.SubElement(overlay_back, "Action")
        overlay_track = ET.SubElement(overlay_action, "Track", {
            "trackName": "0_QuickBackActor593",
            "eventType": "TriggerParticleTick",
            "enabled": "true",
        })
        ET.SubElement(overlay_track, "SkinOrAvatarList", {"id": "59300"})
        overlay_quick = ET.Element("Project")
        quick_action = ET.SubElement(overlay_quick, "Action")
        quick_track = ET.SubElement(quick_action, "Track", {
            "trackName": "0_QuickBackActor593",
            "eventType": "TriggerParticleTick",
            "enabled": "true",
        })
        ET.SubElement(quick_track, "SkinOrAvatarList", {"id": "59300"})
        unused_overlay = encode_aov_resource(
            action_xml("Unused595", True, "59500"), unused, "zstd"
        )
        passive_overlay_root = ET.Element("Project")
        passive_overlay_action = ET.SubElement(passive_overlay_root, "Action")
        for track_name in ("HitTriggerTick0", "SkillFuncDuration0"):
            ET.SubElement(passive_overlay_action, "Track", {
                "trackName": track_name,
                "eventType": "HitTriggerTick",
            })
        overlay = package({
            back: encode_aov_resource(ET.tostring(overlay_back), back, "zstd"),
            quick_back: encode_aov_resource(ET.tostring(overlay_quick), quick_back, "zstd"),
            unused: unused_overlay,
            passive: encode_aov_resource(
                ET.tostring(passive_overlay_root), passive, "zstd"
            ),
        })

        merged, report = merge_common_actions_pkg(
            base, overlay, special_ids={"59300"}, strict_conflicts=True
        )
        with zipfile.ZipFile(io.BytesIO(merged)) as archive:
            result = {name: archive.read(name) for name in archive.namelist()}
        quick_decoded, _ = decode_aov_resource(result[quick_back], quick_back)
        quick_ids = {
            node.attrib.get("id")
            for node in ET.fromstring(quick_decoded).iter("SkinOrAvatarList")
        }
        self.assertEqual(quick_ids, {"59300"})
        self.assertEqual(result[unused], unused_base)
        self.assertEqual(
            result[passive],
            ET.tostring(passive_base, encoding="utf-8", xml_declaration=True),
        )
        self.assertTrue(report["verified"])
        self.assertIn(quick_back, report["merged_files"])

    def test_opaque_inventory_recognizes_semantic_xml_subset(self):
        path = "PassiveResource/junglemark.xml"

        def xml_package(track_names, mode):
            root = ET.Element("Project")
            action = ET.SubElement(root, "Action", {"length": "1.000"})
            for track_name in track_names:
                ET.SubElement(action, "Track", {
                    "trackName": track_name,
                    "eventType": "HitTriggerTick",
                })
            raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            payload = raw if mode == "plain" else encode_aov_resource(raw, path, "zstd")
            return package({path: payload})

        conflicts, identical, subsets = _common_actions_opaque_path_conflicts([
            ("P9-source.zip", xml_package(["HitTriggerTick0", "SkillFuncDuration0", "SetCameraHeightDuration0"], "plain")),
            ("VTK-source.zip", xml_package(["HitTriggerTick0", "SkillFuncDuration0"], "zstd")),
        ])
        self.assertEqual(conflicts, [])
        self.assertEqual(identical, 0)
        self.assertEqual(subsets, [path])



    def test_preflight_filters_shared_common_action_ids_by_source_actor_prefix(self):
        path = "commonresource/Back.xml"
        root = ET.Element("Project")
        action = ET.SubElement(root, "Action")
        track = ET.SubElement(action, "Track", {
            "trackName": "0_SharedBack",
            "eventType": "TriggerParticleTick",
        })
        ET.SubElement(track, "SkinOrAvatarList", {"id": "59300"})
        ET.SubElement(track, "SkinOrAvatarList", {"id": "59500"})
        payload = encode_aov_resource(ET.tostring(root), path, "zstd")
        source = package({path: payload})

        report = analyze_common_actions_overlay_set([
            ("mixed-source.zip", source, {"593"}),
        ])

        self.assertEqual(report["skin_ids"], ["59300"])
        self.assertEqual(
            report["packages"][0]["actor_prefixes"], ["593"]
        )
        self.assertEqual(report["packages"][0]["actor_gate_condition_count"], 1)
        self.assertFalse(report["merge_authorized"])


if __name__ == "__main__":
    unittest.main()
