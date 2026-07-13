---
name: change-governance
description: Use Adaptive Change Governance before technical planning or implementation. Generate evidence, risk assessment, and workflow plan first; wait for human approval before design or execution.
---

# Adaptive Change Governance

Run from the target repository root:

```bash
change-assess "<user request>" --mode assess
```

Required behavior:

- Treat user input as request, not code fact.
- Analyze current repository state before scoring risk.
- Apply hard guardrails before workflow composition.
- Stop after `workflow-plan.md` until human approval is present.
- Do not generate a technical plan or modify business code before approval gates.

For workflow approval after the user edits `human-review.yaml`:

```bash
change-assess --approve-workflow <run_id>
```

Prefer command-line review over asking the user to edit YAML:

```bash
change-assess --review-workflow <run_id>
change-assess --approve-workflow <run_id> --reviewer <name> --add-required threat_analysis
change-assess --review-decision <run_id> --decision reassess --comment "reason"
```
