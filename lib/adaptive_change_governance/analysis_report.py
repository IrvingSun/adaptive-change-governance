from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml, load_yaml


class AnalysisReportError(ValueError):
    pass


@dataclass
class AnalysisReportGenerator:
    workflow_modules: dict[str, Any]

    def generate(self, run_dir: Path) -> dict[str, Any]:
        evidence = load_yaml(run_dir / "evidence-pack.yaml")
        risk = load_yaml(run_dir / "risk-assessment.yaml")
        workflow = load_yaml(run_dir / "workflow-recommendation.yaml")
        rec = workflow["workflow_recommendation"]
        goal = rec.get("request_goal", {})
        report = {
            "version": 1,
            "run_id": run_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_artifacts": [
                "request.md",
                "evidence-pack.yaml",
                "risk-assessment.yaml",
                "workflow-recommendation.yaml",
                "progress.yaml",
            ],
            "request": {
                "original": evidence.get("request", {}).get("original", ""),
                "goal": goal,
                "default_stop_gate": rec.get("default_stop_gate", "workflow_plan_approval"),
            },
            "conclusion": self._conclusion(goal, risk, rec),
            "risk_summary": {
                "final_level": risk.get("final_level"),
                "weighted_score": risk.get("weighted_score"),
                "triggered_guardrails": risk.get("triggered_guardrails", []),
                "weak_guardrail_candidates": [item.get("id") for item in risk.get("weak_guardrail_candidates", [])],
                "prohibited_actions": rec.get("prohibited", []),
            },
            "code_facts": self._code_facts(evidence),
            "module_plan": {
                "required_modules": rec.get("required_modules", []),
                "optional_modules": rec.get("optional_modules", []),
                "human_gates": rec.get("human_gates", []),
            },
            "unknowns": evidence.get("unknowns", []),
            "recommended_next_actions": self._next_actions(goal, rec),
        }
        dump_yaml(run_dir / "analysis-report.yaml", report)
        (run_dir / "analysis-report.md").write_text(self.render_markdown(report), encoding="utf-8")
        (run_dir / ".analysis-complete").write_text(report["created_at"] + "\n", encoding="utf-8")
        return report

    def render_markdown(self, report: dict[str, Any]) -> str:
        conclusion = report.get("conclusion", {})
        request = report.get("request", {})
        risk = report.get("risk_summary", {})
        code = report.get("code_facts", {})
        lines = [
            "# Analysis Report",
            "",
            f"- FACT: run_id={report.get('run_id')}",
            f"- FACT: request={request.get('original')}",
            f"- INFERENCE: request_goal={request.get('goal', {}).get('type', 'implementation')}",
            f"- DECISION: default_stop_gate={request.get('default_stop_gate')}",
            f"- DECISION: conclusion={conclusion.get('summary')}",
            "",
            "## Risk",
            f"- FACT: final_level={risk.get('final_level')}",
            f"- FACT: weighted_score={risk.get('weighted_score')}",
            f"- FACT: triggered_guardrails={risk.get('triggered_guardrails', [])}",
            f"- FACT: weak_guardrail_candidates={risk.get('weak_guardrail_candidates', [])}",
            "",
            "## Code Facts",
        ]
        lines.extend(f"- FACT: direct_file={item}" for item in code.get("direct_files", []) or ["none"])
        lines.extend(f"- FACT: related_file={item}" for item in code.get("related_files", []) or [])
        lines.extend([
            f"- FACT: affected_domains={code.get('affected_domains', [])}",
            f"- FACT: change_types={code.get('change_types', [])}",
            "",
            "## Unknowns",
        ])
        lines.extend(f"- UNKNOWN: {item.replace('UNKNOWN: ', '', 1)}" for item in report.get("unknowns", []) or ["none"])
        lines.extend(["", "## Recommended Next Actions"])
        for item in report.get("recommended_next_actions", []):
            lines.append(f"- DECISION: {item}")
        return "\n".join(lines) + "\n"

    def _conclusion(self, goal: dict[str, Any], risk: dict[str, Any], rec: dict[str, Any]) -> dict[str, str]:
        goal_type = goal.get("type", "implementation")
        if goal_type == "analysis_only":
            summary = "analysis complete; no technical plan or code implementation is required by this request"
        elif goal_type == "decision_support":
            summary = "decision support complete; user should decide whether to approve a follow-up implementation request"
        elif goal_type == "planning_only":
            summary = "planning request assessed; technical plan may be generated after workflow approval"
        else:
            summary = "implementation request assessed; workflow approval is required before technical planning"
        return {
            "summary": summary,
            "final_level": str(risk.get("final_level", "unknown")),
            "stop_gate": str(rec.get("default_stop_gate", "workflow_plan_approval")),
        }

    def _code_facts(self, evidence: dict[str, Any]) -> dict[str, Any]:
        code = evidence.get("code_findings", {})
        return {
            "direct_files": [item.get("path", "") for item in code.get("direct_files", [])],
            "related_files": [item.get("path", "") for item in code.get("related_files", [])[:20]],
            "affected_domains": code.get("affected_domains", []),
            "change_types": code.get("change_types", []),
            "operations": code.get("operations", []),
            "file_risk": code.get("file_risk", {}),
        }

    def _next_actions(self, goal: dict[str, Any], rec: dict[str, Any]) -> list[str]:
        goal_type = goal.get("type", "implementation")
        if goal_type == "analysis_only":
            return [
                "stop at analysis_complete unless the user creates a new implementation request",
                "do not generate technical-plan or edit business code for this run",
            ]
        if goal_type == "decision_support":
            return [
                "present the recommendation to the user and wait for a separate implementation decision",
                "do not generate technical-plan or edit business code for this run",
            ]
        if goal_type == "planning_only":
            return [
                "ask for workflow approval before generating technical-plan",
                "do not edit business code after planning unless a new implementation request is approved",
            ]
        return [
            "ask for workflow approval before technical-plan generation",
            "do not edit business code before technical-plan approval and implementation gate check",
        ]
