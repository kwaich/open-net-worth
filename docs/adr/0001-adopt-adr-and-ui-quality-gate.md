---
number: 0001
title: Adopt ADRs and a blocking UI quality gate
status: Accepted
date: 2026-06-15
deciders: orchestrating agent (Claude) + repo owner
---

# 0001. Adopt ADRs and a blocking UI quality gate

## Context

This is an agent-built repo: most changes land via autonomous agents on feature branches. Two
failure modes kept recurring and cost the owner real time:

- **(a) Untested UI.** UI changes and interactions shipped without being verified against the
  *running* app. The build passed and the diff looked right, but the screen was broken or the flow
  didn't work — so the owner had to test every frontend change by hand.
- **(b) Lost rationale.** The reasoning behind an agent's build choice evaporated into the diff.
  A later agent (or the owner) couldn't tell *why* an approach was taken, and risked relitigating
  or accidentally reversing a settled decision.

Both are structural: they recur because nothing in the process makes them hard to repeat. The
repo already values terse, high-signal docs and has heavier instruments (RFCs in `.claude/rfcs/`,
PRDs in `docs/prd/`) — but those are pre-build proposals, not a lightweight record of decisions
*already made*, and neither addresses UI verification.

## Decision

We will:

1. **Adopt this ADR system** at `docs/adr/` as the record of significant, irreversible, or
   convention-deviating decisions — append-only, numbered, each carrying an **Agent provenance**
   block so an agent's reasoning survives the diff.
2. **Make UI verification a blocking, evidence-bearing gate** per `docs/UI_QUALITY_GATE.md`: a
   frontend change cannot merge without an **acceptance file**, a **green UI run**, and
   **screenshot evidence**.

## Consequences

### Positive
- Decisions become recoverable: the *why* lives next to the *what*, not buried in commit history.
- No more untested UI reaching the owner — verification happens before merge, not by hand after.
- Clear, single ownership of UI verification (the gate), instead of each build agent guessing.

### Negative
- More process overhead on every UI change (acceptance file + run + screenshots).
- ADR discipline is required: agents must recognize the trigger and actually write the record.

### Follow-ups
- Update `CLAUDE.md` "Multi-agent orchestration (standing rules)" and the Definition of Done to
  require an ADR on triggering decisions and the UI gate on frontend changes.
- Give `ui-designer` a verify-build mode (spec → implement → verify against the running app).
- Make the `qa-engineer` gate **blocking** for frontend changes.

## Alternatives considered

- **Keep decisions in commit messages / PR descriptions** — rejected: not discoverable, not
  append-only, and easily lost or rewritten; no place for agent provenance.
- **Rely on RFCs only** — rejected: too heavy for routine decisions, and an RFC is a *pre-build
  proposal*, not the decision of record; many decisions are made mid-task with no RFC at all.
- **Keep UI verification advisory** (status quo) — rejected: advisory verification is exactly what
  let untested UI through and caused the pain; only a blocking, evidence-bearing gate changes the
  outcome.

## Agent provenance

- **Decided by:** the orchestrating Claude agent (multi-agent orchestrator role).
- **Lane / branch:** `chore/agent-ready-cleanup`.
- **Alternatives weighed:** commit/PR prose vs. RFC-only vs. advisory UI checks vs. ADRs + a
  blocking UI gate (chosen).
- **Driving artifacts:** `docs/UI_QUALITY_GATE.md`; `CLAUDE.md` → "Multi-agent orchestration
  (standing rules)".
- **Why this way (one line):** the smallest durable mechanism that makes both failure modes
  structurally hard to repeat.
