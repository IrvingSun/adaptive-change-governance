# Adaptive Change Governance Threat Model

This document states, explicitly, **what Adaptive Change Governance (ACG) defends
against and what it does not**. It exists so that neither users nor maintainers
mistake ACG for a security control it is not. A governance tool that is trusted
beyond its real guarantees is worse than none: it manufactures false confidence.

Read this before extending the enforcement layer (the `implementation_gate.py`
hook, artifact validation, diff verification). Every hardening decision — for
example how the hook behaves on ambiguous input, or how far evidence checking
goes — is settled by the posture declared here.

## Posture in one sentence

ACG is a **guardrail against an honest but fallible actor accidentally skipping
its own process**, not a **security boundary against a motivated adversary who
wants to defeat it**. It is advisory-by-construction and **fails open**.

## Assets we protect

1. **Integrity of the gate verdict** — the decision "this change may / may not
   proceed to implementation" should reflect the run's real approval state, not
   a stale, mismatched, or malformed artifact.
2. **Approval-state files** — `.workflow-approved`, `.technical-plan-approved`,
   `human-review.yaml`, `approved-*.yaml`, `run-state.yaml`, and the other files
   in `PROTECTED_RUN_FILES`. These encode "a human said yes." They must be
   written by the `change-assess` CLI, not fabricated by whoever is being gated.
3. **Faithfulness of generated artifacts** — evidence packs, risk assessments,
   and verification reports should be well-formed and internally consistent, so a
   human reviewer reads real structure rather than plausible-looking filler.

## Actor model

The primary actor is an **AI coding agent** driving edits through the host
(Claude Code / Codex). It is assumed to be:

- **Cooperative but fallible** — it follows instructions and honors a `deny`
  decision, but it may forget the process, jump straight to editing, or generate
  confidently-worded evidence that is not actually grounded in the code.
- **Operating with the same file/shell access as the developer.**

The secondary actor is a **hurried human** who wants to ship and may be tempted
to skip review.

We explicitly do **not** model a malicious actor with shell access who is trying
to defeat the gate. Against that actor ACG offers no guarantee (see below).

## In scope — what ACG defends against

| # | Threat | Control |
| - | ------ | ------- |
| I1 | Agent edits code while a governed implementation run has **not** passed its gate (workflow + technical plan approval, diff verification). | `implementation_gate.py` denies `Edit`/`Write`/`MultiEdit`/`NotebookEdit` on `PreToolUse` and points at `change-assess --next`. |
| I2 | Agent forges approval by writing an approval-state file directly (e.g. `echo > .workflow-approved`, or editing `human-review.yaml`). | The hook denies any edit whose target is a `PROTECTED_RUN_FILE`; those files may only be written by the CLI. |
| I3 | A pending, unapproved run is masked by a newer approved run so its gate is skipped. | The hook evaluates **all** active governed runs, not only the most recent; any active run with an unmet gate blocks. |
| I4 | Malformed or partially-written run artifacts cause the gate to read the wrong field and misjudge state. | State reads are parent-scoped (a key is read under its expected parent), not first-match-wins across nesting. |
| I5 | Evidence / risk / verification artifacts pass validation while being structurally empty or citing nonexistent locations. | Schema validation enforces required fields and fact prefixes; evidence spot-checking confirms cited `file:line` references resolve to real locations. |

## Out of scope — what ACG does NOT defend against

These are **accepted, documented gaps**, not bugs. Do not file them as
vulnerabilities and do not let callers assume they are covered.

- **O1 — Bash / shell writes.** The hook matches `Edit|Write|MultiEdit|NotebookEdit`
  only. An actor that writes files through `Bash` (`sed -i`, `>`, `tee`, `cat <<`)
  bypasses the gate entirely, including writes to protected state files. Covering
  Bash reliably is not feasible from a `PreToolUse` matcher.
- **O2 — Disabling the hook.** `ACG_HOOK_MODE=off` (or `warn`) disables/softens
  enforcement, by design, so a blocked developer has an escape hatch instead of
  being locked out. Anyone who can set the environment can remove the gate.
- **O3 — Fabricated-but-plausible evidence.** Evidence checking confirms a cited
  `file:line` *exists*; it does **not** verify that the cited location actually
  supports the claimed fact, nor that the reasoning is sound. A determined agent
  can write true-looking `FACT:` lines pointing at real-but-irrelevant lines.
  Grounding remains the human reviewer's job.
- **O4 — Compromised CLI or repo.** ACG trusts its own `change-assess` code,
  config (`.ai-governance/*.yaml`), and the Python runtime. If those are modified,
  all guarantees are void. There is no signing or tamper-evidence.
- **O5 — Bypassing the host.** ACG only sees tool calls the host routes through
  the hook. Edits made outside the agent session (another editor, another
  process, git operations) are invisible to it.
- **O6 — Risk-scoring completeness.** The risk model can under-rate a change it
  has no signal for (e.g. an implicit runtime dependency, a language it does not
  profile). A low score is "no risk signal found," never "proven safe."

## Fail-open, and why

The hook fails open on every ambiguity: unreadable stdin, missing run directory,
unparseable artifacts, or a run whose governance state cannot be determined all
result in **allow**, not **deny**.

This is deliberate. The realistic failure of a fail-closed gate is that it blocks
legitimate edits on stale or malformed state — and the developer's response to a
gate that blocks everything is `ACG_HOOK_MODE=off`, which destroys the gate
permanently. A guardrail that gets switched off protects nothing. So ACG prefers
to occasionally miss a gate (a fallible agent can be re-instructed) over
routinely blocking honest work (a frustrated human disables it forever).

The corollary for enforcement code: **only deny when the run state positively and
unambiguously says the gate is unmet.** When in doubt, allow and surface, do not
block.

## Residual risk summary

ACG raises the cost of *accidentally* skipping change governance and makes
approvals auditable. It does **not** prevent a motivated actor — human or agent
with shell access — from bypassing it, and it does **not** certify that generated
evidence is true. Treat its verdict as "the process was followed," never as "the
change is safe." Safety remains a human judgment made on top of the artifacts ACG
collects.
