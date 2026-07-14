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

Use the default industry-neutral profile for reusable governance. Use the optional charging profile when assessing charging platform changes:

```bash
bin/change-assess "修改需求描述" --profile charging-platform
```

The command writes an isolated run under `.ai-governance/runs/`:

- `request.md`
- `evidence-pack.yaml`
- `risk-assessment.yaml`
- `workflow-recommendation.yaml`
- `workflow-plan.md`
- `review.md`
- `human-review.yaml`

The tool stops at `workflow_plan_approval`. Review and approve from the CLI:

```bash
bin/change-assess --review-workflow <run_id>
bin/change-assess --approve-workflow <run_id>
bin/change-assess --approve-workflow <run_id> --add-required threat_analysis
bin/change-assess --review-decision <run_id> --decision reassess --comment "needs dependency analysis"
```

Approval writes:

- `approved-workflow.yaml`
- `approved-workflow-plan.md`
- `.workflow-approved`

Human reviewers can add modules, raise risk, and correct AI assumptions from CLI flags. `human-review.yaml` remains as the audit file, but users do not need to edit it manually. They cannot lower hard-guardrail decisions or remove hard-required modules.

Low-risk menu or copy changes are routed through the lightweight path when structured intent says the change is display-only and code evidence does not contradict it. Weak guardrail signals are shown as candidates for human confirmation, but they do not set the hard minimum risk level unless supported by strong evidence.

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

- `highest_level`: inherent file importance from `file_risk` rules.
- `effective_level`: risk after considering structured change intent. For example, a comment-only change in `app/database.py` keeps `highest_level: high` but may route with `effective_level: low`, while adding an UNKNOWN requiring the implementation diff to prove the change is comment-only.

After workflow approval, generate and approve the technical plan before implementation:

```bash
bin/change-assess --add-context <run_id> --include "..." --exclude "..." --user-fact "..."
bin/change-assess --generate-agent-tasks <run_id>
bin/change-assess --propose-technical-plan <run_id>
bin/change-assess --review-technical-plan <run_id>
bin/change-assess --approve-technical-plan <run_id>
bin/change-assess --check-gate <run_id> --stage implementation
```

For L3/L4 workflows, `agent-tasks.yaml` marks subagents as required. The task plan separates read-only fact gathering, dependency/data impact review, adversarial review, technical-plan review, and implementation-after-gate work. Subagents must cite evidence and may not edit business code unless assigned implementation mode after `GATE OK`.

The technical-plan gate writes:

- `run-context.yaml`
- `agent-tasks.yaml`
- `agent-tasks.md`
- `technical-plan.yaml`
- `technical-plan.md`
- `approved-technical-plan.yaml`
- `approved-technical-plan.md`
- `.technical-plan-approved`

Implementation must remain blocked until `--check-gate <run_id> --stage implementation` returns `GATE OK`.

Run artifacts under `.ai-governance/runs/` are local audit and gate-state files. They are ignored by Git by default. Use the retention policy in `.ai-governance/project-risk.yaml` to control local history size:

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
