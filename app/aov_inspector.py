from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from .aov_format_detector import detect_resource_format, summarize_formats


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
    "commercialize",
    "assetbundle",
    "raw_mat",
    "raw_h",
)


class InspectError(ValueError):
    pass


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\x00" in normalized:
        raise InspectError("ZIP chứa đường dẫn không an toàn.")
    return normalized


def inspect_archives(paths: list[str]) -> str:
    """Create a read-only inventory; never alters or decrypts game assets."""
    if not paths or len(paths) > MAX_FILES:
        raise InspectError(f"Chọn từ 1 đến {MAX_FILES} file ZIP.")

    total_entries = 0
    total_uncompressed = 0
    inventories: list[tuple[str, dict[str, tuple[int, int]], Counter[str], list[str]]] = []
    collisions: dict[str, list[str]] = {}
    bank_files: list[tuple[str, int, int]] = []
    detected_formats: list[tuple[str, str]] = []

    for path in paths:
        try:
            archive = zipfile.ZipFile(path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise InspectError(f"Không mở được ZIP: {Path(path).name}") from exc

        files: dict[str, tuple[int, int]] = {}
        extensions: Counter[str] = Counter()
        important: list[str] = []
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
                if info.flag_bits & 1:
                    format_name = "Encrypted ZIP entry"
                    important.append(f"ZIP mã hóa: {name}")
                else:
                    try:
                        with archive.open(info, "r") as resource_stream:
                            header = resource_stream.read(32)
                    except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
                        raise InspectError(f"Không đọc được header tài nguyên: {name}") from exc
                    format_name, _ = detect_resource_format(name, header)
                detected_formats.append((format_name, f"{Path(path).name} :: {name}"))
                ext = Path(name).suffix.lower() or "[không đuôi]"
                extensions[ext] += 1
                files[name] = (info.file_size, info.CRC)
                lower = name.lower()
                if any(token in lower for token in IMPORTANT_NAMES):
                    important.append(name)
                if Path(name).name.lower() in {"battlebank.bytes", "lobbybank.bytes"}:
                    # Inventory only. Full decompression can expand dramatically and
                    # monopolize a small web instance; keep this endpoint responsive.
                    bank_files.append((Path(path).name + " :: " + name, info.file_size, info.CRC))
                collisions.setdefault(name, []).append(Path(path).name)

        inventories.append((Path(path).name, files, extensions, important))

    format_counts, format_examples = summarize_formats(detected_formats)
    lines = [
        "BÁO CÁO KIỂM TRA CẤU TRÚC ZIP AOV",
        "Chế độ kiểm tra nhanh chỉ lập danh mục ZIP; không giải nén toàn bộ BattleBank/LobbyBank.",
        "Đã đọc tối đa 32 byte đầu mỗi mục để nhận diện chữ ký định dạng, không sửa dữ liệu.",
        "",
        "NHẬN DIỆN ĐỊNH DẠNG THEO CHỮ KÝ",
    ]
    for format_name, count in format_counts.most_common():
        lines.append(f"- {format_name}: {count} mục")
        lines.extend(f"  • {example}" for example in format_examples[format_name][:4])
    lines.append("")
    for filename, files, extensions, important in inventories:
        lines.extend([
            f"[{filename}]",
            f"- Số tệp: {len(files)}",
            "- Định dạng: " + ", ".join(f"{ext} ({count})" for ext, count in extensions.most_common(12)),
            "- Tên có dấu hiệu liên quan tài nguyên game:",
        ])
        lines.extend(f"  • {name}" for name in important[:35] if name)
        if not important:
            lines.append("  • Chưa thấy tên tệp nhận diện được theo bộ từ khóa.")
        lines.append("")

    lines.extend(["", "KIỂM TRA BATTLEBANK / LOBBYBANK"])
    if bank_files:
        for name, size, crc in bank_files:
            lines.append(f"- {name}: đã nhận diện | kích thước trong ZIP: {size:,} byte | CRC32: {crc:08x}")
        lines.append("- Chưa giải nén nội dung ngân hàng ở bước kiểm tra nhanh để tránh treo máy chủ.")
    else:
        lines.append("- Không tìm thấy BattleBank.bytes hoặc LobbyBank.bytes trong các ZIP đã chọn.")

    duplicate_names = [(name, sources) for name, sources in collisions.items() if len(sources) > 1]
    lines.append(f"Tên đường dẫn xuất hiện ở nhiều ZIP: {len(duplicate_names)}")
    for name, sources in duplicate_names[:80]:
        lines.append(f"- {name} [{', '.join(sources)}]")

    lines.extend([
        "",
        "KẾT LUẬN AN TOÀN",
        "- Báo cáo này không chứng minh các tài nguyên có thể ghép tương thích.",
        "- Tệp trùng đường dẫn cần so sánh nội dung và quan hệ phụ thuộc trước khi chọn bản.",
        "- Giải nén Zstandard thành công chỉ là giải nén lớp nén, không đồng nghĩa giải mã toàn bộ dữ liệu game hoặc hợp nhất assetbundle.",
        "- Không dùng ZIP ghép thông thường làm bản mod hoàn chỉnh khi chưa thử trên đúng phiên bản iOS.",
    ])
    return "\n".join(lines)
