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
- Before running assessment, infer structured intent into a YAML file with change_kind, request_goal, included scope, excluded scope, unknowns, and risk_hints.
- Classify `request_goal.type` as `implementation`, `analysis_only`, `decision_support`, or `planning_only` before risk scoring. Analysis-only and decision-support requests stop at their analysis/decision gate and must not be pushed into implementation steps merely because the wording mentions delete, database, API, or permission terms.
- Do not let keyword matches override clear low-risk user intent unless code evidence is strong.
- Analyze current repository state before scoring risk.
- Apply hard guardrails before workflow composition.
- Keep simple menu/copy changes lightweight unless strong evidence shows real data, interface, permission, or deletion impact.
- Treat weak-only guardrail signals as candidates for confirmation, not as hard minimum level triggers.
- Consider configured `file_risk` when explaining risk: small edits in database/auth/scheduler/API files can require heavier workflow than similar-sized UI text edits.
- Distinguish inherent file risk from effective change risk. If the model classifies a high-risk file change as comment-only or docs-only, record that as intent and require later diff verification.
- After implementation, run `change-assess --verify-diff <run_id>` before final handoff. If it reports blocked, stop and present the blocking diff evidence.
- Stop after `workflow-plan.md` until human approval is present.
- For `analysis_only` or `decision_support`, run `change-assess --generate-analysis-report <run_id>` and stop after presenting the analysis report unless the user opens a new implementation request.
- Do not generate a technical plan before workflow approval.
- Do not modify business code before technical-plan approval and implementation gate check.

Use command-line review and approval. Do not ask users to manually edit `human-review.yaml`; it is an audit file.
When showing workflow review, include the Chinese progress status bar so the user can see 未执行 / 执行中 / 已执行 and elapsed time per completed step.
Use `change-assess --status <run_id>` for a consolidated dashboard of current gate, artifacts, blockers, and next commands.
Use `change-assess --next <run_id>` to determine the next action. Use `--execute-next` only when the command reports no user confirmation is required.
When a subagent completes a generated task, run that task's `completion_command` or call `change-assess --complete-step <run_id> --module <module> --artifact <artifact> --agent <agent>` so the status bar records completed state, elapsed time, and artifact path.
If `.ai-governance/artifact-schemas.yaml` defines required fields for that module, artifact validation must pass before claiming the step is complete.
Run artifacts in `.ai-governance/runs/` are local audit and gate-state files; keep them gitignored by default. Use `change-assess --cleanup-runs --cleanup-dry-run` before deleting old runs.

```bash
change-assess --review-workflow <run_id>
change-assess --status <run_id>
change-assess --next <run_id>
change-assess --next <run_id> --execute-next
change-assess --approve-workflow <run_id> --add-required threat_analysis
change-assess --add-context <run_id> --include "scope item" --exclude "out of scope" --user-fact "confirmed fact"
change-assess --generate-analysis-report <run_id>
change-assess --generate-agent-tasks <run_id>
change-assess --propose-technical-plan <run_id>
change-assess --review-technical-plan <run_id>
change-assess --approve-technical-plan <run_id>
change-assess --check-gate <run_id> --stage implementation
change-assess --verify-diff <run_id>
change-assess --validate-artifact <run_id> --module dependency_analysis --artifact dependency-analysis.yaml
change-assess --complete-step <run_id> --module dependency_analysis --artifact dependency-analysis.yaml --agent dependency-analyzer
change-assess --review-decision <run_id> --decision reassess --comment "reason"
```

For L3/L4 workflows, use `agent-tasks.yaml` to split work into subagents. Read-only and review-only subagents must not modify files; implementation subagents require `GATE OK`.
