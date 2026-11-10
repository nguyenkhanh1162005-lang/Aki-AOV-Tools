from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import zlib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .large_merger import MergeError, merge_zip_files
from .aov_merger import AOVMergeError, merge_aov_archives

MAX_PROFILE_ENTRIES = 20_000
MAX_PROFILE_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_PROFILE_ENTRY = 512 * 1024 * 1024


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


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_profile(path: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            entries = archive.infolist()
            if len(entries) > MAX_PROFILE_ENTRIES:
                raise ModPipelineError(
                    f"ZIP có quá nhiều mục (tối đa {MAX_PROFILE_ENTRIES}): {Path(path).name}"
                )
            total_uncompressed = sum(info.file_size for info in entries)
            if total_uncompressed > MAX_PROFILE_UNCOMPRESSED:
                raise ModPipelineError(
                    f"ZIP vượt giới hạn giải nén 1 GiB: {Path(path).name}"
                )
            names = []
            for info in entries:
                if info.is_dir():
                    continue
                normalized = info.filename.replace("\\", "/")
                pure = PurePosixPath(normalized)
                mode = (info.external_attr >> 16) & 0xFFFF
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or "\x00" in normalized
                    or (pure.parts and pure.parts[0].endswith(":"))
                    or stat.S_ISLNK(mode)
                ):
                    raise ModPipelineError(f"Đường dẫn ZIP không an toàn: {info.filename}")
                if info.flag_bits & 1:
                    raise ModPipelineError(f"Không hỗ trợ ZIP mã hóa mật khẩu: {info.filename}")
                if info.file_size > MAX_PROFILE_ENTRY:
                    raise ModPipelineError(
                        f"Mục ZIP vượt giới hạn 512 MiB: {info.filename}"
                    )
                names.append(normalized)
            bad = archive.testzip()
            if bad:
                raise ModPipelineError(f"ZIP lỗi CRC tại {bad}: {Path(path).name}")
    except ModPipelineError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
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

    # This is a user-supplied 10-skin MOD container (Resources.zip + skin list),
    # not a request to use or inject the official game package.
    has_mod_skin_container = (
        "resources.zip" in leaves and "danhsachskin.txt" in leaves
    )
    has_vtk_ifix = "heroskin_2.bytes" in leaves
    has_595_assets = any(
        "595_" in PurePosixPath(name).name.lower()
        or "595" in PurePosixPath(name).name.lower()
        for name in names
        if PurePosixPath(name).suffix.lower() == ".assetbundle"
    )

    if not looks_like_aov:
        source_role = "ordinary_zip"
    elif has_mod_skin_container:
        source_role = "aov_skin_mod_container_candidate"
    elif has_vtk_ifix and has_595_assets:
        source_role = "aov_actor595_mod_candidate"
    else:
        source_role = "aov_mod_unclassified"

    return {
        "name": Path(path).name,
        "entries": len(names),
        "looks_like_aov": looks_like_aov,
        "source_role": source_role,
        "has_mod_skin_container": has_mod_skin_container,
        "has_vtk_ifix": has_vtk_ifix,
        "has_595_assets": has_595_assets,
        "extensions": sorted(extensions),
    }


def _select_adapter(profiles: list[dict[str, Any]]) -> str:
    if any(item["looks_like_aov"] for item in profiles):
        mod_containers = [
            item for item in profiles if item["has_mod_skin_container"]
        ]
        # VTK is often distributed as multiple ZIP parts. Its IFix payload
        # and Actor 595 AssetBundles may therefore be in different input files.
        # Validate the evidence across the complete overlay set, then let the
        # specialist merger verify the exact required asset list and versions.
        has_actor595_overlay = (
            any(item["has_vtk_ifix"] for item in profiles)
            and any(item["has_595_assets"] for item in profiles)
        )
        # The supported route is user-supplied mod pack + user-supplied mod
        # overlay. The 10-skin archive is a mod container, not the official game.
        supported_pair = (
            len(mod_containers) == 1
            and has_actor595_overlay
            and all(item["looks_like_aov"] for item in profiles)
        )
        if supported_pair:
            return "aov_mod_to_mod_vtk_actor595_1.64.1"

        names = ", ".join(item["name"] for item in profiles if item["looks_like_aov"])
        raise ModPipelineError(
            "Phát hiện tài nguyên AoV nhưng chưa có adapter ghép đã xác minh cho bộ này: "
            f"{names}. Không dùng gói game gốc làm nền và không ghi đè .bytes, "
            "AssetBundle hoặc soundbank theo kiểu ZIP thường. "
            "Adapter hiện có chỉ ghép gói mod 10 skin do người dùng cung cấp "
            "với mod Vấn Tâm Kiếm Actor 595 đúng phiên bản; các mod khác cần "
            "parser, serializer và quy tắc tương thích riêng."
        )
    return "generic_zip"


def merge_mod_archives(archive_paths: list[str], output_path: str) -> dict[str, Any]:
    """Select a verified adapter and merge transactionally.

    AoV packs without a dedicated parser/serializer fail closed. The supported
    Actor 595 route is mod-to-mod and never uses the official game package.
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
        if adapter == "aov_mod_to_mod_vtk_actor595_1.64.1":
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
            "official_game_package_injected": False,
            "input_sha256": [
                {"name": Path(path).name, "sha256": _sha256_file(path)}
                for path in archive_paths
            ],
            "output_sha256": _sha256_file(str(destination)),
        })
        return result
    except (MergeError, AOVMergeError) as exc:
        raise ModPipelineError(str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)
