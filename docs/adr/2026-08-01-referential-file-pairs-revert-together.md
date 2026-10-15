# 2026-08-01 — Referential file pairs revert together, and the write choke heals the pointer

**Status:** accepted · **AMENDS** ADR `2026-07-31-opening-balance-is-one-fact` (the "a dangling
pointer RAISES" consequence — still true of every READ, now explicitly not true of the write
choke)
**Where:** `backend/ai/checkpoint.py` (`_COMPANIONS`, `CheckpointContext._capture`),
`backend/services/ledger.py` (`_opening_row_to_patch`, `opening_for_edit`,
`set_account_opening`), `backend/services/accounts.py` (`_rollback_created_account`),
`backend/api/ledger.py`, `backend/ai/tools/accounts.py`
**Tests:** `tests/test_checkpoint_hook.py` (3 new), `tests/test_opening_txn_pointer.py`
(`test_the_write_choke_heals_a_dangling_pointer`,
`test_a_dangling_pointer_still_raises_on_every_read`,
`test_the_edit_side_resolve_carries_the_missing_field_without_raising`,
`test_a_failed_mint_leaves_no_unpointed_starting_balance`)

## Context

Since `opening-balance-is-one-fact`, `accounts.json` holds a POINTER
(`opening_txn_id`) into `transactions.json`, and a dangling pointer raises
`DataIntegrityError` on every money read — deliberately, because degrading to `None` would
un-curtain an account's pre-start history and silently move its balance.

A security review found the state was both **reachable** and **unrecoverable**:

- A checkpoint captures only the files a turn wrote, and `restore()` writes back only those. A
  turn that wrote `transactions.json` alone therefore produced a checkpoint whose restore
  reverted the rows and left `accounts.json` aimed at a row that no longer existed.
  `restore_checkpoint` is a **model-callable tool**, so Lucius could do this to the book on its
  own initiative.
- Every repair surface then raised on the way in: `set_account_opening` resolved the existing
  row through the loud read *first*, and `PUT /api/accounts/{id}` / `update_accounts` /
  `align_tracking_start` all resolve the current opening before writing. `opening_txn_id` is
  server-owned and refused on both write paths, so it could not be cleared either. **A
  hand-edit of `accounts.json` was the only way back.**

## Decision

**1 — Two files that point at each other are ONE checkpoint unit.** A turn that writes either
`accounts.json` or `transactions.json` captures both (`_COMPANIONS`, keyed by file name and
resolved beside the file being written, so it holds under any `DATA_DIR`). A restore therefore
reverts the pair atomically and cannot mint a dangling pointer. Cost: one extra deduped blob
per turn, on a 29-row file.

**2 — Reads stay loud; the WRITE CHOKE heals.** `set_account_opening` resolves the row it will
patch through `_opening_row_to_patch`, the one tolerant lookup in the codebase: a pointer at a
missing or deleted row means "there is no row", so it re-mints and re-points — which IS the
repair. Nothing is lost (the row it named is already gone), and a `WARNING` names the account.
Every read that REPORTS a balance (`opening_amount`, `opening_date`, `account_with_balance`,
`balances`, `net_worth`) still raises.

**3 — An edit that omits a field fills it from the write-side resolve.** `opening_for_edit`
returns `(amount, date)` through the same tolerant lookup, and the REST PUT and the
`update_accounts` tool use it instead of `opening_amount`/`opening_date`. Otherwise an edit
that sends only the amount would raise on the very pointer the call was about to repair.
`update_accounts`' before-snapshot degrades to `None` on an unhydratable account rather than
raising, so a repair is reported as the real change it is.

## Alternatives rejected

- **Make reads tolerant instead.** That is the whole point of the original invariant: a silent
  wrong net worth is worse than a loud outage.
- **Let `restore` re-point or drop dangling pointers.** A restore writes raw bytes past every
  store by design; teaching it one domain's referential rules starts a list that will never be
  complete. Capturing the pair keeps the mechanism dumb and general.
- **Exclude `accounts.json` from checkpoints.** It would make "Lucius created an account"
  un-undoable, and the pointer could then dangle the other way.

## Consequences

- `align_tracking_start` still raises on a dangling pointer, deliberately: it computes a rewind
  FROM the current opening, so there is nothing honest for it to compute. Repair via
  `set_account_opening` (or the account editor) first, then align.
- `create_account`'s rollback now also deletes any transaction it minted. `set_account_opening`
  writes the row before the pointer, so a raise part-way through could otherwise leave a live,
  unpointed "Starting Balance" — an ordinary unprotected `adjustment` that double-counts
  against the retry. For the same reason the pointer write moved ABOVE the audit write (the
  audit store fails closed, so it is a real raise site).
- Any future pointer BETWEEN two stores must be added to `_COMPANIONS` or it inherits this bug.

## Agent provenance

Fixed by a Claude agent (senior-engineer lane, branch `fix/starting-balance-one-place`) from a
security review of the `opening_txn_id` migration; the reviewer reproduced both halves before
any code was written. The judgement call worth recording: the reviewer asked only for
`set_account_opening` to heal, but healing only there leaves the REST and AI repair paths
raising on their pre-read, so the tolerant resolve was given a named public form
(`opening_for_edit`) and wired into both — a repair surface that raises before it writes is not
a repair surface.
