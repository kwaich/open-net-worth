# 2026-07-09 — Starting Balance as a real ledger transaction (ledger-tracking-start Phase 2)

**Status:** accepted (owner-specified design, implemented) · supersedes the scalar
`Account.initial_balance` as the source of truth · see
`docs/superpowers/specs/2026-07-08-ledger-tracking-start-design.md` §4.2 (Phase 1 companion:
`docs/superpowers/plans/2026-07-08-ledger-tracking-start.md`)
**Where:** `backend/services/ledger.py` (`set_account_opening`, `opening_amount`,
`_opening_fallback_date`), `backend/core/txn_sources.py`, `backend/api/ledger.py`
(`add_account`/`update_account`), `backend/ai/tools/accounts.py`, `backend/services/tracking_start.py`
(`align_tracking_start` rework), `backend/scripts/migrate_starting_balance.py`

## Decision

`Account.initial_balance` is no longer the source of truth for an account's opening balance.
It is **input-only** and is **always stored 0** after this change: every REST/AI account
write that carries a nonzero opening reroutes through the new choke,
`ledger.set_account_opening(account_id, amount, as_of)`, which mints (or patches, or deletes)
a single dedicated **Starting Balance transaction** —

```
kind="adjustment", cleared=True, category=None,
external_id="opening:<account_id>", source="opening",
date == account.initial_balance_date (or the null-date fallback),
single leg in the account's native currency:
  amount > 0 → to-leg (asset / positive opening)
  amount < 0 → from-leg (liability / negative opening, raw-negative)
```

`GET`/`POST`/`PUT /api/accounts` (and the matching AI tools) all respond with a **derived**
`initial_balance` — `ledger.opening_amount(account_id)`, the signed amount read back off this
one transaction — so the response shape is byte-identical to before; only the storage
location moved. `account.initial_balance_date` remains a real stored scalar (the cutoff);
only the *amount* relocated.

This is a **value relocation**, not a math change: `account_balance` / `cleared_account_balance`
/ `account_balance_as_of` / `planning._cash_as_of` are untouched. The txn is dated EXACTLY at
the cutoff, so `is_before_tracking_start`'s strict `<` always counts it — the balance fold
sums to the identical number before and after.

The Starting Balance transaction is **structurally protected**: `update_transaction` and
`bulk_delete_transactions` (the two per-txn write chokes) refuse outright (`ConflictError`,
HTTP 409) on any row whose `external_id` starts `opening:`, mirroring how `delete_account` is
refused while transactions still reference it. The filter-based `DELETE /api/transactions`
sweep (`TransactionStore.bulk_delete_where`) also skips it defensively at the store layer.
`set_transaction_cleared` refuses to *uncleared* it (a cleared-only invariant — see below).

A one-shot script, `backend/scripts/migrate_starting_balance.py` (dry-run default, `--apply`,
timestamped data-dir backup, pre-write net-worth-neutrality simulation + a real post-write
recompute), relocates every existing account's scalar. It is idempotent (external-id-keyed)
and was **not** run against live data as part of this change — verified only on a throwaway
clone (see Verification below).

## Why

Per the Phase 1 design doc (§4.2): a scalar opening is engine-opaque, while a real
transaction is a first-class, reconcilable register line that a pluggable budget engine can
reinterpret (assignable cash / RTA inflow / income event) instead of a magic number every
engine has to special-case. This also finally lets opening + reconciliation collapse into one
concept (both are just `kind="adjustment"` rows), and it closes the "the opening never shows
up in the register / audit trail" gap the current design has always had.

## The 4 invariants that keep the fold byte-identical

1. **Sign → leg**, not account `kind` — `amount > 0` → to-leg, `amount < 0` → from-leg
   (mirrors `reconcile()`'s `delta>0→to / delta<0→from` convention exactly, so a caller can
   reason about both the same way).
2. **`cleared=True` always** — an uncleared opening would silently drop
   `cleared_account_balance`, which reconcile diffs against; `set_transaction_cleared` refuses
   to flip it off.
