# Adaptive Change Governance Spec Coverage

This document audits the current implementation against `docs/adaptive-change-governance-spec.md`.

## Summary

MVP status: substantially complete.

The main governance chain is implemented:

```text
request
-> evidence-pack.yaml
-> risk-assessment.yaml
-> workflow-plan.md
-> workflow approval
-> technical-plan.yaml/md
-> technical-plan approval
-> implementation gate
-> diff verification
-> reassessment
-> verification-report.yaml/md
```

The remaining gaps are mostly depth and integration gaps, not missing core gates.

## MVP Completion Definition

| Spec item | Status | Implementation | Test coverage |
| --- | --- | --- | --- |
| Unified `/change-assess` command | Implemented | `bin/change-assess`, `lib/adaptive_change_governance/cli.py` | `test_cli_assess_generates_phase1_artifacts_and_stops` |
| Read project risk profile | Implemented | `.ai-governance/project-risk.yaml`, `config_loader.py`, `schema_validator.py` | `test_config_files_validate` |
| Read hard guardrails | Implemented | `.ai-governance/guardrails.yaml`, `RiskEvaluator` | `test_money_change_triggers_hard_guardrail_and_required_modules`, `test_destructive_database_operation_cannot_drop_hard_gate` |
| Analyze current repository | Implemented, coarse | `RepositoryAnalyzer` | `test_non_git_scan_records_unknowns`, file-risk and guardrail tests |
| Generate evidence pack | Implemented | `evidence-pack.yaml` | `test_cli_assess_generates_phase1_artifacts_and_stops` |
| Risk scoring | Implemented | `RiskEvaluator` | risk-level and guardrail tests |
| Generate workflow plan | Implemented | `WorkflowComposer`, `workflow-plan.md` | workflow tests and CLI artifact test |
| Workflow requires human confirmation | Implemented | `.workflow-approved`, `--approve-workflow` | `test_human_review_can_approve_and_add_modules` |
| Generate technical plan from approved workflow | Implemented | `TechnicalPlanGate`, `--propose-technical-plan` | `test_technical_plan_gate_requires_workflow_then_approval` |
| Technical plan requires human confirmation | Implemented | `.technical-plan-approved`, `--approve-technical-plan` | `test_technical_plan_gate_requires_workflow_then_approval` |
| Reassess after implementation | Implemented | `--reassess`, `reassessment.yaml/md`, `post-*` artifacts | `test_diff_verification_blocks_executable_change_for_comment_only_intent` |
| Output verification report | Implemented | `--generate-verification-report`, `verification-report.yaml/md` | `test_diff_verification_blocks_executable_change_for_comment_only_intent` |
| Store full run artifacts in isolated directory | Implemented | `.ai-governance/runs/<run_id>/` | CLI tests |
| Trace important judgments to facts or rules | Partially implemented | FACT/INFERENCE/UNKNOWN/DECISION labels, evidence files, guardrail evidence | review and guardrail evidence tests |

## Phase Coverage

### Phase 1: Static Configuration

Status: complete for MVP.

Implemented files:

- `.ai-governance/project-risk.yaml`
- `.ai-governance/guardrails.yaml`
- `.ai-governance/workflow-modules.yaml`
- `.ai-governance/artifact-schemas.yaml`
- `lib/adaptive_change_governance/schema_validator.py`

Remaining gap:

- `artifact-schemas.yaml` has lightweight validation only. It checks required fields, not nested field types or evidence quality.

### Phase 2: Code Fact Scanning

Status: implemented at MVP+ level.

Implemented by:

- `RepositoryAnalyzer`
- `file_risk.py`
- `reference_scanner.py` (blast-radius fan-out)
- `code_signals.py` (code-grounded behavior signals)
- evidence output in `evidence-pack.yaml`

Covered facts:

- Git branch/commit/dirty status
- relevant files
- related files
- feature boundary with confirmed files, weak signals, and ambiguous important files
- file semantic role classification
- investigation questions compiled from UNKNOWNs, weak guardrails, feature-boundary uncertainty, and sensitive change signals
- test files
- affected domains (keyword/path/relation evidence plus code-signal grounding)
- reference fan-out (`reference_findings`): inbound reference count, referencing files/modules, shared-contract and cross-module-boundary flags — so a one-line edit to a widely referenced symbol reads as high blast radius
- code signals (`code_signals`): deterministic behavior signals the keyword dictionary misses — money arithmetic, device-protocol calls (`power_off`), route/auth decorators, message publish/consume — emitted as strong domain evidence
- host-model localization (`intent.relevant_files`): files the model identifies by reading the code are merged into scope, so code signals / file risk / reference fan-out run even when keyword search cannot bridge a natural-language/code gap
- host-model domain judgments (`intent.domain_hints`): confidence-graded, additive domain evidence (high → strong / can fire a guardrail; lower → weak candidate); never removes a keyword or code-signal domain
- change types
- operations
- file risk profile with configured rules plus semantic role inference
- current diff signals through later `DiffVerifier`

Remaining gaps:

