# 2026-07-31 — An account's opening balance is ONE fact: `opening_txn_id`

**Status:** accepted (owner's own proposal; RFC
`.claude/rfcs/2026-07-31-opening-balance-is-one-fact.md` approved) · **COMPLETES** ADR
`2026-07-28-starting-balance-single-surface` · **SUPERSEDES** invariants #3 and #4 of ADR
`2026-07-09-starting-balance-as-ledger-transaction` and its `Account.initial_balance`
consequence
**Where:** `backend/models/account.py`, `backend/db/account_store.py`,
`backend/db/transaction_store.py`, `backend/core/txn_sources.py`, `backend/services/`
(`ledger.py`, `accounts.py`, `tracking_start.py`, `planning.py`, `cashflow.py`,
`portfolio_history.py`, `portfolio_rows.py`, `reports.py`, `targets.py`, `audit.py`,
`import_anomalies.py`, `ledger_preview.py`, `mentions/providers.py`), `backend/api/`
(`ledger.py`, `briefing.py`, `chat_suggestions.py`), `backend/ai/tools/`
(`accounts.py`, `ledger.py`), `backend/scripts/migrate_opening_txn_pointer.py`
**Tests:** `tests/test_starting_balance.py` (55, rewritten),
`tests/test_opening_txn_pointer.py` (12, new),
`tests/test_starting_balance_single_surface.py` (17, rewritten),
`tests/test_tracking_start_{predicate,detection,align}.py` (23, rewritten)

## Decision

An account stores **one** opening field:

```jsonc
{ "id": "acc_…", "opening_txn_id": "txn_…" | null }
```

Everything else is derived from the pointed-at row: the **amount** is its signed leg, the
**tracking start** is its `date`, and `null` means no tracking start at all.

**Deleted from the data**, not deprecated: `Account.initial_balance`,
`Account.initial_balance_date`, the row's `source: "opening"` column, and its
`external_refs: [{"source": "opening", "ref": <account_id>}]` entry. The row now stores
`source: "manual"` and no refs. `backend/scripts/migrate_starting_balance.py` and
`scripts/align_tracking_starts.py` are deleted — both read fields that no longer exist.

The predicate becomes pure — `is_before_tracking_start(leg_date, opening_date)` — and the three
folds take the cutoff as a required argument (`account_balance(account, txns, opening_date)`,
likewise `cleared_account_balance` and `account_balance_as_of`). Four helpers in
`services/ledger.py` resolve it: `opening_txn_ids`, `opening_index`, `opening_date`,
`cutoffs_by_account`.

**One marker, one read path.** No fallback, no dual read, no absent-field tolerance. A one-shot
destructive migration put the live 29 accounts into the new shape; after it ran, the old shape
does not exist and the code does not know it ever did.

## Why

### One fact was recorded in four places, and they drifted

`description: "Starting Balance"` (user-editable), `source: "opening"` (a *row purpose* stuffed
into a *provenance* field), `external_refs: [{"source": "opening", "ref": <account_id>}]` (a
*uniqueness constraint* wearing an *identity field's* clothes — the "ref" is our own internal
account id), and `account.initial_balance_date`. The `2026-07-28` ADR removed the second
editing *surface*; this removes the second storage *location*.

The drift was live and its mechanism was a one-line asymmetry: the row was *always* dated, but
the account's cutoff was written only when `as_of` was truthy. Because
`is_before_tracking_start` uses a strict `<`, an opening dated before a stale cutoff **excludes
itself from its own fold and collapses the account balance to 0** — observed twice on a ₫7.2B
account. With one storage location that state is *unrepresentable*, not merely guarded.

### The `amount == 0` delete branch was the bug behind half the mess

`set_account_opening` treated a zero opening as "nothing to relocate" and soft-deleted the row
while leaving the cutoff standing. That is migration-era reasoning: on the write path
`amount == 0` means **"my balance was zero when tracking started"** — a real fact. The live book
carried the receipts: **8 accounts with a tracking start and no opening row**, and one checking
account with **four soft-deleted rows all bearing the same `("opening", account_id)` pair and
zero live ones** (`by_external_ref` filters `deleted`, so each zero call tombstoned and each
nonzero call minted afresh). That pair could never express uniqueness. A scalar pointer gets
uniqueness from the *schema*.

