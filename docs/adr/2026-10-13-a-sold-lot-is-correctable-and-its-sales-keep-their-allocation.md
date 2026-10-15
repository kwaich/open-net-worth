---
title: A sold lot is correctable — its sales keep the lots and units they took
status: Accepted
date: 2026-10-13
deciders: orchestrating agent (Claude) + repo owner
---

# A sold lot is correctable — its sales keep the lots and units they took

## Context

A lot that a sale had used was frozen: its date, quantity, cost, fees and kind could not be
edited, because "those facts are already part of the sale". The only way to fix a wrong date or
cost was to undo the sale, edit the lot and record the sale again — which deletes and re-books
the sale's proceeds cash too.

Found 2026-10-13 by the owner on their own book: an opening-balance PLTR lot of 27 shares,
recorded with no purchase date, had been sold in full. Its real date could not be entered, so
the sale's holding period read "Unknown" with no way to fix it short of unwinding the sale. A
user who owns the data should be able to correct it.

## Decision

`lots.apply_lot_change({"type": "amend"})` corrects a sold lot the same way it corrects an open
one, through `services/lot_corrections.py`:

1. **A sale keeps its allocation.** Which lots each sale used, and how many units from each,
   stay exactly as recorded. A correction never re-runs FIFO, so no other lot and no other
   sale changes.
2. **The sale itself is untouched**: quantity, price, fees and its proceeds cash leg. None of
   them depend on the lot, so the ledger is not rewritten.
3. **Only this lot's lines on each sale follow.** A new date updates their `acquired_at` and
   holding-period `term`; a date, kind or note correction moves no gain. A new cost, fees or
   cost currency recomputes those lines' gains and re-sums the sale's `realized_gain`. Its
   `realized_gain_base` is converted at the rate the sale already stored (per-leg when every
   line carries an acquire stamp, else the native gain at that rate) — never at today's rate.
4. **The quantity can't drop below what has been sold** from the lot. That would mean the
   sale took shares that never existed; the refusal says to edit the sale instead.
5. **Remove lot** stays refused for a sold lot (undo the sale first).
6. Linked purchase cash is rewritten **only when a cash-bearing field changes** (quantity,
   cost, fees, date, currency, funding account). Previously every amend — a note included —
   deleted and re-booked the purchase transaction, dropping its category, cleared and
   reconciled state.
7. **Only a real change counts.** The amend choke first drops every field whose value equals
   the lot's current one (`lots._real_changes`). The edit form resends every field on every
   save, and a model may repeat an unchanged one; counting "present" as "changed" made a note
   save restamp the lot's acquire rate and recompute a sold lot's sales at today's rate (code
   review before merge). With fees on the lot, a new quantity is a new per-unit cost, so it
   recomputes the sales too.

`plan_sold_lot_amend` is pure: Lucius's `update_lots` confirm card shows each affected sale's
gain before → after from the same plan the write uses, and the app's Edit lot sheet reports the
sales it updated after saving.

## Consequences

- A user can correct any lot from the app or through Lucius, sold or not.
- A correction can change a past sale's realized gain (cost/fees) — the card and the post-save
  summary name each sale and its before → after, so it is never silent.
- Re-allocating a past sale (which lots it took) is still done by editing the sale, which
  undoes and re-books it.
- Vest lots stay governed by their vest record in Lucius (`update_lots` refuses them).

## Alternatives considered

- **Cascade undo → amend → re-book every affected sale.** Rewrites each sale's cash leg and can
  re-pick FIFO lots for later sales; a date fix could change gains far from the corrected lot.
- **Re-run FIFO over the position's whole history.** Would rewrite sales the user never touched,
  and the original selection (what was available at the time) is not recoverable.
- **Keep sold lots frozen.** The status quo the owner rejected.
