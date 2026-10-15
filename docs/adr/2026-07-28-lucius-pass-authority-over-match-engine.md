---
number: N/A
title: Give the Lucius draft-ops pass authority over the deterministic match engine's output
status: Accepted
date: 2026-07-28
deciders: orchestrating agent (Claude) + repo owner
---

# Give the Lucius draft-ops pass authority over the deterministic match engine's output

## Context

`backend/services/statements/decisions.py`'s `decision_preview` pipeline currently runs the
Lucius instructions pass (`lucius_pass.run_lucius_pass` / `apply_lucius_ops`, SI-68) BEFORE
`_apply_batch_match` (the deterministic near_duplicate/transfer_pair/transfer_leg_echo matcher
in `backend/services/matching.py`). The ordering was deliberate and minimal: a row Lucius
successfully routes to `kind: "transfer"` is automatically excluded from the matcher's own
candidate filter (`draft.get("kind") != "transfer"`), so no override mechanism was needed — a
routed row simply stops being a matching candidate.

That design has two real gaps, both hit live (a reported bug, 2026-07-28, a MoMo statement):

1. **No recourse on a routing miss.** Lucius's pass is a single LLM call with a closed
   3-op vocabulary (`route_transfer`, `set_category`, `skip`), run before the matcher exists —
   it has no visibility into what the matcher would propose. If `route_transfer` doesn't fire
   for a row that should have been one (a model miss — confirmed live: correct, saved
   instructions, a real billed LLM call, matching row text, zero ops returned for the whole
   statement), the row falls through to the matcher with zero protection and no way to
   intervene after the fact.
2. **The matcher is not instructable at all.** Its tolerance bands (`AMOUNT_REL_TOL`,
   `EXACT_AMOUNT_REL_TOL`, wording-similarity, day-gap windows) are fixed global constants —
   there is no per-row or per-account override for "don't match this against my other
   accounts" or "no amount-drift tolerance here," both of which the owner explicitly asked
   for in a real instructions block and neither of which the system can express today.

Owner directive: "I want lucius to have full control to modify the outcome of the match
engine, or apply all instructions... I want it to have that capabilities" — explicitly
including the ability to both reject a match the engine proposed AND assert one the engine
missed (owner's own scoping choice, weighed against a reject-only option — see Alternatives).

Standing invariant this decision must not violate (owner decision 2026-07-17, carried in
`lucius_pass.py`'s own module docstring): "the LLM may shape DRAFTS, under hard validation and
full visibility... the LEDGER stays untouchable — no op can reference or mutate stored
transactions, and commit remains the one write." A model-asserted match must not silently
book anything — it must still ride the exact same reviewed Link/Import/Leave-out decision
every other matched row gets.

## Decision

We will invert the pipeline order and widen Lucius's op vocabulary so it becomes the final
arbiter over the matcher's output, not a blind predecessor to it:

1. **Reorder**: `_apply_batch_match` runs FIRST (cheap, no LLM) in both `decision_preview` and
   the commit-time result rebuild; `lucius_pass.run_lucius_pass` runs SECOND, with the
   post-match `results` (each row's proposed flag, if any) available to build into the prompt.
2. **Two new ops**, added to the closed vocabulary alongside `route_transfer`/`set_category`/
   `skip`:
   - `reject_match` — clears a near_duplicate/transfer_pair/transfer_leg_echo flag the matcher
     proposed for a row; the row falls back to its own normal (unmatched) default.
   - `force_match` — asserts a specific `existing_id` as the row's match when the matcher found
     none or the wrong one. Validated hard (the id must resolve to a real, non-deleted,
     non-archived transaction the row could plausibly reference) and always synthesized at
     **review band, never strong** — nothing algorithmic vetted it, so it must never silently
     auto-merge; the owner still makes the Link/Import/Leave-out call on it like any other
     matched row.
3. **`route_transfer` now also clears any pre-existing match flag** on the row it routes (the
   matcher ran first under the OLD, unrouted kind, so a stale flag from that pass would
   otherwise survive alongside the new transfer shape).
4. Tolerance-band instructions ("no currency drift") are NOT solved by this ADR — `reject_match`
   lets Lucius veto a bad match ROW BY ROW once it sees it, which covers the owner's concrete
   case, but a genuine per-account tolerance override is out of scope here (see Follow-ups).

## Consequences

### Positive
- Lucius becomes reviewable-safety-net for the deterministic matcher, not a blind predecessor —
  a routing miss is no longer unrecoverable.
- The owner's "don't match those" / "this IS the match, book it" instructions become
  expressible for the first time.
- The core safety invariant is preserved by construction: `force_match` only ever adds a
  *candidate* flag at review band; it never mutates the ledger or auto-books anything.

### Negative
- The pipeline reorder changes WHEN a flag exists relative to Lucius routing — every call site
  that assumed "Lucius runs before match" needs its ordering assumption re-checked (both
  `decision_preview` and the commit-time rebuild in `decisions.py`).
- `force_match`'s validation is now the only thing standing between a hallucinated
  `existing_id` and a wrong-but-plausible-looking match reaching the review UI — it must be
  hard-validated (real, undeleted, unarchived transaction) and rendered with full "why" +
  override the same way every other match already is.
- Per-account tolerance overrides (the "no currency drift" case) remain unsolved — a future
  ADR is needed if the owner wants that too.

### Follow-ups
- Consider a per-account/per-row matcher tolerance override if reject_match's row-by-row veto
  proves insufficient in practice.
- Frontend: `reject_match`/`force_match` need their own provenance chip text (mirroring how
  `route_transfer`/`set_category` already show an "applied by Lucius" strip), so a rejected or
  asserted match is visible and undoable, not a silent diff from what the deterministic engine
  would have shown.

## Alternatives considered

- **Reject-only** (no `force_match`) — rejected: the owner explicitly asked for full control,
  including asserting a match the engine missed, not just suppressing bad ones. Lower risk (no
  model-invented existing_id to validate) but doesn't cover the owner's stated need.
- **Keep Lucius before the matcher, add a "don't match me" flag on the draft the matcher
  checks** — rejected: still can't react to what the matcher WOULD have proposed (no
  visibility), so it can suppress but never override toward a better match, and doesn't help
  when a routing attempt itself misses.
- **Let Lucius write directly to the ledger for a force-match** — rejected outright: violates
  the standing "the LEDGER stays untouchable, commit remains the one write" invariant
  (2026-07-17). `force_match` only ever adds a reviewable candidate.

## Agent provenance

- **Decided by:** orchestrating Claude agent (interactive session), repo owner made the scope
  call (reject + force-match) via an explicit either/or question.
- **Lane / branch:** `fix/starting-balance-one-place` (current session branch).
- **Alternatives weighed:** reject-only vs. reject+force-match (owner's explicit choice);
  matcher-side suppression flag vs. full pipeline reorder.
- **Driving artifacts:** live repro against the owner's own MoMo statement
  (`stmt_25c921820000`) — confirmed instructions were saved correctly, the pass ran and billed,
  and still returned zero ops for the whole statement; owner's own instructions text
  (topup/refund routing + "don't match those" + "no currency drift").
- **Why this way (one line):** Lucius can't have "full control over the match engine's
  outcome" while it runs blind, before the engine even produces one — reordering plus a
  narrow, hard-validated override vocabulary gives it that control without breaking the
  ledger-untouchable invariant.
