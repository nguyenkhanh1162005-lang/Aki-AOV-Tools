from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.aov_bytes_archive import AOVBytesArchiveError, decode_archive_resources
from app.aov_merger import AOVMergeError, _find_outer_entries, merge_aov_archives


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_summary(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        bad = archive.testzip()
        if bad:
            raise ValueError(f"ZIP CRC không hợp lệ tại {bad}: {path.name}")
        return {
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "entries": len(infos),
            "uncompressed_bytes": sum(item.file_size for item in infos),
            "crc": "passed",
        }


def _resource_inventory(wrapper_path: Path, output_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(wrapper_path, "r") as base_outer, zipfile.ZipFile(output_path, "r") as merged_outer:
        base_resources = _find_outer_entries(base_outer, "Resources.zip")
        merged_resources = _find_outer_entries(merged_outer, "Resources.zip")
        if len(base_resources) != 1 or len(merged_resources) != 1:
            raise ValueError("ZIP wrapper đầu ra không có đúng một Resources.zip.")
        with zipfile.ZipFile(io.BytesIO(base_outer.read(base_resources[0])), "r") as base_inner:
            with zipfile.ZipFile(io.BytesIO(merged_outer.read(merged_resources[0])), "r") as merged_inner:
                base_names = {
                    info.filename for info in base_inner.infolist() if not info.is_dir()
                }
                merged_names = {
                    info.filename for info in merged_inner.infolist() if not info.is_dir()
                }
                missing = sorted(base_names - merged_names)
                bad = merged_inner.testzip()
                if bad:
                    raise ValueError(f"Resources.zip sau ghép lỗi CRC tại {bad}")
                return {
                    "base_resource_entries": len(base_names),
                    "merged_resource_entries": len(merged_names),
                    "base_paths_preserved": len(base_names) - len(missing),
                    "base_paths_missing": missing[:100],
                    "inner_crc": "passed",
                    "base_paths_all_preserved": not missing,
                }


def run(wrapper_path: Path, vtk_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "Aki-AOV-10Skin-VanTamKiem-candidate.zip"
    scan_zip_path = output_dir / ".notice-scan-temporary.zip"
    report_path = output_dir / "wrapper-vtk-audit-report.json"
    report: dict[str, Any] = {
        "audit_type": "user_mod_wrapper_plus_vtk",
        "merge_status": "not_run",
        "game_tested": False,
        "sources": [],
        "notice_scan": {},
        "warning": (
            "Structural integration candidate only. No Liên Quân iOS runtime test has been performed. "
            "Notice scan is heuristic and does not authorize resource deletion."
        ),
    }
    try:
        report["sources"] = [
            _archive_summary(wrapper_path),
            _archive_summary(vtk_path),
        ]
    except Exception as exc:
        report["merge_status"] = "failed_source_validation"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    try:
        scan = decode_archive_resources(
            [str(wrapper_path), str(vtk_path)], str(scan_zip_path)
        )
        report["notice_scan"] = {
            "scanned_files": scan.get("notice_scan_files", 0),
            "candidate_count": len(scan.get("notice_candidates", [])),
            "candidates": scan.get("notice_candidates", []),
            "truncated": scan.get("notice_candidates_truncated", False),
            "scope": scan.get("notice_scan_scope", ""),
        }
    except AOVBytesArchiveError as exc:
        report["notice_scan"] = {
            "error": str(exc),
            "candidate_count": 0,
            "candidates": [],
        }
    finally:
        scan_zip_path.unlink(missing_ok=True)

    try:
        merge_report = merge_aov_archives(
            [str(wrapper_path), str(vtk_path)], str(output_path)
        )
        output_summary = _archive_summary(output_path)
        inventory = _resource_inventory(wrapper_path, output_path)
        if not inventory["base_paths_all_preserved"]:
            raise ValueError(
                "ZIP kết quả làm mất đường dẫn nền: "
                + ", ".join(inventory["base_paths_missing"][:10])
            )
        report["merge_status"] = "structurally_validated"
        report["output"] = output_summary
        report["inventory"] = inventory
        report["merge"] = {
            "base_version": merge_report.get("base_version"),
            "vtk_version": merge_report.get("vtk_version"),
            "ifix_fixes": merge_report.get("ifix_fixes"),
            "ifix_methods": merge_report.get("ifix_methods"),
            "common_actions_files": merge_report.get("common_actions_files"),
            "common_actions_tracks": merge_report.get("common_actions_tracks"),
            "assetbundles": merge_report.get("assetbundles"),
            "new_files": merge_report.get("new_files"),
            "packer_entries": merge_report.get("packer_entries"),
            "packer_replaced": merge_report.get("packer_replaced"),
            "packer_added": merge_report.get("packer_added"),
            "validation": merge_report.get("validation"),
            "game_tested": False,
        }
    except (AOVMergeError, ValueError, OSError, zipfile.BadZipFile) as exc:
        report["merge_status"] = "failed_merge_validation"
        report["error"] = f"{type(exc).__name__}: {exc}"
        output_path.unlink(missing_ok=True)

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: audit_wrapper_vtk_pair.py WRAPPER_10SKIN.zip VTK.zip OUTPUT_DIR",
            file=sys.stderr,
        )
        return 2
    wrapper_path, vtk_path, output_dir = map(Path, sys.argv[1:])
    report = run(wrapper_path, vtk_path, output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("merge_status") == "structurally_validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
