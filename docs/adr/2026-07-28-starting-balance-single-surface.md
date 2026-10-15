# 2026-07-28 — The Starting Balance has ONE editing surface: its own transaction

**Status:** accepted (owner-specified) · AMENDS invariant #4 of ADR
`2026-07-09-starting-balance-as-ledger-transaction` (ledger-tracking-start Phase 2)
**Where:** `backend/services/ledger.py` (`set_account_opening`, `update_transaction`,
`_apply_opening_txn_edit`), `backend/ai/tools/accounts.py` (`_ACCOUNT_UPDATE_FIELDS`,
`_apply_one_account_update`), `backend/ai/tools/_shared.py` (`no_op_write_result`),
`frontend/src/components/tabs/accounts/AccountModal.tsx`, `AccountsTab.tsx`, `pickers.tsx`,
`TxnDetail.tsx`, `accounts.css`
**Tests:** `tests/test_starting_balance_single_surface.py` (16),
`tests/test_starting_balance.py::test_update_opening_txn_takes_the_narrow_edit_path` (rewritten)

## Decision

An account's opening balance is edited in exactly one place: **the Starting Balance
transaction**. The account editor reports it, read-only, and links to that row.

Concretely:

1. **`update_transaction` no longer refuses the opening row.** It takes a narrow path
   (`_apply_opening_txn_edit`): amount and date only, routed through `set_account_opening` so
   the row keeps its id. Delete stays refused, `cleared=True` stays enforced, and any patch key
   that would actually *change* something else — category, kind, description, account, imported
   `external_refs` — is refused. Keys present but unchanged are accepted, because the register's
   edit form re-sends the whole row and that is not a user changing anything.
2. **`set_account_opening` now writes `initial_balance_date`** when given an explicit `as_of`.
3. **The account create form asks for the opening on its own step**; edit mode shows it
   read-only with a link into the register. AI tools keep the one-shot path (everything in a
   single `create_account` / `update_accounts` call) — the split is a manual-flow affordance
   only.
4. **Liability openings are negated at the form boundary.** Storage stays raw-negative.

## Why

Phase 2 made the opening a real transaction but left the account fields wired to it, so one
number had two doors — and the door people could reach was the wrong one. Four defects had
shipped together and only made sense as a set:

- **`update_accounts` could not write `initial_balance_date` at all.** The tool's field
  allowlist excluded it on the belief that `set_account_opening` rerouted it; Phase 2 invariant
  #4 forbade `set_account_opening` from touching it on the belief that the caller owned it. It
  fell into the hole between them. Every call still answered `ok: true`.
- **The row's date and the cutoff could drift apart.** `as_of` moved the transaction but not
  `initial_balance_date`; `is_before_tracking_start` uses a strict `<`, so the opening then
  excluded *itself* from its own fold and the account balance collapsed to **0**.
- **The row refused every edit (409)** — with a message pointing at account fields, which is
  the surface we are now removing.
- **The account form promised auto-negation it never performed** ("Enter a positive number —
  tracked as debt automatically"), so a credit card opened with `5000` typed became a **+5000
  asset**.

The incident: the owner spent a 50-message session (`sessions/dc5f96af-…`) watching Lucius call
`update_accounts` four times, get `ok: true` each time, and see ₫0 on a ₫7.2B property loan. It
was eventually fixed by hand in the very modal this ADR retires — which is the clearest possible
evidence that the fields could not simply be deleted first.

**Why YNAB settles the shape:** YNAB keeps the Starting Balance as an ordinary register
transaction and documents editing it when it is wrong ("The Starting Balance transaction is
wrong", "How to Fix a Starting Balance Error in Loan Accounts"). Making it uneditable — the
first instinct — would have stranded any typo permanently. Reconcile is not a substitute: it
mints a *dated-today* delta adjustment and locks the cleared legs, leaving the wrong opening in
place and every net-worth-history point between the two dates still wrong.

## Amendment to Phase 2 invariant #4

Invariant #4 read: *"`initial_balance_date` is PRESERVED, never nulled by
`set_account_opening` — it never writes that field itself (the caller owns it)."*

The **"never nulled"** half is KEPT: `as_of=None` writes nothing, and a null-date account stays
null-date. The **"never writes it"** half is REVERSED: an explicit `as_of` is written back. The
cutoff and the row's date are two halves of one fact, and the original split is precisely what
let them drift into a zeroed balance.

## Consequences / accepted deviations

- **`Account.initial_balance` (the scalar) STAYS, and is not dead code.** It is 0 on all 29 live
  accounts, but `ai/checkpoint.restore()` rewrites `accounts.json` as **raw bytes**, bypassing
  every store and service — so restoring a pre-Phase-2 checkpoint reinstates a nonzero scalar
  with no opening row. The fold seeds at `ledger.py:102`/`:126`/`:2340` are the back-compat leg
  that keeps those balances correct. Removing them was considered and **rejected**; pinned by
  `test_a_pre_migration_account_still_folds_off_the_scalar`.
- **An import merge can no longer absorb the opening row.** `apply_import_merge` reaches
  `update_transaction` with only `{external_refs, cleared}`, both individually harmless, so the
  narrow path refuses a foreign `external_refs` patch explicitly rather than by accident.
- **A zero-amount edit is refused** rather than silently deleting the row — `set_account_opening`
  implements `amount == 0` as a delete, and delete is refused, so the edit path must match.
- **`update_accounts` now distinguishes "applied but nothing changed" from "nothing to apply"**
  via a before/after snapshot, with a message that explicitly tells the model not to retry.
  `no_op_write_result` grew an optional `message` override for this.
- **The create flow gained a step.** Currency stays on the details step (it is account identity,
  not a balance fact — the first cut moved it and the owner caught it immediately); the balance
  step shows the chosen currency rather than re-asking.
- **`source="opening"` finally has its own glyph and "Starting balance" label** in the register
  and the detail drawer — the cosmetic gap Phase 2 explicitly deferred.

## Agent provenance

Found by reading the owner's failed Lucius session out of `~/.open-net-worth/data/sessions/`
after the report "our AI couldn't really update the account details". The root cause was
confirmed by reproducing it in an isolated `DATA_DIR` before any code was changed — `ok: true`,
stored date unmoved, balance 0.0 — and the REST path proven to differ in the same script.
