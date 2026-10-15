# Acceptance — Ledger tracking-start honesty (Phase 1) + data migration

Validate before starting Phase 2. Groups 1 & 2 are the ones to click through in the running app on your real book; Groups 3–4 are API/Lucius; the automated suite already covers the logic.

## Group 1 — The data migration (YOUR live book — check these FIRST)

The migration aligned 8 cash/card accounts (Preserve) and was verified net-worth-neutral (delta +0.00). Backup at `~/.open-net-worth/data-backup-align-tracking-starts`.

- **TS-MIG-1 — net worth intact.** GIVEN the migration ran, WHEN you open Overview, THEN net worth is your real number (unchanged — nothing bigger/smaller).
- **TS-MIG-2 — card balances intact.** WHEN you open Meridian Bank / Harborview Card / Meridian Woman's World, THEN each card's *balance* is exactly what it was before (Preserve rewinds the opening, not the balance).
- **TS-MIG-3 — April card spend now counts.** WHEN you open Harborview Card's register (or the Plan tab for April), THEN your April card transactions now appear — including the Jordan proxy-loan payment (Harborview Card, Apr 5, S$1,200.00) that was hidden before.
- **TS-MIG-4 — start dates moved.** WHEN you Edit Meridian Bank, THEN "Balance as of" = **2026-04-02** (was 05-31); Harborview Card = **04-03**; Meridian Woman's World = **04-12**.
- **TS-MIG-5 — investments/loans untouched.** WHEN you open Meridian Brokerage (SGD) or the Alex/Jordan/Sam loans, THEN their balance AND start date are unchanged (still summarized — correct, not a bug).
- **TS-MIG-6 — April RTA moved (EXPECTED).** WHEN you open the Plan tab for April, THEN Ready-to-Assign differs from before because April card spend now counts. Sanity-check it against reality; net worth is still unchanged.
- **TS-MIG-7 — idempotent / reversible.** (HISTORICAL: the Phase-1 script `scripts/align_tracking_starts.py` was deleted on 2026-07-31 — it wrote `initial_balance`/`initial_balance_date`, which no longer exist. It had already been run; this criterion is closed.) Full undo of THAT migration: `rm -rf ~/.open-net-worth/data && mv ~/.open-net-worth/data-backup-align-tracking-starts ~/.open-net-worth/data`. The current opening migration is `backend/scripts/migrate_opening_txn_pointer.py` — see `docs/qa/acceptance/opening-balance-one-fact.md`.

## Group 2 — New-account defaults (kill the auto-stamp)

- **TS-NEW-1 — zero balance → no curtain.** GIVEN Add account with Current balance = 0, WHEN you save and then add a transaction back-dated a week, THEN it **counts** (balance moves) — no silent hole.
- **TS-NEW-2 — real balance → today.** GIVEN Add account with a nonzero Current balance, THEN its Starting Balance anchors at today without asking for a date.
- **TS-NEW-3 — historical starts remain editable.** GIVEN an existing account needs a historical tracking start, THEN the opening transaction can be edited later in the register; quick create does not ask for a date.

## Group 3 — Preserve-align (API / Lucius)

- **TS-ALIGN-1 — balance preserved.** `POST /api/accounts/{id}/align-tracking-start {"new_date": "..."}` on a cash/card account → response `balance_unchanged: true`; the account's balance is identical, opening rewound.
- **TS-ALIGN-2 — summarizing types refused.** Same call on a brokerage/crypto/loan/property account → 4xx ("summarizes history… would double-count"), unless `allow_summarized: true`.

## Group 4 — Write-time flag (API / Lucius — see Known Gaps)

- **TS-FLAG-1 — pre-start leg flagged.** `POST /api/transactions` dated before a card's start → response includes `warnings: [{type: "before_tracking_start", …}]`; the row is still written (never dropped).
- **TS-FLAG-2 — Lucius parity.** `add_transaction` tool with a pre-start leg → tool result carries the same warning.
- **TS-FLAG-3 — summarizing account NOT flagged.** Same on a brokerage → **no** warning (its pre-start history is summarized by design).
- **TS-HEALTH-1 — endpoint actionable-only.** `GET /api/accounts/tracking-health` on your book → `hidden_total: 0, straddle_count: 0` (the remaining IBKR/loan history is summarized, never counted).

## Automated coverage (already green — run to confirm)

```
.venv/bin/python3 -m pytest tests/test_tracking_start_predicate.py tests/test_tracking_start_detection.py \
  tests/test_tracking_start_align.py tests/test_tracking_health_endpoint.py tests/test_account_no_autostamp.py \
  tests/test_write_and_flag.py tests/test_tool_tracking_flag.py -q
```
Plus: full suite 8732 passed; `make lint` (5 import contracts kept); the shared-predicate refactor is pinned behavior-preserving by the 489 planning/RTA/envelope tests.

## Known gaps (honest — decide if they matter before Phase 2)

1. **The write-flag is API/Lucius-only** — the add-transaction **UI does not render `warnings` yet**, so a person adding a pre-start transaction in the app won't see it (Lucius will). If a person should see it, that needs a small inline surface in the add-transaction form (contextual, actionable — NOT a page banner).
2. **No standing UI surface** for already-hidden transactions (the book-level banner was removed as bad UX). If you want visibility at all, the right shape is a subtle per-account hint on the affected account, with a "fix" affordance — designed, not bolted on.
3. **UI Quality Gate** (`make qa-e2e` + screenshots) was not run for any UI change.
