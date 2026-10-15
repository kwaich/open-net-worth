# 2026-08-01 — Ledger deletes in the import Decide step are STAGED and execute inside `decision_commit`

**Status:** Accepted
**Owner:** the maintainer ("i have to delete every row by myself, can we also mark them as delete and
only do in the confirm button instead of delete one by one?")
**Touches:** `backend/services/statements/decisions.py`, `backend/api/import_api.py`,
`frontend/src/components/tabs/accounts/ImportModal.tsx`,
`frontend/src/components/tabs/accounts/AccountsTab.tsx`,
`frontend/src/components/tabs/accounts/statementImport.css`,
`tests/test_decision_commit_staged_delete.py`,
`frontend/src/components/tabs/accounts/ImportModal.stagedDelete.test.tsx`

## Context

The "In your ledger, not on this statement" panel offered each uncovered entry four actions:
`Open · Link to a new row · Delete · Leave it`. **Delete wrote straight through** —
`DELETE /api/transactions/<id>` fired on click, behind a per-row `window.confirm`.

Every *other* decision in that modal is staged and applied at Confirm, under the footer's own
promise: *"nothing is written until you confirm."* Delete broke that contract three ways:

1. **It lied.** The footer promised nothing was written; eight rows had already been deleted.
2. **It was irreversible, per row, before commit** — including on a statement the user might
   still abandon. Nothing on the screen could undo it.
3. **It was unguarded.** The generic delete endpoint receives an id with no statement context,
   so nothing checked that the row was even *in this statement's uncovered set*. Every other
   destructive/mutating path out of this modal is validated against a server re-derivation
   (SECURITY FIX 2, review 2026-07-16).

The reported trigger was volume: ~8 duplicate ride charges meant 8 disclosures, 8 dialogs and 8
irreversible writes.

## Decision

### 1. Mark, don't write

Delete becomes a local **mark**. The row stays in the panel rendered as marked (`Will delete`
chip, tinted row, danger rail) with `Undo` in the slot `Delete` occupied. Nothing leaves the
panel and no request is made until Confirm.

The mark gets its **own state** (`pendingDelete`), *not* the existing `checkRemoved`.
`checkRemoved` meant "this entry is gone from the ledger" — a fact, written only after a
successful DELETE — and its sole writer no longer exists, so it is retired. A mark is a different
claim with a different lifetime ("delete this *when I confirm*"), and the entry is still real
until commit. Conflating "I've dealt with this" with "delete this from my ledger" is precisely
what would make a mis-marked row silently destructive.

### 2. Deletes execute INSIDE `decision_commit`, not as a sibling call

`decision_commit(..., delete_ids=[...])`. The alternative — a client-side loop of
`DELETE /api/transactions/<id>` before or after the commit — was rejected because:

- **The safe target set only exists here.** The ids are validated against this statement's
  re-derived **uncovered set** (`_manual_link_ids`) — the same set a manual link is validated
  against. Only `decision_commit` can re-derive it. Sent to the generic endpoint the ids carry no
  statement context and get *no* guard, which is the unguarded shape being replaced.
- **The caller's list is a request, never an authorization** — the merge guard's posture,
  extended to the one other destructive thing this screen can do.

Two refusals, both **before any write** (so a bad list cannot half-apply an import):

| Case | Outcome |
|---|---|
| id not in the uncovered set (foreign account, fabricated, covered, out of window) | `ValidationError` — whole commit refused |
| id is also a merge target in the same commit | `ValidationError` — cannot link *and* delete one entry |

The guard is checked **pre-commit deliberately**: once the creates land, this statement's own new
rows are in the ledger and the uncovered set is no longer the one the user reviewed. The set the
deletes are judged against is exactly the panel they were looking at.

### 3. Deletes run LAST, and a failure is reported, not raised

They execute after the row decisions, via `ledger_service.bulk_delete_transactions` (soft-delete,
one batched audit event, atomic refusal of a Starting Balance row). Ordering is load-bearing:
running them first would risk destroying ledger rows for an import that then failed to apply.

Failures land in `delete_errors` — the `merge_errors` contract — so a refused delete never sinks
an import the user already confirmed. The Done screen surfaces them: *"N marked transactions
could not be deleted."* **"delete 8" must never quietly become "deleted 7."**

One special case is *not* a guard breach: an id whose row was **already soft-deleted** between
preview and commit. The user's intent is already satisfied, and it is out of the uncovered set
for that reason alone — so it is dropped from the batch and reported as `already deleted`,
rather than failing the whole confirmed import.

### 4. The footer names the destruction

`Link 1 · import 3 · leave out 1 · **delete 8 from your ledger** — nothing is written until you
confirm.`

The destructive term is emphasised in its own right (`.imp3-foot-del`, danger-toned) rather than
being one more comma-separated fragment — buried in the run it reads as ordinary bookkeeping.
*"from your ledger"* is load-bearing: it is what distinguishes this from *"leave out"*, which
only means "don't import".

A statement with nothing to book but rows marked is still confirmable — deletions are an
outcome, so the CTA must not read "Nothing to book" and sit disabled.

### 5. Bulk: reuse the existing bar, invent no selection paradigm

The section gets `Mark all N for deletion` / `Clear marks` in the **same shape as the existing
Will-link bulk bar** (`.imp-pair-bandhead` + `.imp-seg`, buttons only, no label of its own,
acting on exactly the rows rendered beneath it). No checkboxes, no selection mode — the ban on
"a new selection paradigm" and the buttons-only bulk bar both hold.

### 6. One confirmation, not eight

The per-row dialog is gone; a single `window.confirm` at Confirm names the count. Eight dialogs
become one, and it appears only when something is actually marked.

## Consequences

- The footer's promise is now true for every decision on the screen, without exception.
- Deleting uncovered entries is guarded for the first time.
- `checkRemoved` is retired; `pendingDelete` filters the manual-link option list and a row's
  `alternatives` (you cannot link a statement row to an entry you are deleting), but does **not**
  filter the panel itself — a marked row must stay visible to be undoable.
- `onDecisionCommit` grows a 4th parameter (`deleteIds`); three existing whole-call assertions
  were updated.

## Alternatives rejected

- **Keep write-through, add a bulk "delete all" button.** Faster to build, but it makes the
  contract violation worse: one click, N irreversible writes, still before commit.
- **Reuse `checkRemoved`.** Conflates a fact with an intent — see §1.
- **A sibling bulk-delete endpoint called from `confirmImport`.** Two write moments, no shared
  guard, and a partial failure with no honest way to report which half applied.
