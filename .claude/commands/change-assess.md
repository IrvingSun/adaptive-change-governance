# change-assess

Run Adaptive Change Governance before technical design or implementation.

Command:

```bash
bin/change-assess "$ARGUMENTS" --mode assess
```

Rules:

- Analyze repository facts before risk scoring.
- Apply hard guardrails as non-negotiable requirements.
- Stop after `workflow-plan.md` and wait for human confirmation.
- Do not generate a technical plan or modify business code before confirmation.
- Prefer `change-assess --review-workflow <run_id>` and command-line approval options over asking the user to edit YAML manually.
