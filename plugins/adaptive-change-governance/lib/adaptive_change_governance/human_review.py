from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml, load_yaml
from .workflow_composer import LEVEL_MODULES, WorkflowComposer


LEVEL_ORDER = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}


class ReviewError(ValueError):
    pass


@dataclass
class HumanReviewGate:
    workflow_modules: dict[str, Any]

    def write_review_files(self, run_dir: Path, evidence: dict[str, Any], risk: dict[str, Any], workflow: dict[str, Any]) -> None:
        review = self._default_review(evidence, risk, workflow)
        dump_yaml(run_dir / "human-review.yaml", review)
        (run_dir / "review.md").write_text(self._render_review_markdown(evidence, risk, workflow, review), encoding="utf-8")

    def approve_workflow(self, run_dir: Path, project_risk: dict[str, Any]) -> dict[str, Any]:
        evidence = load_yaml(run_dir / "evidence-pack.yaml")
        risk = load_yaml(run_dir / "risk-assessment.yaml")
        workflow = load_yaml(run_dir / "workflow-recommendation.yaml")
        review = load_yaml(run_dir / "human-review.yaml")
        self._validate_review_shape(review)
        decision = review["decision"]
        if decision != "approve":
            raise ReviewError(f"workflow is not approved; human-review.yaml decision is {decision}")

        rec = workflow["workflow_recommendation"]
        original_required = list(rec.get("required_modules", []))
        required = set(original_required)
        optional = set(rec.get("optional_modules", []))
        hard_required = set(risk.get("required_by_guardrails", []))
        level_required = set(LEVEL_MODULES.get(risk.get("final_level"), []))
        non_removable = hard_required | level_required

        changes = review.get("module_changes", {})
        required.update(changes.get("add_required", []) or [])
        optional.update(changes.get("add_optional", []) or [])
        remove_required = set(changes.get("remove_required", []) or [])
        blocked_removals = sorted(remove_required & non_removable)
        if blocked_removals:
            raise ReviewError("cannot remove required hard-guardrail or level modules: " + ", ".join(blocked_removals))
        required.difference_update(remove_required)
        optional.difference_update(required)

        requested_level = review.get("risk_override", {}).get("final_level")
        final_level = rec["final_level"]
        if requested_level:
            if requested_level not in LEVEL_ORDER:
                raise ReviewError("risk_override.final_level must be L1, L2, L3, or L4")
            if LEVEL_ORDER[requested_level] < LEVEL_ORDER[rec["final_level"]]:
                raise ReviewError("human review cannot lower final_level below AI/hard-guardrail decision")
            final_level = requested_level
            required.update(LEVEL_MODULES[final_level])

        available = self.workflow_modules.get("modules", {})
        unknown_modules = sorted(module for module in required | optional if module not in available)
        if unknown_modules:
            raise ReviewError("unknown workflow module(s): " + ", ".join(unknown_modules))

        ordered_required = self._ordered(required, original_required)
        ordered_optional = self._ordered(optional, rec.get("optional_modules", []))
        approved = {
            "version": 1,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approval": review.get("approval", {}),
            "decision": "approve",
            "risk": {
                "ai_final_level": rec["final_level"],
                "approved_final_level": final_level,
                "triggered_guardrails": rec.get("triggered_guardrails", []),
            },
            "workflow_recommendation": {
                **rec,
                "final_level": final_level,
                "required_modules": ordered_required,
                "optional_modules": ordered_optional,
                "skipped_modules": self._skipped_modules(ordered_required, ordered_optional),
            },
            "human_inputs": {
                "risk_override": review.get("risk_override", {}),
                "module_changes": changes,
                "user_facts": review.get("user_facts", []),
                "user_corrections": review.get("user_corrections", []),
                "comments": review.get("comments", []),
            },
            "audit": [
                "FACT: workflow-plan.md existed before approval.",
                "FACT: human-review.yaml decision was approve.",
                "DECISION: hard-guardrail and level-required modules were enforced during approval.",
                "DECISION: technical-plan generation is now allowed, but business-code changes still require technical-plan approval.",
            ],
        }
        dump_yaml(run_dir / "approved-workflow.yaml", approved)
        composer = WorkflowComposer(project_risk, self.workflow_modules)
        approved_workflow = {
            "version": 1,
            "workflow_recommendation": approved["workflow_recommendation"],
            "judgments": workflow.get("judgments", []) + approved["audit"],
        }
        (run_dir / "approved-workflow-plan.md").write_text(composer.render_markdown(evidence, risk, approved_workflow), encoding="utf-8")
        (run_dir / ".workflow-approved").write_text(approved["approved_at"] + "\n", encoding="utf-8")
        return approved

    def review_summary(self, run_dir: Path) -> str:
        evidence = load_yaml(run_dir / "evidence-pack.yaml")
        risk = load_yaml(run_dir / "risk-assessment.yaml")
        workflow = load_yaml(run_dir / "workflow-recommendation.yaml")
        rec = workflow["workflow_recommendation"]
        lines = [
            "Workflow Review",
            "",
            f"Run: {run_dir.name}",
            f"Request: {evidence['request']['original']}",
            f"Final level: {rec['final_level']}",
            f"Triggered guardrails: {rec.get('triggered_guardrails', [])}",
            f"Prohibited actions: {rec.get('prohibited', [])}",
            "",
            "Guardrail evidence:",
        ]
        lines.extend(self._guardrail_evidence_lines(risk))
        lines.extend([
            "",
            "Recommended execution steps:",
        ])
        lines.extend(self._step_lines(rec.get("required_modules", []), risk))
        lines.extend([
            "",
            "Optional steps:",
        ])
        optional = rec.get("optional_modules", [])
        lines.extend(self._step_lines(optional, risk) if optional else ["  - none"])
        lines.extend([
            "",
            "Unknowns:",
        ])
        lines.extend(f"  - {item.replace('UNKNOWN: ', '', 1)}" for item in evidence.get("unknowns", []))
        lines.extend([
            "",
            "Allowed user changes:",
            "  - approve / reject / request_changes / reassess",
            "  - add required modules",
            "  - add optional modules",
            "  - raise final level",
            "  - add user facts or corrections",
            "",
            "Commands:",
            f"  change-assess --approve-workflow {run_dir.name} --reviewer <name>",
            f"  change-assess --approve-workflow {run_dir.name} --reviewer <name> --add-required threat_analysis --add-required security_regression_test",
            f"  change-assess --approve-workflow {run_dir.name} --reviewer <name> --raise-level L4 --reason \"reason\"",
            f"  change-assess --review-decision {run_dir.name} --decision reassess --comment \"reason\"",
            "",
            "Guardrail constraints:",
            "  - cannot lower the final level",
            "  - cannot remove hard-guardrail or final-level required modules",
            "  - technical plan remains blocked until workflow approval succeeds",
        ])
        return "\n".join(lines) + "\n"

    def approved_summary(self, approved: dict[str, Any]) -> str:
        rec = approved["workflow_recommendation"]
        lines = [
            f"Workflow approved: {approved.get('approval', {}).get('reviewer') or 'unknown reviewer'}",
            f"Approved final level: {approved['risk']['approved_final_level']}",
            "",
            "Approved execution steps:",
        ]
        lines.extend(self._step_lines(rec.get("required_modules", []), {"required_by_guardrails": []}))
        lines.extend([
            "",
            "Next gate: technical_plan_proposal",
        ])
        return "\n".join(lines) + "\n"

    def update_review(
        self,
        run_dir: Path,
        decision: str | None = None,
        reviewer: str | None = None,
        raise_level: str | None = None,
        reason: str | None = None,
        add_required: list[str] | None = None,
        add_optional: list[str] | None = None,
        user_fact: list[str] | None = None,
        correction: list[str] | None = None,
        comment: list[str] | None = None,
    ) -> dict[str, Any]:
        review = load_yaml(run_dir / "human-review.yaml")
        self._validate_review_shape(review)
        if decision:
            review["decision"] = decision
        if reviewer:
            review.setdefault("approval", {})["reviewer"] = reviewer
        if raise_level:
            review.setdefault("risk_override", {})["final_level"] = raise_level
        if reason:
            review.setdefault("risk_override", {})["reason"] = reason
        changes = review.setdefault("module_changes", {})
        self._extend_unique(changes.setdefault("add_required", []), add_required or [])
        self._extend_unique(changes.setdefault("add_optional", []), add_optional or [])
        self._extend_unique(review.setdefault("user_facts", []), user_fact or [])
        self._extend_unique(review.setdefault("user_corrections", []), correction or [])
        self._extend_unique(review.setdefault("comments", []), comment or [])
        if decision == "approve":
            review.setdefault("approval", {})["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        self._validate_review_shape(review)
        dump_yaml(run_dir / "human-review.yaml", review)
        return review

    def _default_review(self, evidence: dict[str, Any], risk: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
        rec = workflow["workflow_recommendation"]
        return {
            "version": 1,
            "decision": "request_changes",
            "risk_override": {
                "final_level": "",
                "reason": "",
            },
            "module_changes": {
                "add_required": [],
                "remove_required": [],
                "add_optional": [],
            },
            "user_facts": [],
            "user_corrections": [],
            "comments": [
                "Edit this file, set decision to approve, reject, request_changes, or reassess, then run approve-workflow.",
            ],
            "approval": {
                "reviewer": "",
                "confirmed_at": "",
            },
            "ai_summary": {
                "request": evidence["request"]["original"],
                "final_level": rec["final_level"],
                "triggered_guardrails": risk.get("triggered_guardrails", []),
                "required_modules": rec.get("required_modules", []),
                "hard_required_modules": risk.get("required_by_guardrails", []),
            },
        }

    def _render_review_markdown(self, evidence: dict[str, Any], risk: dict[str, Any], workflow: dict[str, Any], review: dict[str, Any]) -> str:
        rec = workflow["workflow_recommendation"]
        lines = [
            "# Human Review",
            "",
            "## Summary",
            f"- FACT: request: {evidence['request']['original']}",
            f"- FACT: AI final level: {rec['final_level']}",
            f"- FACT: triggered guardrails: {risk.get('triggered_guardrails', [])}",
            f"- FACT: hard-required modules: {risk.get('required_by_guardrails', [])}",
            "",
            "## Editable File",
            "- DECISION: Edit `human-review.yaml` to approve, reject, request changes, or request reassessment.",
            "- DECISION: User may add required modules, add optional modules, raise final level, and add facts/corrections.",
            "- DECISION: User may not lower final level or remove modules required by hard guardrails or final risk level.",
            "",
            "## Approval Command",
            "```bash",
            "bin/change-assess --approve-workflow <run_id>",
            "```",
            "",
            "## Recommended Execution Steps",
        ]
        lines.extend(f"- DECISION: {line.strip()}" for line in self._step_lines(rec.get("required_modules", []), risk))
        lines.extend([
            "",
            "## Guardrail Evidence",
        ])
        lines.extend(f"- {line.strip()}" for line in self._guardrail_evidence_lines(risk))
        lines.extend([
            "",
            "## Current Unknowns",
        ])
        lines.extend(f"- UNKNOWN: {item.replace('UNKNOWN: ', '', 1)}" for item in evidence.get("unknowns", []))
        return "\n".join(lines) + "\n"

    def _validate_review_shape(self, review: dict[str, Any]) -> None:
        if review.get("version") != 1:
            raise ReviewError("human-review.yaml version must be 1")
        if review.get("decision") not in {"approve", "reject", "request_changes", "reassess"}:
            raise ReviewError("human-review.yaml decision must be approve, reject, request_changes, or reassess")
        changes = review.get("module_changes")
        if not isinstance(changes, dict):
            raise ReviewError("human-review.yaml module_changes must be a mapping")
        for key in ("add_required", "remove_required", "add_optional"):
            if not isinstance(changes.get(key, []), list):
                raise ReviewError(f"module_changes.{key} must be a list")

    def _extend_unique(self, target: list[str], values: list[str]) -> None:
        for value in values:
            if value and value not in target:
                target.append(value)

    def _step_lines(self, modules: list[str], risk: dict[str, Any]) -> list[str]:
        hard_required = set(risk.get("required_by_guardrails", []))
        lines = []
        for index, module in enumerate(modules, start=1):
            meta = self.workflow_modules.get("modules", {}).get(module, {})
            reason = "hard guardrail" if module in hard_required else "risk workflow"
            lines.append(
                f"  {index}. {meta.get('description', module)} "
                f"({module}) -> output: {meta.get('output', 'unknown')}; required by: {reason}"
            )
        return lines

    def _guardrail_evidence_lines(self, risk: dict[str, Any]) -> list[str]:
        details = risk.get("triggered_guardrail_details", [])
        if not details:
            return ["  - none"]
        lines = []
        for detail in details:
            lines.append(f"  - {detail['decision']}")
            for match in detail.get("matches", []):
                lines.append(f"    condition: {match.get('condition')}")
                for fact in match.get("evidence", []):
                    lines.append(f"    {fact}")
        return lines

    def _ordered(self, modules: set[str], preferred: list[str]) -> list[str]:
        result = [module for module in preferred if module in modules]
        result.extend(sorted(module for module in modules if module not in result))
        return result

    def _skipped_modules(self, required: list[str], optional: list[str]) -> list[dict[str, str]]:
        chosen = set(required) | set(optional)
        skipped = []
        for module in sorted(self.workflow_modules.get("modules", {}).keys()):
            if module not in chosen:
                skipped.append({"module": module, "reason": "DECISION: not required after human workflow review."})
        return skipped
