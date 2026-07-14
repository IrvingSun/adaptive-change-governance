# Adaptive Change Governance

Reusable change governance and workflow routing for AI coding agents.

Phase 1 command:

```bash
bin/change-assess "修改需求描述" --mode assess
```

For plugin usage, the host model should first infer structured intent and pass it to the CLI:

```yaml
version: 1
change_kind: menu_label_change
change_nature: display_text_only
summary: rename one menu display label
confidence: high
request_goal:
  type: implementation
  requires_code_change: true
  default_stop_gate: workflow_plan_approval
  rationale: INFERENCE: user asks to modify repository behavior or files
scope:
  included:
    - menu display text
  excluded:
    - database changes
    - public API changes
  unknowns: []
risk_hints:
  data_operation: false
  database_schema_change: false
  public_interface_change: false
  permission_change: false
  security_change: false
  financial_change: false
```

```bash
bin/change-assess "修改需求描述" --intent-file change-intent.yaml
```

`request_goal.type` separates the user's desired outcome from the implementation risk:

- `implementation`: code or config should change; continue through workflow approval, technical plan, and implementation gate.
- `analysis_only`: return facts or risk analysis; stop at `analysis_complete`.
- `decision_support`: return a recommendation before the user decides; stop at `decision_ready`.
- `planning_only`: produce a technical plan but do not edit code; stop at `technical_plan_approval`.

For analysis-only or decision-support runs, generate the final analysis artifact:

```bash
bin/change-assess --generate-analysis-report <run_id>
```

Validate the risk scoring calibration with the built-in scenario suite:

```bash
bin/change-assess --validate-risk-scenarios
```

Use the default industry-neutral profile for reusable governance. Use the optional charging profile when assessing charging platform changes:

```bash
bin/change-assess "修改需求描述" --profile charging-platform
```

The command writes an isolated run under `.ai-governance/runs/`:

- `request.md`
- `evidence-pack.yaml`
- `risk-assessment.yaml`
- `risk-assessment.md`
- `investigation-questions.yaml`
- `investigation-questions.md`
- `workflow-recommendation.yaml`
- `workflow-plan.md`
- `progress.yaml`
- `analysis-report.yaml` when generated
- `analysis-report.md` when generated
- `review.md`
- `human-review.yaml`

Risk calibration is configured in `.ai-governance/risk-calibration.yaml`. Scenario expectations live in `.ai-governance/risk-scenarios.yaml`; validation writes `risk-scenario-report.yaml/md`.

The tool stops at `workflow_plan_approval`. Review and approve from the CLI:

```bash
bin/change-assess --review-workflow <run_id>
bin/change-assess --status <run_id>
bin/change-assess --next <run_id>
bin/change-assess --next <run_id> --execute-next
bin/change-assess --approve-workflow <run_id>
bin/change-assess --approve-workflow <run_id> --add-required threat_analysis
bin/change-assess --review-decision <run_id> --decision reassess --comment "needs dependency analysis"
```

`--review-workflow` prints a Chinese progress status bar. Each step shows `未执行` / `执行中` / `已执行` / `已阻塞`, with terminal colors, elapsed time for completed steps, assigned agent, and produced artifacts when available.

`--status` prints a run dashboard with request goal, current gate, generated artifacts, blockers, the progress status bar, and suggested next commands.

`--next` prints the single recommended next action. `--execute-next` only runs actions that do not require user confirmation, such as generating an analysis report or generating agent tasks after workflow approval.

Approval writes:

- `approved-workflow.yaml`
- `approved-workflow-plan.md`
- `.workflow-approved`

Human reviewers can add modules, raise risk, and correct AI assumptions from CLI flags. `human-review.yaml` remains as the audit file, but users do not need to edit it manually. They cannot lower hard-guardrail decisions or remove hard-required modules.

