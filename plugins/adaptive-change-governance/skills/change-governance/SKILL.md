---
name: change-governance
description: Use Adaptive Change Governance before technical planning or implementation. Generate evidence, risk assessment, and workflow plan first; wait for human approval before design or execution.
---

# Adaptive Change Governance

Run from the target repository root:

```bash
change-assess "<user request>" --mode assess --intent-file <intent-file>
```

## 1. Hard rules (MUST)

You are the enforcement. A `PreToolUse` hook blocks file edits on some hosts, but
it only covers editing tools, it can be switched off, and the Codex CLI ships no
hook at all. **Assume no process will stop you.**

1. MUST NOT generate a technical plan before workflow approval exists.
2. MUST NOT modify business code before technical-plan approval **and** a passing
   `change-assess --check-gate <run_id> --stage implementation` (`GATE OK`).
3. MUST NOT write approval or gate-state files by any means — editor, shell
   redirect, or script. These are `.workflow-approved`, `.technical-plan-approved`,
   `human-review.yaml`, `approved-*.yaml`, and verification reports. Only the
   `change-assess` CLI may write them; writing one by hand forges a human approval.
4. MUST stop after `workflow-plan.md` until human approval is present.
5. MUST NOT omit a domain or downgrade a risk hint to avoid a gate. Raise concerns
   through the human review gate, never by shaping the intent file.
6. If you cannot run `change-assess`, STOP and report the blocker. Never proceed on
   an unverified gate.

## 2. What you produce: the intent file

Treat user input as a request, not as code fact. Before assessment, write a YAML
intent file containing:

- `change_kind`, `change_nature`, `summary`, `confidence`, `scope.included`,
  `scope.excluded`, `scope.unknowns`, `risk_hints`.
- `request_goal.type`: `implementation` | `analysis_only` | `decision_support` |
  `planning_only`. Classify before scoring. Analysis and decision-support requests
  stop at their own gate and must not be pushed toward implementation just because
  the wording mentions delete, database, API, or permission terms.
- `relevant_files`: `[{path, reason}]`. **Do the localization the keyword scanner
  cannot** — read the repository and find the files this request actually touches.
  Keyword search fails across a natural-language/code gap (a Chinese request against
  English code). Your `relevant_files` are what let code signals, file risk, and
  reference fan-out run on the real code. Only list paths that exist.
- `domain_hints`: `[{domain, confidence: high|medium|low, reason, anchors: [{path, line}]}]`.
  Judge risk from what the located code *does*. Use domains the guardrails know:
  `financial-calculation`, `physical-device-control`, `authentication`,
  `authorization`, `public-interface`, `message-contract`, `database-schema`.
  Use `confidence: high` only when code evidence is clear — high fires a hard
  guardrail, lower confidence stays a candidate for confirmation. Hints are
  additive: they raise risk, never suppress a keyword or code-signal domain.

Lightweight routing depends entirely on you: there is no keyword fallback. A
copy/menu/comment change is routed lightweight only when the intent declares a
low-risk `change_kind` (`copy_change`, `menu_label_change`, `ui_text_change`,
`documentation_change`, `comment_change`) or `change_nature` (`comment_only`,
`documentation_only`, `display_text_only`, `metadata_only`) **and** sets every risk
hint false. Otherwise a trivial edit scores heavier — the system errs strict rather
than guessing from wording. Declare it only when true; `--verify-diff` checks the
diff against the claim.

## 3. Judgment rules

- Analyze current repository state before scoring risk; apply hard guardrails before
  composing the workflow.
- Weak-only guardrail signals are candidates for confirmation, not hard minimum-level
  triggers.
- Consider configured `file_risk`: a small edit in a database/auth/scheduler/API file
  can need a heavier workflow than a similar-sized UI text edit.
- Distinguish inherent file risk from effective change risk. If a high-risk file is
  changed comment-only, record that as intent and let diff verification confirm it.
- Treat `investigation-questions.yaml` as the bridge from Python UNKNOWNs to agent
  investigation. After workflow approval run `change-assess --next <run_id>`; if it
  recommends `answer_investigation_question`, produce the artifact with
  FACT/INFERENCE/UNKNOWN evidence before technical planning.

## 4. Operating manual

Flow: assess → workflow approval → technical plan → plan approval → implement →
verify diff → reassess → verification report.

- Use CLI review and approval. Never ask users to hand-edit `human-review.yaml`; it
  is an audit file.
- When showing workflow review, include the Chinese progress bar so the user sees
  未执行 / 执行中 / 已执行 and elapsed time per step.
- `--status` gives a dashboard of gate, artifacts, blockers, next commands.
  `--next` gives the next action; use `--execute-next` only when it reports no user
  confirmation is required.
- Technical-plan approval stays blocked while hard-guardrail analysis modules (e.g.
  `business_rule_confirmation`, `threat_analysis`) are unfinished. Complete them with
  `--complete-step` plus an artifact first.
- When a subagent finishes, run its `completion_command` or `--complete-step ... --module <m> --artifact <a> --agent <name>`
  so the status bar records state, elapsed time, and artifact path. If
  `.ai-governance/artifact-schemas.yaml` defines required fields, artifact validation
  must pass before claiming the step complete. For strict schemas, evidence must be a
  list of mappings with `path`, integer `line`, labeled `fact` (`FACT:`, `INFERENCE:`,
  `UNKNOWN:`, `WEAK SIGNAL:`, `DECISION:`), and `confidence: high|medium|low`.
- After implementation run `--verify-diff`, then `--reassess`, then
  `--generate-verification-report` before final handoff. Verification diffs against
  HEAD (staged included) and scans untracked files, so staging or leaving files
  untracked hides nothing. If it reports blocked, stop and present the blocking diff.
- For `analysis_only` / `decision_support`, run `--generate-analysis-report` and stop
  after presenting it unless the user opens a new implementation request.
- For L3/L4, split work via `agent-tasks.yaml`. Read-only and review-only subagents
  must not modify files; implementation subagents require `GATE OK`.
- Run artifacts in `.ai-governance/runs/` are local audit and gate-state files; keep
  them gitignored by default. Use `--cleanup-runs --cleanup-dry-run` before deleting.

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
change-assess --reassess <run_id>
change-assess --generate-verification-report <run_id>
change-assess --validate-artifact <run_id> --module dependency_analysis --artifact dependency-analysis.yaml
change-assess --complete-step <run_id> --module dependency_analysis --artifact dependency-analysis.yaml --agent dependency-analyzer
change-assess --review-decision <run_id> --decision reassess --comment "reason"
```
