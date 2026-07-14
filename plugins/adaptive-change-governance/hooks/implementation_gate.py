#!/usr/bin/env python3
"""PreToolUse gate: block file edits while a governance run has not passed its
implementation gate, and protect run approval-state files from direct writes.

Stdlib only: run artifacts are read with line scanning against the fixed
layout produced by dump_yaml, so the hook works even without PyYAML.

Modes via ACG_HOOK_MODE: enforce (default) | warn | off.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RUNS_RELATIVE = Path(".ai-governance") / "runs"

# Files inside a run directory that only the change-assess CLI may write.
PROTECTED_RUN_FILES = {
    ".workflow-approved",
    ".technical-plan-approved",
    ".analysis-complete",
    ".verification-complete",
    "human-review.yaml",
    "approved-workflow.yaml",
    "approved-technical-plan.yaml",
    "evidence-pack.yaml",
    "risk-assessment.yaml",
    "workflow-recommendation.yaml",
    "diff-verification.yaml",
    "verification-report.yaml",
    "reassessment.yaml",
    "run-state.yaml",
    "progress.yaml",
}

TERMINAL_STATES = {"COMPLETED", "ABANDONED"}
UNGOVERNED_GOALS = {"analysis_only", "decision_support", "planning_only"}


def main() -> int:
    mode = os.environ.get("ACG_HOOK_MODE", "enforce").strip().lower()
    if mode == "off":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not raw_path:
        return 0
    project = Path(payload.get("cwd") or os.getcwd()).resolve()
    runs_root = project / RUNS_RELATIVE
    if not runs_root.is_dir():
        return 0
    target = Path(raw_path)
    if not target.is_absolute():
        target = project / target
    target = target.resolve()
    if not _is_relative_to(target, project):
        return 0

    protected = _protected_run_file_reason(target, runs_root)
    if protected:
        return _decide(mode, protected)
    if _is_relative_to(target, project / ".ai-governance"):
        return 0

    blocking = _blocking_runs(runs_root)
    if not blocking:
        return 0
    # Enforce every pending run, not only the newest: a newer approved run must
    # not mask an older run whose gate is still unmet. Report the most recent
    # offender so the message is stable.
    run_dir, errors = blocking[0]
    extra = (
        f" ({len(blocking)} governed runs are pending; showing the most recent)"
        if len(blocking) > 1
        else ""
    )
    reason = (
        f"Adaptive Change Governance: run '{run_dir.name}' has not passed the implementation gate "
        f"({'; '.join(errors)}){extra}. Complete the gate first: "
        f"change-assess --next {run_dir.name}. "
        "If this run is obsolete, record it via "
        f"change-assess --review-decision {run_dir.name} --decision reject --comment \"abandoned\". "
        "Set ACG_HOOK_MODE=off to disable this hook."
    )
    return _decide(mode, reason)


def _decide(mode: str, reason: str) -> int:
    if mode == "warn":
        print(json.dumps({"systemMessage": f"[warn] {reason}"}))
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def _protected_run_file_reason(target: Path, runs_root: Path) -> str:
    if not _is_relative_to(target, runs_root):
        return ""
    if target.name in PROTECTED_RUN_FILES:
        return (
            f"Adaptive Change Governance: '{target.name}' is a gate-state file and may only be "
            "written by the change-assess CLI (e.g. --approve-workflow, --complete-step, --verify-diff)."
        )
    return ""


def _active_implementation_runs(runs_root: Path) -> list[Path]:
    runs: list[Path] = []
    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir():
            continue
        if not (run_dir / "workflow-recommendation.yaml").exists():
            continue
        if _run_state(run_dir) in TERMINAL_STATES:
            continue
        if _goal_type(run_dir) in UNGOVERNED_GOALS:
            continue
        if _scalar_at(run_dir / "human-review.yaml", ("decision",)) == "reject":
            continue
        runs.append(run_dir)
    return runs


def _blocking_runs(runs_root: Path) -> list[tuple[Path, list[str]]]:
    """Every active governed run whose implementation gate is not yet met, most
    recent first. Empty when nothing blocks."""
    blocking: list[tuple[Path, list[str]]] = []
    for run_dir in _active_implementation_runs(runs_root):
        errors = _gate_errors(run_dir)
        if errors:
            blocking.append((run_dir, errors))
    blocking.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
    return blocking


def _gate_errors(run_dir: Path) -> list[str]:
    errors = []
    if not (run_dir / ".workflow-approved").exists():
        errors.append("workflow has not been approved")
    if not (run_dir / ".technical-plan-approved").exists():
        errors.append("technical plan has not been approved")
    if not (run_dir / "approved-technical-plan.yaml").exists():
        errors.append("approved-technical-plan.yaml is missing")
    diff_path = run_dir / "diff-verification.yaml"
    if diff_path.exists() and _scalar_at(diff_path, ("status",)) != "pass":
        errors.append("diff verification is blocked")
    return errors


def _run_state(run_dir: Path) -> str:
    return _scalar_at(run_dir / "run-state.yaml", ("state",))


def _goal_type(run_dir: Path) -> str:
    return _scalar_at(
        run_dir / "workflow-recommendation.yaml",
        ("workflow_recommendation", "request_goal", "type"),
    )


def _scalar_at(path: Path, key_path: tuple[str, ...]) -> str:
    """Return the scalar at a nested mapping path in a dump_yaml file.

    Indentation-based, so a key is matched only under its expected parent rather
    than by first-match across nesting (e.g. the top-level `status` in
    diff-verification.yaml, not the nested `low_risk_intent_check.status`).
    Stdlib only: the hook must run without PyYAML. List entries (`- ...`) are
    skipped; every target here is a mapping scalar.
    """
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    stack: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        if ":" not in stripped:
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key, _, value = stripped.partition(":")
        current = tuple(item[1] for item in stack) + (key.strip(),)
        stack.append((indent, key.strip()))
        value = value.strip().strip("'\"")
        if current == key_path and value:
            return value
    return ""


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
