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


SEMANTIC_ROLE_RISK = {
    "database_migration": ("critical", "semantic role: database migration or schema/data operation"),
    "data_access": ("high", "semantic role: persistence or data access code"),
    "auth_or_permission": ("high", "semantic role: authentication, authorization, or permission registry"),
    "background_job": ("high", "semantic role: scheduler, worker, or background engine"),
    "public_api": ("medium", "semantic role: public API, route, or controller"),
    "service_logic": ("medium", "semantic role: service or business logic"),
    "configuration": ("medium", "semantic role: runtime configuration"),
    "frontend_route": ("low", "semantic role: frontend route or navigation display"),
    "ui_view": ("low", "semantic role: UI view or display text"),
    "documentation": ("low", "semantic role: documentation"),
    "test": ("low", "semantic role: automated test"),
    "generated_asset": ("low", "semantic role: generated or bundled asset"),
}


def evaluate_file_risk(
    paths: list[str],
    project_risk: dict[str, Any],
    intent: dict[str, Any] | None = None,
    file_facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rules = project_risk.get("file_risk", [])
    intent = intent or {}
    matches: list[dict[str, Any]] = []
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
    for fact in file_facts or []:
        path = str(fact.get("path", ""))
        role = str(fact.get("role", ""))
        if not path or role not in SEMANTIC_ROLE_RISK:
            continue
        level, reason = SEMANTIC_ROLE_RISK[role]
        matches.append({
            "path": path,
            "pattern": f"semantic:{role}",
            "level": level,
            "score": FILE_RISK_SCORE.get(level, 2),
            "reason": reason,
            "confidence": str(fact.get("confidence", "unknown")),
            "evidence_strength": str(fact.get("strength", "weak")),
        })
    inherent = max((int(item["score"]) for item in matches), default=1)
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
        "matches": sorted(matches, key=lambda item: (-int(item["score"]), str(item["path"]), str(item["pattern"]))),
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
