from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from app.aov_zstd import AoVZstdError, decode_aov_bytes

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
    zstd_results: list[tuple[str, str, int | None]] = []

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
                    important.append(f"ZIP mã hóa: {name}")
                ext = Path(name).suffix.lower() or "[không đuôi]"
                extensions[ext] += 1
                files[name] = (info.file_size, info.CRC)
                lower = name.lower()
                if any(token in lower for token in IMPORTANT_NAMES):
                    important.append(name)
                if Path(name).name.lower() in {"battlebank.bytes", "lobbybank.bytes"}:
                    try:
                        payload = archive.read(info)
                        decoded = decode_aov_bytes(payload)
                        zstd_results.append((Path(path).name + " :: " + name,
                                             "GIẢI NÉN ZSTD THÀNH CÔNG", len(decoded)))
                    except AoVZstdError as exc:
                        zstd_results.append((Path(path).name + " :: " + name,
                                             "CHƯA GIẢI NÉN ĐƯỢC: " + str(exc), None))
                collisions.setdefault(name, []).append(Path(path).name)

        inventories.append((Path(path).name, files, extensions, important))

    lines = [
        "BÁO CÁO KIỂM TRA CẤU TRÚC ZIP AOV",
        "Có thử giải nén Zstandard cho BattleBank/LobbyBank theo wrapper AoV đã nhận diện; không sửa file gốc.",
        "",
    ]
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
    if zstd_results:
        for name, status, decoded_size in zstd_results:
            suffix = f" | dữ liệu sau giải nén: {decoded_size:,} byte" if decoded_size is not None else ""
            lines.append(f"- {name}: {status}{suffix}")
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
