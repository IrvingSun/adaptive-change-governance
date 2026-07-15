from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml, load_yaml


LOW_RISK_NATURES = {"comment_only", "documentation_only", "display_text_only", "metadata_only"}

EXECUTABLE_SIGNALS = (
    "def ",
    "class ",
    "return ",
    "if ",
    "elif ",
    "else:",
    "for ",
    "while ",
    "try:",
    "except ",
    "import ",
    "from ",
    "SELECT ",
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "DROP ",
    "ALTER ",
    "CREATE ",
    "include_router",
    "@router",
    "router.",
    "app.",
    "DATABASE",
    "TOKEN",
    "SECRET",
)


RUNS_PREFIX = ".ai-governance/runs/"


class DiffVerificationError(ValueError):
    pass


@dataclass
class DiffVerifier:
    root: Path

    def verify(self, run_dir: Path) -> dict[str, Any]:
        if not (run_dir / "approved-technical-plan.yaml").exists():
            raise DiffVerificationError("approved technical plan is required before diff verification")
        evidence = load_yaml(run_dir / "evidence-pack.yaml")
        approved_plan = load_yaml(run_dir / "approved-technical-plan.yaml")
        diff_text = self._git_diff()
        untracked_files = self._untracked_files()
        tracked_changed = self._changed_files()
        changed_files = tracked_changed + untracked_files
        allowed_files = self._allowed_files(approved_plan)
        # Fail closed: an empty approved list means *no* file is approved, not
        # "every file is fine". Localization can legitimately find nothing (a
        # cross-language request), which produced an empty files_to_modify, an
        # approvable plan, and a scope check that silently passed any diff.
        unexpected_files = sorted(path for path in changed_files if path not in allowed_files)
        added_lines = self._added_lines(diff_text) + self._untracked_lines(untracked_files)
        low_risk_check = self._low_risk_check(evidence, added_lines)
        errors = []
        if unexpected_files and not allowed_files:
            errors.append(
                "approved technical plan lists no files_to_modify, so no file change is in scope; "
                "add the intended paths and re-approve the plan"
            )
        elif unexpected_files:
            errors.append("diff touches files outside approved technical plan: " + ", ".join(unexpected_files))
        errors.extend(low_risk_check["errors"])
        report = {
            "version": 1,
            "run_id": run_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_artifacts": [
                "evidence-pack.yaml",
                "approved-workflow.yaml",
                "approved-technical-plan.yaml",
            ],
            "status": "pass" if not errors else "blocked",
            "changed_files": changed_files,
            "untracked_files": untracked_files,
            "approved_files": sorted(allowed_files),
            "unexpected_files": unexpected_files,
            "low_risk_intent_check": low_risk_check,
            "errors": errors,
            "unknowns": self._unknowns(evidence, diff_text, tracked_changed),
        }
        dump_yaml(run_dir / "diff-verification.yaml", report)
        (run_dir / "diff-verification.md").write_text(self.render_markdown(report), encoding="utf-8")
        return report

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# Diff Verification",
            "",
            f"- FACT: run_id={report.get('run_id')}",
            f"- DECISION: status={report.get('status')}",
            "",
            "## Changed Files",
        ]
        lines.extend(f"- FACT: {item}" for item in report.get("changed_files", []) or ["none"])
        lines.extend(["", "## Untracked Files"])
        lines.extend(f"- FACT: {item}" for item in report.get("untracked_files", []) or ["none"])
        lines.extend(["", "## Scope Check"])
        lines.extend(f"- FACT: approved_file={item}" for item in report.get("approved_files", []) or ["none"])
        lines.extend(f"- DECISION: unexpected_file={item}" for item in report.get("unexpected_files", []) or [])
        lines.extend(["", "## Low-Risk Intent Check"])
        check = report.get("low_risk_intent_check", {})
        lines.append(f"- FACT: change_nature={check.get('change_nature')}")
        lines.append(f"- DECISION: status={check.get('status')}")
        lines.extend(f"- FACT: signal={item}" for item in check.get("signals", []) or ["none"])
        if report.get("errors"):
            lines.extend(["", "## Blocking Errors"])
            lines.extend(f"- DECISION: {item}" for item in report["errors"])
        lines.extend(["", "## Unknowns"])
        lines.extend(f"- UNKNOWN: {item.replace('UNKNOWN: ', '', 1)}" for item in report.get("unknowns", []) or ["none"])
        return "\n".join(lines) + "\n"

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        # quotePath=false keeps non-ASCII paths raw so prefix filters work.
        # utf-8/replace keeps verification alive on non-UTF-8 path bytes; a
        # mangled path then fails the scope check instead of crashing.
        return subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _diff_args(self) -> list[str]:
        # Diff against HEAD so staged changes stay visible; exclude the tool's
        # own run artifacts so they cannot pollute verification. --no-renames
        # keeps renamed files visible as full delete+add pairs.
        base = ["HEAD"] if self._git(["rev-parse", "--verify", "HEAD"]).returncode == 0 else []
        return [*base, "--", ".", f":(exclude){RUNS_PREFIX.rstrip('/')}"]

    def _git_diff(self) -> str:
        result = self._git(["diff", "--no-renames", *self._diff_args()])
        if result.returncode != 0:
            raise DiffVerificationError(result.stderr.strip() or "git diff failed")
        return result.stdout

    def _untracked_files(self) -> list[str]:
        result = self._git(["ls-files", "--others", "--exclude-standard", "-z"])
        if result.returncode != 0:
            raise DiffVerificationError(result.stderr.strip() or "git ls-files failed")
        return sorted(
            path
            for path in result.stdout.split("\0")
            if path and not path.startswith(RUNS_PREFIX)
        )

    def _changed_files(self) -> list[str]:
        # --name-only with --no-renames also lists deletions, renames (both
        # paths), and mode-only changes, which have no "+++ b/" content line.
        result = self._git(["diff", "--name-only", "--no-renames", "-z", *self._diff_args()])
        if result.returncode != 0:
            raise DiffVerificationError(result.stderr.strip() or "git diff failed")
        return [
            path
            for path in result.stdout.split("\0")
            if path and not path.startswith(RUNS_PREFIX)
        ]

    def _added_lines(self, diff_text: str) -> list[str]:
        lines: list[str] = []
        for line in diff_text.splitlines():
            if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
                continue
            content = line[1:].strip()
            if content:
                lines.append(content)
        return lines

    def _untracked_lines(self, untracked_files: list[str]) -> list[str]:
        lines: list[str] = []
        for path in untracked_files:
            try:
                text = (self.root / path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lines.extend(f"{path}: {line.strip()}" for line in text.splitlines() if line.strip())
        return lines

    def _allowed_files(self, approved_plan: dict[str, Any]) -> set[str]:
        files = set()
        for item in approved_plan.get("implementation_plan", {}).get("files_to_modify", []) or []:
            path = str(item.get("path", "")).strip()
            if path:
                files.add(path)
        return files

    def _low_risk_check(self, evidence: dict[str, Any], added_lines: list[str]) -> dict[str, Any]:
        intent = evidence.get("request", {}).get("model_intent", {})
        change_nature = intent.get("change_nature", "")
        if change_nature not in LOW_RISK_NATURES:
            return {"change_nature": change_nature, "status": "not_applicable", "signals": [], "errors": []}
        signals = []
        for content in added_lines:
            upper_content = content.upper()
            if any(signal in content or signal in upper_content for signal in EXECUTABLE_SIGNALS):
                signals.append(content[:160])
        errors = []
        if signals:
            errors.append("low-risk intent diff includes executable-looking changes")
        return {
            "change_nature": change_nature,
            "status": "pass" if not errors else "blocked",
            "signals": signals[:20],
            "errors": errors,
        }

    def _unknowns(self, evidence: dict[str, Any], diff_text: str, tracked_changed: list[str]) -> list[str]:
        unknowns = []
        file_risk = evidence.get("code_findings", {}).get("file_risk", {})
        if file_risk.get("risk_adjustment") == "lowered_by_change_nature":
            unknowns.append("UNKNOWN: diff verification uses textual heuristics; reviewer should confirm no executable behavior changed in high-risk files.")
        if diff_text and not tracked_changed:
            unknowns.append("UNKNOWN: git diff had content but git reported no changed files.")
        return unknowns
