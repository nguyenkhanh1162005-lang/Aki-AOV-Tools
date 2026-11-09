from __future__ import annotations
import io, zipfile
from pathlib import PurePosixPath

MAX_FILES = 10
MAX_INPUT = 100 * 1024 * 1024
MAX_OUTPUT = 500 * 1024 * 1024
MAX_ENTRIES = 20000

class MergeError(ValueError):
    pass

def merge_zips(items: list[bytes]) -> bytes:
    if not items or len(items) > MAX_FILES:
        raise MergeError(f"Chọn từ 1 đến {MAX_FILES} file ZIP.")
    if sum(map(len, items)) > MAX_INPUT:
        raise MergeError("Tổng dung lượng ZIP vượt quá 100 MB.")
    output = io.BytesIO()
    seen: dict[str, bytes] = {}
    count = total = 0
    for raw in items:
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise MergeError("Có file không phải ZIP hợp lệ.") from exc
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                count += 1
                if count > MAX_ENTRIES:
                    raise MergeError("ZIP có quá nhiều tệp.")
                name = info.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise MergeError("ZIP chứa đường dẫn không an toàn.")
                if info.flag_bits & 1:
                    raise MergeError("Không hỗ trợ ZIP được mã hóa mật khẩu.")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise MergeError("Không hỗ trợ symbolic link trong ZIP.")
                try:
                    data = archive.read(info)
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    raise MergeError(f"Không đọc được tệp: {name}") from exc
                if name in seen:
                    if seen[name] != data:
                        raise MergeError(f"Trùng tên nhưng nội dung khác nhau: {name}")
                    continue
                seen[name] = data
                total += len(data)
                if total > MAX_OUTPUT:
                    raise MergeError("Dung lượng giải nén vượt quá 500 MB.")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as result:
        for name, data in seen.items():
            result.writestr(name, data)
    return output.getvalue()