- Reference fan-out uses import/textual matches, not a resolved AST call graph; reflection and dynamic dispatch are still not counted.
- Framework route discovery and dynamic invocation remain UNKNOWN when not directly visible.
- Cross-service (out-of-repo) consumers are not visible to the static scan and remain a host-model / reassessment concern.
- Request-text keywords are deliberately kept as a conservative escalation floor, not demoted to localization-only. With host-model localization now bridging the language gap, false negatives are covered by `relevant_files` + `domain_hints`; the request keywords are retained because letting model presence suppress a keyword-triggered guardrail would violate spec 3.3 (model may add risk, never remove it). Over-triggering is resolved at the human review gate, not by the model.
- Request wording can no longer *lower* risk. The `_is_text_only_change` keyword heuristic was removed: display-text-only is a property of the intended change, not of the current code or the request phrasing, and it was the one place a literal match could suppress risk. `text_only_change` now comes only from a host-model intent classification (`is_low_risk_intent`: a low-risk `change_kind`/`change_nature` with no risk hints), and remains subject to `--verify-diff`. Consequence: without an intent file, trivial edits (a comment or copy change) are no longer auto-routed to L1 and will score heavier — the system errs strict rather than guessing lightweight.

### Phase 3: Risk Scoring

Status: implemented.

Implemented by:

- `RiskEvaluator`
- weighted dimensions
- hard guardrail minimum level enforcement
- weak signal handling
- structured dimension explanations in `risk_explanation.dimension_explanations`
- structured hard guardrail evaluations in `risk_explanation.guardrail_evaluations`
- `risk-assessment.md` and `post-risk-assessment.md`
- configurable `risk-calibration.yaml`
- scenario regression validation through `risk-scenarios.yaml` and `--validate-risk-scenarios`
- user context corrections applied during reassessment without overriding strong evidence
- request goal handling for analysis-only and decision-support requests

Remaining gaps:

- Default weights and thresholds are configurable, but shipped values are still MVP defaults rather than learned from historical incidents.
- Some semantic distinctions still depend on host-model intent input or explicit user context.

### Phase 4: Workflow Generation

Status: implemented.

Implemented by:

- `WorkflowComposer`
- `workflow-recommendation.yaml`
- `workflow-plan.md`
- `progress.yaml`
- `--status`
- `--next`

Covered:

- required modules
- optional modules
- skipped modules
- human gates
- escalation triggers
- request goal default stop gates

Remaining gaps:

- Module ordering is rule based; there is no separate graph solver for module dependencies.
- User-facing progress is CLI text, not a native UI.

### Phase 5: Technical Plan Association

Status: implemented.

Implemented by:

- `TechnicalPlanGate`
- `technical-plan.yaml/md`
- `approved-technical-plan.yaml/md`
- `.technical-plan-approved`

Covered:

- workflow approval required before plan generation
- required module coverage validation
- prohibited actions inherited into the technical plan

Remaining gaps:

- Generated technical plan is structured but still skeletal. It expects the agent/user to fill concrete implementation detail.
- Section mapping is represented by `module_coverage`, not a full markdown section-by-section trace table.

### Phase 6: Dynamic Reassessment

Status: implemented at MVP level.

Implemented by:

- `DiffVerifier`
- `--verify-diff`
- `ReassessmentRunner`
- `--reassess`
- `VerificationReportGenerator`
- `--generate-verification-report`

Generated artifacts:

- `diff-verification.yaml/md`
- `post-evidence-pack.yaml`
- `post-risk-assessment.yaml`
- `post-workflow-recommendation.yaml`
- `reassessment.yaml/md`
- `verification-report.yaml/md`
- `run-state.yaml`

Remaining gaps:

- Reassessment reuses the coarse repository analyzer, so it is not a full semantic diff analyzer.
- Verification report records gate and artifact status, but does not yet execute project test commands or ingest CI results.
- Human reapproval after reassessment is flagged, but there is no separate `--approve-reassessment` command yet.

## Additional Capabilities Beyond The Original MVP

Implemented after the initial spec:

- Claude and Codex plugin packaging.
- Request goal routing: `implementation`, `analysis_only`, `decision_support`, `planning_only`.
- Local run retention.
- File risk profile with inherent vs effective risk.
- Agent task generation for L3/L4 workflows.
- Subagent completion tracking with elapsed time and artifact path.
- Artifact schema validation before marking subagent steps complete.
- Strict artifact evidence validation with `path`, integer `line`, labeled `fact`, and confidence.
- Diff verification for low-risk intent and approved technical plan scope; diffs against HEAD (staged included), scans untracked files, and excludes `.ai-governance/runs/` artifacts.
- `--status` run dashboard.
- `--next` next-action planner with safe execution mode.
- Investigation-question artifacts with `--next` routing before technical planning.
- Guardrail suppression consistency: `requires_code_change: false` only disarms guardrails for analysis/decision goals, never for implementation or planning goals.
- Honest technical-plan module coverage (`covered` only with completed progress steps; guardrail analysis modules block plan approval until completed).
- Automatic `.ai-governance/runs/.gitignore` in `gitignored` audit mode.
- Claude Code `PreToolUse` hook enforcing the implementation gate and protecting gate-state files (`ACG_HOOK_MODE=enforce|warn|off`).
- Plugin runtime sync script (`scripts/sync-plugin.sh`) with drift-detection tests, mypy config, and GitHub Actions CI.

## Known Non-Goals Still Respected

The implementation does not do:

- automatic deployment
- automatic rollback
- production SQL execution
- cloud resource operations
- full CI/CD integration
- web management UI
- team permission system
- historical incident learning
- full dependency graph database

## Current Highest-Value Remaining Work

1. Add `--approve-reassessment <run_id>` so dynamic risk upgrades have their own explicit human gate.
2. Add project test command capture into `verification-report.yaml`.
3. Strengthen artifact schemas from required-field checks to typed/nested schemas.
4. Improve repository analysis with language-aware parsers for common stacks.
5. Add GitHub PR or CI integration after the local CLI workflow is stable.
