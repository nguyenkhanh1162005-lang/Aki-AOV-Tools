"""Filename- and metadata-based Actor/Skin detection for AoV mod archives.

This module deliberately reports evidence, not merge permission. Actor IDs are
accepted only from resource path patterns with explicit AoV context; arbitrary
three-digit numbers elsewhere in paths are ignored.
"""
from __future__ import annotations

import re
from collections.abc import Iterable


_ACTOR_PATTERNS = (
    re.compile(r"(?:^|/)Actor[_-](\d{3})[_-]Actions\.pkg\.bytes$", re.IGNORECASE),
    re.compile(r"(?:^|/)AssetRefs/Hero/(\d{3})_AssetRef\.bytes$", re.IGNORECASE),
    re.compile(r"(?:^|/)assetbundle/(?:[^/]+/)*(\d{3})_[^/]+\.assetbundle$", re.IGNORECASE),
    re.compile(r"(?:^|/)Prefab_Hero/(\d{3})_[^/]+(?:\.bytes|\.pkg\.bytes)$", re.IGNORECASE),
    re.compile(r"(?:^|/)(?:actor|hero)[_-](\d{3})(?:/|$)", re.IGNORECASE),
)


def detect_actor_ids(paths: Iterable[str]) -> dict[str, list[str]]:
    """Return Actor IDs keyed by the evidence type that identified them."""
    evidence: dict[str, set[str]] = {
        "actor_actions": set(),
        "assetrefs": set(),
        "assetbundles": set(),
        "prefab_paths": set(),
        "actor_directories": set(),
    }
    patterns = (
        ("actor_actions", _ACTOR_PATTERNS[0]),
        ("assetrefs", _ACTOR_PATTERNS[1]),
        ("assetbundles", _ACTOR_PATTERNS[2]),
        ("prefab_paths", _ACTOR_PATTERNS[3]),
        ("actor_directories", _ACTOR_PATTERNS[4]),
    )
    for raw_path in paths:
        path = str(raw_path).replace("\\", "/").lstrip("/")
        for kind, pattern in patterns:
            evidence[kind].update(match.group(1) for match in pattern.finditer(path))
    return {kind: sorted(values) for kind, values in evidence.items() if values}


def extract_skin_ids_from_text(text: str, actor_ids: Iterable[str]) -> list[str]:
    """Find explicit 5–6 digit Skin IDs only when their Actor prefix is known."""
    prefixes = {str(value) for value in actor_ids}
    if not prefixes:
        return []
    candidates = set(re.findall(r"(?<!\d)(\d{5,6})(?!\d)", text or ""))
    return sorted(value for value in candidates if value[:3] in prefixes)


def extract_skin_name_hints(text: str, *, limit: int = 100) -> list[str]:
    """Read human-readable skin names from creator-provided skin-list text."""
    result: list[str] = []
    seen: set[str] = set()
    for raw_line in (text or "").replace("\r", "\n").split("\n"):
        line = raw_line.strip().lstrip("-*•· ").strip()
        if not line or len(line) > 160:
            continue
        if not re.search(r"[A-Za-zÀ-ỹ]", line):
            continue
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            result.append(line)
        if len(result) >= max(1, limit):
            break
    return result
