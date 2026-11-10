"""Conservative outer-pack merger for AoV mod ZIPs.

Supports nested Resources.zip containers, MSES v7 row merging, and targeted
CommonActions XML tracks. Unknown conflicting binary formats fail closed.
"""
from __future__ import annotations

import copy
import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .aov_commonactions import CommonActionsMergeError, merge_common_actions
from .aov_crypto import AoVResourceError, decode_aov_resource
from .aov_mses import MSESError, merge_aov_mses_resource, parse_mses


MAX_INPUTS = 10
MAX_INPUT_BYTES = 500 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
ACTOR_ACTION_RE = re.compile(r"Actor_(\d{3,6})_Actions\.pkg\.bytes$", re.IGNORECASE)
MACOS_PREFIXES = ("__MACOSX/",)
ANDROID_TOKENS = ("/android/", "android/")


class AOVPackageMergeError(ValueError):
    """AOV package merge could not be completed without risking data loss."""


@dataclass(frozen=True)
class AOVPackageMergeReport:
    added_files: int
    merged_mses: tuple[str, ...]
    commonactions_tracks_added: int
    skipped_android_files: int
    warnings: tuple[str, ...]


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\x00" in normalized:
        raise AOVPackageMergeError(f"Đường dẫn ZIP không an toàn: {name}")
    return normalized


def _is_android(name: str) -> bool:
    lower = "/" + name.replace("\\", "/").lower().lstrip("/")
    return any(token in lower for token in ANDROID_TOKENS)


def _is_mses(payload: bytes, name: str) -> bool:
    try:
        return decode_aov_resource(name, payload).startswith(b"MSES")
    except AoVResourceError:
        return False


def _resource_root(names: list[str]) -> str:
    for name in names:
        parts = name.split("/")
        if len(parts) >= 3 and parts[0].lower() == "resources":
            return "/".join(parts[:2]) + "/"
    return ""


def _zip_payloads(data: bytes, label: str) -> tuple[zipfile.ZipFile, io.BytesIO]:
    buffer = io.BytesIO(data)
    try:
        archive = zipfile.ZipFile(buffer, "r")
        if len(archive.infolist()) > MAX_ENTRIES:
            archive.close()
            raise AOVPackageMergeError(f"{label}: quá nhiều mục trong ZIP.")
        return archive, buffer
    except (OSError, zipfile.BadZipFile) as exc:
        raise AOVPackageMergeError(f"Không mở được ZIP: {label}") from exc


def _read_entries(archive: zipfile.ZipFile, label: str) -> tuple[list[zipfile.ZipInfo], dict[str, bytes]]:
    infos = [info for info in archive.infolist() if not info.is_dir()]
    payloads: dict[str, bytes] = {}
    for info in infos:
        name = _safe_name(info.filename)
        if name in payloads:
            raise AOVPackageMergeError(f"{label}: đường dẫn lặp {name}")
        if info.flag_bits & 1:
            raise AOVPackageMergeError(f"{label}: ZIP mã hóa mật khẩu không hỗ trợ ({name})")
        payloads[name] = archive.read(info)
    return infos, payloads


def _normalize_incoming_name(name: str, root: str) -> str:
    normalized = _safe_name(name)
    parts = normalized.split("/")
    if len(parts) >= 3 and parts[0].lower() == "resources":
        return normalized
    return root + normalized


