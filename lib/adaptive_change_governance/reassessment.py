from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml, load_yaml
from .context_adjuster import apply_user_context
from .repository_analyzer import RepositoryAnalyzer
from .risk_evaluator import LEVEL_ORDER, RiskEvaluator, render_risk_markdown
from .workflow_composer import WorkflowComposer


@dataclass
class ReassessmentRunner:
    root: Path
    project_risk: dict[str, Any]
    guardrails: dict[str, Any]
    workflow_modules: dict[str, Any]
    calibration: dict[str, Any] | None = None

    def run(self, run_dir: Path) -> dict[str, Any]:
        initial_evidence = load_yaml(run_dir / "evidence-pack.yaml")
        initial_risk = load_yaml(run_dir / "risk-assessment.yaml")
        initial_workflow = load_yaml(run_dir / "workflow-recommendation.yaml")
        request = initial_evidence.get("request", {}).get("original", "")
        intent = initial_evidence.get("request", {}).get("model_intent", {})
        post_evidence = RepositoryAnalyzer(self.root).analyze(request, self.project_risk, intent=intent)
        context = load_yaml(run_dir / "run-context.yaml") if (run_dir / "run-context.yaml").exists() else {}
        post_evidence = apply_user_context(post_evidence, context)
        post_risk = RiskEvaluator(self.project_risk, self.guardrails, self.calibration).evaluate(post_evidence)
        post_workflow = WorkflowComposer(self.project_risk, self.workflow_modules).compose(post_evidence, post_risk)
        comparison = self._compare(initial_evidence, initial_risk, initial_workflow, post_evidence, post_risk, post_workflow)
        comparison["run_id"] = run_dir.name
        dump_yaml(run_dir / "post-evidence-pack.yaml", post_evidence)
        dump_yaml(run_dir / "post-risk-assessment.yaml", post_risk)
        (run_dir / "post-risk-assessment.md").write_text(render_risk_markdown(post_risk), encoding="utf-8")
        dump_yaml(run_dir / "post-workflow-recommendation.yaml", post_workflow)
        dump_yaml(run_dir / "reassessment.yaml", comparison)
        (run_dir / "reassessment.md").write_text(self.render_markdown(comparison), encoding="utf-8")
        (run_dir / ".reassessment-complete").write_text(comparison["reassessed_at"] + "\n", encoding="utf-8")
        return comparison

    def render_markdown(self, comparison: dict[str, Any]) -> str:
        reassessment = comparison["reassessment"]
        lines = [
            "# Reassessment",
            "",
            f"- FACT: run_id={comparison.get('run_id')}",
            f"- FACT: previous_level={reassessment.get('previous_level')}",
            f"- FACT: new_level={reassessment.get('new_level')}",
            f"- DECISION: requires_human_reapproval={reassessment.get('requires_human_reapproval')}",
            "",
            "## Reasons",
        ]
        lines.extend(f"- DECISION: {item}" for item in reassessment.get("reasons", []) or ["none"])
        lines.extend(["", "## Added Modules"])
        lines.extend(f"- DECISION: {item}" for item in reassessment.get("added_modules", []) or ["none"])
        lines.extend(["", "## Removed Modules"])
        lines.extend(f"- DECISION: {item}" for item in reassessment.get("removed_modules", []) or ["none"])
        lines.extend(["", "## Scope Diff"])
        for item in comparison.get("scope_diff", {}).get("new_direct_files", []) or ["none"]:
            lines.append(f"- FACT: new_direct_file={item}")
        return "\n".join(lines) + "\n"

    def _compare(
        self,
        initial_evidence: dict[str, Any],
        initial_risk: dict[str, Any],
        initial_workflow: dict[str, Any],
        post_evidence: dict[str, Any],
        post_risk: dict[str, Any],
        post_workflow: dict[str, Any],
    ) -> dict[str, Any]:
        previous_level = initial_risk.get("final_level", "L1")
        new_level = post_risk.get("final_level", "L1")
        initial_modules = set(initial_evidence.get("code_findings", {}).get("affected_modules", []))
        post_modules = set(post_evidence.get("code_findings", {}).get("affected_modules", []))
        initial_direct = {item.get("path") for item in initial_evidence.get("code_findings", {}).get("direct_files", [])}
        post_direct = {item.get("path") for item in post_evidence.get("code_findings", {}).get("direct_files", [])}
        initial_required = set(initial_workflow.get("workflow_recommendation", {}).get("required_modules", []))
        post_required = set(post_workflow.get("workflow_recommendation", {}).get("required_modules", []))
        new_domains = sorted(set(post_evidence.get("code_findings", {}).get("affected_domains", [])) - set(initial_evidence.get("code_findings", {}).get("affected_domains", [])))
        new_change_types = sorted(set(post_evidence.get("code_findings", {}).get("change_types", [])) - set(initial_evidence.get("code_findings", {}).get("change_types", [])))
        reasons = []
        if LEVEL_ORDER.get(new_level, 1) > LEVEL_ORDER.get(previous_level, 1):
            reasons.append("risk_level_increased")
        if post_modules - initial_modules:
            reasons.append("actual_change_scope_exceeds_initial_evidence")
        if new_domains:
            reasons.append("discovered_new_affected_domain")
        if any(item in new_change_types for item in ("database_schema", "message_schema", "public_api")):
            reasons.append("discovered_database_message_or_public_interface_change")
        added_modules = sorted(post_required - initial_required) if initial_required else sorted(post_required)
        removed_modules = sorted(initial_required - post_required) if initial_required else []
        requires_reapproval = bool(reasons or added_modules)
        return {
            "version": 1,
            "run_id": "",
            "reassessed_at": datetime.now(timezone.utc).isoformat(),
            "reassessment": {
                "previous_level": previous_level,
                "new_level": new_level,
                "reasons": reasons,
                "added_modules": added_modules,
                "removed_modules": removed_modules,
                "requires_human_reapproval": requires_reapproval,
            },
            "scope_diff": {
                "new_affected_modules": sorted(post_modules - initial_modules),
                "new_direct_files": sorted(path for path in (post_direct - initial_direct) if path),
                "new_affected_domains": new_domains,
                "new_change_types": new_change_types,
            },
        }
