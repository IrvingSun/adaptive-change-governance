from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path

from .config_loader import ConfigError, dump_yaml, load_yaml
from .human_review import HumanReviewGate, ReviewError
from .repository_analyzer import RepositoryAnalyzer
from .risk_evaluator import RiskEvaluator
from .schema_validator import ValidationError, validate_all
from .workflow_composer import WorkflowComposer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="change-assess")
    parser.add_argument("request", nargs="*", help="需求描述")
    parser.add_argument("--mode", choices=["assess", "design", "execute", "reassess"], default="assess")
    parser.add_argument("--risk-profile", default=".ai-governance/project-risk.yaml")
    parser.add_argument("--guardrails", default=".ai-governance/guardrails.yaml")
    parser.add_argument("--workflow-modules", default=".ai-governance/workflow-modules.yaml")
    parser.add_argument("--profile", help="Use .ai-governance/profiles/<profile>/ overrides for project risk and guardrails")
    parser.add_argument("--output", default=".ai-governance/runs")
    parser.add_argument("--run-id")
    parser.add_argument("--review-workflow", help="Print workflow review options for an existing run id or run directory")
    parser.add_argument("--approve-workflow", help="Approve workflow for an existing run id or run directory")
    parser.add_argument("--review-decision", help="Set review decision for an existing run id or run directory")
    parser.add_argument("--decision", choices=["approve", "reject", "request_changes", "reassess"])
    parser.add_argument("--reviewer")
    parser.add_argument("--raise-level", choices=["L1", "L2", "L3", "L4"])
    parser.add_argument("--reason")
    parser.add_argument("--add-required", action="append", default=[])
    parser.add_argument("--add-optional", action="append", default=[])
    parser.add_argument("--user-fact", action="append", default=[])
    parser.add_argument("--correction", action="append", default=[])
    parser.add_argument("--comment", action="append", default=[])
    args = parser.parse_args(argv)

    try:
        root = _project_root()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.profile:
        profile_root = Path(".ai-governance") / "profiles" / args.profile
        args.risk_profile = str(profile_root / "project-risk.yaml")
        args.guardrails = str(profile_root / "guardrails.yaml")
        profile_workflow_modules = root / profile_root / "workflow-modules.yaml"
        if profile_workflow_modules.exists():
            args.workflow_modules = str(profile_root / "workflow-modules.yaml")
    tool_root = Path(os.environ.get("ACG_TOOL_ROOT", str(root)))
    try:
        project_risk = load_yaml(_config_path(root, tool_root, args.risk_profile))
        guardrails = load_yaml(_config_path(root, tool_root, args.guardrails))
        workflow_modules = load_yaml(_config_path(root, tool_root, args.workflow_modules))
        validate_all(project_risk, guardrails, workflow_modules)
    except (ConfigError, ValidationError) as exc:
        print(f"ERROR: {exc}")
        return 2

    if args.review_workflow:
        return _review_workflow(root, Path(args.output), args.review_workflow, workflow_modules)

    if args.review_decision:
        return _review_decision(root, Path(args.output), args.review_decision, args, workflow_modules)

    if args.approve_workflow:
        return _approve_workflow(root, Path(args.output), args.approve_workflow, args, project_risk, workflow_modules)

    if args.mode != "assess":
        return _guarded_mode(args.mode)

    request = " ".join(args.request).strip()
    if not request:
        print("ERROR: request is required for assess mode")
        return 2

    run_dir = _run_dir(root / args.output, request)
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "request.md").write_text(f"# Request\n\n{request}\n", encoding="utf-8")

    evidence = RepositoryAnalyzer(root).analyze(request, project_risk)
    risk = RiskEvaluator(project_risk, guardrails).evaluate(evidence)
    composer = WorkflowComposer(project_risk, workflow_modules)
    workflow = composer.compose(evidence, risk)

    dump_yaml(run_dir / "evidence-pack.yaml", evidence)
    dump_yaml(run_dir / "risk-assessment.yaml", risk)
    dump_yaml(run_dir / "workflow-recommendation.yaml", workflow)
    (run_dir / "workflow-plan.md").write_text(composer.render_markdown(evidence, risk, workflow), encoding="utf-8")
    HumanReviewGate(workflow_modules).write_review_files(run_dir, evidence, risk, workflow)

    print(f"Run created: {run_dir}")
    print(f"Final level: {risk['final_level']}")
    print(f"Triggered guardrails: {risk['triggered_guardrails'] or []}")
    print("Next gate: workflow_plan_approval")
    print(f"Review files: {run_dir / 'review.md'} and {run_dir / 'human-review.yaml'}")
    print("No technical plan or business-code changes were produced.")
    return 0


