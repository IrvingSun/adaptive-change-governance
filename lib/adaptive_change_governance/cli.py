from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path

from .analysis_report import AnalysisReportGenerator, AnalysisReportError
from .agent_tasks import AgentTaskComposer, AgentTaskError
from .artifact_validator import ArtifactValidator, ArtifactValidationError
from .config_loader import ConfigError, dump_yaml, load_yaml
from .diff_verifier import DiffVerifier, DiffVerificationError
from .human_review import HumanReviewGate, ReviewError
from .intent_model import load_intent_file
from .next_action import NextActionPlanner
from .progress import ProgressTracker
from .reassessment import ReassessmentRunner
from .repository_analyzer import RepositoryAnalyzer
from .risk_evaluator import RiskEvaluator, render_risk_markdown
from .risk_scenarios import RiskScenarioValidator
from .run_status import RunStatusRenderer
from .run_retention import cleanup_runs, render_cleanup_summary
from .schema_validator import ValidationError, validate_all, validate_risk_calibration
from .technical_plan import TechnicalPlanError, TechnicalPlanGate
from .verification_report import VerificationReportGenerator
from .workflow_composer import WorkflowComposer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="change-assess")
    parser.add_argument("request", nargs="*", help="需求描述")
    parser.add_argument("--mode", choices=["assess", "design", "execute", "reassess"], default="assess")
    parser.add_argument("--risk-profile", default=".ai-governance/project-risk.yaml")
    parser.add_argument("--guardrails", default=".ai-governance/guardrails.yaml")
    parser.add_argument("--workflow-modules", default=".ai-governance/workflow-modules.yaml")
    parser.add_argument("--artifact-schemas", default=".ai-governance/artifact-schemas.yaml")
    parser.add_argument("--risk-calibration", default=".ai-governance/risk-calibration.yaml")
    parser.add_argument("--risk-scenarios", default=".ai-governance/risk-scenarios.yaml")
    parser.add_argument("--profile", help="Use .ai-governance/profiles/<profile>/ overrides for project risk and guardrails")
    parser.add_argument("--intent-file", help="YAML file produced by the host model with structured change intent")
    parser.add_argument("--output", default=".ai-governance/runs")
    parser.add_argument("--run-id")
    parser.add_argument("--review-workflow", help="Print workflow review options for an existing run id or run directory")
    parser.add_argument("--status", help="Print a run status dashboard for an existing run id or run directory")
    parser.add_argument("--next", help="Print or safely execute the next recommended action for an existing run")
    parser.add_argument("--execute-next", action="store_true", help="Execute the next action only when it does not require user confirmation")
    parser.add_argument("--approve-workflow", help="Approve workflow for an existing run id or run directory")
    parser.add_argument("--review-decision", help="Set review decision for an existing run id or run directory")
    parser.add_argument("--propose-technical-plan", help="Generate technical-plan.yaml/md after workflow approval")
    parser.add_argument("--review-technical-plan", help="Review technical plan coverage and approval commands")
    parser.add_argument("--approve-technical-plan", help="Approve a generated technical plan")
    parser.add_argument("--generate-analysis-report", help="Generate analysis-report.yaml/md for an existing run id or run directory")
    parser.add_argument("--verify-diff", help="Verify current git diff against approved technical plan and low-risk intent")
    parser.add_argument("--reassess", help="Reassess an existing run against the current repository state")
    parser.add_argument("--generate-verification-report", help="Generate verification-report.yaml/md for an existing run")
    parser.add_argument("--generate-agent-tasks", help="Generate agent-tasks.yaml/md after workflow approval")
    parser.add_argument("--review-agent-tasks", help="Print generated agent task plan")
    parser.add_argument("--start-step", help="Mark one workflow module as in progress for an existing run id or run directory")
    parser.add_argument("--complete-step", help="Mark one workflow module as completed for an existing run id or run directory")
    parser.add_argument("--validate-artifact", help="Validate one module artifact for an existing run id or run directory")
    parser.add_argument("--validate-risk-scenarios", action="store_true", help="Validate configured risk scoring scenarios")
    parser.add_argument("--check-gate", help="Check whether a run may enter a stage")
    parser.add_argument("--add-context", help="Add facts, corrections, or scope context to a run id or run directory")
    parser.add_argument("--cleanup-runs", action="store_true", help="Clean old .ai-governance/runs entries according to audit_retention policy")
    parser.add_argument("--cleanup-dry-run", action="store_true", help="Show which run entries would be deleted without deleting them")
    parser.add_argument("--decision", choices=["approve", "reject", "request_changes", "reassess"])
    parser.add_argument("--stage", choices=["technical_plan", "implementation"])
    parser.add_argument("--module")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--agent")
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--reviewer")
    parser.add_argument("--raise-level", choices=["L1", "L2", "L3", "L4"])
    parser.add_argument("--reason")
    parser.add_argument("--add-required", action="append", default=[])
    parser.add_argument("--add-optional", action="append", default=[])
    parser.add_argument("--user-fact", action="append", default=[])
    parser.add_argument("--correction", action="append", default=[])
    parser.add_argument("--comment", action="append", default=[])
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--prohibit", action="append", default=[])
    parser.add_argument("--unknown", action="append", default=[])
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
        artifact_schemas = _load_optional_yaml(_config_path(root, tool_root, args.artifact_schemas))
        risk_calibration = _load_optional_yaml(_config_path(root, tool_root, args.risk_calibration))
        validate_all(project_risk, guardrails, workflow_modules)
        if risk_calibration:
            validate_risk_calibration(risk_calibration)
    except (ConfigError, ValidationError) as exc:
        print(f"ERROR: {exc}")
        return 2

    if args.review_workflow:
        return _review_workflow(root, Path(args.output), args.review_workflow, workflow_modules)

    if args.status:
        return _status(root, Path(args.output), args.status, workflow_modules)

    if args.next:
        return _next(root, Path(args.output), args.next, args, project_risk, guardrails, workflow_modules, risk_calibration)

    if args.review_decision:
        return _review_decision(root, Path(args.output), args.review_decision, args, workflow_modules)

    if args.approve_workflow:
        return _approve_workflow(root, Path(args.output), args.approve_workflow, args, project_risk, workflow_modules)

    if args.add_context:
        return _add_context(root, Path(args.output), args.add_context, args, workflow_modules)

    if args.propose_technical_plan:
        return _propose_technical_plan(root, Path(args.output), args.propose_technical_plan, workflow_modules)

    if args.review_technical_plan:
        return _review_technical_plan(root, Path(args.output), args.review_technical_plan, workflow_modules)

    if args.approve_technical_plan:
        return _approve_technical_plan(root, Path(args.output), args.approve_technical_plan, args, workflow_modules)

    if args.generate_analysis_report:
        return _generate_analysis_report(root, Path(args.output), args.generate_analysis_report, workflow_modules)

    if args.verify_diff:
        return _verify_diff(root, Path(args.output), args.verify_diff)

    if args.reassess:
        return _reassess(root, Path(args.output), args.reassess, project_risk, guardrails, workflow_modules, risk_calibration)

    if args.generate_verification_report:
        return _generate_verification_report(root, Path(args.output), args.generate_verification_report)

    if args.generate_agent_tasks:
        return _generate_agent_tasks(root, Path(args.output), args.generate_agent_tasks, workflow_modules)

    if args.review_agent_tasks:
        return _review_agent_tasks(root, Path(args.output), args.review_agent_tasks)

    if args.start_step:
        return _start_step(root, Path(args.output), args.start_step, args, workflow_modules)

    if args.complete_step:
        return _complete_step(root, Path(args.output), args.complete_step, args, workflow_modules, artifact_schemas)

    if args.validate_artifact:
        return _validate_artifact(root, Path(args.output), args.validate_artifact, args, artifact_schemas)

    if args.validate_risk_scenarios:
        return _validate_risk_scenarios(root, tool_root, args, project_risk, guardrails, risk_calibration)

    if args.check_gate:
        return _check_gate(root, Path(args.output), args.check_gate, args, workflow_modules)

    if args.cleanup_runs:
        return _cleanup_runs(root, Path(args.output), project_risk, args.cleanup_dry_run)

    if args.mode == "reassess" and args.run_id:
        return _reassess(root, Path(args.output), args.run_id, project_risk, guardrails, workflow_modules, risk_calibration)

    if args.mode != "assess":
        return _guarded_mode(args.mode)

    request = " ".join(args.request).strip()
    if not request:
        print("ERROR: request is required for assess mode")
        return 2
    try:
        intent = load_intent_file(_config_path(root, tool_root, args.intent_file)) if args.intent_file else {}
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        return 2

    run_dir = _run_dir(root / args.output, request)
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "request.md").write_text(f"# Request\n\n{request}\n", encoding="utf-8")
    if intent:
        dump_yaml(run_dir / "change-intent.yaml", intent)

    evidence = RepositoryAnalyzer(root).analyze(request, project_risk, intent=intent)
    risk = RiskEvaluator(project_risk, guardrails, risk_calibration).evaluate(evidence)
    composer = WorkflowComposer(project_risk, workflow_modules)
    workflow = composer.compose(evidence, risk)

    dump_yaml(run_dir / "evidence-pack.yaml", evidence)
    dump_yaml(run_dir / "risk-assessment.yaml", risk)
    (run_dir / "risk-assessment.md").write_text(render_risk_markdown(risk), encoding="utf-8")
    dump_yaml(run_dir / "workflow-recommendation.yaml", workflow)
    tracker = ProgressTracker(workflow_modules)
    tracker.initialize(run_dir, workflow["workflow_recommendation"].get("required_modules", []), current="code_fact_scan")
    tracker.mark_done(run_dir, "code_fact_scan")
    (run_dir / "workflow-plan.md").write_text(composer.render_markdown(evidence, risk, workflow), encoding="utf-8")
    HumanReviewGate(workflow_modules).write_review_files(run_dir, evidence, risk, workflow)

    print(f"Run created: {run_dir}")
    print(f"Final level: {risk['final_level']}")
    print(f"Triggered guardrails: {risk['triggered_guardrails'] or []}")
    print(f"Request goal: {workflow['workflow_recommendation'].get('request_goal', {}).get('type', 'implementation')}")
    print(f"Next gate: {workflow['workflow_recommendation'].get('default_stop_gate', 'workflow_plan_approval')}")
    print(f"Review command: change-assess --review-workflow {run_dir.name}")
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


