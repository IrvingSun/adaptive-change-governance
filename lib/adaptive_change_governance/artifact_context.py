from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .config_loader import ConfigError, load_yaml


def apply_validated_artifacts(evidence: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    records = _validated_artifact_records(run_dir)
    if not records:
        return evidence
    adjusted = deepcopy(evidence)
    adjusted["artifact_context"] = {
        "source": "validated_module_artifacts",
        "policy": [
            "DECISION: validated agent artifacts may reduce uncertainty but must not remove strong hard-guardrail facts.",
            "DECISION: user run-context has higher priority than artifact context.",
        ],
        "artifacts": records,
    }
    adjusted.setdefault("evidence_sources", [])
    if "validated_module_artifacts" not in adjusted["evidence_sources"]:
        adjusted["evidence_sources"].append("validated_module_artifacts")
    return adjusted


def _validated_artifact_records(run_dir: Path) -> list[dict[str, Any]]:
    progress = _load_optional(run_dir / "progress.yaml")
    records: list[dict[str, Any]] = []
    for step in progress.get("steps", []):
        if step.get("status") != "done":
            continue
        module = step.get("id", "")
        for artifact in step.get("artifacts", []) or []:
            validation = _load_optional(run_dir / f"artifact-validation-{module}.yaml")
            if validation.get("status") != "pass":
                continue
            data = _load_optional(run_dir / str(artifact))
            evidence_items = [item for item in data.get("evidence", []) if isinstance(item, dict)]
            records.append({
                "module": module,
                "artifact": artifact,
                "validation": f"artifact-validation-{module}.yaml",
                "confidence": _aggregate_confidence(evidence_items),
                "evidence": evidence_items,
            })
    return records


def _aggregate_confidence(evidence_items: list[dict[str, Any]]) -> str:
    values = {str(item.get("confidence", "")) for item in evidence_items}
    if "high" in values:
        return "high"
    if "medium" in values:
        return "medium"
    if "low" in values:
        return "low"
    return "unknown"


def _load_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_yaml(path)
    except ConfigError:
        return {}
