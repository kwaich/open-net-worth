# A disposal's cash deposit is an investment inflow, not a transfer

- **Date:** 2026-08-25
- **Status:** Accepted

## Context

`services/lots.py::_apply_dispose` optionally deposits a disposal's net proceeds into a
chosen cash account (`proceeds_account_id`). Until now it booked that deposit as a
`kind: "transfer"` from a synthetic **"Market"** asset account
(`_find_or_create_market_account`, one auto-created per currency) into the destination —
modeled as if cash arrived from an external counterparty account the app itself invented.

This broke on its first real use. A lot-disposal automation ("Log trade & transactions
from emails") booked a genuine sale, and the Market-account bridge:

1. **Was invisible in the ledger UI.** The register's "all accounts" view picks the
   `from_account_id` leg as a transfer's primary account/sign
   (`frontend/.../txnView.ts`). The Market account it picked had been archived
   (`useAccounts()` only fetches non-archived accounts by default), so the row rendered
   as an unattributed negative amount with a blank counterparty — "— → IBKR (USD)" —
   instead of a normal inflow.
2. **Duplicated bookkeeping that was already handled elsewhere.** The disposal's real
   offsetting entry is the position's quantity dropping (tracked in `positions`/`lots`,
   already summed into net worth). The synthetic transfer leg re-did that accounting a
   second way, on an account (`type: "brokerage"`, per `_BROKERAGE_CASH_ACCOUNT_TYPES`)
   whose own ledger balance is *also* summed into `brokerage_cash` — so a transfer
   between the actual holding account and the cash account (the alternative considered)
   would have double-subtracted the same value from net worth.
3. **Had literally never been exercised before.** Checked against the owner's data:
   the Market account this incident created had exactly two transactions ever, both
   from this one bug. Every other "sale proceeds" row in the book — AAPL, PL, LUNR —
   was already a single-leg `kind: "investment"` inflow, the shape
   `services/aggregation/ibkr.py::_trade_to_draft`'s SELL branch uses for a real trade
   fill.

## Decision

**A disposal's deposit leg is a single-leg `kind: "investment"` inflow — no counterparty
account, no synthetic "Market" account.** `_book_deposit_proceeds` now credits
`proceeds_account_id` directly; `_find_or_create_market_account` and
`_resolve_market_account_if_needed` are deleted.

**The leg carries a payee — the destination account's own `platform`** (e.g. "IBKR"),
reusing the existing payee for that venue rather than leaving it blank or minting a
duplicate. Resolution is by NAME (`payee_registry.resolve_payee_id_by_name`, new),
not by the existing `resolve_payee_id`'s alias matcher — a payee's own display name is
never automatically one of its aliases (aliases are seeded from the raw description
that triggered a mint), so alias matching on a clean name like "IBKR" misses an
existing "IBKR" payee and mints a duplicate. No `platform` on the account → no payee,
never mint one from the generic "Sale proceeds" description.

**Payee resolution runs BEFORE `_apply_dispose`'s locked transaction, not inside it.**
`PayeeStore().create()` (the mint path) doesn't accept `conn=` and can't compose into
`locked_unit_of_work`; calling it from inside self-contends against the same thread's
already-open SQLite write lock. `payee_registry.resolve_payee_id`/`_by_name` swallow
all exceptions and return `None`, so this failure was silent — a real deposit landed
with `payee_id: None` and no error. This is the same class of bug already fixed once
for automation/payee writes inside the ledger's own transaction
(`tests/test_ledger_automation_payee_no_self_deadlock.py`); the fix here is the same
shape — resolve first, compose the write with the already-resolved id.

## Consequences

- Every future disposal-with-deposit renders correctly in the ledger UI and carries a
  payee, with no synthetic account cluttering the account list.
- The one "Market (USD)" account this incident created, and its now-superseded
  transactions, are a live-data cleanup handled separately from this code change (see
  the chat session — both phantom disposals were undone and one clean disposal
  re-booked through the fixed choke).
- A transfer between an account's own holding and cash sub-accounts (the other
  alternative raised) was rejected: see point 2 above — it would double-count.

## Agent provenance

Investigated as a live debugging session: two duplicate "Sale proceeds" rows in the
owner's ledger, traced through `txn_audit`/`realized_events`/`email_snapshots` to
an automation reprocessing the same source email across two scheduled runs (unstable
`idempotency_key` format — separately fixed in the automation's own instructions).

Mid-fix, I wrongly concluded — from a shared "UXXX98317" code appearing in both a
"BOUGHT" and a "SOLD" email — that the sale itself was spurious (IBKR notifying one
fill twice), and deleted both disposals instead of only the duplicate. The owner
corrected this: the sale was real. Checked against the account's full trade history,
"UXXX98317" turned out to be a generic code present on every trade the account has ever
made, not a per-trade order id — the inference that led to the wrong conclusion was
unsound from the start. The lot-level fix (this ADR) and the duplicate-lot removal were
unaffected by that error and stand; the ledger data was corrected by re-booking the one
real disposal through the now-fixed choke, and an automation instruction I'd added on
the same wrong premise ("same order id, opposite sides → stop") was removed rather than
kept.
