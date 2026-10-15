---
title: Force-deleting an account hard-removes its referencing transactions, not just soft-deletes them
status: Accepted
date: 2026-09-06
deciders: orchestrating agent (Claude) + repo owner
---

# 2026-09-06. Force-deleting an account hard-removes its referencing transactions, not just soft-deletes them

## Context

`delete_account` refused (409) whenever ANY non-deleted transaction referenced the account,
pointing the user at archive instead. Two gaps: (1) an account whose ONLY reference is its own
Starting Balance row was refused identically to one with real history, even though that row isn't
"history" anyone would want kept once the account itself is gone; (2) there was no way to delete
an account WITH real history short of archiving forever — no explicit, deliberate "delete this
account and everything on it" path.

The natural-looking fix — soft-delete every referencing transaction (`transactions.soft_delete`,
same as `bulk_delete_transactions`), then delete the account row, both in one
`locked_unit_of_work` — does not work. `transactions.from_account_id`/`to_account_id` are
`ForeignKey("accounts.id", ondelete="RESTRICT")` (`backend/db/tables.py`), added specifically so
"`delete_account` refused while referenced" is DB-enforced, not only service-enforced
(`docs/DATA_MODEL.md`, ADR `2026-08-08-money-core-tables-verbatim-payload-plus-promoted-columns`).
RESTRICT keys off the ROW EXISTING, not its `deleted` flag — a soft-deleted row still carries the
account id, so the FK still refuses the account delete. Reproduced directly (isolated
`ONW_ROOT`): soft-deleting a transaction and then calling `accounts.delete()` raises
`sqlite3.IntegrityError: FOREIGN KEY constraint failed`.

`backend/db/tables.py`'s own `txn_audit` comment anticipates exactly this: `txn_id` there
carries deliberately NO FK "so the audit trail is a forensic record that must remain valid even
against a **hypothetical future hard-delete**" — this feature is that hypothetical arriving.

## Decision

We will make `delete_account(account_id, *, force=False)` a three-way choke:

0. **Refused outright, on EVERY path below, if the account is still tied into net worth through
   a reference the DB does not enforce with a real FK** — `owned_assets.loan_account_id`, a live
   position, a live lot, or an open option contract (`_assert_account_not_net_worth_linked`,
   its own `account_net_worth_linked` 409 shape, never conflated with the transaction-count
   one). This runs before the transaction-count logic even asks its question.
1. **No LIVE references** — delete the account outright.
2. **The ONLY LIVE reference is the account's own opening (Starting Balance) transaction** —
   delete the opening row and the account together, atomically, no `force` needed. This is not
   history a user would ever want preserved once the account itself is gone.
