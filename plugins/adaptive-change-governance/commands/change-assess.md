---
description: Assess a requested code change, generate evidence/risk/workflow artifacts, and stop for human review.
---

# Change Assess

Run Adaptive Change Governance for this repository.

First infer the user's change intent and write it to a temporary YAML file. Treat this as model inference, not code fact:

```yaml
version: 1
change_kind: menu_label_change
change_nature: display_text_only
summary: short intent summary
confidence: high
request_goal:
  type: implementation
  requires_code_change: true
  default_stop_gate: workflow_plan_approval
  rationale: INFERENCE: user asks to modify repository behavior or files
scope:
  included:
    - intended scope item
  excluded:
    - out of scope item
  unknowns:
    - thing to verify
risk_hints:
  data_operation: false
  database_schema_change: false
  public_interface_change: false
  permission_change: false
  security_change: false
  financial_change: false
notes:
  - INFERENCE: why this intent was selected
```

Use `request_goal.type` to separate intent from risk:

- `implementation`: user wants code or config changed; continue through workflow approval, technical plan, and implementation gate.
- `analysis_only`: user wants facts, risk analysis, or an answer; stop at `analysis_complete`.
- `decision_support`: user wants a recommendation before deciding; stop at `decision_ready`.
- `planning_only`: user wants a technical plan but not code edits; stop at `technical_plan_approval`.

Then run the request exactly as provided with the intent file:

```bash
change-assess "$ARGUMENTS" --intent-file <intent-file>
```

After the command finishes:

1. Run `change-assess --review-workflow <run_id>` and show the user the risk, guardrails, required modules, optional modules, unknowns, and available commands.
   The review output includes a Chinese progress status bar with colored step state, elapsed time, assigned agent, and artifact paths.
   Use `change-assess --status <run_id>` whenever the user asks where the run currently stands.
   Use `change-assess --next <run_id>` to decide the next action instead of manually inferring commands.
2. If `request_goal.type` is `analysis_only` or `decision_support`, run `change-assess --generate-analysis-report <run_id>` and present the report. Do not generate a technical plan or edit business code for that run.
3. Otherwise, ask the user which decision or module changes they want.
4. Do not generate a technical plan until workflow approval has succeeded.
4. To approve with command-line changes, run:

```bash
change-assess --approve-workflow <run_id>
change-assess --approve-workflow <run_id> --add-required threat_analysis
change-assess --approve-workflow <run_id> --raise-level L4 --reason "reason"
change-assess --status <run_id>
change-assess --next <run_id>
change-assess --next <run_id> --execute-next
```

Use `change-assess --review-decision <run_id> --decision reassess --comment "reason"` when the user requests reassessment instead of approval.

After workflow approval:

```bash
change-assess --add-context <run_id> --include "scope item" --exclude "out of scope" --user-fact "confirmed fact"
change-assess --generate-analysis-report <run_id>
change-assess --generate-agent-tasks <run_id>
change-assess --propose-technical-plan <run_id>
change-assess --review-technical-plan <run_id>
change-assess --approve-technical-plan <run_id>
change-assess --check-gate <run_id> --stage implementation
change-assess --verify-diff <run_id>
change-assess --validate-artifact <run_id> --module dependency_analysis --artifact dependency-analysis.yaml
```

Do not modify business code until the implementation gate returns `GATE OK`.

When a subagent finishes a generated task, run that task's `completion_command`. For example:

```bash
change-assess --complete-step <run_id> --module dependency_analysis --artifact dependency-analysis.yaml --agent dependency-analyzer
```

If `.ai-governance/artifact-schemas.yaml` defines a schema for that module, `--complete-step` validates the artifact first. Blocked validation means the step must not be presented as complete.

For local run retention, use:

```bash
change-assess --cleanup-runs --cleanup-dry-run
change-assess --cleanup-runs
```

Do not ask users to commit `.ai-governance/runs/`; it is a local audit and gate-state directory and should stay gitignored unless the user explicitly chooses another audit mode.

Hard constraints:

- Treat user input as a request, not code fact.
- Classify `request_goal` before scoring risk. Do not route analysis-only or decision-support questions into implementation steps just because the text mentions delete, database, API, or permission terms.
- Use host-model intent classification first; use keyword scanning as code evidence, not as the source of user intent.
- Analyze repository facts before risk scoring.
- Hard guardrails cannot be downgraded or removed.
- Weak-only guardrail candidates require human confirmation but must not set the hard minimum level.
- Low-risk menu or copy changes should stay lightweight unless strong code evidence shows data, interface, permission, or deletion impact.
- File importance from `file_risk` must influence routing: database, auth, migration, scheduler, and API files are higher impact than UI copy files even when the diff size is similar.
- Distinguish inherent file importance from effective change risk. A comment-only change in a high-risk file may stay lightweight only when structured intent says `change_nature: comment_only`, and the implementation diff must later prove that no executable behavior changed.
- After implementation, run `change-assess --verify-diff <run_id>` to produce `diff-verification.yaml/md`; blocked diff verification must stop handoff.
- For L3/L4 workflows, generate `agent-tasks.yaml` and use subagents for narrow read-only or review-only tasks before implementation.
- Subagents must report completed workflow modules with `change-assess --complete-step`; the progress status bar must show completed state, elapsed time, and artifact path.
- Structured subagent artifacts must satisfy `.ai-governance/artifact-schemas.yaml`; missing required fields block completion.
- Workflow approval must happen before technical planning.
- Technical-plan approval must happen before business-code edits.
- Implementation must be preceded by `change-assess --check-gate <run_id> --stage implementation`.
