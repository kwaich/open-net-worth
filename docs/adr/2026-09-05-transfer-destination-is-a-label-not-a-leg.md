---
title: A share transfer is ONE direction-free "Transfer" row; its destination is a label on the disposal, not a second leg
status: Accepted
date: 2026-09-05
deciders: Claude (hardening-loop turn) + repo owner
---

# 2026-09-05. A share transfer is ONE direction-free "Transfer" row; its destination is a label on the disposal, not a second leg

## Context

The user moved 1242 KSTL from a workplace custodian to IBKR. The model's only shape for "shares
left this account without being sold" is a non-taxable disposal (`is_taxable=false`, zero
proceeds, basis carried out, no gain booked); the receiving side is a separate lot on the IBKR
position. The Activity feed rendered that disposal as `Sell 1242 @ 0 EUR +S$0.00` — a $0 sale
that never happened.

The first fix (`e749c75ca`) gave the feed a `transfer_out` type. The owner rejected the shape:
"should we just have one transfer? … out or in is not relevant cause it's just moving stocks
around the accounts … it was from J.P. Morgan to my IBKR." Direction is not a kind of event;
a move between the user's own accounts is one event with a *from* and a *to*, and the feed
should read like the user thinks: `KSTL · Transfer · 1242 units → IBKR`.

But the stored disposal knew nothing about IBKR. The only ways to get "→ IBKR" onto the row
were (a) infer the destination by pairing the out-disposal with a `transfer_in` lot elsewhere,
(b) build a full two-leg share-transfer choke that mints the receiving lots from the disposal's
consumptions, or (c) record the destination on the disposal itself.

## Decision

1. **One Activity type, `transfer`, with no direction in the name.** The feed emits ONE row per
   non-taxable disposal; the detail carries the direction: `1242 units → IBKR` when the
   disposal names a destination, `1242 units transferred out` when it does not (a gift, a
   write-off, or a move booked before this change). There is no `transfer_in` row and never
   will be from this path — the receiving lot is not an event, it is the other half of the same
   one. `transfer_out` is gone (the API rejects it as an unknown type).
2. **The destination is a LABEL on the disposal (`transfer_to_account_id`), not a second leg.**
   `DisposalCreate` / `dispose_lots` accept an account id OR exact name; the choke resolves it
   through `services/accounts.resolve_account_ref` (unknown → refused with the recovery hint;
   the position's own account → refused, a transfer goes somewhere *else*) and stores the
   resolved id on the realized event. The receiving `transfer_in` lots are still the user's job
   (or a later choke's — see Consequences). Nothing about basis, quantity or cash changes.
3. **A destination is only coherent on a pure share movement.** `compute_disposal` (pure,
   pre-write) refuses `transfer_to_account_id` alongside `is_taxable=true`, a non-zero
   `proceeds_per_unit`, or a `proceeds_account_id` — "a transfer moves shares, not money". This
   keeps the money guard honest: `is_taxable=false` + zero proceeds remains the ONLY $0 path.
4. **Account scoping of the feed matches either side.** `?account_id=<IBKR>` keeps the
   transfer row even though the event's own `account_id` is the source — the receiving account
   asked "what happened to me?" and the answer includes shares arriving. This forced the feed to
   stop pushing `account_id` down to `RealizedEventStore.query_events` (which filters on the
   event's own account) and scope in `_sell_rows`, where both ids are visible.
5. **A vest's FMV is not "Income · 30d".** Found alongside: `_summary_30d` counted vest FMV as
   income, contrary to ACT-7 ("vest events do NOT contribute to any of these summary totals").
   Shares were delivered, no cash arrived; only dividend / coupon / interest count.

## Consequences

- The confirm card (`ChangePreview::DisposalPreview`) shows a "moved to" row with the account
  NAME and is titled "Record transfer"; the Sell form swaps its "Deposit proceeds to" picker for a
  "Moved to" picker (default "Not one of my accounts — gift or write-off", the source account
  excluded) when "Not a sale" is ticked. Both surfaces say, in words, that only the removal is
  recorded here.
- Existing non-taxable disposals have no destination and read `transferred out`. There is no
  update path on `RealizedEventStore`; re-labelling one is `dispose_undo` + `dispose` through the
  chokes (the owner's 2025-07-25 KSTL move is the one candidate).
- The field is deliberately a label, so it can be wrong (the user picks IBKR but books the
  receiving lot in another account) and nothing reconciles it. That is the accepted cost of not
  building the two-leg choke now. The full choke — mint `kind=transfer_in` lots on the
  destination position from the disposal's consumptions with basis and `acquired_at` carried
  over, one DB transaction, one undo — is on the board (`docs/launch/BOARD.md`) and will REUSE
  this field as its destination input, so nothing here is thrown away when it lands.
- `docs/qa/acceptance/investments-activity.md` ACT-7 / ACT-12 / ACT-13 are the acceptance
  criteria; `tests/test_lots_transfer_destination.py` holds the choke's refusals and resolution.

## Alternatives considered

- **Keep `transfer_out` and add a paired `transfer_in` row from `transfer_in` lots** — rejected
  by the owner: two rows with directions in their names for one event the user thinks of as a
  move. It also double-counts in any per-account listing that shows both accounts.
- **Infer the destination by pairing the disposal with a `transfer_in` lot (same instrument,
  same-ish date, same qty)** — rejected: the owner's own IBKR side is `opening` lots with no
  `acquired_at`, so the heuristic fails on the very case that motivated it, and a heuristic that
  silently pairs the wrong lot is worse than an honest "transferred out".
- **Build the full two-leg share-transfer choke now** — deferred, not rejected: it is the right
  end state, but it is a new write path (position lookup/creation on the destination, lot
  minting with basis carry-over, undo of both halves, idempotency) that deserves its own turn
  with its own tests. The label ships the user-visible fix today and is the choke's input later.
- **Store the destination as a free-text note** — rejected: not resolvable to an account, so the
  feed could not filter by it and the choke could not refuse the source account.

## Agent provenance

- **Decided by:** Claude (hardening-loop turn on `main`), shape chosen by the repo owner
  ("Transfer + destination label" over "Transfer only" and the full two-leg choke).
- **Lane / branch:** `main` (direct, per the owner's standing workflow).
- **Alternatives weighed:** paired in/out rows; lot-pairing inference; the full two-leg choke;
  a free-text note.
- **Driving artifacts:** `backend/services/lots.py` (`compute_disposal` pairing refusal,
  `resolve_transfer_destination`), `backend/services/activity.py` (`_movement_detail`,
  either-side account scoping, `_summary_30d`), `backend/models/realized_event.py`,
  `backend/ai/tools/lots.py` (`_DIS_TRANSFER_TO_DESC`, card display),
  `frontend/src/components/shared/DisposalForm.tsx`, `frontend/src/components/chat/ChangePreview.tsx`,
  `docs/qa/acceptance/investments-activity.md` (ACT-7, ACT-12, ACT-13).
- **Why this way (one line):** the user's mental model is one move with a destination, and the
  cheapest honest way to show it is to record the destination where the move is already
  recorded — as a label the future two-leg choke will consume, not a heuristic it would have to
  undo.
