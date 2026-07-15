# Adaptive Change Governance

Reusable change governance and workflow routing for AI coding agents.

## Quick Start

Adaptive Change Governance is a change-governance layer for AI coding agents. It assesses a requested change, writes audit artifacts, recommends the right workflow, and holds implementation until the required gates are approved.

Be precise about what "holds" means, because it decides how much you can rely on it:

- **Locally it is collaboration discipline, not a sandbox.** The agent is the enforcement; the `PreToolUse` hook only raises the bypass cost. An agent with shell access can write around it, and the Codex CLI ships no hook at all. See [Enforcement boundary](#enforcement-boundary).
- **Server-side it can be a real gate — but only once you configure it.** `--ci-gate` scores a pull request diff in CI. That is only a boundary if branch protection makes the check required *and* CODEOWNERS review protects the gate's own files; otherwise a pull request can rewrite the gate that judges it. See [Enforcement boundary](#enforcement-boundary).

Install the Python dependency used by the CLI:

```bash
python3 -m pip install PyYAML
```

Run an assessment from the target repository root:

```bash
change-assess "把后台「群配置」相关的菜单修改为「业务群配置」"
```

During local development of this repository, use the checked-in wrapper instead:

```bash
bin/change-assess "把后台「群配置」相关的菜单修改为「业务群配置」"
```

The command prints a run id and writes audit files under:

```text
.ai-governance/runs/<run_id>/
```

Start with the human-readable files:

```bash
cat .ai-governance/runs/<run_id>/review.md
cat .ai-governance/runs/<run_id>/workflow-plan.md
```

Then let the tool tell you the next step:

```bash
change-assess --status <run_id>
change-assess --next <run_id>
```

For a normal implementation request, the controlled path is:

```bash
change-assess --approve-workflow <run_id>
change-assess --propose-technical-plan <run_id>
change-assess --review-technical-plan <run_id>
change-assess --approve-technical-plan <run_id>
change-assess --check-gate <run_id> --stage implementation
```

Only start editing business code after the implementation gate returns `GATE OK`. After implementation, verify the actual diff and generate the final report:

```bash
change-assess --verify-diff <run_id>
change-assess --reassess <run_id>
change-assess --generate-verification-report <run_id>
```

For analysis-only or decision-support requests, generate the answer artifact and stop without implementation:

```bash
change-assess --generate-analysis-report <run_id>
```

To customize project-specific risk behavior, edit:

- `.ai-governance/project-risk.yaml` for baseline risk, high-risk file patterns, and audit retention.
- `.ai-governance/guardrails.yaml` for hard guardrails such as money, data deletion, permissions, or public interfaces.
- `.ai-governance/workflow-modules.yaml` for which workflow steps each risk level requires.
- `.ai-governance/profiles/<profile>/` for domain-specific overrides.

To bootstrap file-risk rules for a new repository, write a suggestions-only report:

```bash
change-assess --suggest-risk-config
```

After reviewing `.ai-governance/project-risk.suggested.yaml`, apply it with an explicit confirmation prompt:

```bash
change-assess --suggest-risk-config --apply-risk-config
```

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

The plugin also registers a `PreToolUse` hook (`hooks/implementation_gate.py`): while a governance run with an implementation goal has not passed `--check-gate --stage implementation`, `Edit`/`Write` tool calls on project files are denied, and gate-state files under `.ai-governance/runs/` (approval markers, `human-review.yaml`, verification reports) may only be written through the CLI. Set `ACG_HOOK_MODE=warn` to log instead of block, or `ACG_HOOK_MODE=off` to disable.

## Enforcement boundary

The local hook is a speed bump, not a security boundary. Do not present it as one:

- **It only matches editing tools** (`Edit|Write|MultiEdit|NotebookEdit`). A shell write is not intercepted — including a write to a gate-state file. `echo "" > .ai-governance/runs/<run>/.workflow-approved` forges a human approval, which defeats the premise of the gate rather than merely working around it.
- **It can disable itself.** `ACG_HOOK_MODE=off` is an environment variable the agent can export.
- **Codex has no hook at all.** `.codex-plugin/plugin.json` declares only `skills`; there, the SKILL is the whole gate and enforcement is self-discipline.

Widening the hook to `Bash` does not fix this: intercepting arbitrary shell (`>`, `tee`, `sed -i`, `python -c`, heredocs, generated scripts) is whack-a-mole, and an agent with shell access on the same machine cannot be constrained by local file gates — it could sign forged state with any key it can read.

Put enforcement where the agent cannot write:

```bash
change-assess --ci-gate origin/main --ci-fail-level L3 --ci-output gate-summary.md
```

`--ci-gate` scores the diff against a base ref **from code facts only** — no request text and no intent file, so nothing in the working tree can talk the verdict down. Domains come from `code_signals` on the changed code, destructive operations from statements the diff *adds*, blast radius from `reference_scanner`, plus configured `file_risk`. It exits `3` when the level reaches `--ci-fail-level` (default `L3`), meaning *a human must review this*, not *this change is wrong*.

`.github/workflows/change-governance.yml` runs it on every pull request.

### The gate cannot protect itself

For `pull_request`, GitHub runs the workflow definition **from the pull request**. So the workflow is not its own trust anchor: a change can edit the gate that judges it. Two layers narrow that, and one setting closes it:

1. **The gate runs from the base revision.** The workflow checks the tool out at `github.event.pull_request.base.sha` and runs *that* code, with the base revision's rules passed as absolute paths, against the pull request's tree. (Absolute matters: given a relative path, `change-assess` prefers the project's own `.ai-governance/`, which would hand the rules back to the pull request.) Rewriting `ci_gate.py` or `guardrails.yaml` inside a pull request therefore does not change how that pull request is scored.
2. **Touching the gate always requires review.** The base-revision gate forces `review_required` for any diff touching `GOVERNANCE_PATHS` (workflows, `bin/change-assess`, `lib/adaptive_change_governance/**`, `plugins/**`, `.ai-governance/**`, CODEOWNERS) regardless of the computed level.
3. **You must enable the anchor.** The remaining hole is the workflow file itself, which layers 1 and 2 live in. Closing it needs repository settings an agent cannot edit:
   - mark `governance-gate` a **required status check** on protected branches;
   - enable **Require review from Code Owners**, with `.github/CODEOWNERS` (shipped, with `@OWNER` to replace) covering the governance paths.

**Until you configure (3), this workflow is advisory, not a boundary.** Do not describe it as one.

The check is designed to be **clearable**, which matters: GitHub does not let a review approval override a failing required check, so a gate that merely failed on high risk would make every L3/L4 pull request unmergeable forever. Instead, when the gate reports `review_required`, the workflow queries the pull request's reviews and passes once a human *other than the author* has submitted an approving review. It also triggers on `pull_request_review`, because GitHub does not re-run `pull_request` workflows when a review lands — without that, the check would never clear.

Net effect: low-risk pull requests merge without ceremony; high-risk ones need a human approval, which is the one signal an agent cannot forge.

Known artifact: run against this repository, the gate flags `financial-calculation` and `physical-device-control`, because its own test fixtures and pattern definitions literally contain `power_off(...)` and `round(price * qty, 2)`. That is self-reference, not a finding on a normal target repository.

## Development

The root `lib/`, `bin/`, and `.ai-governance/*.yaml` are the single source of truth; `plugins/adaptive-change-governance/` ships a copy. After changing them, run:

```bash
scripts/sync-plugin.sh
```

`test/test_phase1.py` fails if the copies drift. CI (`.github/workflows/ci.yml`) runs `mypy` (see `mypy.ini`) and the test suite. Run the same gates locally before committing:

```bash
python3 -m pip install PyYAML mypy types-PyYAML
scripts/check.sh   # mypy + tests, matching CI
```

`scripts/check.sh` prefers `.venv/bin/python` when present. To run the steps individually:

```bash
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
