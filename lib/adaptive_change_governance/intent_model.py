from __future__ import annotations

from pathlib import Path
from typing import Any

from .config_loader import load_yaml


LOW_RISK_CHANGE_KINDS = {
    "copy_change",
    "menu_label_change",
    "ui_text_change",
    "documentation_change",
}

RISKY_INTENT_FIELDS = {
    "data_operation",
    "database_schema_change",
    "public_interface_change",
    "permission_change",
    "security_change",
    "financial_change",
}


def load_intent_file(path: Path) -> dict[str, Any]:
    intent = load_yaml(path)
    return normalize_intent(intent)


def normalize_intent(intent: dict[str, Any] | None) -> dict[str, Any]:
    if not intent:
        return {}
    scope = intent.get("scope") if isinstance(intent.get("scope"), dict) else {}
    risk = intent.get("risk_hints") if isinstance(intent.get("risk_hints"), dict) else {}
    normalized = {
        "version": intent.get("version", 1),
        "change_kind": str(intent.get("change_kind", "") or ""),
        "summary": str(intent.get("summary", "") or ""),
        "confidence": str(intent.get("confidence", "unknown") or "unknown"),
        "scope": {
            "included": _string_list(scope.get("included")),
            "excluded": _string_list(scope.get("excluded")),
            "unknowns": _string_list(scope.get("unknowns")),
        },
        "risk_hints": {
            key: bool(risk.get(key, False))
            for key in RISKY_INTENT_FIELDS
        },
        "notes": _string_list(intent.get("notes")),
    }
    return normalized


def is_low_risk_intent(intent: dict[str, Any]) -> bool:
    if not intent:
        return False
    if intent.get("change_kind") not in LOW_RISK_CHANGE_KINDS:
        return False
    return not any(intent.get("risk_hints", {}).values())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