3. **Other LIVE references + `force=True`** — cascade: `_cascade_delete_account_transactions`
   mirrors `bulk_delete_transactions`'s soft-delete loop and
   `rejected_identities.remember_deleted(...)` call exactly (so the re-import dedup memory is
   unaffected), records the audit event per-row with the FULL row as `before` (not
   `bulk_delete_transactions`'s `before=None` — see the amendments below).
4. **Other LIVE references + `force=False`** (default) — refuse (`ConflictError`, 409), naming
   the transaction count, any OTHER account a shared transfer also touches, and pointing at
   `force=true`; the 409 carries this as STRUCTURED JSON (`transaction_count`,
   `other_accounts`), never only prose.

Cases 1–3 (every case that actually removes the account) fall through to the SAME single
`locked_unit_of_work(transactions, accounts, txn_audit, rejected_identities)`, which
additionally **hard-deletes EVERY row still referencing the account** — re-querying with
`include_deleted=True`, not just the rows the cascade itself just soft-deleted — so the
RESTRICT foreign key is satisfied before the account row is removed. Unlike
`bulk_delete_transactions`, this does NOT pre-scan/refuse an opening transaction among the
refs — the account itself is going away, so its own opening row is not a fact this cascade
needs to protect. A residual `IntegrityError` past that sweep is still caught and re-raised as
a typed `ConflictError`, never a raw 500.

`DELETE /api/accounts/{account_id}?force=true` exposes case 3; the frontend never sends
`force=true` except as a second, explicit user confirmation after a 409 on the first attempt —
never auto-escalated, and never offered at all for the case-0 refusal (see the second amendment
below).

### Amendment, 2026-09-06 (first review pass) — code/security review before merge

Two HIGH-severity bugs and three should-fix items surfaced in review, all fixed in the same
change (fold into the Decision above, not a separate ADR — this is completing the same
decision, not reversing it):

- **HIGH-1 (unhandled 500):** the cascade's hard-delete sweep originally targeted only the
  LIVE refs it had just soft-deleted. Any account that had EVER had a single transaction
  soft-deleted from it before (unrelated to this feature) still carried a stale row physically
  referencing it — RESTRICT doesn't care about the `deleted` flag — so `accounts.delete()` threw
  a raw `sqlite3.IntegrityError` the route's `except OpenNetWorthError` handler never caught.
  Fixed by re-querying `transactions.query_txns(account_id=..., include_deleted=True)` for the
  hard-delete sweep specifically (the user-facing count/opening-check still reads LIVE-only),
  plus the belt-and-braces `try/except IntegrityError → ConflictError` around `accounts.delete()`
  itself for any reference this choke doesn't know about.
- **HIGH-2 (silent wrong net worth):** `transactions.from_account_id`/`to_account_id` are the
  ONLY real DB FK onto `accounts.id` — `owned_assets.loan_account_id` (a mortgage's linked
  liability account) and a live position/lot's `account_id` are FK-by-convention only, so
  nothing stopped a force-delete from orphaning them; a missing loan account reads as debt=0 in
  the Assets tab's equity fold, silently. Fixed with `_assert_account_not_net_worth_linked`,
  refusing (not cascading — see Alternatives) on either reference, checked before every delete
  path including "no references at all". Deliberately bounded: commitments/income_events/
  statements_meta/import_batches also reference an account by convention but don't move a LIVE
  net-worth number the way a linked loan or an open position does — left as a named, out-of-scope
  gap, not silently ignored.
- **MEDIUM-3:** the frontend used to regex the transaction count out of the 409's prose message
  — broken by a free-text account NAME containing a number ahead of the real count (e.g. "Fund 1
  transaction reserve" masking a true count of 47). Fixed by putting `transaction_count`/
  `other_accounts` in the JSON body's `detail` object (mirroring `add_transaction`'s own
  `PreviouslyDeletedError`/`DuplicateTransactionError` shape), never parsed from prose.
- **MEDIUM-4:** a force-delete silently wipes the OTHER side of any transfer among the refs too,
  and neither the 409 nor the confirm dialog said so. Fixed: `_other_accounts_touched` names
  them, threaded through the structured 409 body and the frontend's escalated confirm copy.
- **MEDIUM-5:** the cascade's audit event used to carry `before=None`/`after=None` like
  `bulk_delete_transactions`'s own batched call — reasonable there (rows stay soft-deleted and
  reconstructable) but wrong here, since the row is hard-deleted and nothing else survives it.
  Fixed: the cascade now calls `audit_service.record_txn_event` per row with the FULL row as
  `before` — a deliberate divergence from `bulk_delete_transactions`'s own convention, scoped to
  this one caller.

### Amendment, 2026-09-06 (second, independent review pass)

A SECOND review pass against the first amendment's own fix commit found the fix for HIGH-1 was
only partial, plus a new UI safety defect the fix itself introduced, plus three more
should-fix items:

- **HIGH-1, residual gap:** the first amendment put the `include_deleted=True` sweep and the
  `try/except IntegrityError` guard INSIDE the `force`/cascade branch only —
  `delete_account` still had an early return BEFORE that (`if not refs: return
  bool(accounts.delete(account_id))`). An account whose transactions were ALL already
  soft-deleted (a plain `delete_transactions_where` sweep, an import-batch rollback, or
  deleting every row by hand before finally deleting the account) has `refs == []` (LIVE-only),
  so the early return fired, skipped the sweep entirely, and hit the same unhandled
  `IntegrityError` the first amendment was supposed to close — reproduced at the service layer,
  the API route, AND the `delete_accounts` AI tool (which only caught `ConflictError`).
  **Fixed** by removing the early return: `delete_account` now computes `only_opening` and
  raises `ConflictError` only when `refs and not only_opening and not force`, then falls through
  UNCONDITIONALLY to the one `locked_unit_of_work` that does the sweep + hard-delete + guarded
  `accounts.delete()` — so "zero live refs, but stale soft-deleted rows exist" is covered by the
  exact same code path as every other case, for free. Reproduced myself, before AND after the
  fix, at the service layer (`svc.delete_account` on an account with only a soft-deleted
  transaction: raw `sqlite3.IntegrityError` before, `True` after), the `delete_transactions_where`
  → `delete_account` sequence, the `DELETE /api/accounts/{id}` route (500 before, 200 after),
  and the `delete_accounts` AI tool (an uncaught exception before, `{"ok": true, ...}` after).
