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

1. Open the generated `review.md`.
2. Ask the user to edit `human-review.yaml` if they want to approve, reject, request changes, request reassessment, raise risk, or add workflow modules.
3. Do not generate a technical plan until workflow approval has succeeded.
4. To approve after the user edits `human-review.yaml`, run:

```bash
change-assess --approve-workflow <run_id>
```

Hard constraints:

- Treat user input as a request, not code fact.
- Analyze repository facts before risk scoring.
- Hard guardrails cannot be downgraded or removed.
- Workflow approval must happen before technical planning.
- Technical-plan approval must happen before business-code edits.