Low-risk menu or copy changes are routed through the lightweight path when structured intent says the change is display-only and code evidence does not contradict it. Weak guardrail signals are shown as candidates for human confirmation, but they do not set the hard minimum risk level unless supported by strong evidence.

`evidence-pack.yaml` includes a feature boundary: confirmed files, weak signal files, ambiguous important files, semantic file roles, and UNKNOWN items for dynamic routing or implicit dependencies. Risk scoring uses this boundary so generic keyword matches do not automatically become hard facts.

File importance is part of risk routing. Configure `file_risk` in `.ai-governance/project-risk.yaml` so similarly sized changes can route differently:

```yaml
file_risk:
  - pattern: "app/database*.py"
    level: high
    reason: database connection or persistence infrastructure
  - pattern: "frontend/src/layouts/**"
    level: low
    reason: navigation and display shell
```

File risk has two levels:

- `highest_level`: inherent file importance from `file_risk` rules plus semantic file role inference.
- `effective_level`: risk after considering structured change intent. For example, a comment-only change in `app/database.py` keeps `highest_level: high` but may route with `effective_level: low`, while adding an UNKNOWN requiring the implementation diff to prove the change is comment-only.

Technical-plan module coverage is honest, not automatic: modules completed via `--complete-step` are marked `covered` with their artifacts as evidence; everything else stays `planned`. Approving the technical plan is blocked while hard-guardrail analysis modules (for example `business_rule_confirmation`, `threat_analysis`, `dependency_analysis`) are still unfinished — complete them with `--complete-step` first.

After workflow approval, generate and approve the technical plan before implementation:

```bash
bin/change-assess --add-context <run_id> --include "..." --exclude "..." --user-fact "..."
bin/change-assess --generate-agent-tasks <run_id>
bin/change-assess --propose-technical-plan <run_id>
bin/change-assess --review-technical-plan <run_id>
bin/change-assess --approve-technical-plan <run_id>
bin/change-assess --check-gate <run_id> --stage implementation
bin/change-assess --verify-diff <run_id>
bin/change-assess --reassess <run_id>
bin/change-assess --generate-verification-report <run_id>
```

For L3/L4 workflows, `agent-tasks.yaml` marks subagents as required. The task plan separates read-only fact gathering, dependency/data impact review, adversarial review, technical-plan review, and implementation-after-gate work. Subagents must cite evidence and may not edit business code unless assigned implementation mode after `GATE OK`.

Each generated agent task includes a `completion_command`. After a subagent finishes a step such as `dependency_analysis`, run that command to mark the workflow step as completed and record elapsed time, agent name, and artifact path:

```bash
bin/change-assess --complete-step <run_id> --module dependency_analysis --artifact dependency-analysis.yaml --agent dependency-analyzer
```

If `.ai-governance/artifact-schemas.yaml` defines a schema for the module, `--complete-step` validates the artifact before marking the step done. You can also validate explicitly:

```bash
bin/change-assess --validate-artifact <run_id> --module dependency_analysis --artifact dependency-analysis.yaml
```

For modules with strict evidence rules, artifact evidence must be structured:

```yaml
evidence:
  - path: app/api/example.py
    line: 12
    fact: "FACT: caller references the changed endpoint"
    confidence: high
```

The technical-plan gate writes:

- `run-context.yaml`
- `agent-tasks.yaml`
- `agent-tasks.md`
- `technical-plan.yaml`
- `technical-plan.md`
- `approved-technical-plan.yaml`
- `approved-technical-plan.md`
- `.technical-plan-approved`
- `diff-verification.yaml`
- `diff-verification.md`
- `post-evidence-pack.yaml`
- `post-risk-assessment.yaml`
- `post-risk-assessment.md`
- `reassessment.yaml`
- `reassessment.md`
- `verification-report.yaml`
- `verification-report.md`
- `run-state.yaml`

