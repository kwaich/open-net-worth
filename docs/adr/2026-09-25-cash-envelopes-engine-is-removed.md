---
title: The Cash Envelopes budget engine is removed; a home naming it reads as never chosen
status: Accepted
date: 2026-09-25
deciders: orchestrating Claude agent + repo owner
---

# 2026-09-25. The Cash Envelopes budget engine is removed

## Context

Plan › Budget shipped three pluggable engines (RFC `2026-08-22-pluggable-budget-engines`):
`zero_based`, `proportional` (50/30/20) and `envelope`, "Cash Envelopes". The last one was the
strict cash-envelope method: fund a pot per envelope, stop at empty, no Ready to Assign
(RFC `2026-08-23-cash-envelopes-shares-containers-owns-amounts`).

The Budget Onboarding (cards) design the owner adopted on 2026-09-25 offers exactly two
methods, Zero-Based and 50/30/20. That same day the UI stopped offering Cash Envelopes. The
owner then asked for the engine itself to go, not just be hidden ("do #2 right now").

The engine's name also kept colliding with zero-based. YNAB-style "envelope budgeting" IS
zero-based, and Lucius once described YNAB's model when asked to set up the Envelope lens
(ADR `2026-08-23-budget-engine-descriptor-splits-model-note-from-description`). Every
description had to carry disambiguation copy for a method almost nobody used.

A book's home method is persisted as `plan_state.payload.budget.home_engine_id`. Its state
lives in `budget_engine_state`, keyed by engine id. `home_engine_id()` returned the stored
value as-is, so a book whose home is `envelope` would point the Plan tab at an engine that
resolves nothing: every view, verb and draft would 404.

## Decision

We will delete the `envelope` engine outright. That covers `budget_engines/envelope.py`,
`envelope_fold.py`, `envelope_draft.py`, `envelope_config.py`, its registry entry, its
per-engine branches (`budget_lens._PROGRESS_MEASURE_KEYS`, the report's "left" stock column),
its frontend pane (`budgetLens/envelope/`, `MethodExplainer`'s pots diagram) and its tests.
Lucius's budget-engine tools and zero-based's `model_note` describe two engines only. The
note now says outright that popular "envelope budgeting" is zero-based here.

`home_engine_id()` will return a stored id only while it names a registered engine. Anything
else reads as never chosen and takes `_unset_home_engine_id()`'s default: zero-based for a book
that has assigned, 50/30/20 for one that hasn't. Nothing is migrated or rewritten.
`budget_engine_state` rows keyed `envelope` stay where they are, inert, because nothing reads
them.

## Consequences

### Positive
- One fewer paradigm to carry through every tool description, report branch and test fixture.
  The envelope/zero-based naming collision is gone rather than footnoted.
- A book still pointing at the removed engine opens on a working budget, not a 404, with no
  boot migration involved.
- An unknown engine id is now an ordinary `NotFoundError` naming the real ones, the same
  recovery path every other bad reference takes.

### Negative
- Anyone who had configured pots loses the method. Their pot amounts stay in
  `budget_engine_state`, unread; there is no in-app way back to them.
- `GenericEnginePane` (the renderer for an engine with no bespoke UI) has no shipped user
  now. It stays as the extension point for a future engine, and its tests use a made-up
  "Caps" engine.

### Follow-ups
- Historical records (`.claude/rfcs/2026-08-23-cash-envelopes-…`, earlier ADRs,
  `docs/archive/`) are left as written. They describe what was true then.

## Alternatives considered
- **Keep the engine, hidden from the UI** (the state earlier on 2026-09-25) — rejected: the
  owner asked for removal, and a hidden engine still cost every tool description, report
  branch and fixture its upkeep.
- **An Alembic data migration** rewriting `home_engine_id: "envelope"` and deleting the
  `envelope` state rows — rejected: it's destructive and irreversible, and it runs on every
  boot of every install for a value a read-time check already neutralizes.
- **Map a removed home to zero-based unconditionally** — rejected: a book that never assigned
  anything would land on an empty zero-based table. The existing unset default already makes
  the right call for both kinds of book.

## Agent provenance

- **Decided by:** orchestrating Claude agent, on the owner's instruction
- **Lane / branch:** `main` (direct)
- **Alternatives weighed:** hide vs remove; migrate vs read-time fallback; fixed vs unset
  default
- **Driving artifacts:** owner request 2026-09-25; the Budget Onboarding (cards) design
  (Claude Design project `a6dc8c36-30fe-458e-a82b-25596b4369b3`); ADR
  `2026-08-23-budget-engine-descriptor-splits-model-note-from-description`
- **Why this way (one line):** delete the method outright, and make a stale pointer to it
  harmless at the one place it is read.
