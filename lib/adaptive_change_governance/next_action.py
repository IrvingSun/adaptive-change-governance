from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import ConfigError, load_yaml


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

    def render(self, plan: dict[str, Any]) -> str:
        lines = [
            "Next Action",
            "",
            f"Run: {plan.get('run_id')}",
            f"Current gate: {plan.get('current_gate')}",
            f"Requires user confirmation: {'yes' if plan.get('requires_user_confirmation') else 'no'}",
            f"Recommended action: {plan.get('recommended_action')}",
            f"Can execute automatically: {'yes' if plan.get('can_execute') else 'no'}",
            "",
            "Command:",
            f"  {plan.get('command') or 'none'}",
            "",
            "Blockers:",
        ]
        lines.extend(f"  - {item}" for item in plan.get("blockers", []) or ["none"])
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
