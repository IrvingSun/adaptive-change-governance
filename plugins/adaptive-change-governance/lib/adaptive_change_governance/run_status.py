from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import ConfigError, load_yaml
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
        blockers = self._blockers(run_dir)
        next_commands = self._next_commands(run_dir, goal, rec, blockers)
        lines = [
            "Run Status",
            "",
            f"Run: {run_dir.name}",
            f"Request: {evidence.get('request', {}).get('original', 'unknown')}",
            f"Request goal: {goal.get('type', 'implementation')}",
            f"Requires code change: {goal.get('requires_code_change', 'unknown')}",
            f"Current gate: {self._current_gate(run_dir, rec)}",
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
        lines.extend(f"  - {item}" for item in next_commands or ["none"])
        return "\n".join(lines) + "\n"

    def _load_if_exists(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return load_yaml(path)
        except ConfigError:
            return {}

    def _current_gate(self, run_dir: Path, rec: dict[str, Any]) -> str:
        if (run_dir / "diff-verification.yaml").exists():
            diff = self._load_if_exists(run_dir / "diff-verification.yaml")
            if diff.get("status") == "blocked":
                return "diff_verification_blocked"
        for path in run_dir.glob("artifact-validation-*.yaml"):
            validation = self._load_if_exists(path)
            if validation.get("status") == "blocked":
                return "artifact_validation_blocked"
        if (run_dir / ".analysis-complete").exists():
            return "analysis_complete"
        if (run_dir / ".technical-plan-approved").exists():
            return "implementation_gate"
        if (run_dir / "technical-plan.yaml").exists():
            return "technical_plan_approval"
        if (run_dir / ".workflow-approved").exists():
            return "technical_plan_proposal"
        return rec.get("default_stop_gate", "workflow_plan_approval")

    def _blockers(self, run_dir: Path) -> list[str]:
        blockers = []
        for path in run_dir.glob("artifact-validation-*.yaml"):
            validation = self._load_if_exists(path)
            if validation.get("status") == "blocked":
                errors = "; ".join(validation.get("errors", []))
                blockers.append(f"{path.name} blocked: {errors}")
        if (run_dir / "diff-verification.yaml").exists():
            diff = self._load_if_exists(run_dir / "diff-verification.yaml")
            if diff.get("status") == "blocked":
                errors = "; ".join(diff.get("errors", []))
                blockers.append(f"diff-verification.yaml blocked: {errors}")
        progress = self._load_if_exists(run_dir / "progress.yaml")
        for step in progress.get("steps", []):
            if step.get("status") == "blocked":
                blockers.append(f"workflow step blocked: {step.get('id')}")
        if not (run_dir / ".workflow-approved").exists() and not (run_dir / ".analysis-complete").exists():
            blockers.append("workflow not approved")
        if (run_dir / ".workflow-approved").exists() and (run_dir / "technical-plan.yaml").exists() and not (run_dir / ".technical-plan-approved").exists():
            blockers.append("technical plan not approved")
        return blockers

    def _next_commands(self, run_dir: Path, goal: dict[str, Any], rec: dict[str, Any], blockers: list[str]) -> list[str]:
        run_id = run_dir.name
        goal_type = goal.get("type", "implementation")
        if blockers:
            commands = [f"change-assess --status {run_id}"]
            if any("artifact-validation" in item for item in blockers):
                commands.append(f"change-assess --validate-artifact {run_id} --module <module> --artifact <artifact>")
            if any("diff-verification" in item for item in blockers):
                commands.append(f"change-assess --verify-diff {run_id}")
            if "workflow not approved" in blockers:
                if goal_type in {"analysis_only", "decision_support"}:
                    commands.append(f"change-assess --generate-analysis-report {run_id}")
                else:
                    commands.append(f"change-assess --approve-workflow {run_id}")
            if "technical plan not approved" in blockers:
                commands.append(f"change-assess --review-technical-plan {run_id}")
                commands.append(f"change-assess --approve-technical-plan {run_id}")
            return commands
        if goal_type in {"analysis_only", "decision_support"}:
            if not (run_dir / ".analysis-complete").exists():
                return [f"change-assess --generate-analysis-report {run_id}"]
            return ["analysis complete"]
        if not (run_dir / ".workflow-approved").exists():
            return [f"change-assess --review-workflow {run_id}", f"change-assess --approve-workflow {run_id}"]
        if not (run_dir / "technical-plan.yaml").exists():
            return [f"change-assess --propose-technical-plan {run_id}"]
        if not (run_dir / ".technical-plan-approved").exists():
            return [f"change-assess --review-technical-plan {run_id}", f"change-assess --approve-technical-plan {run_id}"]
        if rec.get("request_goal", {}).get("requires_code_change", True):
            return [f"change-assess --check-gate {run_id} --stage implementation", f"change-assess --verify-diff {run_id}"]
        return ["no implementation step required"]