### Why a pointer on the account, and not the alternatives

| Option | Why not |
|---|---|
| `kind: "opening"` | 57 sites branch on `kind == "adjustment"`; a 6th kind touches every fold, filter, UI switch and AI schema. |
| `is_opening: bool` on the row | No O(1) lookup by account, and it permits two opening rows on one account. |
| Keep the `opening:` ext ref | A provenance namespace holding an internal id, and it demonstrably cannot express uniqueness. |
| Derive from the tracking start | Ambiguous — "the earliest adjustment at the cutoff" also matches a same-day reconcile. |
| **Pointer on the account** | O(1) both directions; uniqueness is a schema property, not a query convention. |

### Why the predicate takes a date instead of the account

Carrying the old `(leg_date, account)` signature forward would mean the fold reads a key that
may or may not be there, and a caller that forgets to supply it gets `None` and *silently
counts pre-start legs*. Passing the date makes that a call-site error the type checker catches,
at zero runtime cost. All 30 fold sites and all 8 predicate sites can supply it — verified, no
exceptions. Callers that loop accounts hoist one `cutoffs_by_account(accts, txns)` out of the
loop, which is also the only shape that avoids an O(accounts × txns) index rebuild. The four
synthetic `{"initial_balance_date": …}` dicts `planning.py` used to fabricate purely to satisfy
the old signature are gone.

### Why a dangling pointer RAISES

`opening_date()` — the one resolver every fold goes through — raises `DataIntegrityError`
naming the account and the id when the pointer resolves to a missing or deleted row. Returning
`None` would silently un-curtain the account's pre-start history and move its balance. **A loud
outage is fixable; a wrong net worth is not.**

## Consequences

- **ADR `2026-07-09` invariants #1 (sign → leg) and #2 (`cleared=True`) are kept verbatim.**
  Amount `0` books a to-leg by convention.
- **#3 (dated exactly at `initial_balance_date`) becomes a tautology** — the row's date *is* the
  tracking start.
- **#4 (`initial_balance_date` preserved, never nulled) is retired**, along with the field.
  `test_a_pre_migration_account_still_folds_off_the_scalar` is deleted: it pinned a back-compat
  leg that the migration replaces.
- **Two correctness fixes rode along**, found while verifying the shape:
  - `resolve_transaction_row` stamped `base_currency` under `if base_amount is not None and
    src_amt:`. `0.0` is falsy, so a zero-amount row stored `base_amount=0.0` with
    `base_currency=None` — the exact "unstamped legacy row" shape the historical-rate lock
    distrusts. Now `src_amt is not None`, with `fx_rate` left `None` at zero (a rate is
    undefined there; `0.0` would be a lie).
  - `services.accounts.create_account` makes account creation atomic (account → row → pointer,
    compensating delete on failure) instead of two store writes from a router — which also
    clears a standing *"routers call ONE service"* violation.
- **`opening_txn_id` is server-owned.** Absent from `AccountCreate`/`AccountUpdate` and from
  `_ACCOUNT_UPDATE_FIELDS`, and explicitly refused by
  `services.accounts.assert_no_server_owned_fields`: a caller-settable pointer hands one account
  another account's row, i.e. another account's balance.
- **The wire contract did NOT move.** `initial_balance` / `initial_balance_date` remain input
  parameters on REST + AI and remain derived response fields on `account_with_balance`. The
  frontend `data.ts` mapping and `AccountModal.startingBalance.test.tsx` are untouched — that is
  the proof.
- **`portfolio_history.inception_date` simplifies** to `min(txn dates)`: every tracking start is
  now itself a transaction date, so the account term was redundant.