3. **Dated EXACTLY at `initial_balance_date`** — `is_before_tracking_start` uses a strict `<`,
   so a same-date txn always counts; this is what makes the relocation provably neutral.
4. **`initial_balance_date` is PRESERVED, never nulled** by `set_account_opening` — it never
   writes that field itself (the caller — REST/AI/migration — owns it). It stays the cutoff;
   a null-date account stays null-date (no cutoff at all).

## Consequences / accepted deviations

- **Historical net-worth-history divergence, accepted (owner decision).** For a *null-date*
  account, `account_balance_as_of(as_of, …)` used to back-project the scalar unconditionally
  for any `as_of`, even decades before the account existed. Post-relocation, the opening is a
  dated row (earliest own transaction, else today), so an `as_of` *before* that date now
  returns 0 instead of the old back-projection. This is accepted as MORE correct, not
  preserved — pinned by
  `tests/test_starting_balance.py::test_account_balance_as_of_before_null_date_fallback_diverges_from_legacy`.
  The same mechanism can in principle affect `planning._cash_as_of` for a month before a
  null-date account's very first own transaction (a narrow, already-legacy edge case — new
  REST/AI writes always auto-stamp a real cutoff on a nonzero opening since the Phase 1 fix,
  so a null-date *nonzero* account is only reachable via old data).
- **`compute_align` hardened**, not just `align_tracking_start`: the Preserve-align rewind
  math now explicitly skips the account's own `opening:`-prefixed row when folding "newly
  exposed" legs (it's the *current embodiment* of the old opening, never a new leg — folding
  it too would double-count). This mattered even though the common case (an explicit, synced
  cutoff) already excluded it via the date window by coincidence; a null-date account being
  aligned would not have.
- **No `account.created_at` field exists** in this schema, so the design's 3-tier null-date
  fallback (earliest own txn → account created date → today) collapses to 2 tiers here
  (earliest own txn → today).
- **Frontend source-icon glyph deferred.** `source="opening"` is a new, correctly-labeled
  8th value in `DISPLAY_SOURCES`/`_ADDED_BY` ("Starting Balance", not "System"/reconcile), but
  the two frontend `SourceIcon`/`DSourceIcon` renderers (`accounts/pickers.tsx`,
  `accounts/TxnDetail.tsx`) still fall through to the generic "Manual entry" glyph for it —
  this task was scoped to the backend; the fallback is cosmetic (one row per account, never
  wrong data) and left as a follow-up.
- **Not hardened against `external_id` spoofing on create.** `create_transaction`/
  `bulk_create_transactions` do not refuse a caller-supplied `external_id="opening:<id>"` —
  the delete/edit protection guards the two existing-row chokes, not row creation. Considered
  out of scope (an obscure, deliberate adversarial input, not an accidental one).
- **Script location**: placed at `backend/scripts/migrate_starting_balance.py`, matching the
  `backend/scripts/migrate_*.py` convention every other one-shot data-model migration uses
  (`migrate_category_opaque_ids.py`, `migrate_envelope_id_native.py`, …) — not top-level
  `scripts/`, where `align_tracking_starts.py` (the Phase 1 reference script this one's
  dry-run/backup/verify shape is modeled on) happens to live.

## Verification

- `.venv/bin/python3 -m pytest -q -m "not integration"` — 8777 passed, 0 failed (43 new tests
  in `tests/test_starting_balance.py`; 5 pre-existing tests in `tests/test_cost_diet_tools.py`
  updated for the new `svc.transactions`/`svc.opening_amount` reads in `list_accounts`).
- `make lint` — ruff + the 5 import-linter contracts all green.
- Byte-identity on a clone of live data (`~/.open-net-worth`, never written): net worth
  unchanged at 350,000.00 SGD; Ready-to-Assign unchanged for both June 2026 (12,500.00) and
  July 2026 (3,800.00); all 18 accounts carrying a real opening balance relocated cleanly;
  idempotent re-run found nothing left to migrate.
