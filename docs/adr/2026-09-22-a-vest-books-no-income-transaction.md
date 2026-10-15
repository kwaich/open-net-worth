---
title: A vest books no income transaction
status: Accepted
date: 2026-09-22
deciders: repo owner + implementing Claude agent
---

# 2026-09-22. A vest books no income transaction

## Context

`equity._vest_apply` posted a `kind="income"` transaction into the grant's custodian account
for every `tax_event="at_vest"` vest — units × FMV, tagged `vest`, with a deterministic
`external_refs=[{source: "vest", ref: "<grant>:<tranche>"}]` — and then ALSO minted the
FMV-basis lot for the shares. `tax_event="deferred"` skipped the transaction.

The income leg was written for a tax report that no longer exists: the tax engine was removed
on 2026-08-25 (ADR `2026-08-25-remove-the-tax-feature`). What survived is
`services/equity_report.py`, a pure per-year vest report that reads the **vest event**, not
the transaction.

**The bug.** `ledger.net_worth()` is an add-everything fold:
`Σ account balances + Σ position market values`. The income leg raised the custodian account's
balance; the lot raised market positions. No leg ever took the money back out. So every
at-vest vest moved net worth by **twice** the vest.

A normal share purchase does not have this problem — `lots._book_funding_debit` posts a
`kind="investment"` "Share purchase" outflow against the funding account, so cash goes down as
the lot goes up. The vest path mints its lot through `equity_release.create_vest_lot` /
`_mint_vest_lot`, which bypasses `_apply_acquire` and therefore `_book_funding_debit` entirely.

Owner report, 2026-09-22: "Why vest create an income?" — the JP Morgan Workplace Solutions
account showed a balance of exactly €17,961.56, the full value of one KSTL vest, in an account
that has never held a cent of cash. Net worth was overstated by ~S$26,242 for that single
vest. Reproduced on a throwaway instance: a 100-unit vest at 50 moved net worth 0 → 10,000.

The exclusion of `vesting_state="unvested"` positions from net worth (ADR
`2026-07-31-unvested-equity-excluded-from-net-worth`) fixed the mirror-image inflation on the
UNVESTED side and explicitly scoped itself out of this one: "`services/equity.py`'s vest path
is untouched."

## Decision

**A vest writes no ledger transaction — whatever the grant's `tax_event`.** `_vest_apply`
mints the lot (or parks the pending-release units), decrements the locked position, and
appends the vest event. Nothing else. `transactions`/`txn_audit` leave its lock set.

A vest is compensation paid in **shares**. The custodian account never receives money, so the
ledger — which models cash — has nothing to record. The shares are recorded as what they are:
a lot, carrying FMV-at-vest basis, counted once in net worth.

Tax at vest is **information, not a money movement**. Every fact needed to compute it lives on
the vest event: units, `fmv_per_unit`, `fmv_currency`, `price_paid_per_unit`,
`gross_gain_native`, the dates, and the grant's own `tax_event`. `equity_report` already
derives `gross_gain_base` from exactly those fields, and only *refined* the figure from the
linked transaction when one existed. That refinement is kept for events that still have a
`txn_id`.

The alternative — booking the missing "Share purchase" debit so the two legs cancel — was
considered and rejected by the owner: it keeps a pair of offsetting rows in the register for
an event where no money moved, to feed a tax report that does not exist.

### Consequences

- Vest compensation no longer appears in the ledger, the Income sub-tab, or any income fold.
  `equity_report` (Equity Comp → vest report) is now the single record of it. Accepted
  deliberately: "treat tax at vest as information for now."
- `tax_event` no longer changes what a vest WRITES; it remains a stored fact, reported by
  `equity_report` (deferred grants stay excluded from the yearly total — a statement about tax
  treatment, not about the ledger) and editable per grant.
- `vest_event.txn_id` and `lot.txn_id` are `None` for new vests.

### Backward compatibility

Vest events recorded BEFORE this change carry a real `txn_id`, and every reader still honours
it: `_vest_undo` soft-deletes that transaction (its `if txn_id:` guard was already there for
deferred grants), and `equity_report` still recomputes base from it. No migration, no
backfill: existing books keep their history exactly as written. An owner who wants the phantom
balance gone deletes that one transaction, or undoes and re-records the vest.

## Verification

`tests/test_equity_service.py::test_vest_moves_net_worth_by_the_shares_only` asserts the
property directly — net worth moves by exactly the vest's market value, and
`accounts_asset` does not move at all. Confirmed to FAIL (18,000 vs 9,000) with the income leg
reintroduced, so it genuinely pins the regression rather than passing vacuously.
`tests/test_equity_release.py::test_undo_still_reverses_a_legacy_vests_income_row` pins the
backward-compatible undo path.
