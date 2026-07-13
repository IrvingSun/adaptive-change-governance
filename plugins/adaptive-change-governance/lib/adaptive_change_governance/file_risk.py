from __future__ import annotations

from fnmatch import fnmatch
from typing import Any


FILE_RISK_SCORE = {
    "low": 1,
    "medium": 2,
    "high": 4,
    "critical": 5,
}


def evaluate_file_risk(paths: list[str], project_risk: dict[str, Any]) -> dict[str, Any]:
    rules = project_risk.get("file_risk", [])
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
    highest = max((item["score"] for item in matches), default=1)
    return {
        "highest_level": _level_from_score(highest),
        "highest_score": highest,
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
