from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.kpm100_vtk_merger import build as build_kpm100_vtk


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: run_aov_integration.py BASE_KPM100.zip VTK.zip OUTPUT.zip", file=sys.stderr)
        return 2

    base_path, vtk_path, output_path = map(str, sys.argv[1:])
    for path in (base_path, vtk_path):
        if not Path(path).is_file():
            raise SystemExit(f"Missing integration input: {path}")

    report_path = output_path + ".report.json"
    report = build_kpm100_vtk(argparse.Namespace(
        base=base_path,
        vtk=vtk_path,
        output=output_path,
        report=report_path,
    ))

    output = Path(output_path)
    if not output.is_file() or output.stat().st_size <= 0:
        raise SystemExit("Merger did not create a non-empty output ZIP.")

    with zipfile.ZipFile(base_path, "r") as base, zipfile.ZipFile(output, "r") as result:
        bad = result.testzip()
        if bad:
            raise SystemExit(f"Output ZIP CRC failure: {bad}")
        base_names = {i.filename for i in base.infolist() if not i.is_dir()}
        result_names = {i.filename for i in result.infolist() if not i.is_dir()}
        missing_base = sorted(base_names - result_names)
        if missing_base:
            raise SystemExit(f"Output lost {len(missing_base)} KPM100 entries: {missing_base[:10]}")
        if len(result_names) != report["output_entries"]:
            raise SystemExit("Output entry count differs from merger report.")

        expected_resources = {
            "Resources/1.64.1/Thanos/HeroSkin_1.bytes",
            "Resources/1.64.1/Ages/Prefab_Characters/Prefab_Hero/Actor_595_Actions.pkg.bytes",
            "Resources/1.64.1/Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes",
            "Resources/1.64.1/Prefab_Characters/Actor_595_Infos.pkg.bytes",
            "Resources/1.64.1/AssetRefs/Hero/595_AssetRef.bytes",
        }
        missing_resources = sorted(expected_resources - result_names)
        if missing_resources:
            raise SystemExit("Output missing merged Actor595 resources: " + ", ".join(missing_resources))

        expected_assetbundles = {
            "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base.assetbundle",
            "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base_raw_h.assetbundle",
            "Resources/1.64.1/assetbundle/battle/hero/595_lixin_battle_base_raw_mat.assetbundle",
            "Resources/1.64.1/assetbundle/commercializenotify/battle/commercializenotify_blue_595.assetbundle",
            "Resources/1.64.1/assetbundle/commercializenotify/battle/commercializenotify_red_595.assetbundle",
            "Resources/1.64.1/assetbundle/icon/hero/595_305950_default59500_bust.assetbundle",
            "Resources/1.64.1/assetbundle/show/hero/595_lixin_show_base.assetbundle",
        }
        missing_bundles = sorted(expected_assetbundles - result_names)
        if missing_bundles:
            raise SystemExit("Output missing Actor595 AssetBundles: " + ", ".join(missing_bundles))

        if report["kpm_mod_payloads"] != {"skill": 68, "assetref": 1, "actorinfo": 1}:
            raise SystemExit(f"Unexpected KPM_MOD payload merge counts: {report['kpm_mod_payloads']}")
        if not report.get("ifix_fixes") or not report.get("ifix_methods"):
            raise SystemExit("IFix merge did not report merged fixes and methods.")

    result = {
        "output": str(output),
        "output_bytes": output.stat().st_size,
        "output_sha256": report["output_sha256"],
        "base_entries": report["base_entries"],
        "output_entries": report["output_entries"],
        "kpm_mod_payloads": report["kpm_mod_payloads"],
        "ifix_fixes": report["ifix_fixes"],
        "ifix_methods": report["ifix_methods"],
        "common_actions": report["common_actions"],
        "mses": report["mses"],
        "packer": report["packer"],
        "verification": report["verification"],
        "validation": report["validation"],
        "game_tested": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
