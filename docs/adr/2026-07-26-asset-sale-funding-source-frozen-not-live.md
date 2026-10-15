---
title: Asset-sale funding sources store a frozen native amount+currency, not a live-priced value
status: Accepted
date: 2026-07-26
deciders: Claude, acting on an explicit owner directive
---

# Asset-sale funding sources store a frozen native amount+currency, not a live-priced value

## Context

While fixing a display bug in the Commitments Actions tab (a derived checklist item showed
"₫10,000" for a funding source that was really worth ~S$10,000 — the commitment's native
currency, VND, slapped onto a base-currency number that was never actually converted to VND),
the investigation traced to the root cause: `asset_sale` funding sources have never stored a
real native amount at all. Every other type (`cash`/`income`/`loan`/`windfall`) stores its own
`amount`+`currency` — real, entered data. `asset_sale` instead stored only `sell_quantity` (a
share count) + `ref_position_id`, and its dollar value was computed **live** everywhere it was
read: `sell_quantity × the position's current market price` (frontend `resolveFundingValueBase`,
backend `services.commitments.live_source_value`), capped at whatever quantity was currently
held.

An interim fix (tag the derived action with the app's base currency instead of the
commitment's) was proposed and rejected by the owner: "what you are doing here is so wrong...
we should store the original currency + amount, and we show from that... otherwise it will be
so wrong later when changing base currency, or agent will misunderstand! the data should be
source of truth." The owner's own framing of the correct model: "what i think we need to store
is just the position, and quantity, and amount, and currency for that amount, because we
should allow user to adjust later... the funding shouldn't be drifted by the market price,
unless it's intentional."

This mirrors a pattern already established elsewhere in this codebase for the identical
class of problem — the ledger's `base_amount`+`base_currency` historical-rate lock (ADR
2026-07-09) and lots' `fx_rate` stamp (ADR 2026-07-15): a stored monetary fact is frozen at
the moment it's decided, not silently re-derived from live market data on every read.

## Decision

`asset_sale` funding sources now store a real, frozen `amount`+`currency` — the amount the user
actually intends to raise from the sale — exactly like every other funding type.
`sell_quantity` stays as informational context (roughly how many shares that was expected to
take) but is no longer read for valuation at all, and is explicitly allowed to drift out of
sync with `amount` if the user edits one but not the other (that's the point — the plan
doesn't silently re-price itself off the market).

Concretely:

1. **Backend** (`backend/services/commitments.py`): `live_source_value(source, base, rates)`
   dropped its `asset_sale`-live branch and its `posval` parameter entirely — it's now the
   identical one-liner every other type already used
   (`svc.to_base(source["amount"], source["currency"] or base, base, rates)`, `0.0` if unset).
   `_position_value_map()` (built `posval` from `positions_service.hydrated()`) became fully
   dead code once its one caller was removed, and was deleted along with all three call-site
   threadings (`projected_events`, `upcoming_events`, `coverage`).
2. **Frontend** (`frontend/src/components/tabs/commitments/fundingValue.ts`):
   `resolveFundingValueBase(f, commitmentCurrency, fx)` mirrors the backend exactly — the
   `asset_sale` branch and its `positions` parameter were removed. `PositionMeta.unitBase`
   (the live per-unit base price) became dead in the same way and was deleted; `positions` is
   still fetched by `useCommitmentDetailData`, but now purely for DISPLAY (the "340 × €38.97
   live" annotation in `fundingWhen()` and the `AssetLink` deep-link), never for valuation.
3. **`derivedActions()`** (`detailActions.tsx`) — the function that turns a funding source into
   a synthetic checklist item — now uses `f.amount`/`f.currency` directly for every type,
   including `asset_sale`. No special-casing, no live lookup, no base-currency relabeling: the
   bug this whole investigation started from is gone because the underlying data is finally
   real.
4. **No manual "add source" UI** (owner: "lucius will be the one who manage it, no ui to add,
   if we have any atm, remove that!") — `AddSourceForm` and the five "+ Cash earmark / + Loan /
   ..." buttons were deleted from the Funding tab entirely. Creating or editing a funding
   source (including deciding what native amount an asset sale targets) is Lucius-only, via
   `set_funding_plan`/`update_funding_source`.
5. **AI tool description fixed** (`backend/ai/tools/commitments.py`): `set_funding_plan`'s
   description used to explicitly instruct the model "valued LIVE at the current price, so
   DON'T pass an amount" for `asset_sale` — now inverted: the model must compute or ask for a
   real target `amount`+`currency` (from the position's current price × `sell_quantity`, or
   work backward from a user-stated target), and is told explicitly that the plan is frozen at
   that figure and won't re-price itself.

## Consequences

### Positive
- The funding plan is now a real plan, not a live spot-price snapshot masquerading as one — it
  can't silently inflate or deflate the "how funded is this commitment" verdict just because a
  stock moved, and a promoted action carries a real, meaningful amount+currency forever.
- Removes a whole class of "value computed from a possibly-missing/renamed position" failure
  modes (`if (!info) return 0`) from the valuation path — the value is just data now.
- Simpler code: `_position_value_map`, `PositionMeta.unitBase`, and the `posval` parameter
  threaded through three backend functions are all gone; `asset_sale` is no longer a special
  case anywhere in the value-resolution path.

### Negative / accepted gaps
- **No backfill for existing data.** An `asset_sale` source created before this change has
  `amount: null` and will now resolve to a `value_base` of 0 (identical to how an unset
  amount on any other type has always behaved) until Lucius sets a real target on it. This is
  a real, visible behavior change for any commitment with pre-existing asset-sale sources —
  deliberately not migrated, since a one-shot script would have to invent a target amount
  (freeze today's live price) that the owner's own stated principle says shouldn't happen
  silently. The user re-plans these via Lucius instead.
- `sell_quantity` and `amount` can now legitimately disagree (e.g. the market moved since the
  plan was set) with no automatic reconciliation — intentional, per the owner's framing, but
  worth knowing if a future reader wonders why they don't match a live price.

## Alternatives considered

- **Freeze a computed native amount at the moment of promotion** (checking a derived action off
  or marking it "not happening") instead of at funding-source creation — rejected: still lets
  the live-priced number drift for the entire time the source sits unpromoted in the funding
  plan, which is exactly the drift the owner objected to; the plan itself needs to be frozen,
  not just its eventual promoted action.
- **Store share count + price + rate used, so the record is self-documenting about how the
  amount was derived** — a reasonable heavier alternative the owner was offered; not chosen in
  favor of the simpler "just amount + currency, same shape as every other type" model the
  owner asked for directly.
- **Keep live valuation, fix only the display bug** (tag the derived action with the correct
  currency) — this was the interim fix in flight when the owner stopped it; rejected because it
  treats the symptom (a mislabeled number) without fixing the actual defect (the plan silently
  re-prices itself against the market on every read).

## Agent provenance

- **Decided by:** Claude, on an explicit owner directive across several turns — first
  identifying the display bug, then having an interim fix (tag with base currency) explicitly
  rejected ("what you are doing here is so wrong"), then implementing the owner's own stated
  design ("store just the position, and quantity, and amount, and currency for that amount").
- **Alternatives weighed:** freeze-at-promotion vs. store full derivation provenance vs.
  display-only fix vs. the chosen frozen-amount-at-the-source model.
- **Why this way (one line):** matches the owner's explicit instruction and the codebase's own
  existing historical-rate-lock precedent (ledger `base_amount`, lot `fx_rate`) — a stored
  monetary fact is frozen at write time, not re-derived from live data on every read, unless the
  user intentionally changes it.
