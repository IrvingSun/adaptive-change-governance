from __future__ import annotations

from fnmatch import fnmatch
from typing import Any


FILE_RISK_SCORE = {
    "low": 1,
    "medium": 2,
    "high": 4,
    "critical": 5,
}


LOW_EFFECTIVE_RISK_NATURES = {
    "comment_only",
    "documentation_only",
    "display_text_only",
    "metadata_only",
}


def evaluate_file_risk(paths: list[str], project_risk: dict[str, Any], intent: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = project_risk.get("file_risk", [])
    intent = intent or {}
    matches = []
    for path in paths:
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern", ""))
            level = str(rule.get("level", "medium"))
            if pattern and _matches(path, pattern):
                matches.append({
                    "path": path,
                    "pattern": pattern,
                    "level": level,
                    "score": FILE_RISK_SCORE.get(level, 2),
                    "reason": str(rule.get("reason", "")),
                })
    inherent = max((item["score"] for item in matches), default=1)
    effective = _effective_score(inherent, intent)
    constraints = []
    if effective < inherent:
        constraints.append(
            "UNKNOWN: effective file risk was lowered by low-risk change intent; implementation gate must verify the diff is comment/documentation/display-only and does not change executable behavior."
        )
    return {
        "highest_level": _level_from_score(inherent),
        "highest_score": inherent,
        "effective_level": _level_from_score(effective),
        "effective_score": effective,
        "risk_adjustment": "lowered_by_change_nature" if effective < inherent else "none",
        "constraints": constraints,
        "matches": sorted(matches, key=lambda item: (-item["score"], item["path"], item["pattern"])),
    }


def _matches(path: str, pattern: str) -> bool:
    return fnmatch(path, pattern) or fnmatch("/" + path, pattern)


def _level_from_score(score: int) -> str:
    for level, value in FILE_RISK_SCORE.items():
        if value == score:
            return level
    if score >= 5:
        return "critical"
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _effective_score(inherent: int, intent: dict[str, Any]) -> int:
    if str(intent.get("change_nature", "")) in LOW_EFFECTIVE_RISK_NATURES:
        return 1
    return inherent
