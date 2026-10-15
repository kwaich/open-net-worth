---
title: Drop the transaction-level taxable flag
status: Accepted
date: 2026-09-29
deciders: Claude, acting on an explicit owner directive
supersedes: docs/adr/2026-09-21-remove-category-taxability.md
---

# Drop the transaction-level taxable flag

## Context

ADR 2026-08-25 removed the tax feature but kept taxable-income tagging as user data "a future
tax engine will read". ADR 2026-09-21 then removed the category half of that tagging and kept
`transactions.taxable` (tri-state: `None` = income default, `True`/`False` = override) on the
grounds that taxability belongs to an individual event.

Nothing read it. Its only consumer was Planning's `taxable_received` / `taxable_ytd`, which the
frontend typed but never rendered. What remained was carrying cost with no feature behind it: a
promoted column + CHECK, a payload key, REST and Lucius create/update fields (whose description
still pointed at the deleted Tax tab), a mail-automation instruction telling the model when to
set it, and a drawer row. The owner: *"drop it completely — we can introduce it later when we
look into tax, otherwise it's really hard."*

## Decision

Remove `transactions.taxable` end to end:

- **Storage** — revision `202609291600_drop_transaction_taxable` strips the key from every
  transaction payload and drops the column and `ck_transactions_taxable_bool`. On SQLite the
  table rebuild would cascade-delete `transaction_splits` / `transaction_external_refs`
  (`foreign_keys=ON`); both are dumped and restored verbatim around it and verified, the same
  guard as `202608080315_money_decimal`.
- **Contracts** — gone from `models/transaction.py`, the REST create/update bodies, the
  `add_transactions` / `update_transactions` schemas, the change-preview labels and the
  mail-ledger instructions.
- **Readers** — `categories.txn_is_taxable` and Planning's `taxable_received` / `taxable_ytd`
  are deleted; the income view reports received/YTD only.
- **UI** — the TxnDetail "Taxable" row, the stale type fields and two dead pill classes go.

Kept, because they are recorded facts with behaviour, not a tax verdict: disposal `is_taxable`
(false carries basis out and books no gain — a transfer-out/gift), `income_events.tax_withheld`,
`grants.tax_event`, tax lots, and `profile.tax_residencies`.

## Consequences

### Positive

- One less tri-state field on the ledger's widest contract; no model-facing field that points
  at a feature that does not exist.
- A future tax engine starts from a clean slate and designs its own contract.

### Negative / accepted

- Existing explicit overrides (a gift marked non-taxable) are discarded; downgrade re-adds a
  `NULL` column and cannot recover them.
- A stale client or model sending `taxable` has it silently ignored (unknown fields are not
  refused on these bodies).

## Alternatives considered

- **Keep the field, fix the stale tool description** — rejected by the owner: a flag with no
  reader still has to be kept correct on every write path and migration.
- **Drop from the API/tools but leave the column** — rejected: dead storage is the exact
  "kept for later" state this ADR retires.

## Agent provenance

- **Decided by:** Claude, on the owner's explicit instruction in chat.
- **Lane / branch:** local `main` checkout.
- **Alternatives weighed:** description fix only; API-only removal; full removal (chosen).
- **Why this way (one line):** a tax verdict with no reader is cost without value; the tax
  rebuild will define its own facts.
