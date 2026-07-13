# Adaptive Change Governance

Reusable change governance and workflow routing for AI coding agents.

Phase 1 command:

```bash
bin/change-assess "修改需求描述" --mode assess
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
bin/change-assess --approve-workflow <run_id> --reviewer sun --add-required threat_analysis
bin/change-assess --review-decision <run_id> --decision reassess --comment "needs dependency analysis"
```

Approval writes:

- `approved-workflow.yaml`
- `approved-workflow-plan.md`
- `.workflow-approved`

Human reviewers can add modules, raise risk, and correct AI assumptions from CLI flags. `human-review.yaml` remains as the audit file, but users do not need to edit it manually. They cannot lower hard-guardrail decisions or remove hard-required modules.

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
/plugin marketplace add /Users/sun/Documents/harness/governance
/plugin install adaptive-change-governance@adaptive-governance
/reload-plugins
```

After install, use:

```text
/adaptive-change-governance:change-assess 修改后台订单页面上的提示文案。
```

The plugin contributes a `change-assess` executable to Claude Code's Bash PATH. It requires `PyYAML` in the Python environment used by `python3`.
