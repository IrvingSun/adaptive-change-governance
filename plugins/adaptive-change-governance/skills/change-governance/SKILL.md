---
name: change-governance
description: Use Adaptive Change Governance before technical planning or implementation. Generate evidence, risk assessment, and workflow plan first; wait for human approval before design or execution.
---

# Adaptive Change Governance

Run from the target repository root:

```bash
change-assess "<user request>" --mode assess --intent-file <intent-file>
```

Required behavior:

- Treat user input as request, not code fact.
- Before running assessment, infer structured intent into a YAML file with change_kind, included scope, excluded scope, unknowns, and risk_hints.
- Do not let keyword matches override clear low-risk user intent unless code evidence is strong.
- Analyze current repository state before scoring risk.
- Apply hard guardrails before workflow composition.
- Keep simple menu/copy changes lightweight unless strong evidence shows real data, interface, permission, or deletion impact.
- Treat weak-only guardrail signals as candidates for confirmation, not as hard minimum level triggers.
- Consider configured `file_risk` when explaining risk: small edits in database/auth/scheduler/API files can require heavier workflow than similar-sized UI text edits.
- Distinguish inherent file risk from effective change risk. If the model classifies a high-risk file change as comment-only or docs-only, record that as intent and require later diff verification.
- Stop after `workflow-plan.md` until human approval is present.
- Do not generate a technical plan before workflow approval.
- Do not modify business code before technical-plan approval and implementation gate check.

Use command-line review and approval. Do not ask users to manually edit `human-review.yaml`; it is an audit file.
When showing workflow review, include the Chinese progress status bar so the user can see 未执行 / 执行中 / 已执行 and elapsed time per completed step.
Run artifacts in `.ai-governance/runs/` are local audit and gate-state files; keep them gitignored by default. Use `change-assess --cleanup-runs --cleanup-dry-run` before deleting old runs.

```bash
change-assess --review-workflow <run_id>
change-assess --approve-workflow <run_id> --add-required threat_analysis
change-assess --add-context <run_id> --include "scope item" --exclude "out of scope" --user-fact "confirmed fact"
change-assess --generate-agent-tasks <run_id>
change-assess --propose-technical-plan <run_id>
change-assess --review-technical-plan <run_id>
change-assess --approve-technical-plan <run_id>
change-assess --check-gate <run_id> --stage implementation
change-assess --review-decision <run_id> --decision reassess --comment "reason"
```

For L3/L4 workflows, use `agent-tasks.yaml` to split work into subagents. Read-only and review-only subagents must not modify files; implementation subagents require `GATE OK`.
