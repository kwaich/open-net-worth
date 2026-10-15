---
title: Lot acquire optionally books a cash debit; sale edits undo-and-rebook
status: Accepted
date: 2026-09-11
deciders: orchestrating agent + repo owner
---

# Lot acquire optionally books a cash debit; sale edits undo-and-rebook

## Context

Lots and the cash ledger are separate (INV1 D3). A sale already has an optional
`proceeds_account_id` so recording a disposal does not silently sink net worth
(INV1-2, ADR `2026-08-25-sale-proceeds-are-an-investment-inflow-not-a-transfer`).
Acquire has no matching bridge: booking a buy lot never debits cash, and
Lucius is told to follow up with `add_transactions`. The quote-page Position tab
is becoming a write surface (add/edit/remove lots, add/edit sales). Without a
buy-side picker, the common path (I bought this ticker) inflates net worth.

Realized events are an append-only audit ledger. Editing a sale in place would
desync frozen consumptions from the lots they consumed.

## Decision

We will:

1. Add optional `funding_account_id` on `apply_lot_change(acquire)` (REST +
   `acquire_lots`). When set, the choke books a single-leg `kind=investment`
   **outflow** (`from_account_id`) for `qty × unit_cost + fees`, dated
   `acquired_at`, stamps `lot.txn_id`, and commits in the same unit of work.
   Omitting it books no cash — never auto-debit. Unknown unit cost refuses the
   cash leg only; the lot still records.
2. Delete and lot-amend reverse that cash leg when `txn_id` is set. A
   fully-open lot may be patched (`type: amend`); a partially consumed lot refuses
   identity edits (date/qty/cost/kind) because those facts are already stamped on
   realized consumptions. Note-only remains allowed.
3. Editing a sale is `type: dispose_amend`: restore consumed qty, reverse
   proceeds cash, delete the event, then `compute_disposal` and rebook — one
   `locked_unit_of_work`. A failed new disposal rolls the undo back too. We will
   not mutate frozen consumptions in place.

## Consequences

### Positive

- Quote-page add/edit of buys and sales can keep lots and cash consistent
  without a second ledger call.
- D3 stays: cash is still optional; skip still means lots-only.

### Negative

- Acquire now locks `transactions` when a funding account is set (same shape as
  dispose). Callers that omit funding are unchanged.
- A sale edit mints a new `event_id`; anything that mentioned the old id goes
  stale.

### Follow-ups

- Quote Position tab LotsPanel write UI.
- Product concept `docs/product/concepts/positions-lots-and-cost-basis.md`.

## Alternatives considered

- Two sequential REST calls (acquire then `add_transactions`) — rejected:
  a failed second call leaves net worth wrong; the sale path already composed
  cash into the choke.
- In-place PATCH of a realized event — rejected: consumptions and remaining qty
  would lie about history.
- Auto-debit the position's holding account — rejected: the holding account is
  often securities, not the cash wallet; INV1-2 never auto-picked.

## Agent provenance

- **Decided by:** orchestrating agent (implementation of the quote-page lots plan)
- **Lane / branch:** current working tree
- **Alternatives weighed:** sequential ledger follow-up; in-place realized mutation; silent auto-debit
- **Driving artifacts:** INV1 D3, INV1-2, ADR 2026-08-25, quote-page lots plan
- **Why this way (one line):** Mirror the sale proceeds picker on the buy side, and treat a sale edit as undo+rebook so the audit ledger stays honest.
