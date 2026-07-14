from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml, load_yaml


class TechnicalPlanError(ValueError):
    pass


# Guardrail-required modules that produce analysis evidence and must be
# completed (via --complete-step with an artifact) before the technical plan
# can be approved. Execution-stage modules (dry_run, manual_approval, tests,
# release steps) are gated later and stay "planned" here.
PRE_PLAN_ANALYSIS_MODULES = {
    "requirement_confirmation",
    "business_rule_confirmation",
    "dependency_analysis",
    "data_impact_analysis",
    "threat_analysis",
    "compatibility_analysis",
    "consumer_analysis",
    "protocol_verification",
    "failure_mode_analysis",
}


@dataclass
class TechnicalPlanGate:
    workflow_modules: dict[str, Any]

    def add_run_context(
        self,
        run_dir: Path,
        facts: list[str] | None = None,
        corrections: list[str] | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        prohibit: list[str] | None = None,
        unknown: list[str] | None = None,
    ) -> dict[str, Any]:
        context = self._load_context(run_dir)
        self._extend_unique(context["facts"], facts or [])
        self._extend_unique(context["corrections"], corrections or [])
        self._extend_unique(context["scope"]["included"], include or [])
        self._extend_unique(context["scope"]["excluded"], exclude or [])
        self._extend_unique(context["scope"]["prohibited"], prohibit or [])
        self._extend_unique(context["scope"]["unknowns"], unknown or [])
        context["updated_at"] = datetime.now(timezone.utc).isoformat()
        dump_yaml(run_dir / "run-context.yaml", context)
        return context

    def propose(self, run_dir: Path) -> dict[str, Any]:
        self._require_workflow_approved(run_dir)
        evidence = load_yaml(run_dir / "evidence-pack.yaml")
        approved_workflow = load_yaml(run_dir / "approved-workflow.yaml")
        context = self._load_context(run_dir)
        rec = approved_workflow["workflow_recommendation"]
        required = rec.get("required_modules", [])
        plan = {
            "version": 1,
            "run_id": run_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_workflow": "approved-workflow.yaml",
            "request": evidence["request"]["original"],
            "scope": {
                "included": context["scope"]["included"],
                "excluded": context["scope"]["excluded"],
                "prohibited": sorted(set(context["scope"]["prohibited"] + rec.get("prohibited", []))),
                "unknowns": context["scope"]["unknowns"] + evidence.get("unknowns", []),
            },
            "fact_corrections": {
                "facts": context["facts"],
                "corrections": context["corrections"],
            },
            "module_coverage": {
                module: self._module_coverage(module, self._completed_steps(run_dir))
                for module in required
            },
            "implementation_plan": {
                "files_to_modify": self._candidate_file_changes(evidence),
                "data_operations": [],
                "prohibited_actions": rec.get("prohibited", []),
            },
            "validation_plan": {
                "commands": [],
                "required_reports": self._required_outputs(required),
            },
            "risk_controls": {
                "rollback_plan": [],
                "dry_run_required": "dry_run" in required,
                "manual_approval_required": "manual_approval" in required,
            },
            "approval": {
                "decision": "request_changes",
                "reviewer": "",
                "confirmed_at": "",
            },
        }
        errors = self.validate(plan, approved_workflow)
        plan["validation"] = {
            "status": "pass" if not errors else "blocked",
            "errors": errors,
        }
        dump_yaml(run_dir / "technical-plan.yaml", plan)
        (run_dir / "technical-plan.md").write_text(self.render_markdown(plan), encoding="utf-8")
        return plan

    def review_summary(self, run_dir: Path) -> str:
        plan = load_yaml(run_dir / "technical-plan.yaml")
        approved_workflow = load_yaml(run_dir / "approved-workflow.yaml")
        errors = self.validate(plan, approved_workflow)
        lines = [
            "Technical Plan Review",
            "",
            f"Run: {run_dir.name}",
            f"Request: {plan.get('request', '')}",
            f"Validation: {'pass' if not errors else 'blocked'}",
            "",
            "Scope included:",
        ]
        lines.extend(f"  - {item}" for item in plan.get("scope", {}).get("included", []) or ["none"])
        lines.extend(["", "Scope excluded:"])
        lines.extend(f"  - {item}" for item in plan.get("scope", {}).get("excluded", []) or ["none"])
        lines.extend(["", "Module coverage:"])
        for module, coverage in plan.get("module_coverage", {}).items():
            lines.append(f"  - {module}: {coverage.get('status', 'unknown')}")
        if errors:
            lines.extend(["", "Blocking errors:"])
            lines.extend(f"  - {error}" for error in errors)
        lines.extend([
            "",
            "Commands:",
            f"  change-assess --approve-technical-plan {run_dir.name}",
            f"  change-assess --check-gate {run_dir.name} --stage implementation",
        ])
        return "\n".join(lines) + "\n"

    def approve(self, run_dir: Path, reviewer: str | None = None) -> dict[str, Any]:
        self._require_workflow_approved(run_dir)
        plan = load_yaml(run_dir / "technical-plan.yaml")
        approved_workflow = load_yaml(run_dir / "approved-workflow.yaml")
        errors = self.validate(plan, approved_workflow)
        errors.extend(self._unfinished_guardrail_analysis(run_dir))
        if errors:
            raise TechnicalPlanError("technical plan validation failed: " + "; ".join(errors))
        approval = plan.setdefault("approval", {})
        approval["decision"] = "approve"
        approval["reviewer"] = reviewer or approval.get("reviewer") or "human_cli_approval"
        approval["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        plan["validation"] = {"status": "pass", "errors": []}
        dump_yaml(run_dir / "approved-technical-plan.yaml", plan)
        (run_dir / "approved-technical-plan.md").write_text(self.render_markdown(plan), encoding="utf-8")
        (run_dir / ".technical-plan-approved").write_text(approval["confirmed_at"] + "\n", encoding="utf-8")
        return plan

    def check_gate(self, run_dir: Path, stage: str) -> list[str]:
        errors = []
        if stage == "technical_plan":
            if not (run_dir / ".workflow-approved").exists():
                errors.append("workflow has not been approved")
        elif stage == "implementation":
            if not (run_dir / ".workflow-approved").exists():
                errors.append("workflow has not been approved")
            if not (run_dir / ".technical-plan-approved").exists():
                errors.append("technical plan has not been approved")
            if not (run_dir / "approved-technical-plan.yaml").exists():
                errors.append("approved-technical-plan.yaml is missing")
            diff_path = run_dir / "diff-verification.yaml"
            if diff_path.exists():
                diff = load_yaml(diff_path)
                if diff.get("status") != "pass":
                    errors.append("diff verification is blocked")
        else:
            errors.append("stage must be technical_plan or implementation")
        return errors

    def validate(self, plan: dict[str, Any], approved_workflow: dict[str, Any]) -> list[str]:
        errors = []
        required = approved_workflow.get("workflow_recommendation", {}).get("required_modules", [])
        coverage = plan.get("module_coverage")
        if not isinstance(coverage, dict):
            return ["technical-plan.yaml module_coverage must be a mapping"]
        for module in required:
            item = coverage.get(module)
            if not isinstance(item, dict):
                errors.append(f"required module {module} is missing from module_coverage")
                continue
            if item.get("status") not in {"covered", "planned", "not_applicable_with_evidence"}:
                errors.append(f"required module {module} must be covered, planned, or not_applicable_with_evidence")
            if item.get("status") == "covered" and not item.get("evidence"):
                errors.append(f"required module {module} claims covered without evidence")
            if not item.get("evidence") and not item.get("decision"):
                errors.append(f"required module {module} needs evidence or decision")
        prohibited = set(approved_workflow.get("workflow_recommendation", {}).get("prohibited", []))
        plan_prohibited = set(plan.get("scope", {}).get("prohibited", [])) | set(plan.get("implementation_plan", {}).get("prohibited_actions", []))
        missing_prohibited = sorted(prohibited - plan_prohibited)
        if missing_prohibited:
            errors.append("technical plan must inherit prohibited actions: " + ", ".join(missing_prohibited))
        return errors

    def render_markdown(self, plan: dict[str, Any]) -> str:
        lines = [
            "# Technical Plan",
            "",
            f"- FACT: run_id={plan.get('run_id')}",
            f"- FACT: request={plan.get('request')}",
            f"- DECISION: validation={plan.get('validation', {}).get('status', 'unknown')}",
            "",
            "## Scope",
            "### Included",
        ]
        lines.extend(f"- FACT: {item}" for item in plan.get("scope", {}).get("included", []) or ["none"])
        lines.extend(["", "### Excluded"])
        lines.extend(f"- CONSTRAINT: {item}" for item in plan.get("scope", {}).get("excluded", []) or ["none"])
        lines.extend(["", "### Prohibited"])
        lines.extend(f"- DECISION: {item}" for item in plan.get("scope", {}).get("prohibited", []) or ["none"])
        lines.extend(["", "## Fact Corrections"])
        lines.extend(f"- FACT: {item}" for item in plan.get("fact_corrections", {}).get("facts", []) or ["none"])
        lines.extend(f"- CORRECTION: {item}" for item in plan.get("fact_corrections", {}).get("corrections", []))
        lines.extend(["", "## Module Coverage"])
        for module, coverage in plan.get("module_coverage", {}).items():
            lines.append(f"- DECISION: {module} -> {coverage.get('status')}; output={coverage.get('output')}")
        lines.extend(["", "## Implementation Plan"])
        for item in plan.get("implementation_plan", {}).get("files_to_modify", []) or []:
            lines.append(f"- {item.get('action')}: {item.get('path')} ({item.get('reason')})")
        lines.extend(["", "## Validation Plan"])
        for command in plan.get("validation_plan", {}).get("commands", []) or ["TBD: add project-specific verification command"]:
            lines.append(f"- `{command}`")
        return "\n".join(lines) + "\n"

    def _unfinished_guardrail_analysis(self, run_dir: Path) -> list[str]:
        risk_path = run_dir / "risk-assessment.yaml"
        if not risk_path.exists():
            return []
        risk = load_yaml(risk_path)
        completed = self._completed_steps(run_dir)
        errors = []
        for module in risk.get("required_by_guardrails", []):
            if module in PRE_PLAN_ANALYSIS_MODULES and module not in completed:
                errors.append(
                    f"hard-guardrail analysis module {module} is not completed; "
                    f"run change-assess --complete-step {run_dir.name} --module {module} --artifact <artifact> first"
                )
        return errors

    def _require_workflow_approved(self, run_dir: Path) -> None:
        if not (run_dir / ".workflow-approved").exists() or not (run_dir / "approved-workflow.yaml").exists():
            raise TechnicalPlanError("workflow approval is required before technical plan")

    def _load_context(self, run_dir: Path) -> dict[str, Any]:
        path = run_dir / "run-context.yaml"
        if path.exists():
            return load_yaml(path)
        return {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "facts": [],
            "corrections": [],
            "scope": {
                "included": [],
                "excluded": [],
                "prohibited": [],
                "unknowns": [],
            },
        }

    def _completed_steps(self, run_dir: Path) -> dict[str, dict[str, Any]]:
        path = run_dir / "progress.yaml"
        if not path.exists():
            return {}
        progress = load_yaml(path)
        return {
            step.get("id"): step
            for step in progress.get("steps", [])
            if step.get("status") == "done"
        }

    def _module_coverage(self, module: str, completed: dict[str, dict[str, Any]]) -> dict[str, Any]:
        meta = self.workflow_modules.get("modules", {}).get(module, {})
        step = completed.get(module)
        if step:
            artifacts = [str(item) for item in step.get("artifacts") or []]
            evidence = [f"FACT: {module} completed" + (f" with artifact(s): {', '.join(artifacts)}" if artifacts else " via --complete-step") + "."]
            return {
                "status": "covered",
                "output": meta.get("output", "unknown"),
                "evidence": evidence,
                "artifacts": artifacts,
                "decision": meta.get("description", module),
            }
        return {
            "status": "planned",
            "output": meta.get("output", "unknown"),
            "evidence": [],
            "artifacts": [],
            "decision": f"PLAN: {meta.get('description', module)}; complete with change-assess --complete-step <run_id> --module {module}.",
        }

    def _candidate_file_changes(self, evidence: dict[str, Any]) -> list[dict[str, str]]:
        changes = []
        for item in evidence.get("code_findings", {}).get("direct_files", [])[:20]:
            changes.append({
                "path": item["path"],
                "action": "review",
                "reason": item.get("reason", "FACT: direct file finding"),
            })
        return changes

    def _required_outputs(self, required: list[str]) -> list[str]:
        outputs = []
        for module in required:
            output = self.workflow_modules.get("modules", {}).get(module, {}).get("output")
            if output:
                outputs.append(output)
        return outputs

    def _extend_unique(self, target: list[str], values: list[str]) -> None:
        for value in values:
            if value and value not in target:
                target.append(value)