- **One deliberate weakening, named.** The filter-sweep exemption moved from an unconditional
  in-store check to a `protected_ids` parameter the service choke
  (`ledger.delete_transactions_where`) supplies. The guard now holds only if the caller passes
  it. Chosen over `transaction_store` importing `account_store` — which would be the first
  cross-store import in `db/` and would teach the transaction store about accounts — and backed
  by an AST test pinning `bulk_delete_where` to exactly one caller.
- **`ai/checkpoint.restore()` writes files as raw bytes**, so restoring a pre-migration
  `accounts.json` reintroduces the old shape. The migration script is idempotent and
  re-runnable; run it.

## The migration, and what it proved

`backend/scripts/migrate_opening_txn_pointer.py` — dry-run default, `--apply`, data-dir backup.
Per account: adopt the live row (18); adopt and **re-date it to the account's cutoff** on a
drift (2 — the account field is the user's assertion, the row's date came from
`_opening_fallback_date`, an inference); mint a zero row at the cutoff where the cutoff had
nothing behind it (8); leave the pointer null (1). Tombstoned opening rows are hard-deleted (4,
one account).

**The deliverable is the refusal, not the write.** Before touching disk it computes every
account's balance and cleared balance with the OLD fold, simulates the whole migration in
memory, recomputes with the NEW fold, and **refuses if any of them moves** — which is what
catches the inverse-drift case (`account_cutoff > row_date`, the self-excluding collapse) and
hands it to a human instead of silently repairing it into a different number. It re-verifies
after writing, and refuses outright on a surviving nonzero legacy scalar or on two live opening
rows for one account.

Result on a real production book: **all 29 account balances and all 29 cleared balances
byte-identical**, net worth identical, and Ready-to-Assign identical for three months. The only
figures that moved are the two drifted accounts' *historical* as-of points for January 2026:
the apartment (+₫3,500,000,000) and its offsetting installment liability (−₫3,500,000,000) now
appear from the tracking start the user actually asserted (2026-01-01) instead of the inferred
2026-02-06/07. They cancel exactly, so **net worth is unchanged at every point of the history**
— only the band decomposition of that one month changes. That movement *is* the drift repair.

> **Correction (2026-08-01, ADR `2026-08-01-migrations-write-the-new-fact-before-deleting-the-old`):**
> they do NOT cancel on every day. The two rows carried *different* inferred dates (02-07 and
> 02-06), so on **2026-02-06** the liability was counted and the asset was not: measured day by
> day on a copy of the live book, that one date moves by **+SGD 336,007.52** (old −336,007.52 →
> new 0.00). Every other day in the window does cancel. The migration removes a spurious one-day
> −SGD 336k dip — the right outcome, but it was asserted rather than measured. The script now
> prices the whole re-dated window per account and refuses `--apply` without
> `--accept-history-shift`.

## Agent provenance

Implemented end-to-end by a Claude agent (senior-engineer lane) from the approved RFC, on the
owner's standing directive: *"i don't want any fucking dirty code to support old behaviors or
past data or whatever. if data is not suitable, migrate data!"* Single user, one live book,
self-hosted — so no back-compat shim was written anywhere; where the old data didn't fit, the
migration changed the data.

Two judgement calls the RFC did not spell out, made by the agent and recorded here:

1. **`tracking_start.compute_align` and `flags_for_txn` take their facts as parameters.**
   `compute_align` used to read `initial_balance`/`initial_balance_date` off the account dict,
   and `align_tracking_start` fabricated a synthetic `{**acct, "initial_balance": …}` to feed
   it — exactly the fabrication this RFC exists to kill. Both now receive `old_start` /
   `old_opening` (and `txns`) from the caller, which keeps them pure and testable instead of
   reaching into a store mid-function.
2. **`opening_amount` takes the ACCOUNT DICT, not an account id.** The RFC wrote
   `find_by_id(account["opening_txn_id"])`, which needs the account anyway; every caller
   already held it. Taking the id would have forced an accounts-store read per account inside
   `account_with_balance`, re-introducing the N+1 the old `txns` parameter existed to avoid.
   `opening_index(txns, accts)` is the shared-index escape hatch for list endpoints.