def _review_workflow(root: Path, output_root: Path, run_id: str, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        print(HumanReviewGate(workflow_modules).review_summary(run_dir), end="")
    except (ConfigError, ReviewError) as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0


def _review_decision(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        review = HumanReviewGate(workflow_modules).update_review(
            run_dir,
            decision=args.decision,
            reviewer=args.reviewer,
            raise_level=args.raise_level,
            reason=args.reason,
            add_required=args.add_required,
            add_optional=args.add_optional,
            user_fact=args.user_fact,
            correction=args.correction,
            comment=args.comment,
        )
    except (ConfigError, ReviewError) as exc:
        print(f"BLOCKED: {exc}")
        return 3
    print(f"Review updated: {run_dir}")
    print(f"Decision: {review['decision']}")
    print(f"Added required modules: {review.get('module_changes', {}).get('add_required', [])}")
    print("Run --review-workflow to inspect, or --approve-workflow to approve when ready.")
    return 0


def _approve_workflow(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, project_risk: dict, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        gate = HumanReviewGate(workflow_modules)
        gate.update_review(
            run_dir,
            decision="approve",
            reviewer=args.reviewer,
            raise_level=args.raise_level,
            reason=args.reason,
            add_required=args.add_required,
            add_optional=args.add_optional,
            user_fact=args.user_fact,
            correction=args.correction,
            comment=args.comment,
        )
        approved = gate.approve_workflow(run_dir, project_risk)
    except (ConfigError, ReviewError) as exc:
        print(f"BLOCKED: {exc}")
        return 3
    print(f"Workflow approved: {run_dir}")
    print(gate.approved_summary(approved), end="")
    return 0


def _guarded_mode(mode: str) -> int:
    if mode == "design":
        print("BLOCKED: design mode requires a generated workflow-plan and explicit workflow approval. Phase 2 will implement this transition.")
    elif mode == "execute":
        print("BLOCKED: execute mode requires approved workflow and approved technical plan. Phase 3 will implement this transition.")
    elif mode == "reassess":
        print("BLOCKED: reassess mode requires an existing run baseline. Phase 3 will implement this transition.")
    return 3


def _run_dir(output_root: Path, request: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", request.strip().lower()).strip("-")[:40]
    if not slug:
        slug = "change"
    base = output_root / f"{timestamp}-{slug}"
    candidate = base
    counter = 2
    while candidate.exists():
        candidate = output_root / f"{timestamp}-{slug}-{counter}"
        counter += 1
    return candidate


def _resolve_run_dir(root: Path, output_root: Path, run_id: str) -> Path:
    candidate = Path(run_id)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    return root / output_root / run_id


def _config_path(project_root: Path, tool_root: Path, configured: str) -> Path:
    path = Path(configured)
    if path.is_absolute():
        return path
    project_path = project_root / path
    if project_path.exists():
        return project_path
    return tool_root / path


def _project_root() -> Path:
    try:
        return Path.cwd()
    except OSError:
        pwd = os.environ.get("PWD")
        if pwd:
            candidate = Path(pwd)
            if candidate.exists():
                return candidate.resolve()
        raise RuntimeError("cannot determine current working directory; run change-assess from an existing project directory")
