from __future__ import annotations

from typing import Any


class ValidationError(ValueError):
    pass


def _require_mapping(data: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"{context}.{key} must be a mapping")
    return value


def _require_list(data: dict[str, Any], key: str, context: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValidationError(f"{context}.{key} must be a list")
    return value


def validate_project_risk(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValidationError("project-risk.yaml version must be 1")
    project = _require_mapping(data, "project", "project-risk.yaml")
    for key in ("name", "baseline_level"):
        if not project.get(key):
            raise ValidationError(f"project-risk.yaml.project.{key} is required")
    if project["baseline_level"] not in {"L1", "L2", "L3", "L4"}:
        raise ValidationError("project baseline_level must be L1, L2, L3, or L4")
    for section in ("business_risk", "engineering_health"):
        values = _require_mapping(data, section, "project-risk.yaml")
        for key, value in values.items():
            if not isinstance(value, int) or value < 1 or value > 5:
                raise ValidationError(f"{section}.{key} must be an integer from 1 to 5")
    for key in ("critical_domains", "critical_paths", "known_constraints", "default_human_gates"):
        _require_list(data, key, "project-risk.yaml")


def validate_guardrails(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValidationError("guardrails.yaml version must be 1")
    guardrails = _require_list(data, "hard_guardrails", "guardrails.yaml")
    seen = set()
    for item in guardrails:
        if not isinstance(item, dict):
            raise ValidationError("guardrails.yaml hard_guardrails entries must be mappings")
        guardrail_id = item.get("id")
        if not guardrail_id:
            raise ValidationError("guardrail id is required")
        if guardrail_id in seen:
            raise ValidationError(f"duplicate guardrail id: {guardrail_id}")
        seen.add(guardrail_id)
        when = _require_mapping(item, "when", f"guardrail {guardrail_id}")
        any_conditions = when.get("any")
        if not isinstance(any_conditions, list) or not any_conditions:
            raise ValidationError(f"guardrail {guardrail_id} must define when.any")
        if "require" in item and not isinstance(item["require"], list):
            raise ValidationError(f"guardrail {guardrail_id}.require must be a list")
        if "prohibit" in item and not isinstance(item["prohibit"], list):
            raise ValidationError(f"guardrail {guardrail_id}.prohibit must be a list")
    overrides = _require_mapping(data, "level_overrides", "guardrails.yaml")
    for guardrail_id, override in overrides.items():
        if not isinstance(override, dict) or override.get("minimum_level") not in {"L1", "L2", "L3", "L4"}:
            raise ValidationError(f"level_overrides.{guardrail_id}.minimum_level must be L1-L4")


def validate_workflow_modules(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValidationError("workflow-modules.yaml version must be 1")
    modules = _require_mapping(data, "modules", "workflow-modules.yaml")
    for module_id, module in modules.items():
        if not isinstance(module, dict):
            raise ValidationError(f"workflow module {module_id} must be a mapping")
        if not module.get("description") or not module.get("output"):
            raise ValidationError(f"workflow module {module_id} requires description and output")


def validate_all(project_risk: dict[str, Any], guardrails: dict[str, Any], workflow_modules: dict[str, Any]) -> None:
    validate_project_risk(project_risk)
    validate_guardrails(guardrails)
    validate_workflow_modules(workflow_modules)
