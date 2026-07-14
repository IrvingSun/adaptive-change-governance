from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import ConfigError, load_yaml


# Human-readable reason for each recommended action. Presentation only: this
# never changes what --execute-next may run, nor any approval/gate semantics.
ACTION_PHRASES = {
    "approve_workflow": "Workflow approval is required before technical planning.",
    "approve_technical_plan": "Technical-plan approval is required before implementation.",
    "generate_analysis_report": "Request goal is analysis-only; generate the analysis report.",
    "generate_agent_tasks": "L3/L4 workflow: generate agent tasks to split the work.",
    "propose_technical_plan": "Workflow is approved; propose the technical plan.",
    "answer_investigation_question": "Open investigation questions must be answered before technical planning.",
    "reassess": "Implementation is done; run reassessment before verification.",
    "review_reassessment": "Reassessment requests human re-approval.",
    "generate_verification_report": "Generate the final verification report.",
    "implementation_gate_ready": "Check the implementation gate before editing business code.",
    "resolve_blockers": "Resolve the blockers below before continuing.",
    "complete": "This run is complete.",
    "none": "No further action is available.",
}


@dataclass
class NextActionPlanner:
    def plan(self, run_dir: Path) -> dict[str, Any]:
        workflow = self._load_if_exists(run_dir / "workflow-recommendation.yaml")
        rec = workflow.get("workflow_recommendation", {})
        goal = rec.get("request_goal", {})
        goal_type = goal.get("type", "implementation")
        blockers = self.blockers(run_dir)
        gate = self.current_gate(run_dir, rec)
        action = {
            "version": 1,
            "run_id": run_dir.name,
            "current_gate": gate,
            "request_goal": goal,
            "requires_user_confirmation": False,
            "recommended_action": "none",
            "command": "",
            "can_execute": False,
            "blockers": blockers,
        }
        if blockers:
            return self._blocked_action(action, blockers)
        if goal_type in {"analysis_only", "decision_support"}:
            if not (run_dir / ".analysis-complete").exists():
                return self._auto(action, "generate_analysis_report", f"change-assess --generate-analysis-report {run_dir.name}")
            action["recommended_action"] = "complete"
            return action
        if not (run_dir / ".workflow-approved").exists():
            return self._manual(action, "approve_workflow", f"change-assess --approve-workflow {run_dir.name}")
        open_questions = self._open_investigation_questions(run_dir)
        if open_questions:
            first = open_questions[0]
            action["recommended_action"] = "answer_investigation_question"
            action["command"] = (
                f"produce {first.get('expected_artifact')} for {first.get('module')}; "
                f"then run change-assess --complete-step {run_dir.name} "
                f"--module {first.get('module')} --artifact {first.get('expected_artifact')}"
            )
            action["requires_user_confirmation"] = False
            action["can_execute"] = False
            action["investigation_questions"] = open_questions
            return action
        if not (run_dir / "agent-tasks.yaml").exists() and rec.get("final_level") in {"L3", "L4"}:
            return self._auto(action, "generate_agent_tasks", f"change-assess --generate-agent-tasks {run_dir.name}")
        if not (run_dir / "technical-plan.yaml").exists():
            return self._auto(action, "propose_technical_plan", f"change-assess --propose-technical-plan {run_dir.name}")
        if not (run_dir / ".technical-plan-approved").exists():
            return self._manual(action, "approve_technical_plan", f"change-assess --approve-technical-plan {run_dir.name}")
        if not (run_dir / "reassessment.yaml").exists():
            return self._auto(action, "reassess", f"change-assess --reassess {run_dir.name}")
        reassessment = self._load_if_exists(run_dir / "reassessment.yaml")
        if reassessment.get("reassessment", {}).get("requires_human_reapproval"):
            return self._manual(action, "review_reassessment", f"change-assess --review-workflow {run_dir.name}")
        if not (run_dir / "verification-report.yaml").exists():
            return self._auto(action, "generate_verification_report", f"change-assess --generate-verification-report {run_dir.name}")
        verification = self._load_if_exists(run_dir / "verification-report.yaml")
        if verification.get("status") == "pass":
            action["recommended_action"] = "complete"
            return action
        action["recommended_action"] = "implementation_gate_ready"
        action["command"] = f"change-assess --check-gate {run_dir.name} --stage implementation"
        action["can_execute"] = False
        action["requires_user_confirmation"] = False
        return action

    def current_gate(self, run_dir: Path, rec: dict[str, Any]) -> str:
        if (run_dir / ".verification-complete").exists():
            return "completed"
        if (run_dir / "verification-report.yaml").exists():
            verification = self._load_if_exists(run_dir / "verification-report.yaml")
            if verification.get("status") == "blocked":
                return "verification_blocked"
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

    def blockers(self, run_dir: Path) -> list[str]:
        blockers = []
        if (run_dir / "verification-report.yaml").exists():
            verification = self._load_if_exists(run_dir / "verification-report.yaml")
            if verification.get("status") == "blocked":
                errors = "; ".join(verification.get("blockers", []))
                blockers.append(f"verification-report.yaml blocked: {errors}")
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
            workflow = self._load_if_exists(run_dir / "workflow-recommendation.yaml")
            goal_type = workflow.get("workflow_recommendation", {}).get("request_goal", {}).get("type", "implementation")
            if goal_type not in {"analysis_only", "decision_support"}:
                blockers.append("workflow not approved")
        if (run_dir / ".workflow-approved").exists() and (run_dir / "technical-plan.yaml").exists() and not (run_dir / ".technical-plan-approved").exists():
            blockers.append("technical plan not approved")
        return blockers

    def _open_investigation_questions(self, run_dir: Path) -> list[dict[str, Any]]:
        artifact = self._load_if_exists(run_dir / "investigation-questions.yaml")
        questions = []
        for item in artifact.get("questions", []):
            expected = item.get("expected_artifact")
            if item.get("status") == "answered":
                continue
            if expected and (run_dir / expected).exists():
                continue
            questions.append(item)
        return questions

    def operator_summary(self, run_dir: Path, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        """Presentation-layer summary for a run, built entirely from plan() and
        existing artifacts. It surfaces state and a recommended next step; it does
        not approve, execute, or change any gate/risk/approval semantics.

        `plan` may be passed in by a caller that already computed it (e.g.
        --status) to avoid recomputing; when omitted it is computed here, so the
        default `operator_summary(run_dir)` usage is unchanged."""
        if plan is None:
            plan = self.plan(run_dir)
        risk = self._load_if_exists(run_dir / "risk-assessment.yaml")
        workflow = self._load_if_exists(run_dir / "workflow-recommendation.yaml")
        rec = workflow.get("workflow_recommendation", {})
        action = plan.get("recommended_action", "none")
        return {
            "run_id": run_dir.name,
            "current_stage": plan.get("current_gate"),
            "risk_level": risk.get("final_level", rec.get("final_level", "unknown")),
            "request_goal": (plan.get("request_goal") or {}).get("type", "implementation"),
            "why": self._risk_why(risk, rec),
            "recommended_action": action,
            "recommended_action_reason": ACTION_PHRASES.get(action, ""),
            "requires_user_confirmation": bool(plan.get("requires_user_confirmation")),
            "blocked_by": list(plan.get("blockers", [])),
            "command": plan.get("command", ""),
            "audit_location": f".ai-governance/runs/{run_dir.name}/",
        }

    def render_operator_summary(self, summary: dict[str, Any]) -> str:
        lines = [
            "# Operator Summary",
            "",
            f"Current stage: {summary.get('current_stage')}",
            f"Risk: {summary.get('risk_level')}",
            f"Request goal: {summary.get('request_goal')}",
            "",
            "Why:",
        ]
        lines.extend(f"  - {item}" for item in summary.get("why") or ["unknown"])
        lines.extend([
            "",
            f"Recommended action: {summary.get('recommended_action_reason') or summary.get('recommended_action')}",
            f"Requires human confirmation: {'yes' if summary.get('requires_user_confirmation') else 'no'}",
            "",
            "Blocked by:",
        ])
        lines.extend(f"  - {item}" for item in summary.get("blocked_by") or ["none"])
        lines.extend([
            "",
            "Command:",
            f"  {summary.get('command') or 'none'}",
            "",
            f"Audit files: {summary.get('audit_location') or 'none'}",
        ])
        return "\n".join(lines) + "\n"

    def _risk_why(self, risk: dict[str, Any], rec: dict[str, Any]) -> list[str]:
        triggered = rec.get("triggered_guardrails") or risk.get("triggered_guardrails") or []
        if triggered:
            return [f"{name} guardrail triggered" for name in triggered]
        weak = [item.get("id") for item in risk.get("weak_guardrail_candidates", []) if item.get("id")]
        if weak:
            return [
                "No hard guardrails triggered by current evidence.",
                f"Weak signals need confirmation: {', '.join(str(item) for item in weak)}",
            ]
        return ["No hard guardrails (database/API/permission/security/financial) triggered by current evidence."]

    def render(self, plan: dict[str, Any]) -> str:
        action = plan.get("recommended_action", "none")
        lines = [
            f"Next action: {action}",
            f"Requires human confirmation: {'yes' if plan.get('requires_user_confirmation') else 'no'}",
            f"Reason: {ACTION_PHRASES.get(action, 'See run status for details.')}",
            "Command:",
            f"  {plan.get('command') or 'none'}",
        ]
        blockers = plan.get("blockers", [])
        if blockers:
            lines.extend(["", "Blocked by:"])
            lines.extend(f"  - {item}" for item in blockers)
        questions = plan.get("investigation_questions", [])
        if questions:
            lines.extend(["", "Investigation questions:"])
            for item in questions[:5]:
                lines.append(f"  - [{item.get('priority')}] {item.get('module')} -> {item.get('expected_artifact')}: {item.get('question')}")
        return "\n".join(lines) + "\n"

    def _blocked_action(self, action: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
        if "workflow not approved" in blockers:
            return self._manual(action, "approve_workflow", f"change-assess --approve-workflow {action['run_id']}")
        if "technical plan not approved" in blockers:
            return self._manual(action, "approve_technical_plan", f"change-assess --approve-technical-plan {action['run_id']}")
        action["recommended_action"] = "resolve_blockers"
        action["can_execute"] = False
        if any("artifact-validation" in item for item in blockers):
            action["command"] = f"change-assess --validate-artifact {action['run_id']} --module <module> --artifact <artifact>"
        elif any("diff-verification" in item for item in blockers):
            action["command"] = f"change-assess --verify-diff {action['run_id']}"
        elif any("verification-report" in item for item in blockers):
            action["command"] = f"change-assess --generate-verification-report {action['run_id']}"
        else:
            action["command"] = f"change-assess --status {action['run_id']}"
        return action

    def _manual(self, action: dict[str, Any], recommended: str, command: str) -> dict[str, Any]:
        action["recommended_action"] = recommended
        action["command"] = command
        action["requires_user_confirmation"] = True
        action["can_execute"] = False
        return action

    def _auto(self, action: dict[str, Any], recommended: str, command: str) -> dict[str, Any]:
        action["recommended_action"] = recommended
        action["command"] = command
        action["requires_user_confirmation"] = False
        action["can_execute"] = True
        return action

    def _load_if_exists(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return load_yaml(path)
        except ConfigError:
            return {}
