from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
import zlib
from pathlib import Path, PurePosixPath

MAX_FILES = 10
MAX_INPUT_BYTES = 500 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 20000
CHUNK_SIZE = 1024 * 1024


class MergeError(ValueError):
    pass


def _safe_name(name: str) -> str:
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise MergeError("ZIP chứa đường dẫn không an toàn.")
    if "\x00" in name:
        raise MergeError("ZIP chứa tên tệp không hợp lệ.")
    return name


def _digest_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    try:
        with archive.open(info, "r") as source:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except (RuntimeError, zipfile.BadZipFile, OSError, zlib.error, EOFError, NotImplementedError) as exc:
        raise MergeError(f"Không đọc được tệp: {info.filename}") from exc
    return digest.hexdigest()


def merge_zip_files(input_paths: list[str], output_path: str) -> int:
    """Merge ordinary ZIP archives using disk-backed, two-pass processing."""
    if not input_paths or len(input_paths) > MAX_FILES:
        raise MergeError(f"Chọn từ 1 đến {MAX_FILES} file ZIP.")

    try:
        total_input = sum(Path(path).stat().st_size for path in input_paths)
    except OSError as exc:
        raise MergeError("Không đọc được thông tin ZIP đầu vào.") from exc
    if total_input > MAX_INPUT_BYTES:
        raise MergeError("Tổng dung lượng ZIP vượt quá 500 MB.")

    seen: dict[str, str] = {}
    unique_entries: list[tuple[int, zipfile.ZipInfo, str]] = []
    entry_count = 0
    total_uncompressed = 0
    archives: list[zipfile.ZipFile] = []

    try:
        for archive_index, path in enumerate(input_paths):
            try:
                archive = zipfile.ZipFile(path, "r")
            except (zipfile.BadZipFile, OSError) as exc:
                raise MergeError(f"Không mở được ZIP: {Path(path).name}") from exc
            archives.append(archive)

            for info in archive.infolist():
                if info.is_dir():
                    continue
                entry_count += 1
                if entry_count > MAX_ENTRIES:
                    raise MergeError("ZIP có quá nhiều tệp.")
                name = _safe_name(info.filename)
                if info.flag_bits & 1:
                    raise MergeError("Không hỗ trợ ZIP được mã hóa mật khẩu.")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise MergeError("Không hỗ trợ symbolic link trong ZIP.")
                if info.file_size > MAX_ENTRY_BYTES:
                    raise MergeError("Một tệp trong ZIP vượt quá giới hạn 512 MiB.")

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_OUTPUT_BYTES:
                    raise MergeError("Tổng dung lượng giải nén vượt quá 1 GB.")

                digest = _digest_entry(archive, info)
                if name in seen:
                    if seen[name] != digest:
                        raise MergeError(f"Trùng tên nhưng nội dung khác nhau: {name}")
                    continue
                seen[name] = digest
                unique_entries.append((archive_index, info, name))

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(output, "w", allowZip64=True) as result:
                for archive_index, info, name in unique_entries:
                    source_archive = archives[archive_index]
                    target_info = zipfile.ZipInfo(name, date_time=info.date_time)
                    target_info.compress_type = zipfile.ZIP_DEFLATED
                    target_info.external_attr = info.external_attr
                    target_info.create_system = info.create_system
                    with source_archive.open(info, "r") as source, result.open(
                        target_info, "w"
                    ) as target:
                        shutil.copyfileobj(source, target, length=CHUNK_SIZE)
            size = output.stat().st_size
            if size > MAX_OUTPUT_BYTES:
                output.unlink(missing_ok=True)
                raise MergeError("File ZIP kết quả vượt quá 1 GB.")
            return size
        except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error, EOFError) as exc:
            output.unlink(missing_ok=True)
            raise MergeError("Không thể tạo file ZIP kết quả.") from exc
    finally:
        for archive in archives:
            archive.close()
