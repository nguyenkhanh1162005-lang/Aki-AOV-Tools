from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .large_merger import MergeError, merge_zip_files
from .aov_merger import AOVMergeError, merge_aov_archives


class ModPipelineError(ValueError):
    """Raised when an archive set cannot be merged by a verified adapter."""


AOV_MARKERS = {
    "resources.zip",
    "battlebank.bytes",
    "lobbybank.bytes",
    "heroskin_1.bytes",
    "heroskin_2.bytes",
    "commonactions.pkg.bytes",
    "resourcepackerinfosetall.assetbundle",
    "resourceverificationinfosetall.assetbundle",
}
AOV_EXTENSIONS = {".bytes", ".assetbundle", ".bnk", ".wem"}


def _archive_profile(path: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise ModPipelineError(f"ZIP lỗi CRC tại {bad}: {Path(path).name}")
            names = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                normalized = info.filename.replace("\\", "/")
                pure = PurePosixPath(normalized)
                if pure.is_absolute() or ".." in pure.parts or "\x00" in normalized:
                    raise ModPipelineError(f"Đường dẫn ZIP không an toàn: {info.filename}")
                names.append(normalized)
    except ModPipelineError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ModPipelineError(f"Không đọc được ZIP: {Path(path).name}") from exc

    leaves = {PurePosixPath(name).name.lower() for name in names}
    extensions = {PurePosixPath(name).suffix.lower() for name in names}
    has_resource_tree = any(
        name.lower().startswith(("resources/", "databin/", "ages/", "thanos/", "assetbundle/"))
        for name in names
    )
    looks_like_aov = bool(
        leaves.intersection(AOV_MARKERS)
        or (has_resource_tree and extensions.intersection(AOV_EXTENSIONS))
    )
    has_base = any(
        PurePosixPath(name).name.lower() == "resources.zip" for name in names
    ) and any(PurePosixPath(name).name.lower() == "danhsachskin.txt" for name in names)
    has_vtk_ifix = any(PurePosixPath(name).name.lower() == "heroskin_2.bytes" for name in names)
    has_595_assets = any(
        "595_" in PurePosixPath(name).name.lower()
        or "595" in PurePosixPath(name).name.lower()
        for name in names
        if PurePosixPath(name).suffix.lower() == ".assetbundle"
    )
    return {
        "name": Path(path).name,
        "entries": len(names),
        "looks_like_aov": looks_like_aov,
        "has_base": has_base,
        "has_vtk_ifix": has_vtk_ifix,
        "has_595_assets": has_595_assets,
        "extensions": sorted(extensions),
    }


def _select_adapter(profiles: list[dict[str, Any]]) -> str:
    if any(item["looks_like_aov"] for item in profiles):
        supported_pair = (
            sum(bool(item["has_base"]) for item in profiles) == 1
            and any(item["has_vtk_ifix"] and item["has_595_assets"] for item in profiles)
        )
        if supported_pair:
            return "aov_vtk_actor595_1.64.1"
        names = ", ".join(item["name"] for item in profiles if item["looks_like_aov"])
        raise ModPipelineError(
            "Phát hiện tài nguyên AoV nhưng chưa có adapter ghép đã xác minh cho bộ này: "
            f"{names}. Hệ thống không ghi đè .bytes, AssetBundle hoặc soundbank theo kiểu ZIP thường. "
            "Hiện adapter chuyên biệt chỉ nhận đúng bộ nền 10 skin + Vấn Tâm Kiếm Actor 595 "
            "đã khóa phiên bản; các loại mod khác cần parser và quy tắc tương thích riêng."
        )
    return "generic_zip"


def merge_mod_archives(archive_paths: list[str], output_path: str) -> dict[str, Any]:
    """Inspect, select a verified adapter, merge transactionally, and CRC-validate output.

    This intentionally fails closed for AoV resource packs without a dedicated adapter.
    A detector match is never treated as proof that two game resources are compatible.
    """
    if not archive_paths:
        raise ModPipelineError("Chưa có ZIP đầu vào.")
    profiles = [_archive_profile(path) for path in archive_paths]
    adapter = _select_adapter(profiles)

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp.zip", dir=str(destination.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink(missing_ok=True)

    try:
        if adapter == "aov_vtk_actor595_1.64.1":
            result = merge_aov_archives(archive_paths, str(temporary))
        else:
            size = merge_zip_files(archive_paths, str(temporary))
            result = {"output_bytes": size, "adapter": adapter}

        with zipfile.ZipFile(temporary, "r") as output_zip:
            bad = output_zip.testzip()
            if bad:
                raise ModPipelineError(f"ZIP kết quả lỗi CRC tại {bad}.")
        os.replace(temporary, destination)
        result.update({
            "adapter": adapter,
            "input_count": len(archive_paths),
            "input_profiles": profiles,
            "output_bytes": destination.stat().st_size,
            "structurally_validated": True,
            "game_compatibility_tested": False,
        })
        return result
    except (MergeError, AOVMergeError) as exc:
        raise ModPipelineError(str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)
