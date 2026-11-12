from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

# Running this file as tools/run_aov_integration.py puts tools/ on sys.path,
# so explicitly add the repository root before importing the application package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.aov_merger import merge_aov_archives


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: run_aov_integration.py BASE_MOD.zip VTK_PARTS.zip OUTPUT.zip", file=sys.stderr)
        return 2

    base_path, vtk_path, output_path = map(str, sys.argv[1:])
    for path in (base_path, vtk_path):
        if not Path(path).is_file():
            raise SystemExit(f"Missing integration input: {path}")

    # Print a compact archive inventory before adapter selection so wrapper
    # detection failures can be diagnosed against the actual pinned ZIP layout.
    for path in (base_path, vtk_path):
        with zipfile.ZipFile(path, "r") as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
            relevant = [
                name for name in names
                if any(token in name.casefold() for token in (
                    "resources", "skin", "danhsach", "heroskin", "595", "lixin"
                ))
            ]
            print(json.dumps({
                "input": Path(path).name,
                "entry_count": len(names),
                "first_entries": names[:30],
                "relevant_entries": relevant[:100],
            }, ensure_ascii=False, indent=2), flush=True)

    report = merge_aov_archives([base_path, vtk_path], output_path)
    output = Path(output_path)
    if not output.is_file() or output.stat().st_size <= 0:
        raise SystemExit("Merger did not create a non-empty output ZIP.")

    with zipfile.ZipFile(output, "r") as outer:
        bad_outer = outer.testzip()
        if bad_outer:
            raise SystemExit(f"Outer ZIP CRC failure: {bad_outer}")
        nested_names = [
            info.filename for info in outer.infolist()
            if not info.is_dir() and info.filename.lower().endswith("/resources.zip")
        ]
        if not nested_names:
            raise SystemExit("Output mod wrapper has no nested Resources.zip.")
        if len(nested_names) != 1:
            raise SystemExit(f"Expected exactly one nested Resources.zip, got {len(nested_names)}.")
        nested_bytes = outer.read(nested_names[0])

    import io
    with zipfile.ZipFile(io.BytesIO(nested_bytes), "r") as inner:
        bad_inner = inner.testzip()
        if bad_inner:
            raise SystemExit(f"Nested Resources.zip CRC failure: {bad_inner}")
        inner_names = set(inner.namelist())

    expected_bundle_paths = {
        "Resources/1.64.1/" + item
        for item in (
            "assetbundle/battle/hero/595_lixin_battle_base.assetbundle",
            "assetbundle/battle/hero/595_lixin_battle_base_raw_h.assetbundle",
            "assetbundle/battle/hero/595_lixin_battle_base_raw_mat.assetbundle",
            "assetbundle/commercializenotify/battle/commercializenotify_blue_595.assetbundle",
            "assetbundle/commercializenotify/battle/commercializenotify_red_595.assetbundle",
            "assetbundle/icon/hero/595_305950_default59500_bust.assetbundle",
            "assetbundle/uisystem/battle/battleotherui.assetbundle",
            "assetbundle/uisystem/battle/battlecommon_raw.assetbundle",
            "assetbundle/uisystem/atlas/primary/allshared.assetbundle",
            "assetbundle/show/hero/595_lixin_show_base.assetbundle",
        )
    }
    missing = sorted(expected_bundle_paths - inner_names)
    if missing:
        raise SystemExit("Output is missing required Actor 595 AssetBundles: " + ", ".join(missing))

    # This integration check proves parser/serializer and archive structure on the
    # pinned source pair. It cannot prove that the game renders or plays assets.
    result = {
        "output": str(output),
        "output_bytes": output.stat().st_size,
        "base_version": report.get("base_version"),
        "vtk_version": report.get("vtk_version"),
        "ifix_fixes": report.get("ifix_fixes"),
        "ifix_methods": report.get("ifix_methods"),
        "common_actions_files": report.get("common_actions_files"),
        "common_actions_tracks": report.get("common_actions_tracks"),
        "mses_reports": report.get("mses_reports"),
        "assetbundles": report.get("assetbundles"),
        "new_files": report.get("new_files"),
        "packer_replaced": report.get("packer_replaced"),
        "packer_added": report.get("packer_added"),
        "verification_updated_count": len(report.get("verification_updated", [])),
        "outer_crc": "passed",
        "inner_crc": "passed",
        "required_actor_595_bundles": len(expected_bundle_paths),
        "required_actor_595_bundles_present": len(expected_bundle_paths & inner_names),
        "game_tested": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
