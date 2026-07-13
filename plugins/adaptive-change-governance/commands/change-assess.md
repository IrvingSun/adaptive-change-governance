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

Then run the request exactly as provided with the intent file:

```bash
change-assess "$ARGUMENTS" --intent-file <intent-file>
```

After the command finishes:

1. Run `change-assess --review-workflow <run_id>` and show the user the risk, guardrails, required modules, optional modules, unknowns, and available commands.
2. Ask the user which decision or module changes they want.
3. Do not generate a technical plan until workflow approval has succeeded.
4. To approve with command-line changes, run:

```bash
change-assess --approve-workflow <run_id>
change-assess --approve-workflow <run_id> --add-required threat_analysis
change-assess --approve-workflow <run_id> --raise-level L4 --reason "reason"
```

Use `change-assess --review-decision <run_id> --decision reassess --comment "reason"` when the user requests reassessment instead of approval.

After workflow approval:

```bash
change-assess --add-context <run_id> --include "scope item" --exclude "out of scope" --user-fact "confirmed fact"
change-assess --propose-technical-plan <run_id>
change-assess --review-technical-plan <run_id>
change-assess --approve-technical-plan <run_id>
change-assess --check-gate <run_id> --stage implementation
```

Do not modify business code until the implementation gate returns `GATE OK`.

For local run retention, use:

```bash
change-assess --cleanup-runs --cleanup-dry-run
change-assess --cleanup-runs
```

Do not ask users to commit `.ai-governance/runs/`; it is a local audit and gate-state directory and should stay gitignored unless the user explicitly chooses another audit mode.

Hard constraints:

- Treat user input as a request, not code fact.
- Use host-model intent classification first; use keyword scanning as code evidence, not as the source of user intent.
- Analyze repository facts before risk scoring.
- Hard guardrails cannot be downgraded or removed.
- Weak-only guardrail candidates require human confirmation but must not set the hard minimum level.
- Low-risk menu or copy changes should stay lightweight unless strong code evidence shows data, interface, permission, or deletion impact.
- File importance from `file_risk` must influence routing: database, auth, migration, scheduler, and API files are higher impact than UI copy files even when the diff size is similar.
- Distinguish inherent file importance from effective change risk. A comment-only change in a high-risk file may stay lightweight only when structured intent says `change_nature: comment_only`, and the implementation diff must later prove that no executable behavior changed.
- Workflow approval must happen before technical planning.
- Technical-plan approval must happen before business-code edits.
- Implementation must be preceded by `change-assess --check-gate <run_id> --stage implementation`.
