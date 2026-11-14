from __future__ import annotations

import io
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.main import _aov_download_filename
from app.mod_pipeline import analyze_aov_input_set


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_INDEX = REPO_ROOT / "web" / "index.html"

P9_ACTORS = {"107", "130", "137", "163", "509", "510", "518", "540", "597"}
P10_ACTORS = {"111", "132", "150", "157", "167", "190", "524", "567", "598", "599"}
P9_P10_ADAPTER = "aov_p9_p10_skin_union_1.64.1"


def _nested_resources(actor_ids: set[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", allowZip64=True) as archive:
        for actor_id in sorted(actor_ids):
            archive.writestr(
                "Resources/1.64.1/Ages/Prefab_Characters/Prefab_Hero/"
                f"Actor_{actor_id}_Actions.pkg.bytes",
                b"synthetic actor-action fixture",
            )
    return buffer.getvalue()


def _wrapper(path: Path, family: str, actor_ids: set[str]) -> str:
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        archive.writestr("mod/Resources.zip", _nested_resources(actor_ids))
        archive.writestr("mod/DanhSachSkin.txt", f"{family} test fixture\n")
    return str(path)


class P9P10WebAdapterRoutingTests(unittest.TestCase):
    def test_exact_p9_p10_set_is_merge_ready_in_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p9 = _wrapper(root / "P9.zip", "P9", P9_ACTORS)
            p10 = _wrapper(root / "P10.zip", "P10", P10_ACTORS)

            report = analyze_aov_input_set([p9, p10])

        self.assertEqual(report["selected_adapter"], P9_P10_ADAPTER)
        self.assertTrue(report["ready_to_merge"])
        self.assertEqual(report["blockers"], [])

    def test_p9_p10_download_uses_its_own_file_name(self):
        self.assertEqual(
            _aov_download_filename(P9_P10_ADAPTER),
            "Aki-AOV-P9-P10-19Skin.zip",
        )

    def test_unknown_adapter_keeps_the_legacy_filename_fallback(self):
        self.assertEqual(
            _aov_download_filename("unknown-adapter"),
            "Aki-AOV-KPM100-VTK.zip",
        )

    def test_every_merge_capability_is_wired_into_both_web_flows(self):
        html = WEB_INDEX.read_text(encoding="utf-8")
        expected = {
            "aov_kpm100_vtk_actor595_1.64.1",
            "aov_mod_to_mod_vtk_actor595_1.64.1",
            P9_P10_ADAPTER,
        }
        auto_match = re.search(
            r"const supportedAovAdapters=\[(.*?)\];", html, re.DOTALL
        )
        capability_match = re.search(
            r"const mergeableIds=new Set\(\[(.*?)\]\);", html, re.DOTALL
        )
        self.assertIsNotNone(auto_match, "automatic merge adapter list is missing")
        self.assertIsNotNone(
            capability_match, "capability-driven button adapter list is missing"
        )
        for adapter_id in expected:
            with self.subTest(adapter=adapter_id):
                self.assertIn(adapter_id, auto_match.group(1))
                self.assertIn(adapter_id, capability_match.group(1))


if __name__ == "__main__":
    unittest.main()
