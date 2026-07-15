from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml


SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".ai-governance/runs"}


@dataclass(frozen=True)
class PatternRule:
    pattern: str
    level: str
    reason: str
    source: str


RULE_CATALOG = [
    PatternRule("**/migrations/**", "critical", "database migration or schema/data operation", "migration_dir"),
    PatternRule("migrations/**", "critical", "database migration or schema/data operation", "migration_dir"),
    PatternRule("**/*.sql", "critical", "raw SQL can affect persisted data", "sql_file"),
    PatternRule("**/models.py", "high", "ORM model or persisted data mapping", "data_model"),
    PatternRule("**/database*.py", "high", "database connection or persistence infrastructure", "database_code"),
    PatternRule("**/*db*.py", "high", "database connection or persistence infrastructure", "database_code"),
    PatternRule("**/auth*.py", "high", "authentication or authorization code", "auth_code"),
    PatternRule("**/permission*.py", "high", "permission or role registry", "auth_code"),
    PatternRule("**/permissions*.py", "high", "permission or role registry", "auth_code"),
    PatternRule("**/api/**", "medium", "API surface", "api_surface"),
    PatternRule("**/routes/**", "medium", "HTTP route surface", "api_surface"),
    PatternRule("**/controllers/**", "medium", "controller surface", "api_surface"),
    PatternRule("**/jobs/**", "high", "scheduler, worker, or background job", "background_job"),
    PatternRule("**/tasks/**", "high", "scheduler, worker, or background job", "background_job"),
    PatternRule("**/workers/**", "high", "scheduler, worker, or background job", "background_job"),
    PatternRule("**/scheduler/**", "high", "scheduler, worker, or background job", "background_job"),
    PatternRule("**/.env*", "high", "environment or credential-bearing configuration", "runtime_config"),
    PatternRule("**/config/**", "medium", "runtime configuration", "runtime_config"),
    PatternRule("**/settings*.py", "medium", "runtime configuration", "runtime_config"),
    PatternRule("frontend/src/views/**", "low", "UI view layer", "ui_layer"),
    PatternRule("frontend/src/layouts/**", "low", "navigation and display shell", "ui_layer"),
    PatternRule("static/assets/**", "low", "generated or bundled frontend asset", "generated_asset"),
    PatternRule("docs/**", "low", "documentation", "documentation"),
    PatternRule("README.md", "low", "documentation", "documentation"),
    PatternRule("tests/**", "low", "automated test", "test_code"),
    PatternRule("**/tests/**", "low", "automated test", "test_code"),
]


