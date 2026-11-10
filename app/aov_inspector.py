from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath


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
    normalized = name.replace("\\\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\x00" in normalized:
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

        inventories.append((Path(path).name, files, extensions, important, local_duplicates))

    lines = [
        "BÁO CÁO KIỂM TRA CẤU TRÚC ZIP AOV",
        "Chế độ chỉ đọc: không sửa, không giải mã và không tự ý xóa tài nguyên.",
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
    lines.append(f"- ZIP/lưu trữ lồng bên trong: {len(nested_archives)}")
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
        "- Giải nén được một lớp nén không đồng nghĩa giải mã hoặc hợp nhất được asset độc quyền.",
        "- Chỉ nên tạo bản mod sau khi xác định đúng cấu trúc, phiên bản game và quan hệ phụ thuộc của tài nguyên.",
    ])
    return "\\n".join(lines)