- **NEW (introduced by the first amendment's own HIGH-2 fix):** `_assert_account_not_net_worth_
  linked`'s `ConflictError` carried `linked_asset_ids`/`open_position_count` in its context, not
  `transaction_count` — so the API route's "is this the structured shape" check (keyed on
  `transaction_count`) missed it entirely, and it fell through to the plain-string `detail`
  path. The frontend's `AccountDeleteConflictError` was thrown for ANY 409 regardless of shape
  (including a bare string), and the UI unconditionally escalated to "Force delete and remove N
  transaction(s) — this can't be undone" whenever it caught that error type. Net effect: an
  account blocked ONLY by a linked mortgage (zero transactions) showed a scary, WRONG
  force-delete confirmation whose Force delete button would just 409 again — the real reason
  (a linked loan) never reached the user until they clicked through the false escalation.
  Reproduced myself over the wire (frontend test written against the UNFIXED code first,
  confirmed failing — a "Force delete" button rendered for a linked-loan-only 409 — before
  fixing it). **Fixed**: the net-worth-linked refusal gets its OWN structured shape,
  `{"error": "account_net_worth_linked", "message", "linked_asset_ids", "open_position_count",
  "open_lot_count", "open_option_count"}`, routed by the API layer on `error_code ==
  "account_net_worth_linked"` in the exception's context — checked BEFORE the
  `transaction_count` check, and mutually exclusive with it. The frontend now throws
  `AccountDeleteConflictError` ONLY when the body's `transaction_count` is a genuine number
  (the ONE shape `force=true` can act on); every other 409 shape throws the new
  `AccountDeleteBlockedError` instead, which the UI never escalates on — it just toasts the
  real message.
- **MEDIUM-5, residual gap:** a row the hard-delete sweep found ALREADY soft-deleted (not
  soft-deleted by this cascade's own loop) got NO audit event at all this round — its only
  trail was whatever its ORIGINAL delete wrote (typically `before=None`, from
  `bulk_delete_transactions`'s own convention). Fixed: the sweep now writes one more `delete`
  audit event, WITH the full row as `before`, for any row it hard-deletes that wasn't already
  in this round's `deleted_rows` — hard-delete is the last moment that row's fields exist
  anywhere, so nothing hard-deletes with zero trace, regardless of when it was originally
  soft-deleted.
- **HIGH-2, scope gap #1 (docs promised more than the code did):** the docstring/ADR/
  INVARIANTS.md/CLAUDE.md all said "a live position/**lot**", but the code only ever queried
  `PositionStore` — no `LotStore` check existed. Fixed by adding one, same shape as the
  position check: refuse if any lot has `remaining_qty != 0` on this account.
- **HIGH-2, scope gap #2 (a real gap, not just a docs mismatch):** open option contracts
  (`option_contracts.account_id`/`collateral_account_id`) fold into net worth
  (`_compute_options_mark_base`) and were not covered — force-deleting the venue/collateral
  account would silently reclassify a covered call as uncovered (the covered-call check keys on
  `(instrument_id, account_id)`), and this wasn't even in the ADR's named-gap list the way
  commitments/income_events/etc. correctly are (they don't move a live number; options do).
  Decision: EXTEND the guard rather than accept the gap — refuse when the account has any
  `status="open"` option contract on either `account_id` or `collateral_account_id`, the same
  "refuse, don't silently orphan" posture as the loan/position/lot checks.

## Consequences

### Positive
- An account's opening-balance-only "ghost" no longer needs an archive to go away.
- A user can actually delete an account with real history, with its consequences named up front
  (transaction count) and only after an explicit second confirmation.
- The audit trail (`txn_audit`, no FK by design) and `rejected_identities` bookkeeping survive the
  hard-delete untouched — a re-import of a deleted transaction's source event is still refused,
  and `audit.trace()` still returns its create/delete history even though the row itself is gone.

### Negative
- This is the first place in the codebase that hard-deletes a transaction row outside the
  rebuild-and-swap cleanup case (`update_transaction`/`set_account_opening`'s "drop the freshly
  re-appended duplicate" step). There is no "undo" for a force-deleted account's transactions,
  deliberately: the account they belonged to no longer exists to restore them into. A forensic
  trace of a force-deleted transaction's OWN fields (amount, category, memo, …) DOES survive,
  though — via `txn_audit`'s per-row event, which carries the full row as `before` specifically
  on this path (the 2026-09-06 amendment; `bulk_delete_transactions`'s own batched call still
  writes `before=None`, correctly, since its rows stay soft-deleted and reconstructable).
- `_cascade_delete_account_transactions` duplicates (rather than reuses) the relevant third of
  `bulk_delete_transactions`'s body, because that function's opening-transaction pre-scan refusal
  is exactly backwards for this caller and its signature/behavior must not change for its other
  callers.
- The net-worth-link guard (`_assert_account_not_net_worth_linked`) is a REFUSAL, not a fix — a
  user who genuinely wants to delete a mortgage's liability account must unlink the asset (or
  close the position) first, as a separate step. This is the conservative choice on purpose (see
  Alternatives).

### Follow-ups
- The AI tool surface (`ai/tools/accounts.py`'s `delete_accounts`) is untouched — it still calls
  the old two-way behavior by omitting `force` (default `False`), so nothing there changed
  behaviorally beyond what the opening-only branch and the net-worth-link guard now also apply to
  it for free. Its description text was updated to describe the new three-way behavior
  accurately. Wiring `force` into the tool (and its own confirm-gate story) remains a deliberate
  follow-up, not bundled into this change — an unattended, always-allow Lucius session force-
  cascading real transaction history is a materially different risk than the opening-only branch
  it already exercises today.
- `commitments`/`income_events`/`statements_meta.account_id`/`import_batches.source_account_id`
  are FK-by-convention onto `accounts.id`, same as `owned_assets.loan_account_id`, but are NOT
  checked by `_assert_account_not_net_worth_linked` — named, bounded gap (the guard is scoped to
  the four references that move a LIVE net-worth number today: a linked loan, a live position, a
  live lot, and an open option contract — the second review pass added the latter two). A future
  pass could extend the guard, or accept an orphaned reference there as a lesser harm (those
  stores don't recompute a headline number the way equity/holdings/options do).

- **(2026-10-13 amendment) Commitments are no longer part of that gap.** `delete_account` now
  clears a commitment's `linked_account_id` and every funding source's `ref_account_id` that
  name the account, inside the same unit of work (`_unlink_account_from_commitments`), rather
  than refusing: those links move no number, so unlinking costs nothing, while leaving them was
  found live as a deleted account still named by one commitment and 26 of its funding sources.
  `income_events`/`statements_meta`/`import_batches` remain the named gap.

## Alternatives considered
- **Null out `from_account_id`/`to_account_id` on the soft-deleted rows instead of hard-deleting**
  — rejected: those columns are promoted from `payload`, so nulling only the SQL column without
  rewriting the payload's own account-id fields would desync the two; rewriting the payload too
  would make the row internally inconsistent (`deleted=True` transactions.md says which account a
  deleted row belonged to, needed for the register's read paths and any future account-scoped
  audit query).
- **Loosen the FK from RESTRICT to SET NULL / CASCADE** — rejected: the FK is deliberately
  RESTRICT so an ordinary (non-force) account delete stays DB-enforced-refused, matching
  `docs/DATA_MODEL.md`'s stated invariant; weakening it for every caller to accommodate one opt-in
  cascade path would remove a real safety net for the common case.
- **Reuse `bulk_delete_transactions()` for the cascade** — rejected per the task brief: it
  unconditionally refuses when the opening transaction is among the ids, which is correct for its
  other callers but exactly backwards here (the account itself is being deleted).
- **(2026-09-06 amendment) Auto-unlink `owned_assets.loan_account_id` / auto-close the position
  instead of refusing** — rejected: silently detaching a mortgage from its asset (or force-
  closing an open holding) is its own destructive, consequential write with its own UI/undo
  story; bundling it into an account-delete confirmation buries a decision the owner should make
  explicitly, on the Assets/Investments surface where they can see what they're doing to it.
- **(2026-09-06 amendment) Cover every FK-by-convention reference (commitments, income_events,
  statements_meta, import_batches) in the same guard** — rejected as over-scope for this change:
  none of those move a LIVE net-worth number the way a linked loan or an open position does, so
  the security review's own bar ("bounded, don't build a full cross-store cascade") is better
  served leaving them a named gap than growing this choke into a general referential-integrity
  auditor.

## Agent provenance

- **Decided by:** orchestrating Claude agent (implementer for this feature)
- **Lane / branch:** `feature/delete-account-force-and-opening-only`
- **Alternatives weighed:** null-out-FK-columns vs. loosen FK to SET NULL/CASCADE vs. hard-delete
  after soft-delete (chosen) — see Alternatives considered above.
- **Driving artifacts:** owner task brief (delete-account force + opening-only); empirical
  reproduction of the FK RESTRICT failure against an isolated `ONW_ROOT` instance;
  `backend/db/tables.py`'s `txn_audit` module comment, which named this exact hypothetical;
  TWO independent code/security-review passes before merge — the first (HIGH-1 unhandled 500 on
  a stale soft-deleted reference, HIGH-2 silent wrong net worth via an unenforced loan/position
  reference, MEDIUM-3/4/5 on error-body structure, cross-account disclosure, and audit-snapshot
  fidelity) and a SECOND, independent pass against the first pass's own fix commit (HIGH-1's
  fix was only partial — an early return still skipped the sweep for "zero live refs"; the
  HIGH-2 fix introduced a NEW UI defect, a false force-delete escalation for a refusal
  `force=true` can't act on; plus the lot/option scope gaps and the MEDIUM-5 residual case) —
  every BLOCKING finding on both passes reproduced directly (service layer, API route, AI tool,
  and — for the UI defect — a frontend test written against the unfixed code first) before and
  after each fix.
- **Why this way (one line):** RESTRICT keys off row existence, not the `deleted` flag, so
  satisfying it while still recording the same audit/rejected-identity bookkeeping
  `bulk_delete_transactions` does means soft-delete-then-hard-delete, not soft-delete alone.
