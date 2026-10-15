---
number: 2026-09-13
title: RTA breakdown popover reframed as YNAB's flow ledger, literal wording
status: Accepted
date: 2026-09-13
deciders: orchestrating Claude agent + repo owner (owner: "I want everything to match YNAB. So don't make any deviation.")
---

# 2026-09-13. RTA breakdown popover reframed as YNAB's flow ledger, literal wording

## Context

ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category` restored a **STOCK**
Ready-to-Assign identity — `RTA(m) = cash(m) − baseline − held_in_envelopes(m) −
held_outside_envelopes(m) − card_spend_not_covered(m)`, recomputed fresh each month from total
on-budget cash, never carried as a running balance. The RTA breakdown popover
(`frontend/src/components/tabs/plan/budgetRta.tsx`, row-builder in `rtaLines.ts`) presented that
identity as six flat terms: Cash on hand, Held in envelopes, Held outside envelopes, Card
spending not covered, Baseline, Assigned in future months.

The owner asked the popover to instead match real YNAB's own breakdown, confirmed via
`support.ynab.com/en_us/the-plan-header` (search-engine snippets only — the page is JS-rendered
and unfetchable in full): an **Added/Deducted flow ledger** — "Ready to Assign Left Over from
{PrevMonth}", "Inflow: Ready to Assign Transactions in {Month}", "Assigned in {Month}", "Cash
Overspending in {PrevMonth}", "Assigned in {FutureMonth}". This is structurally a **FLOW**
decomposition (previous balance + this month's movement), not the STOCK formula the engine
actually computes.

This app spent the immediately preceding session finding and fixing six variants of one bug
class in this exact area: two computations of "the same" financial quantity silently drifting
apart (cross-currency transfers, archived cards, income refolding — see ADR
`2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end` and
`tests/test_rta_identity_property.py`'s differential-fuzz technique). Building the flow terms as
a second, independently-computed running total would reintroduce exactly that risk. A prior
attempt at this exact problem exists in the repo's history for a DIFFERENT (now-superseded, 2026-07)
RTA identity: `.claude/rfcs/rta-breakdown-popover-spec.md` §10-§11, which folded "Left over from
{PrevMonth}" and "Overspent in {PrevMonth}" into one sign-switching line because showing both
separately "reads as double-counting" under that identity, where the overspend had already
drained RTA in-month.

The owner's own wording ("I want everything to match YNAB. So don't make any deviation.")
explicitly overrode an earlier draft's softer, non-literal naming choice ("Inflow in {Month}"),
made because our `inflow` term is broader than YNAB's own (it also captures new account opening
balances, uncategorized transfers, and FX-lock drift — none of which have a clean YNAB
equivalent).

## Decision

We will render the RTA breakdown popover as an exact **algebraic rearrangement** of the existing
stock identity into YNAB's flow terms — never a second, independently-computed running total —
using YNAB's own literal wording with the real month name interpolated wherever YNAB's own
wording has one:

```
RTA(m) = carried_in + inflow − assigned(m) − cash_overspending_prev − assigned_in_future(m)

carried_in            = _ready_to_assign(ctx, prev)                     — the prior month's RTA
                                                                           BEFORE its own future-
                                                                           assigned clamp
inflow(m)             = Δcash(m) − held_in_envelopes_activity(m)
                          − held_outside_envelopes(m) − card_spend_not_covered(m)
cash_overspending(m)  = Σ_rows max(0, −available(m, row)) − card_spend_not_covered(m)
                          − held_outside_envelopes(m)                   — provably ≥ 0 (proof:
                                                                           design doc §2.4 — the
                                                                           card term cancels
                                                                           exactly against
                                                                           card-caused
                                                                           overspend), which is
                                                                           why it is genuinely
                                                                           "CASH overspending"
