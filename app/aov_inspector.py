from __future__ import annotations

import hashlib
import zipfile
import zlib
from collections import Counter
from pathlib import Path, PurePosixPath

from .aov_format_detector import detect_resource_format, summarize_formats
from .wwise_bnk import WwiseBNKError, inspect_wwise_bnk
from .assetbundle_inspector import (
    AssetBundleInspectError,
    compare_assetbundle_overlays,
    inspect_assetbundle,
)


MAX_FILES = 10
MAX_ENTRIES = 20000
MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_BNK_INSPECT_BYTES = 64 * 1024 * 1024
MAX_ASSETBUNDLE_INSPECT_BYTES = 64 * 1024 * 1024
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


def _entry_sha256(archive_path: str, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(archive_path, "r") as archive:
        with archive.open(info, "r") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def inspect_archives(paths: list[str]) -> str:
    """Create a read-only inventory; never alters or decrypts game assets."""
    if not paths or len(paths) > MAX_FILES:
        raise InspectError(f"Chọn từ 1 đến {MAX_FILES} file ZIP.")

    total_entries = 0
    total_uncompressed = 0
    inventories: list[tuple[str, dict[str, tuple[int, int]], Counter[str], list[str]]] = []
    collisions: dict[str, list[tuple[str, str, int, int, zipfile.ZipInfo]]] = {}
    bank_files: list[tuple[str, int, int]] = []
    detected_formats: list[tuple[str, str]] = []
    wwise_reports: list[tuple[str, dict | str]] = []
    assetbundle_reports: list[tuple[str, dict | str]] = []
    assetbundle_comparisons: list[tuple[str, dict | str]] = []

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
                    except (RuntimeError, zipfile.BadZipFile, OSError, zlib.error) as exc:
                        raise InspectError(f"Không đọc được header tài nguyên: {name}") from exc
                    format_name, _ = detect_resource_format(name, header)
                detected_formats.append((format_name, f"{Path(path).name} :: {name}"))
                if format_name == "Wwise SoundBank" and Path(name).suffix.lower() == ".bnk":
                    label = Path(path).name + " :: " + name
                    if info.file_size > MAX_BNK_INSPECT_BYTES:
                        wwise_reports.append((label, "Bỏ qua phân tích sâu: BNK vượt quá 64 MiB."))
                    else:
                        try:
                            wwise_reports.append((label, inspect_wwise_bnk(archive.read(info))))
                        except (WwiseBNKError, RuntimeError, zipfile.BadZipFile, OSError, zlib.error) as exc:
                            wwise_reports.append((label, f"BNK không hợp lệ hoặc chưa hỗ trợ: {exc}"))
                if Path(name).suffix.lower() == ".assetbundle" and header.startswith((b"UnityFS\x00", b"UnityRaw\x00", b"UnityWeb\x00")):
                    label = Path(path).name + " :: " + name
                    if info.file_size > MAX_ASSETBUNDLE_INSPECT_BYTES:
                        assetbundle_reports.append((label, "Bỏ qua phân tích sâu: AssetBundle vượt quá 64 MiB."))
                    else:
                        try:
                            assetbundle_reports.append((label, inspect_assetbundle(archive.read(info), label)))
                        except (AssetBundleInspectError, RuntimeError, zipfile.BadZipFile, OSError, zlib.error) as exc:
                            assetbundle_reports.append((label, f"AssetBundle không hợp lệ hoặc chưa hỗ trợ: {exc}"))
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
                collisions.setdefault(name, []).append(
                    (Path(path).name, path, info.file_size, info.CRC, info)
                )

        inventories.append((Path(path).name, files, extensions, important))

    # Compare only a small number of same-path AssetBundles across input ZIPs.
    # This is a structural report, not an authorization to merge their objects.
    compared_assetbundles = 0
    for name, sources in collisions.items():
        if compared_assetbundles >= 5 or Path(name).suffix.lower() != ".assetbundle":
            continue
        unique_sources = []
        seen_archives = set()
        for filename, source_path, size, crc, info in sources:
            if source_path not in seen_archives:
                unique_sources.append((filename, source_path, size, info))
                seen_archives.add(source_path)
        if len(unique_sources) < 2:
            continue
        left, right = unique_sources[:2]
        label = f"{name} [{left[0]} vs {right[0]}]"
        if left[2] > MAX_ASSETBUNDLE_INSPECT_BYTES or right[2] > MAX_ASSETBUNDLE_INSPECT_BYTES:
            assetbundle_comparisons.append((label, "Bỏ qua so sánh sâu: ít nhất một AssetBundle vượt quá 64 MiB."))
            compared_assetbundles += 1
            continue
        try:
            with zipfile.ZipFile(left[1], "r") as left_zip, zipfile.ZipFile(right[1], "r") as right_zip:
                left_data = left_zip.read(left[3])
                right_data = right_zip.read(right[3])
            assetbundle_comparisons.append((
                label,
                compare_assetbundle_overlays(left_data, right_data),
            ))
        except (AssetBundleInspectError, RuntimeError, zipfile.BadZipFile, OSError, zlib.error) as exc:
            assetbundle_comparisons.append((label, f"Không thể so sánh an toàn: {exc}"))
        compared_assetbundles += 1

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

    lines.extend(["", "KIỂM TRA CẤU TRÚC WWISE BNK"])
    if wwise_reports:
        for name, result in wwise_reports:
            lines.append(f"- {name}")
            if isinstance(result, str):
                lines.append(f"  • {result}")
            else:
                chunk_names = ", ".join(item["id"] for item in result["chunks"])
                lines.append(
                    f"  • Version {result['version']} | Bank ID {result['bank_id']} | "
                    f"Chunk: {chunk_names}"
                )
                lines.append(
                    f"  • Media: {result['media_count']} | "
                    f"HIRC objects: {result['hirc_object_count']}"
                )
                if result["unknown_chunks"]:
                    lines.append("  • Chunk chưa nhận diện: " + ", ".join(result["unknown_chunks"]))
    else:
        lines.append("- Không tìm thấy Wwise BNK có chữ ký BKHD để phân tích.")
    lines.append("- Chỉ kiểm tra cấu trúc và chỉ mục; chưa chỉnh sửa hoặc hợp nhất SoundBank.")

    lines.extend(["", "KIỂM TRA CẤU TRÚC VÀ PHỤ THUỘC ASSETBUNDLE"])
    if assetbundle_reports:
        for name, result in assetbundle_reports:
            lines.append(f"- {name}")
            if isinstance(result, str):
                lines.append(f"  • {result}")
            else:
                lines.append(
                    f"  • Serialized files: {result['serialized_file_count']} | "
                    f"Objects: {result['object_count']} | "
                    f"Container entries: {result['container_entry_count']}"
                )
                lines.append(
                    f"  • External references: {result['external_reference_count']}"
                )
                lines.append(
                    f"  • Object references: {result['object_reference_count']} | "
                    f"Quét đầy đủ: {result['reference_scan_complete']}"
                )
                if result["reference_scan_errors"]:
                    lines.append(
                        "  • Object không đọc được: "
                        + ", ".join(result["reference_scan_errors"][:5])
                    )
                for reference in result["object_references"][:8]:
                    lines.append(
                        f"  • PPtr {reference['from_file']}:{reference['from_path_id']} "
                        f"→ {reference['target_file']}:{reference['path_id']}"
                    )
                if result["assetbundle_metadata"]:
                    for metadata in result["assetbundle_metadata"][:5]:
                        lines.append(
                            f"  • AssetBundle object {metadata['path_id']}: "
                            f"preload={metadata['preload_table_count']} | "
                            f"Container={metadata['container_entry_count']} | "
                            f"trùng key={metadata['duplicate_container_path_count']}"
                        )
                        lines.append(
                            f"    Preload range lỗi={len(metadata['invalid_preload_ranges'])} | "
                            f"PPtr nội bộ thiếu={len(metadata['missing_local_pointers'])} | "
                            f"FileID ngoài phạm vi={len(metadata['invalid_pointer_indices'])}"
                        )
                if not result["assetbundle_metadata_complete"]:
                    lines.append(
                        "  • Cảnh báo: có AssetBundle object không đọc được metadata: "
                        + ", ".join(result["assetbundle_metadata_errors"][:5])
                    )
                integrity = result["reference_integrity"]
                lines.append(
                    f"  • PPtr nội bộ hợp lệ: {integrity['local_resolved']} | "
                    f"thiếu Path ID nội bộ: {len(integrity['local_missing'])}"
                )
                lines.append(
                    f"  • PPtr CAB đã tìm thấy: {integrity['external_resolved']} | "
                    f"phụ thuộc CAB ngoài gói: {len(integrity['external_dependencies'])} | "
                    f"tên CAB mơ hồ: {len(integrity['external_ambiguous_targets'])}"
                )
                if integrity["invalid_external_indices"]:
                    lines.append(
                        f"  • FileID ngoài phạm vi: {len(integrity['invalid_external_indices'])}"
                    )
                if result["object_type_counts"]:
                    types = ", ".join(
                        f"{key}={value}"
                        for key, value in list(result["object_type_counts"].items())[:8]
                    )
                    lines.append("  • Object types: " + types)
                if result["external_paths"]:
                    lines.append(
                        "  • External paths cần đối chiếu: "
                        + ", ".join(result["external_paths"][:8])
                    )
    else:
        lines.append("- Không tìm thấy AssetBundle UnityFS/UnityRaw/UnityWeb phù hợp để phân tích sâu.")
    lines.append("- Đã kiểm tra thêm preload table, Container AssetInfo và PPtr của AssetBundle; chưa sửa hoặc hợp nhất.")

    lines.extend(["", "ĐỐI CHIẾU ASSETBUNDLE TRÙNG ĐƯỜNG DẪN"])
    if assetbundle_comparisons:
        for name, result in assetbundle_comparisons:
            lines.append(f"- {name}")
            if isinstance(result, str):
                lines.append(f"  • {result}")
            else:
                lines.append(
                    f"  • Unity version khớp: {result['unity_versions_match']} | "
                    f"Platform khớp: {result['platforms_match']} | "
                    f"Container đầy đủ: {result['container_inventory_complete']}"
                )
                lines.append(
                    f"  • Đường dẫn chung: {result['shared_container_paths']} | "
                    f"ứng viên thay thế: {len(result['replacement_candidates'])} | "
                    f"đường dẫn mới: {len(result['new_container_paths'])}"
                )
                if result["container_type_conflicts"]:
                    lines.append(
                        "  • Xung đột loại object: "
                        + ", ".join(result["container_type_conflicts"][:8])
                    )
                object_overlaps = result["object_id_overlaps"]
                if object_overlaps:
                    overlap_total = sum(item["shared_path_id_count"] for item in object_overlaps)
                    type_conflicts = sum(item["type_conflict_count"] for item in object_overlaps)
                    lines.append(
                        f"  • PathID trùng trong CAB tương ứng: {overlap_total} | "
                        f"xung đột loại Object: {type_conflicts}"
                    )
                    payload_changed = sum(item["payload_changed_count"] for item in object_overlaps)
                    payload_identical = sum(item["payload_identical_count"] for item in object_overlaps)
                    payload_unknown = sum(item["payload_unknown_count"] for item in object_overlaps)
                    lines.append(
                        f"  • Payload Object: {payload_identical} giống nhau | "
                        f"{payload_changed} đã thay đổi | {payload_unknown} chưa đối chiếu được"
                    )
                if result["ambiguous_object_id_cabs"]:
                    lines.append(
                        "  • CAB trùng tên, không thể đối chiếu PathID chắc chắn: "
                        + ", ".join(result["ambiguous_object_id_cabs"][:8])
                    )
                cross_refs = result["cross_bundle_references"]
                lines.append(
                    f"  • PPtr overlay → CAB base hợp lệ: {cross_refs['resolved_count']} | "
                    f"Path ID thiếu: {cross_refs['missing_path_id_count']}"
                )
                lines.append(
                    f"  • CAB tên mơ hồ: {cross_refs['ambiguous_cab_count']} | "
                    f"chưa tìm thấy CAB: {cross_refs['unknown_cab_count']} | "
                    f"index chưa đầy đủ: {cross_refs['index_incomplete']}"
                )
                lines.append(
                    f"  • Kiểm tra cấu trúc đạt: {result['structural_checks_pass']} | "
                    "Cho phép ghép tự động: False"
                )
    else:
        lines.append("- Không có đường dẫn AssetBundle trùng giữa các ZIP để đối chiếu.")
    lines.append("- Đối chiếu chỉ mang tính cấu trúc; chưa remap object ID, preload table hoặc CAB reference.")

    lines.extend(["", "KIỂM TRA BATTLEBANK / LOBBYBANK"])
    if bank_files:
        for name, size, crc in bank_files:
            lines.append(f"- {name}: đã nhận diện | kích thước trong ZIP: {size:,} byte | CRC32: {crc:08x}")
        lines.append("- Chưa giải nén nội dung ngân hàng ở bước kiểm tra nhanh để tránh treo máy chủ.")
    else:
        lines.append("- Không tìm thấy BattleBank.bytes hoặc LobbyBank.bytes trong các ZIP đã chọn.")

    duplicate_names = [
        (name, sources) for name, sources in collisions.items() if len(sources) > 1
    ]
    lines.append(f"Tên đường dẫn xuất hiện nhiều lần: {len(duplicate_names)}")
    identical_count = 0
    conflict_count = 0
    for name, sources in duplicate_names[:80]:
        try:
            digests = [
                _entry_sha256(source_path, info)
                for _, source_path, _, _, info in sources
            ]
        except (OSError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
            raise InspectError(
                f"Không thể so sánh nội dung file trùng: {name}"
            ) from exc
        identical = len(set(digests)) == 1
        if identical:
            identical_count += 1
            verdict = "NỘI DUNG GIỐNG HỆT (SHA-256)"
        else:
            conflict_count += 1
            verdict = "XUNG ĐỘT NỘI DUNG (SHA-256 KHÁC)"
        details = ", ".join(
            f"{filename}: {size:,} byte, CRC32 {crc:08x}"
            for filename, _, size, crc, _ in sources
        )
        lines.append(f"- {name}: {verdict} [{details}]")
    lines.append(
        f"Kết quả so sánh: {identical_count} đường dẫn giống hệt; "
        f"{conflict_count} đường dẫn có nội dung khác."
    )
    if len(duplicate_names) > 80:
        lines.append(
            f"- Còn {len(duplicate_names) - 80} đường dẫn trùng chưa hiển thị chi tiết."
        )

    lines.extend([
        "",
        "KẾT LUẬN AN TOÀN",
        "- Báo cáo này không chứng minh các tài nguyên có thể ghép tương thích.",
        "- Tệp trùng đường dẫn cần so sánh nội dung và quan hệ phụ thuộc trước khi chọn bản.",
        "- Giải nén Zstandard thành công chỉ là giải nén lớp nén, không đồng nghĩa giải mã toàn bộ dữ liệu game hoặc hợp nhất assetbundle.",
        "- Không dùng ZIP ghép thông thường làm bản mod hoàn chỉnh khi chưa thử trên đúng phiên bản iOS.",
    ])
    return "\n".join(lines)
