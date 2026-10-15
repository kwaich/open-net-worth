# 2026-07-31 — A vest and the DELIVERY of its shares are two events, not one

**Status:** Accepted
**Owner:** the maintainer (design approved in-session)
**Touches:** `backend/models/equity.py`, `backend/services/equity.py`,
`backend/services/equity_release.py` (new), `backend/services/positions.py`,
`backend/db/vest_event_store.py`, `backend/api/equity.py`, `backend/ai/tools/equity.py`,
`frontend/src/types/equity.ts`, `frontend/src/components/tabs/equity/*`,
`frontend/src/hooks/usePortfolio.ts`

## Context

The equity model had exactly two states for a grant's units: `locked` (unvested) and
`liquid` (vested, therefore sellable). `vest_apply` moved units between them in one atomic
step — income transaction, FMV lot on the liquid position, locked decrement.

Many plans do not work that way. The shares are handed over some time AFTER the vest date:
broker settlement lag, country-specific delivery rules, blackout/lock-up windows. In that
window the units are genuinely owned — they move with the price and belong in net worth —
but they cannot be sold. Booking them straight into the liquid position claims otherwise.

Owner-reported against a representative holding: a grant whose June-15 tranche was recorded as vested
sat in the liquid position as 600.00 sellable units while the shares had not been delivered.
The one adjacent lever, `tax_event="deferred"`, only skips the income transaction — the
shares still land in `liquid` at vest. It models deferred *taxation*, not deferred *delivery*.

The owner's explicit steer during design: **do not design this for one grant — design it for
all users.** That ruled out anything keyed to a specific plan or jurisdiction.

## Decision

A **two-step vest**, opt-in per grant via `grant.release_required` (default `false`).

**The pending state reuses a shape the codebase already trusts.** The grant's `locked`
position is quantity-only with no lots ("unvested units have no lot" — the original RFC).
Vested-but-undelivered units park in a sibling of exactly that shape, in the **`contingent`**
bucket, which already means "not fully in your control" everywhere it surfaces. So:

| Step | What moves |
|---|---|
| `vest_apply` (release_required) | income txn as usual · locked− · **pending position+** · NO lot |
| `release_apply` | pending− · **FMV lot on the liquid position** · vest event annotated |

Three constraints fix the design:

1. **A release moves SHARES ONLY.** It never creates, edits, or deletes a transaction. The
   income leg is struck by the vest at vest-date FMV, and the lot the release creates is
   back-stamped to the **vest date and vest-date price**. A delivery lag therefore cannot
   move money, change cost basis, or restart a holding period.
2. **All-or-nothing.** A release delivers exactly the units that vested; there is no partial
   or multi-batch delivery, and no `units` parameter to get wrong.
3. **Opt-in, and the default path is untouched.** A grant without the flag takes the old
   one-step code path byte-for-byte, with no new field to fill in. Flipping the flag affects
   only FUTURE vests — it never rewrites a vest already recorded.

`positions_service`'s grant-managed guard was widened to cover the pending position, so its
quantity is as un-hand-editable as the locked position's — both are mirrors of grant state.

`vest_undo` grew a second shape: a still-pending vest drains the pending position (there is
no lot yet); a delivered one deletes its lot as before.

New surface: `POST /api/equity/vest-events/{id}/release`, `GET /api/equity/pending-releases`,
and the `record_release` / `list_pending_releases` tools, so the state is reachable by the AI
from day one rather than being UI-only.

## Alternatives considered

**Create the lot at vest on the contingent position, move it to liquid at release.** Cost
basis would be visible from the vest, and `vest_undo` would keep one shape. Rejected: a lot's
`position_id` is immutable today, so this needs a new `lot.move` primitive on a money choke —
with its own audit, resync, and undo semantics — to buy a display nicety.

**A `released` flag on the lot, one position throughout.** Cheapest to write. Rejected:
`bucket` is position-level in every consumer (`positions_service` rows, `usePortfolio`,
`netWorth.tsx`), so one position would straddle two bands, which no consumer expects — and
the shares would still read as sellable in the positions UI, the exact bug being fixed.

## Consequences

- Liquidity is honest: undelivered units show in the Contingent band at market value, and no
  lot exists for them, so nothing reports them as sellable.
- **During the pending window a holding has no lot**, so its cost basis is not visible until
  delivery. Accepted: the vest event carries every fact needed (units, FMV, currency, date),
  and the lot created at release is struck from exactly those.
- **Not built (deliberate, owner's call): taxation at release.** Jurisdictions that tax on
  inflow/delivery rather than at vest must still correct the income transaction's date and
  amount by hand. The seam is naturally there — `release_apply` is its own choke op, so an
  income leg can be added to it additively — but no code anticipates it today.
- Anything tax-related was explicitly out of scope for this change at the owner's direction.
- The tool-schema budget was raised 380K → 385K (the tenth raise). Prose was trimmed on the
  five affected schemas first, taking the additions from ~3,900 to ~2,700 chars; the
  remainder is the capability. Neither new tool is in CORE, so the cost-sensitive
  default-offer budget is unaffected.

## Agent provenance

Designed and implemented by Claude Opus 5 in-session on 2026-07-31, from an owner question
("have we implemented the case where I vest a stock but my stock is not released yet?").
Three design decisions were taken by the owner during brainstorming and are load-bearing
above: income stays at vest (not at release); the expected release date is optional, per-vest,
and never inferred from a grant-level lag; release is all-or-nothing. The owner then directed
the agent to stop asking and implement, and separately scoped tax out entirely.

Evidence: `tests/test_equity_release.py` (13 tests — opt-in regression guard, no-money-at-
release, vest-date basis, both undo shapes, flag-flip safety), `tests/test_equity_api.py` +5,
`tests/test_equity_tools.py` +4. `make lint` / `make imports` (5 contracts kept) green.
