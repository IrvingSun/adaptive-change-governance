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

Use command-line review and approval. Do not ask users to manually edit `human-review.yaml`; it is an audit file.
Run artifacts in `.ai-governance/runs/` are local audit and gate-state files; keep them gitignored by default. Use `change-assess --cleanup-runs --cleanup-dry-run` before deleting old runs.

```bash
change-assess --review-workflow <run_id>
change-assess --approve-workflow <run_id> --add-required threat_analysis
change-assess --review-decision <run_id> --decision reassess --comment "reason"
```
