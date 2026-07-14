from __future__ import annotations

from pathlib import Path
from typing import Any

from .config_loader import load_yaml


LOW_RISK_CHANGE_KINDS = {
    "copy_change",
    "menu_label_change",
    "ui_text_change",
    "documentation_change",
    "comment_change",
}

LOW_RISK_CHANGE_NATURES = {
    "comment_only",
    "documentation_only",
    "display_text_only",
    "metadata_only",
}

RISKY_INTENT_FIELDS = {
    "data_operation",
    "database_schema_change",
    "public_interface_change",
    "permission_change",
    "security_change",
    "financial_change",
}

GOAL_TYPES = {
    "implementation",
    "analysis_only",
    "decision_support",
    "planning_only",
}

GOAL_STOP_GATES = {
    "implementation": "workflow_plan_approval",
    "analysis_only": "analysis_complete",
    "decision_support": "decision_ready",
    "planning_only": "technical_plan_approval",
}


def load_intent_file(path: Path) -> dict[str, Any]:
    intent = load_yaml(path)
    return normalize_intent(intent)


def normalize_intent(intent: dict[str, Any] | None) -> dict[str, Any]:
    if not intent:
        return {}
    scope_value = intent.get("scope")
    scope: dict[str, Any] = scope_value if isinstance(scope_value, dict) else {}
    risk_value = intent.get("risk_hints")
    risk: dict[str, Any] = risk_value if isinstance(risk_value, dict) else {}
    request_goal = _normalize_request_goal(intent.get("request_goal"), intent)
    normalized = {
        "version": intent.get("version", 1),
        "change_kind": str(intent.get("change_kind", "") or ""),
        "change_nature": str(intent.get("change_nature", "") or ""),
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
        "request_goal": request_goal,
        "notes": _string_list(intent.get("notes")),
    }
    return normalized


def infer_request_goal_from_text(request: str) -> dict[str, Any]:
    text = request.lower()
    analysis_markers = ("分析", "评估", "判断", "是否", "为什么", "风险", "结论", "review", "analyze", "assess", "investigate")
    planning_markers = ("方案", "计划", "设计", "plan", "design")
    implementation_markers = ("修改", "实现", "删除", "移除", "新增", "修复", "改成", "更新", "change", "implement", "remove", "delete", "fix", "add")
    if any(marker in text for marker in implementation_markers):
        goal_type = "implementation"
    elif any(marker in text for marker in planning_markers):
        goal_type = "planning_only"
    elif any(marker in text for marker in analysis_markers):
        goal_type = "decision_support"
    else:
        goal_type = "implementation"
    return {
        "type": goal_type,
        "requires_code_change": _requires_code_change(goal_type),
        "default_stop_gate": GOAL_STOP_GATES[goal_type],
        "rationale": _goal_rationale(goal_type),
    }


def is_low_risk_intent(intent: dict[str, Any]) -> bool:
    if not intent:
        return False
    if intent.get("change_kind") not in LOW_RISK_CHANGE_KINDS and intent.get("change_nature") not in LOW_RISK_CHANGE_NATURES:
        return False
    return not any(intent.get("risk_hints", {}).values())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _normalize_request_goal(value: Any, intent: dict[str, Any]) -> dict[str, Any]:
    goal = value if isinstance(value, dict) else {}
    goal_type = str(goal.get("type", "") or "").strip()
    if goal_type not in GOAL_TYPES:
        goal_type = _infer_goal_type(intent)
    requires = goal.get("requires_code_change", None)
    if requires not in {True, False}:
        requires = _requires_code_change(goal_type)
    rationale = str(goal.get("rationale", "") or _goal_rationale(goal_type))
    if goal_type == "implementation" and requires is False:
        # Contradictory intent must not suppress guardrails: an implementation
        # goal always requires code change, so the flag is corrected here.
        requires = True
        rationale += " CORRECTION: requires_code_change=false contradicts request_goal.type=implementation and was ignored."
    stop_gate = str(goal.get("default_stop_gate", "") or "").strip() or GOAL_STOP_GATES[goal_type]
    return {
        "type": goal_type,
        "requires_code_change": requires,
        "default_stop_gate": stop_gate,
        "rationale": rationale,
    }


def _infer_goal_type(intent: dict[str, Any]) -> str:
    change_kind = str(intent.get("change_kind", "") or "")
    change_nature = str(intent.get("change_nature", "") or "")
    summary = str(intent.get("summary", "") or "").lower()
    if change_kind in {"analysis", "risk_analysis", "code_review", "investigation"} or change_nature == "analysis_only":
        return "analysis_only"
    if change_kind in {"decision_support", "feasibility_assessment"}:
        return "decision_support"
    if change_kind in {"technical_plan", "planning"} or change_nature == "planning_only":
        return "planning_only"
    if any(token in summary for token in ("analy", "review", "investigat", "评估", "分析", "是否", "可否")):
        return "decision_support"
    return "implementation"


def _requires_code_change(goal_type: str) -> bool:
    return goal_type in {"implementation"}


def _goal_rationale(goal_type: str) -> str:
    if goal_type == "analysis_only":
        return "INFERENCE: user asks for analysis output rather than repository modification."
    if goal_type == "decision_support":
        return "INFERENCE: user asks for a decision or recommendation before any implementation."
    if goal_type == "planning_only":
        return "INFERENCE: user asks for a technical plan but not implementation."
    return "INFERENCE: user asks to change repository behavior or files."
