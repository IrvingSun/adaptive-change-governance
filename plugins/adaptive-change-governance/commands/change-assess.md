---
description: Assess a requested code change, generate evidence/risk/workflow artifacts, and stop for human review.
---

# Change Assess

Run Adaptive Change Governance for this repository.

Use the request exactly as provided:

```bash
change-assess "$ARGUMENTS"
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

For local run retention, use:

```bash
change-assess --cleanup-runs --cleanup-dry-run
change-assess --cleanup-runs
```

Do not ask users to commit `.ai-governance/runs/`; it is a local audit and gate-state directory and should stay gitignored unless the user explicitly chooses another audit mode.

Hard constraints:

- Treat user input as a request, not code fact.
- Analyze repository facts before risk scoring.
- Hard guardrails cannot be downgraded or removed.
- Workflow approval must happen before technical planning.
- Technical-plan approval must happen before business-code edits.
