# Acceptance — an account's opening balance is ONE fact (`opening_txn_id`)

ADR `2026-07-31-opening-balance-is-one-fact` · RFC
`.claude/rfcs/2026-07-31-opening-balance-is-one-fact.md`

The wire contract did **not** move — `initial_balance` / `initial_balance_date` are still the
form fields and still ride every account response. So most of this reads as "nothing changed",
which is the point. The two genuinely new behaviours are **a zero opening is a real row** and
**the tracking start can no longer drift**.

Automated coverage: `tests/test_starting_balance.py` (55),
`tests/test_opening_txn_pointer.py` (12), `tests/test_starting_balance_single_surface.py` (17),
`tests/test_tracking_start_{predicate,detection,align}.py` (23). This file is the
interaction-level pass in the RUNNING app.

## Group 1 — The migration on the real book (do these FIRST)

Backup at `~/.open-net-worth/data-backup-migrate-opening-txn-pointer`.

- **OB-MIG-1 — net worth unchanged.** GIVEN the migration ran, WHEN you open Overview, THEN
  net worth is exactly the number it was before.
- **OB-MIG-2 — every account balance unchanged.** WHEN you open the Accounts tab, THEN all 29
  balances (and their cleared figures) are exactly what they were. The migration REFUSES to
  write if any of them would move, so a completed run is itself the assertion — this is the
  eyeball confirmation.
- **OB-MIG-3 — the 8 revived accounts now show their tracking start.** WHEN you Edit Meridian
  Bank Wallet / Meridian Bank Multi-Currency (EUR) / Harborview Workplace Solutions / Meridian
  Brokerage / CryptoVault / Nguyen Van A / CoinHaven / Test Account, THEN "Balance as of" shows
  the same date as before, and "Open the Starting Balance transaction" now resolves to a real
  S$0.00 row (before, the cutoff had nothing behind it).
- **OB-MIG-4 — the two drifted accounts moved to the asserted date.** WHEN you open the
  Starting Balance row for Parkview Residences Apartment and Parkview Outstanding
  Installments, THEN each is dated **2026-01-01** (the date you asserted), not 2026-02-07 /
  2026-02-06. EXPECTED: the January point of the net-worth history now shows the apartment and
  its offsetting liability; they cancel exactly, so the net-worth LINE is unchanged.
- **OB-MIG-5 — idempotent.** `.venv/bin/python3 -m backend.scripts.migrate_opening_txn_pointer`
  re-run prints "Nothing to do". Full undo:
  `rm -rf ~/.open-net-worth/data && mv ~/.open-net-worth/data-backup-migrate-opening-txn-pointer ~/.open-net-worth/data`.

## Group 2 — Create (the form's own step)

- **OB-NEW-1 — nonzero opening.** GIVEN Add account with a real opening and a chosen "Balance
  as of", WHEN you save, THEN the account's balance is that number AND its register shows one
  "Starting Balance" row, dated exactly at that date, with the Starting Balance glyph.
- **OB-NEW-2 — ZERO opening with an explicit date (NEW).** GIVEN Add account with the balance
  left at 0 and an explicit "Balance as of", WHEN you save, THEN a **real S$0.00 Starting
  Balance row exists** at that date and the tracking start is set. (Before, this silently
  produced a cutoff with no row.)
- **OB-NEW-3 — blank balance, blank date → no curtain.** GIVEN Add account with both left
  empty, WHEN you save and then add a transaction back-dated a week, THEN it **counts** — no
  Starting Balance row, no tracking start.
- **OB-NEW-4 — liability sign.** GIVEN Add card with "you owe" 608.88, THEN the balance reads
  −608.88 and the row is a from-leg.

## Group 3 — Edit the row; the tracking start follows it

- **OB-EDIT-1 — amount.** WHEN you open the Starting Balance row from the register and change
  its amount, THEN the account's balance moves by the delta and the row keeps its id.
- **OB-EDIT-2 — date moves the tracking start (the drift is gone).** WHEN you change the row's
  DATE, THEN re-opening the account editor shows the new date in "Balance as of" — and the
  account's balance is still correct, not 0. This is the exact failure the old two-field shape
  produced.
- **OB-EDIT-3 — zero is allowed now.** WHEN you edit the row's amount to 0, THEN it saves; the
  row survives and the account's balance drops accordingly. (It used to 409: zero meant delete.)
- **OB-EDIT-4 — delete unavailable.** WHEN you open the Starting Balance row, THEN its Delete
  control is disabled and muted. An ordinary transaction's Delete control remains enabled.
- **OB-EDIT-5 — repurpose refused.** WHEN you try to change its kind/category/description or
  move it to another account, THEN 409.

## Group 4 — The register and the plan

- **OB-REG-1 — glyph + "Added by".** WHEN you view the register, THEN each Starting Balance row
  shows the Starting Balance source glyph and "Starting Balance" as Added by — driven now by
  the account's pointer, not a stored `source` string.
- **OB-REG-2 — source filter.** WHEN you filter the register by source = opening, THEN exactly
  the accounts' Starting Balance rows come back (this is how the account editor's "Open the
  Starting Balance transaction" link resolves).
- **OB-RTA-1 — RTA popover.** WHEN you open the Ready-to-Assign popover for a month, THEN the
  "Starting balances" line is the same figure it was before the migration.

## Group 5 — AI surface

- **OB-AI-1 — parity.** `create_account` with `initial_balance` + `initial_balance_date`
  produces the same row and the same response fields as the form.
- **OB-AI-2 — the date is writable and the write is honest.** `update_accounts` with ONLY
  `initial_balance_date` changes the row's date and does **not** report `no_op`.
- **OB-AI-3 — the pointer is refused.** `update_accounts` with `opening_txn_id` returns
  `ok: false` naming the field, and nothing is written.
