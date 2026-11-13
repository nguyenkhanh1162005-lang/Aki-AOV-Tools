"""Decode supported AoV-wrapped .bytes resources from uploaded ZIP archives.

This is a diagnostic extraction step. It does not merge resources or modify the
uploaded archives.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .aov_bytes import AOVBytesError, decode_aov_resource
from .aov_mses import MSESError, parse_mses
from .ifix_merger import IFixMergeError, parse_ifix_patch

MAX_ARCHIVE_ENTRIES = 20000
MAX_ARCHIVE_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_ENTRY_UNCOMPRESSED = 256 * 1024 * 1024
MAX_DECODED_RESOURCE = 256 * 1024 * 1024
MAX_TOTAL_DECODED_BYTES = 1024 * 1024 * 1024
MAX_SCAN_DECODED_BYTES = 256 * 1024 * 1024
MAX_NESTED_ZIP_DEPTH = 2
MAX_NOTICE_CANDIDATES = 100
MAX_NOTICE_SNIPPET = 180
MAX_NOTICE_ASSETBUNDLE_BYTES = 64 * 1024 * 1024
MAX_NOTICE_ASSETBUNDLE_OBJECTS = 500
MAX_NOTICE_ASSETBUNDLE_NODES = 20000
MAX_NOTICE_ASSETBUNDLE_TEXT_CHARS = 200000
NOTICE_TEXT_EXTENSIONS = {
    ".lua", ".txt", ".xml", ".json", ".json5", ".ini", ".cfg", ".csv", ".bytes",
}
NOTICE_PATTERNS = (
    ("startup_notice", r"\b(?:startup|first[_\s-]?login|on[_\s-]?game[_\s-]?start|game[_\s-]?start)[_\s-]?(?:notice|popup|message)?\b"),
    ("login_notice", r"\b(?:show[_\s-]?)?(?:login[_\s-]?)?notice\b"),
    ("announcement", r"\b(?:announcement|notification|notify|toast|popup)\b"),
    ("vietnamese_notice", r"thông\s*báo|khởi\s*động|vào\s*game"),
    ("cjk_notice", r"公告|通知|提示|弹窗|登录公告|登入公告"),
)


# Resource-path semantics are a safety boundary for notice tooling. A keyword hit
# in a kill-notification/billboard resource must never become a startup-removal
# candidate merely because its payload contains words such as "notify" or "popup".
_KILL_NOTIFICATION_PATH_MARKERS = (
    "commercializenotify",
    "reskillbillboardcfg",
    "killnotify",
    "kill_notification",
)
_BILLBOARD_PATH_MARKERS = (
    "resbillboardcfg",
    "resbillboardskincfg",
)
_STARTUP_FLOW_PATH_MARKERS = (
    "gameentrycontrol",
    "lobbyview",
    "loginnotice",
    "startup",
    "firstlogin",
    "first_login",
    "first-login",
)


def _classify_notice_candidate(path: str, keyword_group: str) -> dict[str, Any]:
    normalized = path.replace(chr(92), "/").casefold()
    if any(marker in normalized for marker in _KILL_NOTIFICATION_PATH_MARKERS):
        return {
            "resource_role": "protected_kill_notification",
            "startup_removal_eligible": False,
            "protection_reason": "Đây là tài nguyên thông báo hạ gục; phải giữ nguyên.",
        }
    if any(marker in normalized for marker in _BILLBOARD_PATH_MARKERS):
        return {
            "resource_role": "protected_billboard",
            "startup_removal_eligible": False,
            "protection_reason": "Đây là bảng billboard trong game; không phải popup khởi động.",
        }
    if (
        keyword_group in {"startup_notice", "login_notice"}
        and any(marker in normalized for marker in _STARTUP_FLOW_PATH_MARKERS)
    ):
        return {
            "resource_role": "startup_flow_candidate",
            "startup_removal_eligible": True,
            "protection_reason": (
                "Chỉ là ứng viên trong luồng đăng nhập/khởi động; phải xác nhận "
                "đúng lệnh gọi popup trước khi sửa."
            ),
        }
    return {
        "resource_role": "unclassified_notice_text",
        "startup_removal_eligible": False,
        "protection_reason": (
            "Từ khóa chưa đủ chứng minh đây là popup khởi động; giữ nguyên."
        ),
    }


def _build_notice_candidate(
    source: str, default_path: str, finding: dict[str, Any]
) -> dict[str, Any]:
    candidate = {"source": source, **finding}
    candidate.setdefault("path", default_path)
    candidate.update(
        _classify_notice_candidate(
            str(candidate.get("path") or default_path),
            str(candidate.get("keyword_group") or ""),
        )
    )
    return candidate

# Exact legacy labels requested for the Aki branding pass. This remains a
# read-only inventory; discovering a string is not permission to rewrite a
# Unity object, binary package, or game configuration.
BRANDING_TERMS = (
    ("creator_credit", "Mod được tạo bởi youtuber KIANA MOD AOV"),
    ("brand", "KIANA MOD AOV"),
    ("web_mod", "Web Mod"),
    ("youtube", "Youtube"),
)
MAX_BRANDING_CANDIDATES = 100


class AOVBytesArchiveError(ValueError):
    pass


# A small set of AoV lobby scripts use a custom Lua-family wrapper instead of
# the normal 22 4A AoV resource envelope. The marker at offset 128 identifies
# this container family, but it is NOT a complete Lua decompiler or serializer.

_CUSTOM_LUA_RESOURCE_NAMES = {
    "gameentrycontrol_lua.bytes",
    "lobbyview_lua.bytes",
    "clobbysystem_lua.bytes",
}
_CUSTOM_LUA_CHUNK_OFFSET = 128
_CUSTOM_LUA_CHUNK_SIGNATURE = bytes([0x1B]) + b"LuaS"
_CUSTOM_LUA_FORMAT_BYTE = 0x01
_MAX_CUSTOM_LUA_INSPECT_BYTES = 2 * 1024 * 1024
_MAX_CUSTOM_LUA_STRINGS = 300
_MAX_CUSTOM_LUA_STRING_LENGTH = 180


def _inspect_custom_lua_resource(
    payload: bytes, path: str
) -> dict[str, Any] | None:
    """Recognize a specific custom Lua chunk prefix and extract bounded ASCII strings."""
    leaf = PurePosixPath(path.replace(chr(92), "/")).name.casefold()
    if leaf not in _CUSTOM_LUA_RESOURCE_NAMES:
        return None
    offset = _CUSTOM_LUA_CHUNK_OFFSET
    if len(payload) <= offset + len(_CUSTOM_LUA_CHUNK_SIGNATURE):
        return None
    if payload[offset:offset + len(_CUSTOM_LUA_CHUNK_SIGNATURE)] != _CUSTOM_LUA_CHUNK_SIGNATURE:
        return None
    format_offset = offset + len(_CUSTOM_LUA_CHUNK_SIGNATURE)
    format_byte = payload[format_offset]
    if format_byte != _CUSTOM_LUA_FORMAT_BYTE:
        return None

    inspected = payload[offset:offset + _MAX_CUSTOM_LUA_INSPECT_BYTES]
    strings: list[str] = []
    current = bytearray()

    def flush_ascii_run() -> None:
        if len(current) >= 4 and len(strings) < _MAX_CUSTOM_LUA_STRINGS:
            strings.append(
                current.decode("ascii", errors="ignore")[:_MAX_CUSTOM_LUA_STRING_LENGTH]
            )
        current.clear()

    for value in inspected:
        if 0x20 <= value <= 0x7E:
            current.append(value)
        else:
            flush_ascii_run()
            if len(strings) >= _MAX_CUSTOM_LUA_STRINGS:
                break
    if len(strings) < _MAX_CUSTOM_LUA_STRINGS:
        flush_ascii_run()

    return {
        "resource_path": path,
        "format_family": "aov_custom_lua53_family",
        "format_byte": format_byte,
        "header_offset": offset,
        "chunk_size": len(payload) - offset,
        "resource_size": len(payload),
        "resource_sha256": hashlib.sha256(payload).hexdigest(),
        "chunk_sha256": hashlib.sha256(payload[offset:]).hexdigest(),
        "strings": strings,
        "strings_truncated": len(strings) >= _MAX_CUSTOM_LUA_STRINGS,
        "recognized_only": True,
        "decompiled": False,
        "startup_removal_eligible": False,
        "merge_authorized": False,
        "note": (
            "Đã nhận diện header Lua tùy biến và trích chuỗi ASCII; chưa có "
            "decompiler/serializer đã xác minh nên không được sửa bytecode."
        ),
    }


def _safe_member_path(name: str) -> str:
    normalized = name.replace("\\", "/")
    candidate = normalized[:-1] if normalized.endswith("/") else normalized
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.split("/"))
        or "\x00" in normalized
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise AOVBytesArchiveError(f"Đường dẫn ZIP không an toàn: {name}")
    return path.as_posix()


def _expected_decoded_size(data: bytes) -> int | None:
    if len(data) < 8 or data[:2] != b"\x22\x4a":
        return None
    return int.from_bytes(data[4:8], "little")


def _notice_text_view(data: bytes) -> tuple[str, str] | None:
    """Decode bounded text members without treating arbitrary binary as source code."""
    sample = data[:2 * 1024 * 1024]
    candidates: list[tuple[float, str, str]] = []
    for encoding in ("utf-8-sig", "utf-16-le", "utf-16-be"):
        try:
            decoded = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not decoded:
            continue
        printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in decoded)
        ratio = printable / max(1, len(decoded))
        candidates.append((ratio, encoding, decoded.replace("\x00", "")))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1] == "utf-8-sig"), reverse=True)
    ratio, encoding, decoded = candidates[0]
    if ratio < 0.82 or len(decoded) < 8:
        return None
    return encoding, decoded


def _scan_notice_text_view(decoded: str, path: str, encoding: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for label, expression in NOTICE_PATTERNS:
        for match in list(re.finditer(expression, decoded, flags=re.IGNORECASE))[:2]:
            start = max(0, match.start() - 70)
            end = min(len(decoded), match.end() + 110)
            excerpt = " ".join(decoded[start:end].split())
            findings.append({
                "keyword_group": label,
                "path": path,
                "matched_text": match.group(0)[:48],
                "encoding": encoding,
                "excerpt": excerpt[:MAX_NOTICE_SNIPPET],
            })
    return findings


def _scan_branding_text_view(
    decoded: str, path: str, encoding: str
) -> tuple[list[dict[str, str]], bool]:
    """Find exact legacy branding labels in bounded text views, without editing them."""
    findings: list[dict[str, str]] = []
    occupied: list[tuple[int, int]] = []
    truncated = False
    for label, term in BRANDING_TERMS:
        # Word boundaries prevent the standalone "Youtube" label from also
        # matching the beginning of "youtuber" in the longer creator credit.
        expression = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
        matches = expression.finditer(decoded)
        for match in matches:
            if any(start < match.end() and end > match.start() for start, end in occupied):
                continue
            start = max(0, match.start() - 90)
            end = min(len(decoded), match.end() + 120)
            excerpt = " ".join(decoded[start:end].split())
            findings.append({
                "label": label,
                "path": path,
                "term": term,
                "matched_text": match.group(0)[:100],
                "encoding": encoding,
                "excerpt": excerpt[:240],
            })
            occupied.append((match.start(), match.end()))
            if len(findings) >= 25:
                truncated = True
                return findings, truncated
    return findings, truncated


def _scan_notice_text(data: bytes, path: str) -> list[dict[str, str]]:
    """Find likely notice/startup tokens in text-like resources, without editing them."""
    suffix = PurePosixPath(path.lower()).suffix
    if suffix not in NOTICE_TEXT_EXTENSIONS or data.startswith(b"MSES"):
        return []
    stream = io.BytesIO(data)
    if data.startswith(b"PK") and zipfile.is_zipfile(stream):
        return []
    view = _notice_text_view(data)
    if view is None:
        return []
    encoding, decoded = view
    return _scan_notice_text_view(decoded, path, encoding)


def _collect_bounded_strings(value: Any) -> tuple[list[str], bool]:
    """Collect bounded string keys/values from a Unity typetree without editing it."""
    stack = [value]
    strings: list[str] = []
    nodes = 0
    chars = 0
    truncated = False
    while stack:
        node = stack.pop()
        nodes += 1
        if nodes > MAX_NOTICE_ASSETBUNDLE_NODES:
            truncated = True
            break
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(key, str) and key:
                    strings.append(key)
                    chars += len(key)
                stack.append(child)
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
        elif isinstance(node, str) and node:
            strings.append(node)
            chars += len(node)
        if chars >= MAX_NOTICE_ASSETBUNDLE_TEXT_CHARS:
            truncated = True
            break
    if chars > MAX_NOTICE_ASSETBUNDLE_TEXT_CHARS:
        remaining = MAX_NOTICE_ASSETBUNDLE_TEXT_CHARS
        bounded = []
        for item in strings:
            if remaining <= 0:
                break
            bounded.append(item[:remaining])
            remaining -= len(bounded[-1])
        strings = bounded
    return strings, truncated


def _scan_notice_assetbundle(data: bytes, path: str) -> dict[str, Any]:
    """Bounded, read-only scan of Unity object names and text fields."""
    signatures = (b"UnityFS\x00", b"UnityRaw\x00", b"UnityWeb\x00")
    payload = data
    if not payload.startswith(signatures):
        if _expected_decoded_size(payload) is None:
            return {"scanned": False, "findings": [], "reason": "not_unity_or_aov_wrapped"}
        try:
            payload, _ = decode_aov_resource(payload, path)
        except AOVBytesError as exc:
            return {"scanned": False, "findings": [], "reason": f"decode_failed: {exc}"}
    if not payload.startswith(signatures):
        return {"scanned": False, "findings": [], "reason": "decoded_payload_is_not_unity"}
    if len(payload) > MAX_NOTICE_ASSETBUNDLE_BYTES:
        return {"scanned": False, "findings": [], "reason": "assetbundle_over_64_mib"}

    try:
        from .assetbundle_inspector import _load_unitypy_aov
        UnityPy_AOV = _load_unitypy_aov()
        env = UnityPy_AOV.load(payload)
    except Exception as exc:
        return {
            "scanned": False, "findings": [],
            "reason": f"assetbundle_parse_failed: {type(exc).__name__}",
        }

    findings: list[dict[str, str]] = []
    branding_findings: list[dict[str, str]] = []
    inspected_objects = 0
    text_truncated = False
    branding_truncated = False
    object_scan_errors = 0
    for asset_file in (getattr(env, "assets", []) or []):
        if inspected_objects >= MAX_NOTICE_ASSETBUNDLE_OBJECTS:
            text_truncated = True
            break
        objects = getattr(asset_file, "objects", {}) or {}
        remaining_objects = MAX_NOTICE_ASSETBUNDLE_OBJECTS - inspected_objects
        selected_objects = list(objects.values())[:remaining_objects]
        if len(objects) > remaining_objects:
            text_truncated = True
        for obj in selected_objects:
            object_type = getattr(getattr(obj, "type", None), "name", "")
            if object_type not in {"MonoBehaviour", "TextAsset", "ScriptableObject"}:
                continue
            inspected_objects += 1
            object_path = (
                f"{path}::{getattr(asset_file, 'name', 'assets')}"
                f"#{getattr(obj, 'path_id', '?')}"
            )
            try:
                if object_type == "TextAsset":
                    parsed = obj.read()
                    name = getattr(parsed, "m_Name", "")
                    script = getattr(parsed, "m_Script", "")
                    if isinstance(script, bytes):
                        view = _notice_text_view(script)
                        strings = [name]
                        if view:
                            strings.append(view[1])
                    else:
                        strings = [name, str(script or "")]
                    truncated = False
                else:
                    tree = obj.read_typetree()
                    object_name = (
                        str(tree.get("m_Name", "") or "")
                        if isinstance(tree, dict) else ""
                    )
                    strings, truncated = _collect_bounded_strings(tree)
                if object_type == "TextAsset":
                    object_name = str(name or "")
                text_truncated = text_truncated or truncated
                text = "\n".join(item for item in strings if item)
                if not text:
                    continue
                label_path = f"{object_path}/{object_name}" if object_name else object_path
                findings.extend(
                    _scan_notice_text_view(text, label_path, "unity-typetree")
                )
                brand_matches, brand_truncated = _scan_branding_text_view(
                    text, label_path, "unity-typetree"
                )
                branding_findings.extend(brand_matches)
                branding_truncated = branding_truncated or brand_truncated
                if len(branding_findings) >= MAX_BRANDING_CANDIDATES:
                    branding_findings = branding_findings[:MAX_BRANDING_CANDIDATES]
                    branding_truncated = True
                if len(findings) >= MAX_NOTICE_CANDIDATES:
                    findings = findings[:MAX_NOTICE_CANDIDATES]
                    text_truncated = True
                    break
            except Exception:
                object_scan_errors += 1
        if len(findings) >= MAX_NOTICE_CANDIDATES:
            break
    return {
        "scanned": True,
        "findings": findings,
        "branding_findings": branding_findings,
        "branding_truncated": branding_truncated,
        "objects_inspected": inspected_objects,
        "object_scan_errors": object_scan_errors,
        "truncated": text_truncated,
    }


def _inspect_decoded_payload(data: bytes) -> dict[str, Any]:
    if data.startswith(b"MSES"):
        try:
            table = parse_mses(data, verify_checksum=False)
        except MSESError as exc:
            return {"payload_type": "mses_invalid", "reason": str(exc)}
        # This is a structural inventory only. The first four bytes are
        # exposed as a raw prefix, not asserted to be a semantic table key.
        # Keep the report bounded for large production tables.
        inventory_limit = 4096
        marker_tokens = (b"Hero_LiXin_Skin_C",)
        row_inventory = []
        for index, row in enumerate(table.rows[:inventory_limit]):
            prefix = int.from_bytes(row[:4], "little")
            row_inventory.append({
                "row_index": index,
                "row_prefix_u32_le": prefix,
                "row_prefix_hex": f"0x{prefix:08x}",
                "row_size": len(row),
                "markers": [
                    marker.decode("ascii")
                    for marker in marker_tokens
                    if marker in row
                ],
            })
        prefix_counts: dict[int, int] = {}
        for row in table.rows:
            prefix = int.from_bytes(row[:4], "little")
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        duplicate_prefixes = sorted(
            (key for key, count in prefix_counts.items() if count > 1)
        )
        return {
            "payload_type": "mses_v7",
            "version": table.version,
            "field_count": table.field_count,
            "row_count": len(table.rows),
            "checksum_valid": table.checksum_valid,
            "row_prefix_inventory": row_inventory,
            "row_prefix_inventory_limit": inventory_limit,
            "row_prefix_inventory_truncated": len(table.rows) > inventory_limit,
            "duplicate_row_prefixes": [
                f"0x{key:08x}" for key in duplicate_prefixes[:256]
            ],
            "duplicate_row_prefixes_truncated": len(duplicate_prefixes) > 256,
        }
    stream = io.BytesIO(data)
    if zipfile.is_zipfile(stream):
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream, "r") as package:
                return {
                    "payload_type": "zip_package",
                    "entry_count": len(package.infolist()),
                    "uncompressed_bytes": sum(
                        item.file_size for item in package.infolist()
                    ),
                }
        except zipfile.BadZipFile:
            return {"payload_type": "zip_invalid"}
    return {"payload_type": "opaque_binary"}


def decode_archive_resources(
    input_paths: list[str], output_path: str, *, scan_only: bool = False
) -> dict[str, Any]:
    if not input_paths:
        raise AOVBytesArchiveError("Chưa có ZIP đầu vào.")
    report: dict[str, Any] = {
        "scan_only": scan_only,
        "decoded": [],
        "unsupported": [],
        "ignored_files": 0,
        "total_entries": 0,
        "decoded_bytes": 0,
        "nested_packages_expanded": 0,
        "nested_packages_not_expanded": [],
        "notice_scan_files": 0,
        "notice_assetbundles_scanned": 0,
        "notice_assetbundles_skipped": 0,
        "notice_assetbundle_scan_errors": [],
        "notice_candidates": [],
        "notice_candidates_truncated": False,
        "custom_lua_resources": [],
        "custom_lua_resources_truncated": False,
        "branding_candidates": [],
        "branding_candidates_truncated": False,
        "branding_scan_terms": [term for _, term in BRANDING_TERMS],
        "notice_scan_scope": "Text-like Lua/XML/JSON/TXT/config/CSV, decoded non-MSES .bytes, bounded custom Lua-family header/string inventory for selected lobby scripts, raw ZIP-based .pkg.bytes packages, and bounded UnityFS AssetBundle object text (MonoBehaviour/TextAsset/ScriptableObject, up to 64 MiB and 500 objects per bundle). Opaque binary remains unscanned.",
        "advisory_only": True,
        "merge_authorized": False,
        "game_tested": False,
    }
    output = Path(output_path)
    seen_output_paths: set[str] = set()
    total_uncompressed = 0

    def add_branding_candidates(source: str, candidates: list[dict[str, str]], truncated: bool = False) -> None:
        report["branding_candidates_truncated"] = (
            report["branding_candidates_truncated"] or truncated
        )
        for candidate in candidates:
            if len(report["branding_candidates"]) >= MAX_BRANDING_CANDIDATES:
                report["branding_candidates_truncated"] = True
                break
            report["branding_candidates"].append({
                "source": source,
                **candidate,
            })

    def process_archive(archive: zipfile.ZipFile, prefix: str, depth: int) -> None:
        nonlocal total_uncompressed
        infos = archive.infolist()
        report["total_entries"] += len(infos)
        if report["total_entries"] > MAX_ARCHIVE_ENTRIES:
            raise AOVBytesArchiveError("ZIP vượt giới hạn 20.000 mục.")
        seen_names: set[str] = set()
        for info in infos:
            path = _safe_member_path(info.filename)
            if path in seen_names:
                raise AOVBytesArchiveError(f"ZIP có đường dẫn trùng lặp: {path}")
            seen_names.add(path)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode) or info.flag_bits & 1:
                raise AOVBytesArchiveError(f"ZIP chứa mục liên kết hoặc mã hóa không hỗ trợ: {path}")
            if info.is_dir():
                continue
            if info.file_size > MAX_ENTRY_UNCOMPRESSED:
                raise AOVBytesArchiveError(f"ZIP entry vượt giới hạn 256 MiB: {path}")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED:
                raise AOVBytesArchiveError("Tổng dữ liệu giải nén vượt giới hạn 1 GiB.")
            try:
                raw = archive.read(info)
            except (RuntimeError, zipfile.BadZipFile, OSError, EOFError) as exc:
                raise AOVBytesArchiveError(f"Không đọc được ZIP entry: {path}") from exc

            lower_path = path.casefold()
            is_raw_pkg_zip = (
                lower_path.endswith(".pkg.bytes")
                and raw.startswith((b"PK\x03\x04", b"PK\x05\x06"))
            )
            if (
                (lower_path.endswith(".zip") or is_raw_pkg_zip)
                and depth < MAX_NESTED_ZIP_DEPTH
            ):
                try:
                    with zipfile.ZipFile(io.BytesIO(raw), "r") as nested:
                        process_archive(nested, f"{prefix}/{path}", depth + 1)
                    if is_raw_pkg_zip:
                        report["nested_packages_expanded"] += 1
                except zipfile.BadZipFile as exc:
                    if lower_path.endswith(".zip") or is_raw_pkg_zip:
                        raise AOVBytesArchiveError(
                            f"Package ZIP lồng có header nhưng dữ liệu không hợp lệ: {path}"
                        ) from exc
                continue

            if PurePosixPath(path.lower()).suffix == ".assetbundle":
                report["ignored_files"] += 1
                bundle_scan = _scan_notice_assetbundle(raw, path)
                if bundle_scan.get("scanned"):
                    report["notice_assetbundles_scanned"] += 1
                    report["notice_scan_files"] += int(bundle_scan.get("objects_inspected", 0))
                    if bundle_scan.get("truncated"):
                        report["notice_candidates_truncated"] = True
                    if bundle_scan.get("object_scan_errors"):
                        report["notice_assetbundle_scan_errors"].append({
                            "source": prefix,
                            "path": path,
                            "object_errors": bundle_scan["object_scan_errors"],
                        })
                    for finding in bundle_scan.get("findings", []):
                        if len(report["notice_candidates"]) >= MAX_NOTICE_CANDIDATES:
                            report["notice_candidates_truncated"] = True
                            break
                        report["notice_candidates"].append(
                            _build_notice_candidate(prefix, path, finding)
                        )
                    add_branding_candidates(
                        prefix,
                        bundle_scan.get("branding_findings", []),
                        bundle_scan.get("branding_truncated", False),
                    )
                else:
                    report["notice_assetbundles_skipped"] += 1
                    reason = bundle_scan.get("reason", "")
                    if reason and reason not in {"not_unity_or_aov_wrapped"}:
                        report["notice_assetbundle_scan_errors"].append({
                            "source": prefix,
                            "path": path,
                            "reason": reason,
                        })
                continue

            if not path.lower().endswith(".bytes"):
                report["ignored_files"] += 1
                if PurePosixPath(path.lower()).suffix in NOTICE_TEXT_EXTENSIONS:
                    report["notice_scan_files"] += 1
                    for finding in _scan_notice_text(raw, path):
                        if len(report["notice_candidates"]) >= MAX_NOTICE_CANDIDATES:
                            report["notice_candidates_truncated"] = True
                            break
                        report["notice_candidates"].append(
                            _build_notice_candidate(prefix, path, finding)
                        )
                    text_view = _notice_text_view(raw)
                    if text_view is not None:
                        encoding, decoded_text = text_view
                        matches, was_truncated = _scan_branding_text_view(
                            decoded_text, path, encoding
                        )
                        add_branding_candidates(prefix, matches, was_truncated)
                continue
            expected_size = _expected_decoded_size(raw)
            if expected_size is None:
                custom_lua = _inspect_custom_lua_resource(raw, path)
                if custom_lua is not None:
                    if len(report["custom_lua_resources"]) < MAX_NOTICE_CANDIDATES:
                        report["custom_lua_resources"].append({
                            "source": prefix,
                            **custom_lua,
                            "decode_mode": "custom_prefix_unwrapped",
                        })
                    else:
                        report["custom_lua_resources_truncated"] = True
                    report["notice_scan_files"] += 1
                    extracted_text = "\n".join(custom_lua["strings"])
                    for finding in _scan_notice_text_view(
                        extracted_text, path, "lua_ascii_strings"
                    ):
                        if len(report["notice_candidates"]) >= MAX_NOTICE_CANDIDATES:
                            report["notice_candidates_truncated"] = True
                            break
                        custom_candidate = _build_notice_candidate(
                            prefix, path, finding
                        )
                        custom_candidate.update({
                            "resource_role": "custom_lua_string_candidate",
                            "startup_removal_eligible": False,
                            "protection_reason": (
                                "Chuỗi được trích từ bytecode Lua tùy biến; chưa decompile "
                                "được luồng gọi nên không được xem là mục tiêu xóa popup."
                            ),
                        })
                        report["notice_candidates"].append(custom_candidate)
                    continue
                report["unsupported"].append({
                    "source": prefix,
                    "path": path,
                    "reason": "Header không thuộc định dạng AoV đã nhận diện.",
                })
                continue
            if expected_size > MAX_DECODED_RESOURCE:
                raise AOVBytesArchiveError(
                    f"Tài nguyên sau giải mã vượt giới hạn 256 MiB: {path}"
                )
            try:
                decoded, mode_name = decode_aov_resource(raw, path)
            except AOVBytesError as exc:
                report["unsupported"].append({
                    "source": prefix,
                    "path": path,
                    "reason": str(exc),
                })
                continue
            decoded_limit = MAX_SCAN_DECODED_BYTES if scan_only else MAX_TOTAL_DECODED_BYTES
            if report["decoded_bytes"] + len(decoded) > decoded_limit:
                limit_label = "256 MiB cho chế độ quét chỉ đọc" if scan_only else "1 GiB"
                raise AOVBytesArchiveError(
                    f"Tổng dữ liệu sau giải mã vượt giới hạn {limit_label}."
                )
            output_name = f"decoded/{prefix}/{path}"
            if output_name in seen_output_paths:
                raise AOVBytesArchiveError(f"Đường dẫn đầu ra bị trùng: {output_name}")
            seen_output_paths.add(output_name)
            if not scan_only:
                output.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as target:
                    target.writestr(output_name, decoded)
            payload_details = _inspect_decoded_payload(decoded)
            custom_lua = _inspect_custom_lua_resource(decoded, path)
            if custom_lua is not None:
                if len(report["custom_lua_resources"]) < MAX_NOTICE_CANDIDATES:
                    report["custom_lua_resources"].append({
                        "source": prefix,
                        **custom_lua,
                        "decode_mode": mode_name,
                    })
                else:
                    report["custom_lua_resources_truncated"] = True
                extracted_text = "\n".join(custom_lua["strings"])
                for finding in _scan_notice_text_view(
                    extracted_text, path, "lua_ascii_strings"
                ):
                    if len(report["notice_candidates"]) >= MAX_NOTICE_CANDIDATES:
                        report["notice_candidates_truncated"] = True
                        break
                    custom_candidate = _build_notice_candidate(
                        prefix, path, finding
                    )
                    custom_candidate.update({
                        "resource_role": "custom_lua_string_candidate",
                        "startup_removal_eligible": False,
                        "protection_reason": (
                            "Chuỗi được trích từ bytecode Lua tùy biến; chưa decompile "
                            "được luồng gọi nên không được xem là mục tiêu xóa popup."
                        ),
                    })
                    report["notice_candidates"].append(custom_candidate)
            report["notice_scan_files"] += 1
            for finding in _scan_notice_text(decoded, path):
                if len(report["notice_candidates"]) >= MAX_NOTICE_CANDIDATES:
                    report["notice_candidates_truncated"] = True
                    break
                report["notice_candidates"].append(
                    _build_notice_candidate(prefix, path, finding)
                )
            text_view = _notice_text_view(decoded)
            if text_view is not None:
                encoding, decoded_text = text_view
                matches, was_truncated = _scan_branding_text_view(
                    decoded_text, path, encoding
                )
                add_branding_candidates(prefix, matches, was_truncated)
            if path.lower().endswith(("heroskin_1.bytes", "heroskin_2.bytes")):
                try:
                    patch = parse_ifix_patch(decoded)
                    payload_details = {
                        "payload_type": "ifix_patch",
                        "fix_count": len(patch["fixes"]),
                        "method_count": len(patch["methods"]),
                        "type_count": len(patch["types"]),
                        "string_count": len(patch["strings"]),
                        "field_count": len(patch["fields"]),
                    }
                except IFixMergeError as exc:
                    payload_details = {
                        "payload_type": "ifix_candidate_invalid",
                        "reason": str(exc),
                    }
            report["decoded_bytes"] += len(decoded)
            report["decoded"].append({
                "source": prefix,
                "path": path,
                "output_path": None if scan_only else output_name,
                "mode": mode_name,
                "original_size": len(raw),
                "decoded_size": len(decoded),
                "payload": payload_details,
            })

            # AoV package resources such as CommonActions.pkg.bytes can contain
            # another ZIP after their outer AES/Zstandard wrapper is removed.
            # Keep the decoded package itself and inspect its members as well.
            if path.lower().endswith(".pkg.bytes"):
                package_stream = io.BytesIO(decoded)
                if zipfile.is_zipfile(package_stream):
                    if depth >= MAX_NESTED_ZIP_DEPTH:
                        report["nested_packages_not_expanded"].append({
                            "source": prefix,
                            "path": path,
                            "reason": "Đã đạt giới hạn độ sâu ZIP lồng.",
                        })
                    else:
                        package_stream.seek(0)
                        try:
                            with zipfile.ZipFile(package_stream, "r") as nested_package:
                                process_archive(
                                    nested_package,
                                    f"{prefix}/{path}::decoded",
                                    depth + 1,
                                )
                            report["nested_packages_expanded"] += 1
                        except zipfile.BadZipFile as exc:
                            raise AOVBytesArchiveError(
                                f"Package sau giải mã có cấu trúc ZIP lỗi: {path}"
                            ) from exc

    try:
        if not scan_only and output.exists():
            output.unlink()
        for index, input_path in enumerate(input_paths, start=1):
            label = f"archive_{index}_{Path(input_path).name}"
            try:
                with zipfile.ZipFile(input_path, "r") as archive:
                    process_archive(archive, label, 0)
            except zipfile.BadZipFile as exc:
                raise AOVBytesArchiveError(f"File đầu vào không phải ZIP hợp lệ: {Path(input_path).name}") from exc
        if (
            not scan_only
            and not report["decoded"]
            and not report["notice_candidates"]
        ):
            raise AOVBytesArchiveError(
                "Không tìm thấy tài nguyên .bytes giải mã được hoặc chuỗi thông báo trong các ZIP đã tải lên."
            )
        if not scan_only:
            with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as target:
                target.writestr(
                    "decode-report.json",
                    json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
                )
    except Exception:
        if not scan_only:
            output.unlink(missing_ok=True)
        raise
    return report
