from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml, load_yaml


class AgentTaskError(ValueError):
    pass


@dataclass
class AgentTaskComposer:
    workflow_modules: dict[str, Any]

    def generate(self, run_dir: Path) -> dict[str, Any]:
        if not (run_dir / ".workflow-approved").exists():
            raise AgentTaskError("workflow approval is required before agent task generation")
        approved = load_yaml(run_dir / "approved-workflow.yaml")
        evidence = load_yaml(run_dir / "evidence-pack.yaml")
        investigation = load_yaml(run_dir / "investigation-questions.yaml") if (run_dir / "investigation-questions.yaml").exists() else {}
        rec = approved["workflow_recommendation"]
        final_level = rec.get("final_level", "L1")
        required = rec.get("required_modules", [])
        questions_by_module = self._questions_by_module(investigation)
        tasks = self._tasks_for_level(final_level, required, run_dir.name, questions_by_module)
        artifact = {
            "version": 1,
            "run_id": run_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "final_level": final_level,
            "request": evidence.get("request", {}).get("original", ""),
            "policy": self._policy(final_level),
            "investigation_questions": investigation.get("questions", []),
            "tasks": tasks,
        }
        dump_yaml(run_dir / "agent-tasks.yaml", artifact)
        (run_dir / "agent-tasks.md").write_text(self.render_markdown(artifact), encoding="utf-8")
        return artifact

    def render_markdown(self, artifact: dict[str, Any]) -> str:
        lines = [
            "# Agent Tasks",
            "",
            f"- FACT: run_id={artifact.get('run_id')}",
            f"- FACT: final_level={artifact.get('final_level')}",
            f"- FACT: request={artifact.get('request')}",
            "",
            "## Policy",
        ]
        for item in artifact.get("policy", {}).get("constraints", []):
            lines.append(f"- DECISION: {item}")
        lines.extend(["", "## Tasks"])
        for task in artifact.get("tasks", []):
            lines.extend([
                f"### {task['id']}",
                f"- agent: {task['agent']}",
                f"- mode: {task['mode']}",
                f"- output: {task['output']}",
                f"- completion_command: `{task['completion_command']}`",
                "- constraints:",
            ])
            lines.extend(f"  - {item}" for item in task.get("constraints", []))
            lines.append("")
        return "\n".join(lines)

    def _policy(self, final_level: str) -> dict[str, Any]:
        return {
            "subagents_required": final_level in {"L3", "L4"},
            "constraints": [
                "main agent owns gate state and user-facing decisions",
                "subagents must not edit business code unless explicitly assigned implementation mode after gate check",
                "subagents must cite file paths, line numbers, git diff, or explicit inference labels",
                "implementation mode requires change-assess --check-gate <run_id> --stage implementation",
            ],
        }

    def _tasks_for_level(self, final_level: str, required: list[str], run_id: str, questions_by_module: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
        questions_by_module = questions_by_module or {}
        tasks = []
        if final_level in {"L1", "L2"}:
            tasks.append(self._task(
                "code_fact_scan",
                "code-fact-scanner",
                "read_only",
                "code-fact-report.yaml",
                ["confirm touched files and whether change remains within approved scope"] + questions_by_module.get("code_fact_scan", []),
                run_id,
            ))
            if final_level == "L2":
                tasks.append(self._task("technical_plan_review", "technical-plan-reviewer", "review_only", "technical-plan-review.yaml", ["check required module coverage before approval"], run_id))
            return tasks

        tasks.append(self._task("code_fact_scan", "code-fact-scanner", "read_only", "code-fact-report.yaml", ["identify relevant files, line references, and generated artifacts", "do not propose implementation"] + questions_by_module.get("code_fact_scan", []), run_id))
        if "dependency_analysis" in required or "consumer_analysis" in required:
            tasks.append(self._task("dependency_analysis", "dependency-analyzer", "read_only", "dependency-analysis.yaml", ["identify upstream/downstream callers and consumers", "mark dynamic or implicit dependencies as UNKNOWN"] + questions_by_module.get("dependency_analysis", []) + questions_by_module.get("consumer_analysis", []), run_id))
        if any(module in required for module in ("data_impact_analysis", "dry_run", "affected_row_estimation", "backup_or_restore_plan")):
            tasks.append(self._task("data_impact_analysis", "data-impact-reviewer", "read_only", "data-impact-review.yaml", ["identify data operations, dry-run query, affected row estimate, and rollback evidence", "do not execute production data changes"] + questions_by_module.get("data_impact_analysis", []), run_id))
        if "adversarial_review" in required or final_level == "L4":
            tasks.append(self._task("adversarial_review", "adversarial-reviewer", "review_only", "adversarial-review.yaml", ["look for shared-module deletion, missing consumers, violated prohibited actions, and weak assumptions"], run_id))
        tasks.append(self._task("technical_plan_review", "technical-plan-reviewer", "review_only", "technical-plan-review.yaml", ["verify every required module has evidence or decision", "verify hard guardrails are not downgraded"], run_id))
        tasks.append(self._task("implementation_gate", "implementation-agent", "implementation_after_gate_only", "implementation-report.yaml", ["run change-assess --check-gate <run_id> --stage implementation before edits", "only implement approved technical plan scope"], run_id))
        return tasks

    def _questions_by_module(self, investigation: dict[str, Any]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for item in investigation.get("questions", []):
            module = item.get("module")
            if not module:
                continue
            grouped.setdefault(module, []).append(
                f"answer investigation question {item.get('id')}: {item.get('question')} (expected artifact: {item.get('expected_artifact')})"
            )
        return grouped

    def _task(self, task_id: str, agent: str, mode: str, output: str, constraints: list[str], run_id: str) -> dict[str, Any]:
        return {
            "id": task_id,
            "agent": agent,
            "mode": mode,
            "inputs": [
                "request.md",
                "evidence-pack.yaml",
                "risk-assessment.yaml",
                "approved-workflow.yaml",
                "technical-plan.yaml if present",
            ],
            "output": output,
            "completion_command": f"change-assess --complete-step {run_id} --module {task_id} --artifact {output} --agent {agent}",
            "constraints": constraints,
        }
