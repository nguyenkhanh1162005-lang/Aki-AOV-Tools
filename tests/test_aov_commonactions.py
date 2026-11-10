import io
import unittest
import uuid
import zipfile
from xml.etree import ElementTree as ET

from app.aov_commonactions import merge_common_actions
from app.aov_zstd import decode_aov_bytes, encode_aov_bytes


def make_pkg(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class CommonActionsMergeTests(unittest.TestCase):
    def test_appends_only_tracks_for_requested_skin_ids_and_keeps_other_entries(self):
        base_xml = (
            b'<Project><Action>'
            b'<Track trackName="baseTrack" guid="base-guid"/>'
            b'</Action></Project>'
        )
        incoming_xml = (
            b'<Project><Action>'
            b'<Track trackName="skinTrack"><Value id="59500"/></Track>'
            b'<Track trackName="otherSkin"><Value id="59502"/></Track>'
            b'</Action></Project>'
        )
        base_pkg = make_pkg({
            "commonresource/Back.xml": encode_aov_bytes(base_xml),
            "keep.bin": b"base-data",
        })
        incoming_pkg = make_pkg({
            "commonresource/Back.xml": encode_aov_bytes(incoming_xml),
            "new.bin": b"incoming-unique",
        })

        merged, report = merge_common_actions(base_pkg, incoming_pkg)

        with zipfile.ZipFile(io.BytesIO(merged)) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, {
                "commonresource/Back.xml", "keep.bin", "new.bin"
            })
            self.assertEqual(archive.read("keep.bin"), b"base-data")
            self.assertEqual(archive.read("new.bin"), b"incoming-unique")
            root = ET.fromstring(
                decode_aov_bytes(archive.read("commonresource/Back.xml"))
            )

        tracks = {track.attrib["trackName"]: track for track in root.findall(".//Track")}
        self.assertEqual(set(tracks), {"baseTrack", "skinTrack"})
        self.assertEqual(tracks["skinTrack"].attrib["useRefParam"], "false")
        self.assertEqual(tracks["skinTrack"].attrib["refParamName"], "")
        uuid.UUID(tracks["skinTrack"].attrib["guid"])
        self.assertEqual(report.tracks_added, 1)
        self.assertEqual(report.changed_files, ("commonresource/Back.xml",))

    def test_keeps_base_xml_when_no_target_track_exists(self):
        xml = b'<Project><Action><Track trackName="base"/></Action></Project>'
        base_pkg = make_pkg({"commonresource/Back.xml": encode_aov_bytes(xml)})
        incoming_pkg = make_pkg({"commonresource/Back.xml": encode_aov_bytes(xml + b" ")})
        merged, report = merge_common_actions(base_pkg, incoming_pkg)
        self.assertEqual(report.tracks_added, 0)
        with zipfile.ZipFile(io.BytesIO(merged)) as archive:
            decoded = decode_aov_bytes(archive.read("commonresource/Back.xml"))
        self.assertEqual(decoded, xml)

    def test_rejects_non_zip_package(self):
        with self.assertRaises(ValueError):
            merge_common_actions(b"not a zip", b"not a zip")


if __name__ == "__main__":
    unittest.main()
