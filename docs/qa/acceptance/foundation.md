# Acceptance criteria — Foundation (ledger + net worth correctness)

The money-math trust contract for the **Foundation** flow (the `① Foundation` lane
in [`docs/launch/BOARD.md`](../../launch/BOARD.md)). These are **computational**
criteria — checkable at the service/API layer (and via the UI where noted) — and
complement the UI-interaction criteria in [`accounts.md`](accounts.md) and
[`cashflow.md`](cashflow.md). The QA agent must exercise every criterion against
the running app / service layer with constructed inputs and report PASS/FAIL per ID.

Base currency in these examples is SGD unless stated. "Net worth" = the value shown
by the net-worth computation (`net_worth()` / `GET /api/net-worth`).

Legend: ✅ = verified by the 2026-06-28 reality audit · 🧪 = lands with the
`lane/F1-F3-foundation-correctness` fixes (verify on merge).

---

### FND-NETWORTH-MULTICCY ✅
- GIVEN asset accounts in SGD, USD, and EUR with known balances and live FX rates
- WHEN net worth is computed
- THEN it equals the sum of each account's balance converted to base via the live
  FX rates (`to_base`), rounded to 2 dp — matching a hand-computed total.

### FND-LIABILITY-SUBTRACTS ✅
- GIVEN a liability account (e.g. a credit card) with an owed balance
- WHEN net worth is computed
- THEN the liability reduces net worth (stored raw-negative, summed as-is).

### FND-TRANSFER-NEUTRAL ✅
- GIVEN two accounts in different currencies
- WHEN a `kind=transfer` moves value from one to the other
- THEN net worth is unchanged (value moved, not created/destroyed); both account
  balances update by their respective legs.

### FND-FX-RECOMPUTE-LIVE ✅
- GIVEN an expense in a non-base currency with a stored `base_amount` snapshot
- WHEN the FX rate changes and totals/balances are recomputed
- THEN displayed base values reflect the NEW rate — `txn_base_amount()` recomputes
  from native leg amounts and never trusts the stored `base_amount`.

### FND-FX-CACHE-CLEAR ✅
- GIVEN a populated FX cache
- WHEN the base currency is changed (`profile.handle_base_currency_change()`)
- THEN the FX cache is cleared and the new base reads as rate 1.0 (clean pivot table).

### FND-SPLIT-SUM ✅
- GIVEN a split expense whose split lines sum to the leg amount (±0.01)
- WHEN split base amounts are computed
- THEN each `txn_split_base_amount = split.amount / leg × txn_base_amount`, and the
  split base amounts sum exactly to the transaction's base amount.

### FND-DELETED-EXCLUDED ✅
- GIVEN a transaction that is soft-deleted (`deleted=true`)
- WHEN account balances and net worth are computed via `account_balance()` / `net_worth()`
- THEN the deleted row is excluded from every fold.

### FND-ORIGINAL-DISPLAY-ONLY ✅
- GIVEN a foreign charge carrying `original_amount` / `original_currency`
- WHEN balances, net worth, splits, and `txn_base_amount` are computed
- THEN those fields are NEVER read by the math — they affect display/audit only.

### FND-TRANSFER-LEGS-ENFORCED ✅
- GIVEN a `kind=transfer`
- WHEN it is created/updated
- THEN both `from_account_id` and `to_account_id` are required, distinct, and must
  reference existing accounts (archived OK); self-transfer and one-legged transfers
  are refused at the choke point.

### FND-CATEGORY-INVARIANTS ✅
- GIVEN transactions of each kind
- WHEN they pass through `create_transaction()`
- THEN expense/income rows always exit with a non-blank `category` (sinking to
  `uncategorized`); transfer/adjustment rows always exit with `category=None`; a
  kind name can never be a category value.

### FND-CUTOFF-RESPECTED ✅
- GIVEN an account with an `initial_balance` + `initial_balance_date`
- WHEN balances are computed
- THEN transactions dated before the cutoff are hard-excluded (pre-cutoff history
  lives only in `initial_balance`).

### FND-ARCHIVED-EXCLUDED ✅
- GIVEN an archived account
- WHEN net worth is computed
- THEN the archived account is excluded from the total.

### FND-RECONCILE-AUDIT 🧪 (F1)
- GIVEN a single account whose cleared balance differs from the entered statement balance
- WHEN `reconcile()` books the balance-adjustment transaction
- THEN the adjustment routes through `create_transaction()` (the write choke point),
  a `create` event for it appears in `txn_audit.json`, and the adjustment invariant
  holds (`category=None`, `cleared=true`).

### FND-BALANCE-ASOF-DELETED 🧪 (F2)
- GIVEN a deleted transaction leg touching an account
- WHEN `account_balance_as_of(account, date)` is computed
- THEN the deleted leg is excluded (parity with `account_balance()`), regardless of
  whether the caller pre-filtered deleted rows.

### FND-LIST-SUMMARY-PARITY 🧪 (F3)
- GIVEN income/expense rows including legacy rows whose stored `base_amount` is `None`
- WHEN the transaction-list totals (`get_transactions` / `GET /api/transactions`) and
  the summary totals (`get_ledger_summary`) are computed for the same date range
- THEN both recompute every non-transfer row via `txn_base_amount()`, no non-transfer
  row is silently excluded, and the two endpoints' `total_income_base` /
  `total_expenses_base` agree to the cent.

---

**Flow is "rock-solid / lockable" when:** every ID above is PASS, the regression suite
(`tests/test_foundation_acceptance.py`, F4) is green in `make check`, and the owner has
signed off on the real numbers in the running app.
