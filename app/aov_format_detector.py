from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath


# Read-only signature detection. This identifies containers/codecs, not whether
# two resources are compatible or can be merged safely.
def detect_resource_format(name: str, header: bytes) -> tuple[str, str]:
    lower = name.lower()
    suffix = PurePosixPath(lower).suffix
    if header.startswith(b"\x22\x4a\x67\xef"):
        return "AOV AES + Zstandard", "AoV wrapped encrypted/compressed payload"
    if header.startswith(b"\x22\x4a\x67\x00"):
        return "AOV AES", "AoV wrapped encrypted payload"
    if header.startswith(b"\x22\x4a\x00\xef"):
        return "AOV Zstandard wrapper", "AoV size/header wrapper"
    if header.startswith(b"\x28\xb5\x2f\xfd"):
        return "Zstandard", "Zstandard frame"
    if header.startswith((b"UnityFS", b"UnityRaw", b"UnityWeb")):
        return "Unity AssetBundle", "Unity bundle signature"
    if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "ZIP container", "Nested ZIP/archive"
    if header.startswith(b"BKHD"):
        return "Wwise SoundBank", "Wwise BNK header"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "WAV audio", "RIFF/WAVE"
    if header.startswith(b"OggS"):
        return "Ogg audio", "Ogg stream"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG image", "PNG signature"
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG image", "JPEG signature"
    if header.startswith(b"\x1f\x8b"):
        return "GZIP", "GZIP stream"
    stripped = header.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<?xml") or (stripped.startswith(b"<") and b">" in stripped[:32]):
        return "XML/text", "XML-like text header"
    if stripped.startswith((b"{", b"[")):
        return "JSON/text", "JSON-like text header"
    if suffix == ".bytes":
        return "Unknown .bytes payload", "No supported AoV header signature"
    if suffix == ".assetbundle":
        return "Unknown AssetBundle payload", "No Unity bundle signature"
    if suffix in {".bnk", ".wem"}:
        return "Unknown audio payload", "Extension suggests audio; signature not recognized"
    if not header:
        return "Empty file", "Zero-byte entry"
    return "Unknown binary", "No known signature matched"


def summarize_formats(items: list[tuple[str, str]]) -> tuple[Counter[str], dict[str, list[str]]]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for format_name, path in items:
        counts[format_name] += 1
        examples.setdefault(format_name, [])
        if len(examples[format_name]) < 8:
            examples[format_name].append(path)
    return counts, examples
