"""Deterministic reference fan-out scanner (blast radius).

Risk is not only about what the changed code does, but how many other places
reference it. A one-line edit to a symbol imported by 200 files is high risk
even though the edit is tiny. This scanner counts, deterministically, how many
repository files reference the symbols defined in the changed files, so risk
scoring can treat blast radius as a first-class, code-grounded fact rather than
guessing from request wording.

It is intentionally a *floor*: import/textual references are cheap and
reproducible. It cannot resolve reflection, dynamic dispatch, or cross-service
calls; the host model refines those on top and may only raise risk, never lower
it below this floor.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# Paths that, by convention, hold shared contracts. A change here is broad even
# if the reference count scan under-counts (dynamic consumers, other services).
SHARED_PATH_HINTS = (
    "model", "models", "enum", "enums", "schema", "schemas", "common", "shared",
    "constant", "constants", "type", "types", "proto", "contract", "contracts",
    "interface", "interfaces", "dto", "entity", "entities",
)

# Stems too generic to be meaningful reference anchors on their own.
STOPWORD_STEMS = {
    "index", "main", "app", "__init__", "utils", "util", "helper", "helpers",
    "test", "tests", "setup", "config", "constants", "types", "base", "core",
}

_DEF = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_TOP_CONST = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*[:=]", re.MULTILINE)
_JS_EXPORT = re.compile(
    r"export\s+(?:default\s+)?(?:const|function|class|let|var|enum|interface|type)\s+([A-Za-z_$][\w$]*)"
)

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build"}
MAX_REPO_FILES = 8000
MAX_ANCHORS = 16
MAX_REFERENCES_PER_FILE = 20
MAX_FILE_BYTES = 400_000


def scan_references(root: Path, direct_files: list[str], repo_files: list[str]) -> dict[str, Any]:
    """Return blast-radius facts for the given changed files.

    ``direct_files`` and ``repo_files`` are repo-relative POSIX paths. The result
    is deterministic for a given repository state.
    """
    own_modules = {_top_module(path) for path in direct_files if path}
    anchors, anchor_to_source = _collect_anchors(root, direct_files)
    if not anchors:
        return _empty(own_modules, anchors=[])

    # Exclude only the files that define an anchor (self-references), not every
    # localized file: a file that references the changed symbol is a consumer
    # even if coarse localization also flagged it as directly relevant.
    anchor_sources = set(anchor_to_source.values())
    candidates = [
        path for path in sorted(repo_files)
        if path not in anchor_sources and not _skip(path)
    ][:MAX_REPO_FILES]

    inbound_reference_count = 0
    referencing_files: list[str] = []
    referencing_modules: set[str] = set()
    examples: list[dict[str, Any]] = []
    for path in candidates:
        text = _read(root / path)
        if not text:
            continue
        hits = _reference_hits(text, anchors)
        if not hits:
            continue
        occurrences = min(sum(count for _, count in hits), MAX_REFERENCES_PER_FILE)
        inbound_reference_count += occurrences
        referencing_files.append(path)
        referencing_modules.add(_top_module(path))
        if len(examples) < 10:
            symbol, _ = max(hits, key=lambda item: item[1])
            examples.append({
                "path": path,
                "line": _first_line(text, symbol),
                "fact": f"FACT: {path} references '{symbol}' ({anchor_to_source.get(symbol, 'changed file')}).",
                "confidence": "high",
            })

    cross_modules = sorted(referencing_modules - own_modules)
    is_shared_contract = len(referencing_modules) >= 3 or _has_shared_path(direct_files)
    return {
        "changed_symbols": sorted(anchors),
        "inbound_reference_count": inbound_reference_count,
        "referencing_files": len(referencing_files),
        "referencing_modules": sorted(referencing_modules),
        "crosses_module_boundary": bool(cross_modules),
        "cross_module_consumers": cross_modules,
        "is_shared_contract": is_shared_contract,
        "fan_out_confidence": _confidence(inbound_reference_count, len(referencing_modules)),
        "evidence": examples,
        "notes": [
            "INFERENCE: reference counts use import/textual matches; reflection and dynamic dispatch are not resolved.",
            "UNKNOWN: cross-service consumers outside this repository are not visible to the static scan.",
        ],
    }


def _empty(own_modules: set[str], anchors: list[str]) -> dict[str, Any]:
    return {
        "changed_symbols": sorted(anchors),
        "inbound_reference_count": 0,
        "referencing_files": 0,
        "referencing_modules": [],
        "crosses_module_boundary": False,
        "cross_module_consumers": [],
        "is_shared_contract": False,
        "fan_out_confidence": "low",
        "evidence": [],
        "notes": ["UNKNOWN: no referenceable symbols were extracted from the changed files."],
    }


def _collect_anchors(root: Path, direct_files: list[str]) -> tuple[set[str], dict[str, str]]:
    anchors: set[str] = set()
    anchor_to_source: dict[str, str] = {}
    for path in direct_files:
        text = _read(root / path)
        symbols: list[str] = []
        if text:
            symbols.extend(_DEF.findall(text))
            symbols.extend(_TOP_CONST.findall(text))
            symbols.extend(_JS_EXPORT.findall(text))
        stem = Path(path).stem
        if stem and stem.lower() not in STOPWORD_STEMS and len(stem) >= 3:
            symbols.append(stem)
        for symbol in symbols:
            if len(symbol) < 3 or symbol.lower() in STOPWORD_STEMS:
                continue
            if symbol not in anchor_to_source:
                anchor_to_source[symbol] = path
            anchors.add(symbol)
            if len(anchors) >= MAX_ANCHORS:
                return anchors, anchor_to_source
    return anchors, anchor_to_source


def _reference_hits(text: str, anchors: set[str]) -> list[tuple[str, int]]:
    hits = []
    for symbol in anchors:
        count = len(re.findall(r"\b" + re.escape(symbol) + r"\b", text))
        if count:
            hits.append((symbol, count))
    return hits


def _top_module(path: str) -> str:
    parts = Path(path).parts
    return parts[0] if parts else path


def _has_shared_path(direct_files: list[str]) -> bool:
    for path in direct_files:
        lowered = {part.lower() for part in Path(path).parts}
        stem = Path(path).stem.lower()
        if lowered & set(SHARED_PATH_HINTS) or stem in SHARED_PATH_HINTS:
            return True
    return False


def _confidence(inbound: int, modules: int) -> str:
    if inbound >= 50 or modules >= 3:
        return "high"
    if inbound >= 10 or modules >= 2:
        return "medium"
    return "low"


def _skip(path: str) -> bool:
    parts = set(Path(path).parts)
    if parts & SKIP_DIRS:
        return True
    return ".ai-governance/runs/" in path or path.startswith(".ai-governance/runs")


def _read(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _first_line(text: str, symbol: str) -> int:
    pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return number
    return 1
