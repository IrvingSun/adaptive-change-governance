from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import ConfigError, load_yaml
from .next_action import NextActionPlanner
from .progress import ProgressTracker


ARTIFACTS = [
    "request.md",
    "evidence-pack.yaml",
    "risk-assessment.yaml",
    "workflow-recommendation.yaml",
    "workflow-plan.md",
    "progress.yaml",
    "analysis-report.yaml",
    "analysis-report.md",
    "approved-workflow.yaml",
    "approved-workflow-plan.md",
    "agent-tasks.yaml",
    "agent-tasks.md",
    "technical-plan.yaml",
    "technical-plan.md",
    "approved-technical-plan.yaml",
    "approved-technical-plan.md",
    "diff-verification.yaml",
    "diff-verification.md",
]


@dataclass
class RunStatusRenderer:
    workflow_modules: dict[str, Any]

    def render(self, run_dir: Path) -> str:
        evidence = self._load_if_exists(run_dir / "evidence-pack.yaml")
        risk = self._load_if_exists(run_dir / "risk-assessment.yaml")
        workflow = self._load_if_exists(run_dir / "workflow-recommendation.yaml")
        rec = workflow.get("workflow_recommendation", {})
        goal = rec.get("request_goal", {})
        planner = NextActionPlanner()
        next_action = planner.plan(run_dir)
        blockers = next_action.get("blockers", [])
        lines = [
            "Run Status",
            "",
            f"Run: {run_dir.name}",
            f"Request: {evidence.get('request', {}).get('original', 'unknown')}",
            f"Request goal: {goal.get('type', 'implementation')}",
            f"Requires code change: {goal.get('requires_code_change', 'unknown')}",
            f"Current gate: {next_action.get('current_gate')}",
            f"Final level: {risk.get('final_level', rec.get('final_level', 'unknown'))}",
            f"Triggered guardrails: {risk.get('triggered_guardrails', rec.get('triggered_guardrails', []))}",
            "",
            ProgressTracker(self.workflow_modules).render(run_dir, color=True).rstrip(),
            "",
            "Artifacts:",
        ]
        lines.extend(f"  - [{'yes' if (run_dir / item).exists() else 'no'}] {item}" for item in ARTIFACTS)
        extra_validations = sorted(path.name for path in run_dir.glob("artifact-validation-*.yaml"))
        if extra_validations:
            lines.extend(f"  - [yes] {item}" for item in extra_validations)
        lines.extend(["", "Blockers:"])
        lines.extend(f"  - {item}" for item in blockers or ["none"])
        lines.extend(["", "Suggested next commands:"])
        lines.append(f"  - {next_action.get('command') or 'none'}")
        return "\n".join(lines) + "\n"

    def _load_if_exists(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return load_yaml(path)
        except ConfigError:
            return {}