Use `--verify-diff` after implementation to compare the working tree against the approved technical plan. Verification diffs against `HEAD`, so staged changes stay visible, and untracked files are scanned as additions; the tool's own `.ai-governance/runs/` artifacts are excluded. If low-risk intent lowered file risk, diff verification checks for executable-looking changes before the implementation gate can pass.
Use `--reassess` after implementation to rescan the current repository and compare the new risk against the initial assessment. Use `--generate-verification-report` to produce the final MVP verification report.

Implementation must remain blocked until `--check-gate <run_id> --stage implementation` returns `GATE OK`.

Run artifacts under `.ai-governance/runs/` are local audit and gate-state files. When `audit_retention.audit_mode` is `gitignored` (the default), the first assessment writes `.ai-governance/runs/.gitignore` so run artifacts never enter Git or pollute diff verification. Use the retention policy in `.ai-governance/project-risk.yaml` to control local history size:

```yaml
audit_retention:
  audit_mode: gitignored
  retain_latest: 20
  retain_days: 30
```

Preview or apply cleanup:

```bash
bin/change-assess --cleanup-runs --cleanup-dry-run
bin/change-assess --cleanup-runs
```

Cleanup removes old approved/inactive runs only; active runs waiting for workflow approval are skipped.

## Claude Code plugin

This repository includes a Claude Code marketplace and plugin package:

- Marketplace: `.claude-plugin/marketplace.json`
- Plugin: `plugins/adaptive-change-governance/`

Local development test:

```bash
claude --plugin-dir ./plugins/adaptive-change-governance
```

Install from this local marketplace in Claude Code:

```text
/plugin marketplace add <repo-root>
/plugin install adaptive-change-governance@adaptive-governance
/reload-plugins
```

After install, use:

```text
/adaptive-change-governance:change-assess 修改后台订单页面上的提示文案。
```

The plugin contributes a `change-assess` executable to Claude Code's Bash PATH. It requires `PyYAML` in the Python environment used by `python3`.

The plugin also registers a `PreToolUse` hook (`hooks/implementation_gate.py`) that enforces the implementation gate at the harness level: while a governance run with an implementation goal has not passed `--check-gate --stage implementation`, `Edit`/`Write` tool calls on project files are denied, and gate-state files under `.ai-governance/runs/` (approval markers, `human-review.yaml`, verification reports) may only be written through the CLI. Set `ACG_HOOK_MODE=warn` to log instead of block, or `ACG_HOOK_MODE=off` to disable. Known residual risk: the hook intercepts file-editing tools only — shell-based edits (`sed`, `tee`, ...) are not intercepted, so the hook raises the bypass cost but is not a sandbox.

## Development

The root `lib/`, `bin/`, and `.ai-governance/*.yaml` are the single source of truth; `plugins/adaptive-change-governance/` ships a copy. After changing them, run:

```bash
scripts/sync-plugin.sh
```

`test/test_phase1.py` fails if the copies drift. CI (`.github/workflows/ci.yml`) runs `mypy` (see `mypy.ini`) and the test suite:

```bash
python3 -m pip install PyYAML mypy types-PyYAML
mypy lib/adaptive_change_governance plugins/adaptive-change-governance/hooks/implementation_gate.py
python3 test/test_phase1.py
```

## Codex plugin

This repository also includes a Codex marketplace and plugin package:

- Marketplace: `.agents/plugins/marketplace.json`
- Plugin: `plugins/adaptive-change-governance/`

Install from GitHub:

```bash
codex plugin marketplace add IrvingSun/adaptive-change-governance --ref main
codex plugin add adaptive-change-governance@adaptive-governance
```

Install from a local checkout:

```bash
codex plugin marketplace add <repo-root>
codex plugin add adaptive-change-governance@adaptive-governance
```

After install, start a new Codex session and ask it to use Adaptive Change Governance before technical planning or implementation. The plugin contributes the `change-governance` skill and uses the same `change-assess` CLI runtime as the Claude Code plugin.
