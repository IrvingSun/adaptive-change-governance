from __future__ import annotations

from copy import deepcopy
from typing import Any


EXCLUSION_GROUPS = {
    "database": {
        "terms": ["database", "db", "sql", "schema", "数据", "数据库", "表结构"],
        "fields": {
            "change_types": ["database_schema"],
            "affected_domains": ["database-schema", "data-integrity"],
            "operations": ["delete", "truncate", "irreversible_migration", "bulk_update"],
        },
        "flags": ["database_changes"],
    },
    "public_interface": {
        "terms": ["api", "interface", "endpoint", "route", "接口", "公共接口"],
        "fields": {
            "change_types": ["public_api", "message_schema"],
            "affected_domains": ["public-interface", "message-contract"],
        },
        "flags": ["public_api_changes", "message_schema_changes"],
    },
    "financial": {
        "terms": ["financial", "money", "payment", "refund", "billing", "金额", "支付", "退款", "计费"],
        "fields": {
            "affected_domains": ["financial-calculation"],
        },
        "flags": [],
    },
    "security": {
        "terms": ["auth", "permission", "role", "security", "权限", "授权", "认证", "安全"],
        "fields": {
            "affected_domains": ["authentication", "authorization", "credentials"],
        },
        "flags": [],
    },
}


def apply_user_context(evidence: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context, dict):
        return evidence
    adjusted = deepcopy(evidence)
    adjusted.setdefault("user_context", context)
    adjusted.setdefault("evidence_sources", [])
    if "user_context" not in adjusted["evidence_sources"]:
        adjusted["evidence_sources"].append("user_context")
    changes: list[str] = []
    conflicts: list[str] = []
    excluded_text = " ".join(str(item).lower() for item in context.get("scope", {}).get("excluded", []))
    correction_text = " ".join(str(item).lower() for item in context.get("corrections", []))
    text = f"{excluded_text} {correction_text}"
    code = adjusted.setdefault("code_findings", {})
    for group, config in EXCLUSION_GROUPS.items():
        if not any(term.lower() in text for term in config["terms"]):
            continue
        for field, values in config["fields"].items():
            for value in values:
                if value not in code.get(field, []):
                    continue
                if _has_strong_evidence(code, field, value):
                    conflicts.append(f"UNKNOWN: user context excludes {value}, but strong code evidence still supports it; hard evidence was preserved.")
                    continue
                code[field] = [item for item in code.get(field, []) if item != value]
                changes.append(f"DECISION: removed weak {field}={value} because user context explicitly excluded {group}.")
        for flag in config["flags"]:
            if code.get(flag) and not _flag_has_strong_evidence(code, flag):
                code[flag] = False
                changes.append(f"DECISION: set {flag}=false because user context excluded {group} and only weak evidence was present.")
    if changes or conflicts:
        adjusted["context_adjustments"] = {
            "applied": changes,
            "conflicts": conflicts,
            "source": "run-context.yaml",
        }
        adjusted.setdefault("unknowns", []).extend(conflicts)
    return adjusted


def _has_strong_evidence(code: dict[str, Any], field: str, value: str) -> bool:
    evidence_key = {
        "affected_domains": "domain_evidence",
        "change_types": "change_type_evidence",
        "operations": "operation_evidence",
    }.get(field)
    if not evidence_key:
        return False
    matching = [item for item in code.get(evidence_key, []) if item.get("value") == value]
    return any(item.get("strength") == "strong" for item in matching)


def _flag_has_strong_evidence(code: dict[str, Any], flag: str) -> bool:
    if flag == "database_changes":
        return any(_has_strong_evidence(code, "change_types", value) for value in ["database_schema"]) or any(
            _has_strong_evidence(code, "operations", value)
            for value in ["delete", "truncate", "irreversible_migration", "bulk_update"]
        )
    if flag == "public_api_changes":
        return _has_strong_evidence(code, "change_types", "public_api")
    if flag == "message_schema_changes":
        return _has_strong_evidence(code, "change_types", "message_schema")
    return False
