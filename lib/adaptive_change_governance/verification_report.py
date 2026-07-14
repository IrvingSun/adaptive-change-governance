from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import ConfigError, dump_yaml, load_yaml


@dataclass
class VerificationReportGenerator:
    def generate(self, run_dir: Path) -> dict[str, Any]:
        workflow = self._load_if_exists(run_dir / "workflow-recommendation.yaml")
        rec = workflow.get("workflow_recommendation", {})
        required_modules = rec.get("required_modules", [])
        progress = self._load_if_exists(run_dir / "progress.yaml")
        completed_modules = {
            step.get("id")
            for step in progress.get("steps", [])
            if step.get("status") == "done"
        }
        missing_modules = sorted(module for module in required_modules if module not in completed_modules)
        artifact_validations = self._artifact_validations(run_dir)
        diff = self._load_if_exists(run_dir / "diff-verification.yaml")
        reassessment = self._load_if_exists(run_dir / "reassessment.yaml")
        blockers = []
        implementation_goal = rec.get("request_goal", {}).get("type") not in {"analysis_only", "decision_support"}
        if implementation_goal:
            if not (run_dir / ".workflow-approved").exists():
                blockers.append("workflow approval is missing")
            if not (run_dir / ".technical-plan-approved").exists():
                blockers.append("technical plan approval is missing")
            if not diff:
                blockers.append("diff verification is missing")
            if not reassessment:
                blockers.append("reassessment is missing")
        if missing_modules:
            blockers.append("required modules not completed: " + ", ".join(missing_modules))
        for item in artifact_validations:
            if item.get("status") == "blocked":
                blockers.append(f"artifact validation blocked: {item.get('module')}")
        if diff and diff.get("status") != "pass":
            blockers.append("diff verification is blocked")
        if reassessment.get("reassessment", {}).get("requires_human_reapproval"):
            blockers.append("reassessment requires human reapproval")
        report = {
            "version": 1,
            "run_id": run_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pass" if not blockers else "blocked",
            "approvals": {
                "workflow_approved": (run_dir / ".workflow-approved").exists(),
                "technical_plan_approved": (run_dir / ".technical-plan-approved").exists(),
            },
            "module_completion": {
                "required_modules": required_modules,
                "completed_modules": sorted(module for module in completed_modules if module),
                "missing_modules": missing_modules,
            },
            "artifact_validations": artifact_validations,
            "diff_verification": {
                "present": bool(diff),
                "status": diff.get("status", "missing") if diff else "missing",
                "errors": diff.get("errors", []) if diff else [],
            },
            "reassessment": {
                "present": bool(reassessment),
                "requires_human_reapproval": reassessment.get("reassessment", {}).get("requires_human_reapproval", False) if reassessment else False,
                "previous_level": reassessment.get("reassessment", {}).get("previous_level") if reassessment else "",
                "new_level": reassessment.get("reassessment", {}).get("new_level") if reassessment else "",
                "reasons": reassessment.get("reassessment", {}).get("reasons", []) if reassessment else [],
            },
            "blockers": blockers,
            "residual_risks": self._residual_risks(run_dir, diff, reassessment),
        }
        dump_yaml(run_dir / "verification-report.yaml", report)
        (run_dir / "verification-report.md").write_text(self.render_markdown(report), encoding="utf-8")
        if report["status"] == "pass":
            (run_dir / ".verification-complete").write_text(str(report["created_at"]) + "\n", encoding="utf-8")
        return report

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# Verification Report",
            "",
            f"- FACT: run_id={report.get('run_id')}",
            f"- DECISION: status={report.get('status')}",
            "",
            "## Approvals",
            f"- FACT: workflow_approved={report.get('approvals', {}).get('workflow_approved')}",
            f"- FACT: technical_plan_approved={report.get('approvals', {}).get('technical_plan_approved')}",
            "",
            "## Module Completion",
        ]
        for item in report.get("module_completion", {}).get("missing_modules", []) or ["none"]:
            lines.append(f"- DECISION: missing_module={item}")
        lines.extend(["", "## Diff Verification"])
        diff = report.get("diff_verification", {})
        lines.append(f"- FACT: present={diff.get('present')}")
        lines.append(f"- DECISION: status={diff.get('status')}")
        lines.extend(["", "## Reassessment"])
        reassessment = report.get("reassessment", {})
        lines.append(f"- FACT: present={reassessment.get('present')}")
        lines.append(f"- DECISION: requires_human_reapproval={reassessment.get('requires_human_reapproval')}")
        lines.extend(["", "## Blockers"])
        lines.extend(f"- DECISION: {item}" for item in report.get("blockers", []) or ["none"])
        lines.extend(["", "## Residual Risks"])
        lines.extend(f"- UNKNOWN: {item.replace('UNKNOWN: ', '', 1)}" for item in report.get("residual_risks", []) or ["none"])
        return "\n".join(lines) + "\n"

    def _artifact_validations(self, run_dir: Path) -> list[dict[str, Any]]:
        validations = []
        for path in sorted(run_dir.glob("artifact-validation-*.yaml")):
            data = self._load_if_exists(path)
            validations.append({
                "file": path.name,
                "module": data.get("module", ""),
                "artifact": data.get("artifact", ""),
                "status": data.get("status", "unknown"),
                "errors": data.get("errors", []),
            })
        return validations

    def _residual_risks(self, run_dir: Path, diff: dict[str, Any], reassessment: dict[str, Any]) -> list[str]:
        risks = []
        if diff:
            risks.extend(diff.get("unknowns", []))
        if reassessment:
            reasons = reassessment.get("reassessment", {}).get("reasons", [])
            if reasons:
                risks.append("UNKNOWN: reassessment found changes requiring human review before completion.")
        evidence = self._load_if_exists(run_dir / "evidence-pack.yaml")
        risks.extend(evidence.get("unknowns", []))
        return risks

    def _load_if_exists(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return load_yaml(path)
        except ConfigError:
            return {}