@dataclass
class RiskConfigSuggester:
    root: Path
    project_risk: dict[str, Any]

    def suggest(self) -> dict[str, Any]:
        files = self._repository_files()
        existing = self.project_risk.get("file_risk", [])
        candidates = []
        for rule in RULE_CATALOG:
            examples = self._matching_examples(files, rule.pattern)
            if not examples:
                continue
            already_configured = self._covered_by_existing(rule.pattern, existing)
            candidates.append({
                "pattern": rule.pattern,
                "level": rule.level,
                "reason": rule.reason,
                "source": rule.source,
                "matched_files": len(examples),
                "examples": examples[:5],
                "already_configured": already_configured,
            })
        recommended = [item for item in candidates if not item["already_configured"]]
        return {
            "version": 1,
            "status": "suggestions_only",
            "project_root": str(self.root),
            "draft_config": ".ai-governance/project-risk.suggested.yaml",
            "summary": {
                "scanned_files": len(files),
                "candidate_rules": len(candidates),
                "new_candidate_rules": len(recommended),
                "already_configured_rules": len(candidates) - len(recommended),
            },
            "recommended_file_risk": recommended,
            "all_candidates": candidates,
            "notes": [
                "DECISION: this command only writes suggestions; it does not modify project-risk.yaml or guardrails.yaml.",
                "INFERENCE: suggested levels come from repository path conventions and must be reviewed by a human.",
                "UNKNOWN: path-based scanning cannot prove business criticality, runtime traffic, data sensitivity, or external consumers.",
            ],
        }

    def write(self, output_dir: Path) -> dict[str, Any]:
        report = self.suggest()
        output_dir.mkdir(parents=True, exist_ok=True)
        dump_yaml(output_dir / "risk-config-suggestions.yaml", report)
        dump_yaml(output_dir / "project-risk.suggested.yaml", self._suggested_project_risk(report))
        (output_dir / "risk-config-suggestions.md").write_text(self.render_markdown(report), encoding="utf-8")
        return report

    def render_markdown(self, report: dict[str, Any]) -> str:
        summary = report.get("summary", {})
        lines = [
            "# Risk Config Suggestions",
            "",
            "- DECISION: suggestions only; no configuration was modified.",
            f"- FACT: draft_config={report.get('draft_config')}",
            f"- FACT: scanned_files={summary.get('scanned_files')}",
            f"- FACT: candidate_rules={summary.get('candidate_rules')}",
            f"- FACT: new_candidate_rules={summary.get('new_candidate_rules')}",
            "",
            "## Recommended file_risk entries",
            "",
        ]
        recommended = report.get("recommended_file_risk", [])
        if not recommended:
            lines.append("- DECISION: no new file_risk entries suggested.")
        for item in recommended:
            lines.extend([
                f"- pattern: `{item.get('pattern')}`",
                f"  - level: {item.get('level')}",
                f"  - reason: {item.get('reason')}",
                f"  - matched_files: {item.get('matched_files')}",
                f"  - examples: {', '.join(item.get('examples', [])) or 'none'}",
            ])
        lines.extend([
            "",
            "## Already configured candidates",
            "",
        ])
        configured = [item for item in report.get("all_candidates", []) if item.get("already_configured")]
        if not configured:
            lines.append("- FACT: none")
        for item in configured:
            lines.append(f"- FACT: `{item.get('pattern')}` is already covered; examples={', '.join(item.get('examples', [])[:3])}")
        lines.extend([
            "",
            "## Draft config",
            "",
            f"- FACT: review `{report.get('draft_config')}` to see the full project-risk.yaml with new suggestions merged.",
            "- DECISION: replace the active config only after human review.",
            "",
            "## Notes",
        ])
        lines.extend(f"- {item}" for item in report.get("notes", []))
        return "\n".join(lines) + "\n"

    def _suggested_project_risk(self, report: dict[str, Any]) -> dict[str, Any]:
        draft = deepcopy(self.project_risk)
        file_risk = list(draft.get("file_risk", []) or [])
        for item in report.get("recommended_file_risk", []):
            file_risk.append({
                "pattern": item.get("pattern"),
                "level": item.get("level"),
                "reason": item.get("reason"),
            })
        draft["file_risk"] = file_risk
        return draft

    def _repository_files(self) -> list[str]:
        result = []
        for current_root, dirs, files in os.walk(self.root):
            rel_root = Path(current_root).relative_to(self.root)
            dirs[:] = [d for d in dirs if not self._skip_dir(rel_root / d)]
            for filename in files:
                rel = (rel_root / filename).as_posix()
                if rel == ".":
                    rel = filename
                if ".ai-governance/runs/" in rel:
                    continue
                result.append(rel)
        return sorted(result)

    def _skip_dir(self, rel: Path) -> bool:
        rel_posix = rel.as_posix()
        return rel.name in SKIP_DIRS or rel_posix in SKIP_DIRS or rel_posix.startswith(".ai-governance/runs")

    def _matching_examples(self, files: list[str], pattern: str) -> list[str]:
        return [path for path in files if self._matches(path, pattern)]

    def _covered_by_existing(self, pattern: str, existing: list[Any]) -> bool:
        for item in existing:
            if not isinstance(item, dict):
                continue
            existing_pattern = str(item.get("pattern", ""))
            if existing_pattern == pattern:
                return True
        return False

    def _matches(self, path: str, pattern: str) -> bool:
        return fnmatch(path, pattern) or fnmatch("/" + path, pattern)
