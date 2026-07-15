"""Server-side CI gate: score a pull request from its diff alone.

Local hooks are a speed bump, not a security boundary — an agent with shell access
can write around a PreToolUse hook, forge gate-state files, or set ACG_HOOK_MODE=off.
Real enforcement has to run where the agent cannot write: CI. This module scores the
changed files server-side, from code facts only.

It deliberately takes no user request and no model intent:

- The diff *is* the code fact. Domains come from `code_signals` on the changed files
  and from destructive statements in the added lines; blast radius comes from
  `reference_scanner`; inherent risk comes from `file_risk`.
- Nothing here can be talked down by wording or by an intent file, so the verdict is
  reproducible and cannot be forged from inside the working tree.

The gate reports a level and fails the check above a threshold. Blocking merges is
then branch protection's job (required status check + required human review), which
is the part an agent genuinely cannot bypass.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .code_signals import scan_code_signals
from .file_risk import evaluate_file_risk
from .reference_scanner import scan_references
from .risk_evaluator import LEVEL_ORDER, RiskEvaluator

RUNS_PREFIX = ".ai-governance/runs/"

# Destructive statements detected in *added* diff lines. At CI time the real diff is
# available, so this is precise: it fires on what the change introduces, not on a
# string that merely exists somewhere in a touched file.
_ADDED_OPERATIONS: list[tuple[str, re.Pattern[str]]] = [
    ("delete", re.compile(r"\bdelete\s+from\b", re.IGNORECASE)),
    ("truncate", re.compile(r"\btruncate\b", re.IGNORECASE)),
    ("irreversible_migration", re.compile(r"\bdrop\s+(table|column)\b", re.IGNORECASE)),
    ("bulk_update", re.compile(r"\bupdate\b[^;]*\bset\b(?![^;]*\bwhere\b)", re.IGNORECASE)),
]

_SCHEMA_PATTERNS = re.compile(r"\b(alter\s+table|create\s+table)\b", re.IGNORECASE)


class CiGateError(RuntimeError):
    pass


@dataclass
class CiGate:
    root: Path
    project_risk: dict[str, Any]
    guardrails: dict[str, Any]
    calibration: dict[str, Any] | None = None
    fail_level: str = "L3"
    _repo_files: list[str] = field(default_factory=list)

    def run(self, base_ref: str) -> dict[str, Any]:
        changed = self._changed_files(base_ref)
        if not changed:
            return self._empty_report(base_ref)
        added = self._added_lines(base_ref)
        evidence = self._build_evidence(changed, added)
        risk = RiskEvaluator(self.project_risk, self.guardrails, self.calibration).evaluate(evidence)
        level = str(risk.get("final_level", "L1"))
        blocking = LEVEL_ORDER.get(level, 1) >= LEVEL_ORDER.get(self.fail_level, 3)
        return {
            "version": 1,
            "base_ref": base_ref,
            "changed_files": changed,
            "final_level": level,
            "fail_level": self.fail_level,
            "status": "review_required" if blocking else "pass",
            "triggered_guardrails": risk.get("triggered_guardrails", []),
            "affected_domains": evidence["code_findings"]["affected_domains"],
            "reference_findings": evidence["code_findings"]["reference_findings"],
            "code_signals": evidence["code_findings"]["code_signals"],
            "operations": evidence["code_findings"]["operations"],
            "weighted_score": risk.get("weighted_score"),
            "notes": [
                "FACT: this verdict is computed server-side from the diff only; no request text or model intent is used.",
                "DECISION: a blocking verdict means a human must review and approve this pull request, not that the change is wrong.",
            ],
        }

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# Change Governance — CI Gate",
            "",
            f"- DECISION: status={report.get('status')}",
            f"- DECISION: final_level={report.get('final_level')} (fails at {report.get('fail_level')})",
            f"- FACT: base_ref={report.get('base_ref')}",
            f"- FACT: weighted_score={report.get('weighted_score')}",
            "",
            "## Triggered hard guardrails",
            "",
        ]
        lines.extend(f"- DECISION: {item}" for item in report.get("triggered_guardrails", []) or ["none"])
        lines.extend(["", "## Affected domains", ""])
        lines.extend(f"- FACT: {item}" for item in report.get("affected_domains", []) or ["none"])
        lines.extend(["", "## Blast radius", ""])
        reference = report.get("reference_findings", {})
        lines.append(f"- FACT: inbound_reference_count={reference.get('inbound_reference_count', 0)}")
        lines.append(f"- FACT: referencing_modules={', '.join(reference.get('referencing_modules', [])) or 'none'}")
        lines.append(f"- FACT: is_shared_contract={reference.get('is_shared_contract', False)}")
        lines.extend(["", "## Code signals", ""])
        signals = report.get("code_signals", []) or []
        if not signals:
            lines.append("- FACT: none")
        for signal in signals[:20]:
            lines.append(f"- FACT: {signal.get('path')}:{signal.get('line')} {signal.get('kind')}")
        lines.extend(["", "## Changed files", ""])
        lines.extend(f"- FACT: {item}" for item in report.get("changed_files", [])[:50] or ["none"])
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {item}" for item in report.get("notes", []))
        return "\n".join(lines) + "\n"

    def _empty_report(self, base_ref: str) -> dict[str, Any]:
        return {
            "version": 1,
            "base_ref": base_ref,
            "changed_files": [],
            "final_level": "L1",
            "fail_level": self.fail_level,
            "status": "pass",
            "triggered_guardrails": [],
            "affected_domains": [],
            "reference_findings": {},
            "code_signals": [],
            "operations": [],
            "weighted_score": 0,
            "notes": ["FACT: no reviewable files changed against the base ref."],
        }

    def _build_evidence(self, changed: list[str], added: list[str]) -> dict[str, Any]:
        direct_files = [{"path": path, "reason": "FACT: changed in this pull request diff"} for path in changed]
        signals = scan_code_signals(self.root, changed)
        operations, operation_evidence = self._added_operations(added)
        change_types = self._change_types(changed, added)
        reference = scan_references(self.root, changed, self._repository_files())
        file_risk = evaluate_file_risk(changed, self.project_risk, intent={}, file_facts=[])
        domains = sorted(set(signals["domains"]) | self._operation_domains(operations, change_types))
        return {
            "version": 1,
            "request": {
                "original": "",
                "normalized_intent": "CI gate: risk computed from the diff only.",
                "model_intent": {},
                "request_goal": {
                    "type": "implementation",
                    "requires_code_change": True,
                    "default_stop_gate": "workflow_plan_approval",
                    "rationale": "FACT: a pull request diff changes repository behavior.",
                },
            },
            "repository": {"branch": "unknown", "commit": "unknown", "dirty": False},
            "code_findings": {
                "direct_files": direct_files,
                "related_files": [],
                "affected_modules": sorted({Path(p).parts[0] for p in changed if Path(p).parts}),
                "affected_domains": domains,
                "reference_findings": reference,
                "code_signals": signals["signals"],
                "change_types": change_types,
                "operations": operations,
                "domain_evidence": signals["domain_evidence"],
                "change_type_evidence": [],
                "operation_evidence": operation_evidence,
                "database_changes": "database_schema" in change_types or bool(set(operations)),
                "message_schema_changes": "message_schema" in change_types,
                "public_api_changes": "public_api" in change_types,
                "text_only_change": False,
                "feature_boundary": {"summary": {"confidence": "high", "ambiguous_important_files": 0}},
                "file_risk": file_risk,
                "scheduled_jobs_affected": False,
                "configuration_changes": "configuration" in change_types,
            },
            "dependency_findings": {"upstream": [], "downstream": [], "external_dependencies": []},
            "test_findings": {
                "existing_tests": [p for p in changed if "test" in p.lower()],
                "coverage_confidence": "medium" if any("test" in p.lower() for p in changed) else "low",
                "missing_test_areas": [],
            },
            "runtime_findings": {
                "production_usage": "unknown",
                "traffic_level": "unknown",
                "observability": "unknown",
                "rollback_capability": "unknown",
            },
            "unknowns": [],
            "evidence_sources": ["git_diff", "code_signals", "reference_scan", "project_risk_profile", "guardrails"],
        }

    def _added_operations(self, added: list[str]) -> tuple[list[str], list[dict[str, str]]]:
        operations: set[str] = set()
        evidence: list[dict[str, str]] = []
        for line in added:
            for name, pattern in _ADDED_OPERATIONS:
                if pattern.search(line):
                    operations.add(name)
                    evidence.append({
                        "value": name,
                        "source": "git_diff",
                        "keyword": name,
                        "strength": "strong",
                        "fact": f"FACT: this diff adds a line matching {name}: {line.strip()[:120]}",
                    })
        return sorted(operations), evidence[:20]

    def _operation_domains(self, operations: list[str], change_types: list[str]) -> set[str]:
        domains: set[str] = set()
        if operations:
            domains.add("data-integrity")
        if "database_schema" in change_types:
            domains.add("database-schema")
        return domains

    def _change_types(self, changed: list[str], added: list[str]) -> list[str]:
        types: set[str] = set()
        for path in changed:
            lowered = path.lower()
            if lowered.endswith(".sql") or "/migrations/" in lowered or lowered.startswith("migrations/"):
                types.add("database_schema")
            if lowered.endswith((".md", ".txt", ".rst")):
                types.add("documentation")
            if lowered.endswith((".yaml", ".yml", ".json", ".toml", ".ini")):
                types.add("configuration")
        if any(_SCHEMA_PATTERNS.search(line) for line in added):
            types.add("database_schema")
        return sorted(types)

    def _repository_files(self) -> list[str]:
        if self._repo_files:
            return self._repo_files
        result = self._git(["ls-files", "-z"])
        self._repo_files = [p for p in result.stdout.split("\0") if p and not p.startswith(RUNS_PREFIX)]
        return self._repo_files

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _diff_range(self, base_ref: str) -> str:
        # Three-dot: compare against the merge base, which is what a PR actually adds.
        probe = self._git(["merge-base", base_ref, "HEAD"])
        if probe.returncode != 0:
            raise CiGateError(f"cannot find merge base for '{base_ref}': {probe.stderr.strip()}")
        return f"{base_ref}...HEAD"

    def _changed_files(self, base_ref: str) -> list[str]:
        result = self._git([
            "diff", "--name-only", "--no-renames", "-z", self._diff_range(base_ref),
            "--", ".", f":(exclude){RUNS_PREFIX.rstrip('/')}",
        ])
        if result.returncode != 0:
            raise CiGateError(result.stderr.strip() or "git diff failed")
        return sorted(
            path for path in result.stdout.split("\0")
            if path and not path.startswith(RUNS_PREFIX)
        )

    def _added_lines(self, base_ref: str) -> list[str]:
        result = self._git([
            "diff", "--no-renames", self._diff_range(base_ref),
            "--", ".", f":(exclude){RUNS_PREFIX.rstrip('/')}",
        ])
        if result.returncode != 0:
            raise CiGateError(result.stderr.strip() or "git diff failed")
        return [
            line[1:].strip()
            for line in result.stdout.splitlines()
            if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
        ]