```

Two small backend helpers (`_cash_overspending`, `_flow_terms` in `backend/services/planning.py`)
compute these from state `budget_month`'s existing context already has for months `m` and `m−1`,
resolved in ONE `_build_context` call. Two new additive wire fields (`flow`, `cash_overspending`)
carry them — no store, no schema, no migration, no new route.

**Two previous-month lines render SEPARATELY** — "Ready to Assign Left Over from {PrevMonth}"
(Added) and "Cash Overspending in {PrevMonth}" (Deducted) — reversing the retired 2026-07
popover spec's fold, because under the CURRENT (hold-until-month-end) identity the overspend is
charged for the first and only time on THIS screen; folding it away would delete the one place
the hold/reset mechanic can show its work, and the fold's original justification (a
folded value's sign deciding its section, causing flicker) no longer applies now that
`cash_overspending` is provably non-negative.

**Literal YNAB wording, with the real month name interpolated**, per the owner's explicit
instruction — including reversing this task's own earlier "Inflow in {Month}" draft in favor of
"Inflow: Ready to Assign Transactions in {Month}". The ONE line that cannot be literal: "Assigned
in future months" stays generic because it is a clamped aggregate over potentially several later
months — naming a single month there would be a false claim, not merely an imperfect one.

**The desktop rail (`budgetRail.tsx`) keeps the STOCK framing, unchanged.** The rail answers
"where is the money right now" (an always-visible glance); the popover answers "how did I get
this number" (an on-demand click-through). Both self-check against the same `rta` total, so they
can disagree in framing but never in arithmetic. The fresh-start dialog (`freshStart.ts`) is
likewise unchanged — a fresh start makes `month` the floor, so it has no predecessor by
construction, and the flow framing is `null` (falls back to the stock rows) at and below the
floor for exactly that reason.

## Consequences

### Positive
- The popover's wording now matches what a YNAB-migrating user already knows, rather than
  teaching a fourth vocabulary for the same numbers.
- The hold/reset mechanic (an envelope holding its own overspend until month-end) becomes
  visible and verifiable on screen: this month's footnote promise ("$X held until month-end,
  charged to {NextMonth}") is provably kept by next month's own "Cash Overspending in {Month}"
  line — proven in the property test (assertion 7, cross-payload agreement).
- Zero new drift risk: every new term is a rearrangement of already-computed, already-verified
  state, proven via the same differential-fuzz technique (`tests/test_rta_flow_terms_property.py`)
  that caught the prior six-variant bug family, including two independent, non-circular oracles
  (a second `_build_context` for `carried_in`, a raw-transaction Δcash walk for `inflow`).

### Negative
- The popover's `inflow` term is broader than YNAB's literal "Ready to Assign Transactions"
  concept (it also carries new account opening balances, uncategorized transfers, and
  FX-lock drift) — disclosed via the design doc rather than hidden, but a literal read of the
  label overclaims precision relative to what YNAB's own term covers.
- `carried_in` is last month's RTA **before** its own future-assigned clamp, which can differ
  from the number the header actually showed the user last month if a later month has
  assignments, or if foreign cash revalues at today's rate — both are algebraically reconciled
  (visible via the adjacent "Assigned in {Month}"/"Assigned in future months" lines and the
  existing `has_foreign_cash` banner) but add a second thing a careful reader has to reconcile.
- Two now-divergent framings of the same identity exist in the codebase (rail = stock, popover =
  flow) — a future change to the underlying identity must be re-derived and re-proven on BOTH
  builders, not just one.

### Follow-ups
- The "Added"/"Deducted" section headers are an unconfirmed best guess (search snippets
  described the two sections' meaning, never quoted a verbatim heading string) — flag to the
  owner to verify against a real YNAB plan header and correct if wrong.
- `docs/PLANNING_MODEL.md` gains the flow identity (§2.3 of the design doc); `docs/INVARIANTS.md`
  gains the `cash_overspending ≥ 0` theorem; `docs/qa/acceptance/budgeting.md` gains the new
  `BUD-RTA-BREAKDOWN-FLOW-*` criteria; `CLAUDE.md`'s Budgeting invariants section gets one
  sentence noting the popover's presentation-layer rearrangement.

## Alternatives considered

- **Compute the flow terms client-side from two `budget_month(m)`/`budget_month(m−1)` API
  responses** — rejected: monetary math in TypeScript (against CLAUDE.md's "all monetary math in
  `services/`"), and two separate HTTP responses/FX-cache reads/query-cache lifetimes is exactly
  the "two computations of one quantity, drift risk" pattern this session's prior six-bug family
  came from.
- **Fold the two previous-month lines into one sign-switching line**, per the retired 2026-07
  popover spec's own precedent — rejected: that precedent was sound for an identity where the
  overspend had already drained RTA in-month; under the current hold-until-month-end identity the
  fold would delete the only place the mechanic's charge is ever shown, and the flicker risk the
  fold guarded against doesn't apply once `cash_overspending`'s non-negativity is a proven
  theorem.
- **Use our own softer wording ("Inflow in {Month}") instead of YNAB's literal, broader-scoped
  phrase** — this was the FIRST decision (owner initially picked it, recommended because our term
  covers more than YNAB's own) but was explicitly REVERSED by the owner mid-implementation ("I
  want everything to match YNAB. So don't make any deviation.") in favor of the literal phrase,
  accepting the label-overclaims-precision tradeoff named above.
- **Switch the desktop rail to the same flow framing** — rejected (owner decision): the rail's
  job is an always-visible glance at where money currently sits, which the stock framing serves
  better than a flow ledger that needs a predecessor month and hides mid-month state behind two
  previous-month lines.

## Agent provenance

- **Decided by:** orchestrating Claude agent (design by a dispatched solution-architect agent),
  ratified by the repo owner via explicit sign-off on three named decision points, then one
  further owner correction reversing the wording decision mid-implementation.
- **Lane / branch:** `feat/rta-popover-flow-framing`, isolated worktree
  (`.claude/worktrees/rta-popover-flow-framing`).
- **Alternatives weighed:** client-side two-response derivation; folded single previous-month
  line; softer non-literal wording (initially chosen, later reversed).
- **Driving artifacts:** `docs/superpowers/specs/2026-09-13-rta-popover-ynab-flow-framing-design.md`
  (the full derivation and proof); `docs/superpowers/plans/2026-09-13-rta-popover-flow-framing-plan.md`
  (the implementation plan, amended in place for the wording reversal);
  `.claude/rfcs/rta-breakdown-popover-spec.md` §10-§11 (the superseded precedent this ADR
  explicitly re-derives against, not blindly copies); `tests/test_rta_flow_terms_property.py`
  (the differential-fuzz proof).
- **Why this way (one line):** the popover's wording should match what a YNAB-migrating user
  already knows, and it can do so safely only because every new term is a proven rearrangement
  of state the engine already computes and has already been fuzz-tested to be correct — never a
  second, independently-computed number.
