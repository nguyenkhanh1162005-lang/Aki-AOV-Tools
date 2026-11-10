from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_FILES = 10
MAX_INPUT_BYTES = 500 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 20000
CHUNK_SIZE = 1024 * 1024


class MergeError(ValueError):
    pass


def _safe_name(name: str) -> str:
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
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
    except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
        raise MergeError(f"Không đọc được tệp: {info.filename}") from exc
    return digest.hexdigest()


def _verify_output(path: Path, expected: dict[str, tuple[str, int]]) -> None:
    """Read the finished archive back and prove every retained payload survived."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = [info.filename for info in infos]
            if len(names) != len(expected) or set(names) != set(expected):
                raise MergeError("Kiểm tra sau khi ghép thất bại: danh sách tệp không khớp.")
            damaged = archive.testzip()
            if damaged is not None:
                raise MergeError(f"ZIP kết quả lỗi CRC tại: {damaged}")
            for info in infos:
                digest, size = expected[info.filename]
                if info.file_size != size or _digest_entry(archive, info) != digest:
                    raise MergeError(
                        f"Kiểm tra sau khi ghép thất bại, dữ liệu bị thay đổi: {info.filename}"
                    )
    except MergeError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise MergeError("Không thể đọc lại ZIP kết quả để xác minh.") from exc


def merge_zip_files(input_paths: list[str], output_path: str) -> int:
    """Merge ZIPs without overwriting conflicts; verify all payloads before publishing."""
    if not input_paths or len(input_paths) > MAX_FILES:
        raise MergeError(f"Chọn từ 1 đến {MAX_FILES} file ZIP.")

    try:
        total_input = sum(Path(path).stat().st_size for path in input_paths)
    except OSError as exc:
        raise MergeError("Không tìm thấy một hoặc nhiều file ZIP đầu vào.") from exc
    if total_input > MAX_INPUT_BYTES:
        raise MergeError("Tổng dung lượng ZIP vượt quá 500 MB.")

    output = Path(output_path)
    resolved_output = output.resolve()
    if any(Path(path).resolve() == resolved_output for path in input_paths):
        raise MergeError("File kết quả không được trùng với file ZIP nguồn.")

    seen: dict[str, str] = {}
    expected: dict[str, tuple[str, int]] = {}
    unique_entries: list[tuple[int, zipfile.ZipInfo, str]] = []
    entry_count = 0
    total_uncompressed = 0
    archives: list[zipfile.ZipFile] = []
    temporary_path: Path | None = None

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

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_OUTPUT_BYTES:
                    raise MergeError("Tổng dung lượng giải nén vượt quá 1 GB.")

                digest = _digest_entry(archive, info)
                if name in seen:
                    if seen[name] != digest:
                        raise MergeError(
                            f"Trùng tên nhưng nội dung khác nhau: {name}. "
                            "Đã dừng để tránh ghi đè hoặc làm mất tài nguyên."
                        )
                    continue
                seen[name] = digest
                expected[name] = (digest, info.file_size)
                unique_entries.append((archive_index, info, name))

        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
        )
        os.close(fd)
        temporary_path = Path(temp_name)

        try:
            with zipfile.ZipFile(temporary_path, "w", allowZip64=True) as result:
                for archive_index, info, name in unique_entries:
                    source_archive = archives[archive_index]
                    target_info = zipfile.ZipInfo(name, date_time=info.date_time)
                    target_info.compress_type = zipfile.ZIP_DEFLATED
                    target_info.external_attr = info.external_attr
                    target_info.create_system = info.create_system
                    target_info.comment = info.comment
                    with source_archive.open(info, "r") as source, result.open(
                        target_info, "w"
                    ) as target:
                        shutil.copyfileobj(source, target, length=CHUNK_SIZE)

            size = temporary_path.stat().st_size
            if size > MAX_OUTPUT_BYTES:
                raise MergeError("File ZIP kết quả vượt quá 1 GB.")

            _verify_output(temporary_path, expected)
            os.replace(temporary_path, output)
            temporary_path = None
            return output.stat().st_size
        except MergeError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise MergeError("Không thể tạo hoặc xác minh file ZIP kết quả.") from exc
    finally:
        for archive in archives:
            archive.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
