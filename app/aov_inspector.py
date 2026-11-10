from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from .aov_crypto import AoVResourceError, peek_mses_header


MAX_FILES = 10
MAX_ENTRIES = 20000
MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024
IMPORTANT_NAMES = (
    "battlebank.bytes",
    "lobbybank.bytes",
    "actor",
    "skill",
    "effect",
    "particle",
    "sound",
    "audio",
    "voice",
    "ui",
    "button",
    "recall",
    "return",
    "kill",
    "notify",
    "notification",
    "startup",
    "welcome",
    "popup",
    "loading",
    "commercialize",
    "assetbundle",
    "raw_mat",
    "raw_h",
)
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".pak", ".obb"}
METHOD_NAMES = {
    zipfile.ZIP_STORED: "STORE",
    zipfile.ZIP_DEFLATED: "DEFLATE",
    zipfile.ZIP_BZIP2: "BZIP2",
    zipfile.ZIP_LZMA: "LZMA",
}


class InspectError(ValueError):
    pass


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\x00" in normalized:
        raise InspectError("ZIP chứa đường dẫn không an toàn.")
    return normalized


def inspect_archives(paths: list[str]) -> str:
    """Read-only inventory of ZIP archives; never alters or decrypts game assets."""
    if not paths or len(paths) > MAX_FILES:
        raise InspectError(f"Chọn từ 1 đến {MAX_FILES} file ZIP.")

    total_entries = 0
    total_uncompressed = 0
    inventories: list[tuple[str, dict[str, tuple[int, int]], Counter[str], list[str], Counter[str]]] = []
    path_sources: dict[str, list[tuple[str, int, int]]] = {}
    bank_files: list[tuple[str, int, int]] = []
    nested_archives: list[str] = []
    suspicious_files: list[str] = []
    encrypted_files: list[str] = []
    method_counts: Counter[str] = Counter()
    mses_tables: list[tuple[str, str, int, int, int]] = []
    package_entries: list[tuple[str, str]] = []

    for path in paths:
        try:
            archive = zipfile.ZipFile(path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise InspectError(f"Không mở được ZIP: {Path(path).name}") from exc

        files: dict[str, tuple[int, int]] = {}
        extensions: Counter[str] = Counter()
        important: list[str] = []
        local_duplicates: Counter[str] = Counter()
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                total_entries += 1
                total_uncompressed += info.file_size
                if total_entries > MAX_ENTRIES:
                    raise InspectError("Các ZIP có quá nhiều mục để kiểm tra an toàn.")
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                    raise InspectError("Tổng dung lượng giải nén vượt quá 1 GB.")
                name = _safe_name(info.filename)
                lower = name.lower()
                source_label = Path(path).name
                if name in files:
                    local_duplicates[name] += 1
                files[name] = (info.file_size, info.CRC)
                path_sources.setdefault(name, []).append((source_label, info.file_size, info.CRC))

                if info.flag_bits & 1:
                    encrypted_files.append(f"{source_label} :: {name}")
                ext = Path(name).suffix.lower() or "[không đuôi]"
                extensions[ext] += 1
                method_counts[METHOD_NAMES.get(info.compress_type, f"UNKNOWN({info.compress_type})")] += 1

                if ext in ARCHIVE_EXTENSIONS:
                    nested_archives.append(f"{source_label} :: {name} ({info.file_size:,} byte)")
                if any(token in lower for token in IMPORTANT_NAMES):
                    important.append(name)
                if any(token in lower for token in ("startup", "notification", "notify", "welcome", "popup")):
                    suspicious_files.append(f"{source_label} :: {name}")
                if Path(name).name.lower() in {"battlebank.bytes", "lobbybank.bytes"}:
                    bank_files.append((source_label + " :: " + name, info.file_size, info.CRC))
                if ext == ".bytes" and info.file_size >= 16:
                    try:
                        prefix = archive.open(info, "r").read(24)
                        header = peek_mses_header(name, prefix)
                        if header:
                            mses_tables.append((source_label, name, header.version, header.field_count, header.row_count))
                    except (OSError, RuntimeError, AoVResourceError):
                        pass

        inventories.append((Path(path).name, files, extensions, important, local_duplicates))


    # Inspect known ZIP containers inside mod wrappers (Resources.zip and PKG .bytes).
    # Use a spooled temporary file so large nested archives do not accumulate in RAM.
    def scan_nested(
        nested: zipfile.ZipFile,
        source_label: str,
        *,
        depth: int,
        resource_root: bool,
        package_prefix: str = "",
    ) -> None:
        nonlocal total_entries, total_uncompressed
        for child_info in nested.infolist():
            if child_info.is_dir():
                continue
            total_entries += 1
            total_uncompressed += child_info.file_size
            if total_entries > MAX_ENTRIES:
                raise InspectError("Các ZIP và gói PKG lồng có quá nhiều mục.")
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                raise InspectError("Tổng dung lượng khai báo vượt quá 1 GB.")
            child_name = _safe_name(child_info.filename)
            child_lower = child_name.lower()
            child_ext = Path(child_name).suffix.lower() or "[không đuôi]"
            normalized = child_name
            if resource_root:
                parts = normalized.split("/")
                if len(parts) >= 3 and parts[0].lower() == "resources":
                    normalized = "/".join(parts[2:])
            key = f"{package_prefix}!{normalized}" if package_prefix else normalized
            path_sources.setdefault(key, []).append(
                (source_label, child_info.file_size, child_info.CRC)
            )
            if package_prefix:
                package_entries.append((source_label, child_name))
            if Path(child_name).name.lower() in {"battlebank.bytes", "lobbybank.bytes"}:
                bank_files.append((
                    f"{source_label} :: {child_name}",
                    child_info.file_size,
                    child_info.CRC,
                ))
            if child_ext == ".bytes" and child_info.file_size >= 16:
                try:
                    with nested.open(child_info, "r") as stream:
                        prefix = stream.read(24)
                    header = peek_mses_header(child_name, prefix)
                    if header:
                        mses_tables.append((
                            source_label, child_name, header.version,
                            header.field_count, header.row_count,
                        ))
                except (OSError, RuntimeError, AoVResourceError, zipfile.BadZipFile):
                    pass

            if depth >= 2 or not child_name.lower().endswith(".pkg.bytes"):
                continue
            try:
                with nested.open(child_info, "r") as source:
                    signature = source.read(4)
                    if not signature.startswith(b"PK\x03\x04"):
                        continue
                    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as spool:
                        spool.write(signature)
                        shutil.copyfileobj(source, spool, length=1024 * 1024)
                        spool.seek(0)
                        with zipfile.ZipFile(spool, "r") as pkg:
                            nested_archives.append(
                                f"{source_label} :: {child_name} ({child_info.file_size:,} byte)"
                            )
                            scan_nested(
                                pkg,
                                f"{source_label} :: {child_name}",
                                depth=depth + 1,
                                resource_root=False,
                                package_prefix=key,
                            )
            except (OSError, RuntimeError, zipfile.BadZipFile):
                suspicious_files.append(
                    f"{source_label} :: {child_name} [PKG lồng không đọc được]"
                )

    for path in paths:
        try:
            with zipfile.ZipFile(path, "r") as outer:
                for outer_info in outer.infolist():
                    if outer_info.is_dir():
                        continue
                    outer_name = _safe_name(outer_info.filename)
                    lower_name = outer_name.lower()
                    is_resources = Path(outer_name).name.lower() == "resources.zip"
                    is_pkg = lower_name.endswith(".pkg.bytes")
                    if not (is_resources or is_pkg):
                        continue
                    try:
                        with outer.open(outer_info, "r") as source:
                            signature = source.read(4)
                            if not signature.startswith(b"PK\x03\x04"):
                                continue
                            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as spool:
                                spool.write(signature)
                                shutil.copyfileobj(source, spool, length=1024 * 1024)
                                spool.seek(0)
                                with zipfile.ZipFile(spool, "r") as nested:
                                    nested_archives.append(
                                        f"{Path(path).name} :: {outer_name} ({outer_info.file_size:,} byte)"
                                    )
                                    scan_nested(
                                        nested,
                                        f"{Path(path).name} :: {outer_name}",
                                        depth=1,
                                        resource_root=is_resources,
                                        package_prefix="" if is_resources else outer_name,
                                    )
                    except (OSError, RuntimeError, zipfile.BadZipFile):
                        suspicious_files.append(
                            f"{Path(path).name} :: {outer_name} [gói lồng không đọc được]"
                        )
        except (OSError, zipfile.BadZipFile) as exc:
            raise InspectError(f"Không mở lại được ZIP: {Path(path).name}") from exc

    nested_archives = list(dict.fromkeys(nested_archives))

    lines = [
        "BÁO CÁO KIỂM TRA CẤU TRÚC ZIP AOV",
        "Chế độ chỉ đọc: không sửa hoặc xóa tài nguyên; có đọc header AES/MSES khi nhận diện được.",
        f"Tổng ZIP: {len(paths)} | Tổng tệp: {total_entries:,} | Dung lượng giải nén khai báo: {total_uncompressed:,} byte",
        "",
    ]
    for filename, files, extensions, important, local_duplicates in inventories:
        lines.extend([
            f"[{filename}]",
            f"- Số tệp duy nhất theo đường dẫn: {len(files):,}",
            "- Định dạng: " + ", ".join(f"{ext} ({count})" for ext, count in extensions.most_common(12)),
            "- Tên có dấu hiệu liên quan tài nguyên game:",
        ])
        lines.extend(f"  • {name}" for name in important[:50] if name)
        if not important:
            lines.append("  • Chưa thấy tên tệp nhận diện được theo bộ từ khóa.")
        if local_duplicates:
            lines.append(f"- Đường dẫn lặp ngay trong ZIP này: {len(local_duplicates)}")
            lines.extend(f"  • {name} (lặp {count + 1} mục)" for name, count in local_duplicates.most_common(20))
        lines.append("")

    lines.extend(["", "KIỂM TRA BATTLEBANK / LOBBYBANK"])
    if bank_files:
        for name, size, crc in bank_files:
            lines.append(f"- {name}: đã nhận diện | {size:,} byte | CRC32: {crc:08x}")
        lines.append("- Chỉ nhận diện tên và metadata; chưa phân tích cấu trúc nhị phân bên trong.")
    else:
        lines.append("- Không tìm thấy BattleBank.bytes hoặc LobbyBank.bytes trong các ZIP đã chọn.")

    lines.extend(["", "KIỂM TRA HEADER BẢNG MSES"])
    if mses_tables:
        for source, name, version, fields, rows in mses_tables:
            lines.append(f"- {source} :: {name}: MSES v{version} | {fields} fields | {rows} records")
        lines.append("- Đã giải mã/đọc header AES theo tên tài nguyên; chưa parse nội dung record hoặc checksum bảng.")
    else:
        lines.append("- Không nhận diện được header MSES trong các .bytes đã quét.")

    lines.extend(["", "TỆP BÊN TRONG CÁC PKG"])
    if package_entries:
        lines.append(f"- Đã đọc {len(package_entries)} mục trong các PKG dạng ZIP:")
        lines.extend(f"  • {source} :: {name}" for source, name in package_entries[:100])
    else:
        lines.append("- Không phát hiện tệp con trong PKG dạng ZIP.")

    lines.extend(["", "ĐƯỜNG DẪN TRÙNG GIỮA CÁC FILE"])
    same_metadata = []
    different_metadata = []
    for name, sources in sorted(path_sources.items()):
        if len(sources) < 2:
            continue
        signatures = {(size, crc) for _, size, crc in sources}
        record = (name, sources)
        if len(signatures) == 1:
            same_metadata.append(record)
        else:
            different_metadata.append(record)
    lines.append(f"- Trùng đường dẫn, cùng kích thước + CRC32: {len(same_metadata)} (metadata giống nhau, chưa phải xác minh SHA-256)")
    for name, sources in same_metadata[:50]:
        lines.append(f"  • {name} [{', '.join(source for source, _, _ in sources)}]")
    lines.append(f"- Trùng đường dẫn nhưng khác kích thước hoặc CRC32: {len(different_metadata)}")
    for name, sources in different_metadata[:80]:
        detail = ", ".join(f"{source}: {size:,} byte / CRC {crc:08x}" for source, size, crc in sources)
        lines.append(f"  • {name} [{detail}]")

    lines.extend(["", "DẤU HIỆU CẦN KIỂM TRA THÊM"])
    lines.append(f"- Mục có tên gợi ý thông báo/khởi động/popup: {len(suspicious_files)}")
    lines.extend(f"  • {item}" for item in suspicious_files[:80])
    lines.append(f"- ZIP/lưu trữ lồng đã mở: {len(nested_archives)}")
    lines.extend(f"  • {item}" for item in nested_archives[:40])
    lines.append(f"- Mục được đánh dấu mã hóa ZIP: {len(encrypted_files)}")
    lines.extend(f"  • {item}" for item in encrypted_files[:40])
    lines.append("- Phương thức nén: " + (", ".join(f"{name} ({count})" for name, count in method_counts.most_common()) or "không có"))

    lines.extend([
        "",
        "KẾT LUẬN AN TOÀN",
        "- Báo cáo giúp tìm đường dẫn trùng và dấu hiệu tài nguyên; không chứng minh các gói mod tương thích.",
        "- Cùng kích thước + CRC32 chỉ là đối chiếu nhanh, không thay thế so sánh SHA-256 từng nội dung.",
        "- Tên có chữ notify/startup/popup chỉ là manh mối, không đủ căn cứ để xóa file.",
        "- Header MSES hợp lệ không đồng nghĩa đã parse hoặc hợp nhất được record bên trong.",
        "- Giải nén được một lớp nén không đồng nghĩa hợp nhất được asset độc quyền.",
        "- Chỉ nên tạo bản mod sau khi xác định đúng cấu trúc, phiên bản game và quan hệ phụ thuộc của tài nguyên.",
    ])
    return "\n".join(lines)


def requires_format_aware_merge(report: str) -> bool:
    """Return True when generic ZIP merging could flatten or overwrite AOV resources."""
    if "MSES v7" in report:
        return True
    for line in report.splitlines():
        if line.startswith("- ZIP/lưu trữ lồng đã mở:"):
            try:
                return int(line.rsplit(":", 1)[1].strip()) > 0
            except (IndexError, ValueError):
                return True
    return False