def _status(root: Path, output_root: Path, run_id: str, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    print(RunStatusRenderer(workflow_modules).render(run_dir), end="")
    return 0


def _next(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, project_risk: dict, guardrails: dict, workflow_modules: dict, risk_calibration: dict | None = None) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    planner = NextActionPlanner()
    plan = planner.plan(run_dir)
    print(planner.render(plan), end="")
    if not args.execute_next:
        return 0
    if plan.get("requires_user_confirmation"):
        print("BLOCKED: next action requires user confirmation")
        return 3
    if not plan.get("can_execute"):
        print("BLOCKED: next action cannot be executed automatically")
        return 3
    action = plan.get("recommended_action")
    if action == "generate_analysis_report":
        return _generate_analysis_report(root, output_root, run_id, workflow_modules)
    if action == "generate_agent_tasks":
        return _generate_agent_tasks(root, output_root, run_id, workflow_modules)
    if action == "propose_technical_plan":
        return _propose_technical_plan(root, output_root, run_id, workflow_modules)
    if action == "reassess":
        return _reassess(root, output_root, run_id, project_risk, guardrails, workflow_modules, risk_calibration)
    if action == "generate_verification_report":
        return _generate_verification_report(root, output_root, run_id)
    print("BLOCKED: unsupported automatic next action")
    return 3


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
        ProgressTracker(workflow_modules).mark_current(run_dir, "technical_design", strict=False)
    except (ConfigError, ReviewError) as exc:
        print(f"BLOCKED: {exc}")
        return 3
    print(f"Workflow approved: {run_dir}")
    print(gate.approved_summary(approved), end="")
    return 0


def _cleanup_runs(root: Path, output_root: Path, project_risk: dict, dry_run: bool) -> int:
    policy = project_risk.get("audit_retention", {})
    result = cleanup_runs(root / output_root, policy, dry_run=dry_run)
    print(render_cleanup_summary(result), end="")
    return 0


def _add_context(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    context = TechnicalPlanGate(workflow_modules).add_run_context(
        run_dir,
        facts=args.user_fact,
        corrections=args.correction,
        include=args.include,
        exclude=args.exclude,
        prohibit=args.prohibit,
        unknown=args.unknown,
    )
    print(f"Run context updated: {run_dir}")
    print(f"Facts: {len(context.get('facts', []))}")
    print(f"Corrections: {len(context.get('corrections', []))}")
    return 0


def _propose_technical_plan(root: Path, output_root: Path, run_id: str, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    workflow = load_yaml(run_dir / "workflow-recommendation.yaml")
    goal_type = workflow.get("workflow_recommendation", {}).get("request_goal", {}).get("type", "implementation")
    if goal_type in {"analysis_only", "decision_support"}:
        print(f"BLOCKED: request goal is {goal_type}; default stop gate is {workflow.get('workflow_recommendation', {}).get('default_stop_gate')}")
        return 3
    try:
        plan = TechnicalPlanGate(workflow_modules).propose(run_dir)
        ProgressTracker(workflow_modules).mark_done(run_dir, "technical_design", strict=False)
        ProgressTracker(workflow_modules).mark_current(run_dir, "test_design", strict=False)
    except (ConfigError, TechnicalPlanError) as exc:
        print(f"BLOCKED: {exc}")
        return 3
    print(f"Technical plan generated: {run_dir / 'technical-plan.yaml'}")
    print(f"Validation: {plan.get('validation', {}).get('status')}")
    print(f"Review command: change-assess --review-technical-plan {run_dir.name}")
    print(f"Agent task command: change-assess --generate-agent-tasks {run_dir.name}")
    return 0


def _review_technical_plan(root: Path, output_root: Path, run_id: str, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        print(TechnicalPlanGate(workflow_modules).review_summary(run_dir), end="")
    except (ConfigError, TechnicalPlanError) as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0


def _approve_technical_plan(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        TechnicalPlanGate(workflow_modules).approve(run_dir, reviewer=args.reviewer)
        ProgressTracker(workflow_modules).mark_done(run_dir, "test_design", strict=False)
    except (ConfigError, TechnicalPlanError) as exc:
        print(f"BLOCKED: {exc}")
        return 3
    print(f"Technical plan approved: {run_dir}")
    print(f"Gate command: change-assess --check-gate {run_dir.name} --stage implementation")
    return 0


def _generate_analysis_report(root: Path, output_root: Path, run_id: str, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        report = AnalysisReportGenerator(workflow_modules).generate(run_dir)
    except (ConfigError, AnalysisReportError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Analysis report generated: {run_dir / 'analysis-report.yaml'}")
    print(f"Conclusion: {report.get('conclusion', {}).get('summary')}")
    print(f"Stop gate: {report.get('request', {}).get('default_stop_gate')}")
    return 0


def _verify_diff(root: Path, output_root: Path, run_id: str) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        report = DiffVerifier(root).verify(run_dir)
    except (ConfigError, DiffVerificationError) as exc:
        print(f"BLOCKED: {exc}")
        return 3
    print(f"Diff verification generated: {run_dir / 'diff-verification.yaml'}")
    print(f"Status: {report.get('status')}")
    if report.get("errors"):
        print("Errors: " + "; ".join(report["errors"]))
    return 0 if report.get("status") == "pass" else 3


def _validate_risk_scenarios(root: Path, tool_root: Path, args: argparse.Namespace, project_risk: dict, guardrails: dict, risk_calibration: dict) -> int:
    scenarios_path = _config_path(root, tool_root, args.risk_scenarios)
    try:
        report = RiskScenarioValidator(root, project_risk, guardrails, risk_calibration).validate(scenarios_path, root / ".ai-governance")
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Risk scenario report: {root / '.ai-governance/risk-scenario-report.yaml'}")
    print(f"Status: {report.get('status')}")
    summary = report.get("summary", {})
    print(f"Passed: {summary.get('passed')}/{summary.get('total')}")
    for item in report.get("results", []):
        print(f"- {item.get('id')}: {item.get('status')} expected={item.get('expected_level')} actual={item.get('actual_level')}")
        for error in item.get("errors", []):
            print(f"  {error}")
    return 0 if report.get("status") == "pass" else 3


def _reassess(root: Path, output_root: Path, run_id: str, project_risk: dict, guardrails: dict, workflow_modules: dict, risk_calibration: dict | None = None) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        report = ReassessmentRunner(root, project_risk, guardrails, workflow_modules, risk_calibration).run(run_dir)
    except (ConfigError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}")
        return 2
    _write_run_state(run_dir, "REASSESSING" if report.get("reassessment", {}).get("requires_human_reapproval") else "VERIFYING")
    reassessment = report.get("reassessment", {})
    print(f"Reassessment generated: {run_dir / 'reassessment.yaml'}")
    print(f"Previous level: {reassessment.get('previous_level')}")
    print(f"New level: {reassessment.get('new_level')}")
    print(f"Requires human reapproval: {reassessment.get('requires_human_reapproval')}")
    return 0


def _generate_verification_report(root: Path, output_root: Path, run_id: str) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    report = VerificationReportGenerator().generate(run_dir)
    _write_run_state(run_dir, "COMPLETED" if report.get("status") == "pass" else "FAILED_VERIFICATION")
    print(f"Verification report generated: {run_dir / 'verification-report.yaml'}")
    print(f"Status: {report.get('status')}")
    if report.get("blockers"):
        print("Blockers: " + "; ".join(report["blockers"]))
    return 0 if report.get("status") == "pass" else 3


def _generate_agent_tasks(root: Path, output_root: Path, run_id: str, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    try:
        artifact = AgentTaskComposer(workflow_modules).generate(run_dir)
    except (ConfigError, AgentTaskError) as exc:
        print(f"BLOCKED: {exc}")
        return 3
    print(f"Agent tasks generated: {run_dir / 'agent-tasks.yaml'}")
    print(f"Subagents required: {artifact.get('policy', {}).get('subagents_required')}")
    print(f"Task count: {len(artifact.get('tasks', []))}")
    return 0


def _review_agent_tasks(root: Path, output_root: Path, run_id: str) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    path = run_dir / "agent-tasks.md"
    if not path.exists():
        print("ERROR: agent-tasks.md is missing; run --generate-agent-tasks first")
        return 2
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def _start_step(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    if not args.module:
        print("ERROR: --module is required")
        return 2
    try:
        tracker = ProgressTracker(workflow_modules)
        tracker.mark_current(run_dir, args.module, agent=args.agent, notes=args.note)
    except (ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Step started: {args.module}")
    print(ProgressTracker(workflow_modules).render(run_dir, color=True), end="")
    return 0


def _complete_step(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, workflow_modules: dict, artifact_schemas: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    if not args.module:
        print("ERROR: --module is required")
        return 2
    if args.artifact:
        for artifact in args.artifact:
            report = ArtifactValidator(artifact_schemas).validate(run_dir, args.module, artifact)
            if report.get("status") != "pass":
                ProgressTracker(workflow_modules).mark_blocked(
                    run_dir,
                    args.module,
                    artifacts=args.artifact,
                    agent=args.agent,
                    notes=(args.note or []) + report.get("errors", []),
                )
                print(f"Artifact validation blocked: {artifact}")
                print("Errors: " + "; ".join(report.get("errors", [])))
                return 3
    try:
        tracker = ProgressTracker(workflow_modules)
        tracker.mark_done(
            run_dir,
            args.module,
            artifacts=args.artifact,
            agent=args.agent,
            notes=args.note,
        )
    except (ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Step completed: {args.module}")
    print(ProgressTracker(workflow_modules).render(run_dir, color=True), end="")
    return 0


def _validate_artifact(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, artifact_schemas: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    if not args.module:
        print("ERROR: --module is required")
        return 2
    if not args.artifact:
        print("ERROR: --artifact is required")
        return 2
    try:
        reports = [ArtifactValidator(artifact_schemas).validate(run_dir, args.module, artifact) for artifact in args.artifact]
    except ArtifactValidationError as exc:
        print(f"ERROR: {exc}")
        return 2
    status = "pass" if all(report.get("status") == "pass" for report in reports) else "blocked"
    print(f"Artifact validation: {status}")
    for report in reports:
        print(f"Artifact: {report.get('artifact')} status={report.get('status')}")
        if report.get("errors"):
            print("Errors: " + "; ".join(report["errors"]))
    return 0 if status == "pass" else 3


def _check_gate(root: Path, output_root: Path, run_id: str, args: argparse.Namespace, workflow_modules: dict) -> int:
    run_dir = _resolve_run_dir(root, output_root, run_id)
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_id}")
        return 2
    stage = args.stage or "implementation"
    errors = TechnicalPlanGate(workflow_modules).check_gate(run_dir, stage)
    if errors:
        print("BLOCKED: " + "; ".join(errors))
        return 3
    if stage == "implementation":
        ProgressTracker(workflow_modules).mark_current(run_dir, "regression_test", strict=False)
    print(f"GATE OK: {stage} may start")
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


def _load_optional_yaml(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "schemas": {}}
    return load_yaml(path)


def _write_run_state(run_dir: Path, state: str) -> None:
    dump_yaml(run_dir / "run-state.yaml", {
        "version": 1,
        "state": state,
        "updated_at": datetime.now().isoformat(),
    })


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