def merge_aov_archives(
    input_paths: list[str],
    output_path: str,
    *,
    target_skin_ids: tuple[str, ...] | list[str] | None = None,
) -> tuple[int, AOVPackageMergeReport]:
    """Merge an outer AoV pack and loose resource ZIPs, failing on unknown conflicts."""
    if not input_paths or len(input_paths) > MAX_INPUTS:
        raise AOVPackageMergeError(f"Chọn từ 1 đến {MAX_INPUTS} ZIP.")
    try:
        total_input = sum(Path(path).stat().st_size for path in input_paths)
    except OSError as exc:
        raise AOVPackageMergeError("Không tìm thấy ZIP đầu vào.") from exc
    if total_input > MAX_INPUT_BYTES:
        raise AOVPackageMergeError("Tổng ZIP đầu vào vượt quá 500 MB.")

    output = Path(output_path)
    if any(Path(path).resolve() == output.resolve() for path in input_paths):
        raise AOVPackageMergeError("File kết quả không được trùng ZIP nguồn.")

    outer_archives: list[tuple[str, zipfile.ZipFile, io.BytesIO, list[zipfile.ZipInfo]]] = []
    resources_entry: tuple[str, zipfile.ZipInfo, bytes] | None = None
    base_outer_index: int | None = None
    try:
        for path in input_paths:
            label = Path(path).name
            archive, buffer = _zip_payloads(Path(path).read_bytes(), label)
            infos = [info for info in archive.infolist() if not info.is_dir()]
            outer_archives.append((label, archive, buffer, infos))
            matches = [info for info in infos if Path(info.filename).name.lower() == "resources.zip"]
            if matches:
                if resources_entry is not None:
                    raise AOVPackageMergeError("Có nhiều ZIP nguồn chứa Resources.zip; không xác định được gói nền.")
                if len(matches) != 1:
                    raise AOVPackageMergeError(f"{label}: có nhiều Resources.zip.")
                info = matches[0]
                resources_entry = (label, copy.copy(info), archive.read(info))
                base_outer_index = len(outer_archives) - 1

        if resources_entry is None or base_outer_index is None:
            raise AOVPackageMergeError(
                "Không tìm thấy gói nền có Resources.zip. Bộ xử lý AOV cần một ZIP nền rõ ràng."
            )

        base_label, resources_info, base_resources_data = resources_entry
        base_resources_archive, base_resources_buffer = _zip_payloads(
            base_resources_data, f"{base_label} :: Resources.zip"
        )
        try:
            base_resource_infos, resource_payloads = _read_entries(
                base_resources_archive, f"{base_label} :: Resources.zip"
            )
        finally:
            base_resources_archive.close()
            base_resources_buffer.close()

        root = _resource_root(list(resource_payloads))
        if not root:
            raise AOVPackageMergeError("Không xác định được thư mục phiên bản trong Resources.zip.")

        hero_prefixes: set[str] = set()
        incoming_items: list[tuple[str, zipfile.ZipInfo, bytes, str]] = []
        skipped_android = 0
        total_entries = len(resource_payloads)

        for archive_index, (label, archive, buffer, infos) in enumerate(outer_archives):
            for info in infos:
                # The base outer ZIP contributes its existing Resources.zip and wrapper
                # metadata only; do not copy manifest.json or other wrapper files into Resources.zip.
                if archive_index == base_outer_index:
                    continue
                name = _safe_name(info.filename)
                if any(name.startswith(prefix) for prefix in MACOS_PREFIXES):
                    continue
                if _is_android(name):
                    skipped_android += 1
                    continue
                if Path(name).name.lower() == "resources.zip":
                    nested_data = archive.read(info)
                    nested_archive, nested_buffer = _zip_payloads(nested_data, f"{label} :: {name}")
                    try:
                        nested_infos, nested_payloads = _read_entries(nested_archive, f"{label} :: {name}")
                    finally:
                        nested_archive.close()
                        nested_buffer.close()
                    for nested_info in nested_infos:
                        nested_name = _safe_name(nested_info.filename)
                        if any(nested_name.startswith(prefix) for prefix in MACOS_PREFIXES):
                            continue
                        if _is_android(nested_name):
                            skipped_android += 1
                            continue
                        full_name = _normalize_incoming_name(nested_name, root)
                        incoming_items.append((label, copy.copy(nested_info), nested_payloads[nested_name], full_name))
                    continue

                full_name = _normalize_incoming_name(name, root)
                payload = archive.read(info)
                incoming_items.append((label, copy.copy(info), payload, full_name))

        for _, _, _, name in incoming_items:
            match = ACTOR_ACTION_RE.search(Path(name).name)
            if match:
                hero_prefixes.add(match.group(1)[:3])

        if not hero_prefixes:
            raise AOVPackageMergeError(
                "Không tìm thấy Actor_<ID>_Actions.pkg.bytes để xác định ID tướng cho bảng MSES."
            )
        prefixes = tuple(sorted(hero_prefixes))
        skin_ids = tuple(sorted(set(target_skin_ids or [
            f"{prefix}00" for prefix in prefixes
        ] + [
            f"{prefix}01" for prefix in prefixes
        ])))

        merged_mses: list[str] = []
        tracks_added = 0
        added_files = 0
        warnings: list[str] = []
        unresolved: list[str] = []

        for label, info, incoming_payload, name in incoming_items:
            if name not in resource_payloads:
                resource_payloads[name] = incoming_payload
                info.filename = name
                base_resource_infos.append(info)
                added_files += 1
                total_entries += 1
                continue

            base_payload = resource_payloads[name]
            if hashlib.sha256(base_payload).digest() == hashlib.sha256(incoming_payload).digest():
                continue

            if _is_mses(base_payload, name) and _is_mses(incoming_payload, name):
                try:
                    merged_payload, report = merge_aov_mses_resource(
                        name,
                        base_payload,
                        incoming_payload,
                        target_prefixes=prefixes,
                    )
                    resource_payloads[name] = merged_payload
                    merged_mses.append(
                        f"{name}: +{report.rows_added} dòng, thay {report.rows_replaced} dòng"
                    )
                    continue
                except MSESError as exc:
                    unresolved.append(f"{name} [MSES: {exc}]")
                    continue

            if Path(name).name.lower() == "commonactions.pkg.bytes":
                try:
                    merged_payload, report = merge_common_actions(
                        base_payload,
                        incoming_payload,
                        target_skin_ids=skin_ids,
                    )
                    resource_payloads[name] = merged_payload
                    tracks_added += report.tracks_added
                    non_xml_skips = [
                        item for item in report.skipped_conflicting_files
                        if not item.lower().endswith(".xml")
                    ]
                    if non_xml_skips:
                        unresolved.extend(f"{name}!{item}" for item in non_xml_skips)
                    continue
                except CommonActionsMergeError as exc:
                    unresolved.append(f"{name} [CommonActions: {exc}]")
                    continue

            unresolved.append(f"{name} [xung đột nhị phân chưa có bộ ghép chuyên biệt]")

        if total_entries > MAX_ENTRIES:
            raise AOVPackageMergeError("Gói có quá nhiều tài nguyên sau khi hợp nhất.")
        if unresolved:
            details = "\n".join(f"- {item}" for item in sorted(set(unresolved))[:80])
            raise AOVPackageMergeError(
                "Chưa thể xuất gói AOV an toàn vì còn xung đột chưa có parser chuyên biệt:\n"
                + details
                + "\nKhông tạo file kết quả; ZIP nguồn được giữ nguyên."
            )

        # Build the inner Resources.zip in a spooled file.
        with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as inner_spool:
            with zipfile.ZipFile(inner_spool, "w", allowZip64=True) as inner_out:
                inner_out.comment = base_resources_archive.comment if "base_resources_archive" in locals() else b""
                written: set[str] = set()
                for info in base_resource_infos:
                    if info.filename in written:
                        continue
                    written.add(info.filename)
                    inner_out.writestr(copy.copy(info), resource_payloads[info.filename])
            inner_spool.seek(0)

            output.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
            )
            os.close(fd)
            temp_path = Path(temp_name)
            try:
                with zipfile.ZipFile(temp_path, "w", allowZip64=True) as outer_out:
                    base_label, base_archive, _, base_infos = outer_archives[base_outer_index]
                    outer_out.comment = base_archive.comment
                    for info in base_infos:
                        if info.filename == resources_info.filename:
                            with outer_out.open(copy.copy(resources_info), "w") as target:
                                shutil.copyfileobj(inner_spool, target, length=CHUNK_SIZE)
                            inner_spool.seek(0)
                        else:
                            outer_out.writestr(copy.copy(info), base_archive.read(info))
                if temp_path.stat().st_size > MAX_OUTPUT_BYTES:
                    raise AOVPackageMergeError("ZIP kết quả vượt quá 1 GB.")
                _verify_aov_output(temp_path, resources_info.filename, resource_payloads)
                os.replace(temp_path, output)
                temp_path = None
                return output.stat().st_size, AOVPackageMergeReport(
                    added_files=added_files,
                    merged_mses=tuple(merged_mses),
                    commonactions_tracks_added=tracks_added,
                    skipped_android_files=skipped_android,
                    warnings=tuple(warnings),
                )
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
    finally:
        for _, archive, buffer, _ in outer_archives:
            archive.close()
            buffer.close()


def _verify_aov_output(
    path: Path,
    resources_path: str,
    expected: dict[str, bytes],
) -> None:
    try:
        with zipfile.ZipFile(path, "r") as outer:
            info = next(
                info for info in outer.infolist()
                if info.filename == resources_path
            )
            nested_data = outer.read(info)
            with zipfile.ZipFile(io.BytesIO(nested_data), "r") as inner:
                if inner.testzip() is not None:
                    raise AOVPackageMergeError("CRC lỗi trong Resources.zip kết quả.")
                names = {info.filename for info in inner.infolist() if not info.is_dir()}
                if names != set(expected):
                    raise AOVPackageMergeError("Danh sách tài nguyên sau ghép không khớp.")
                for name, payload in expected.items():
                    if hashlib.sha256(inner.read(name)).digest() != hashlib.sha256(payload).digest():
                        raise AOVPackageMergeError(f"Xác minh SHA-256 thất bại: {name}")
    except (OSError, zipfile.BadZipFile, StopIteration) as exc:
        raise AOVPackageMergeError("Không thể xác minh ZIP AOV đầu ra.") from exc
