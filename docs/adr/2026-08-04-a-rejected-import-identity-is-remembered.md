---
title: A rejected or deleted import identity is remembered — a re-sync must not resurrect it
status: Superseded
date: 2026-08-04
deciders: orchestrating agent (Claude) + repo owner
---

# A rejected or deleted import identity is remembered — a re-sync must not resurrect it

> **Superseded by [2026-09-02-a-deleted-identity-is-remembered](2026-09-02-a-deleted-identity-is-remembered.md)**,
> which built it. The durable, reversible, portable record and "rollback ≠ reject" stand; three
> parts of the sketch did not survive contact with the code: the write point is the **delete
> choke** (not "reject an import"), the key is **movement-scoped** `(source, ref, from, to)` (a
> per-account key collides the two legs of one Wise email), and the consult is at **every** create
> choke outside the `force` gate (not inside the opt-in `dedup_by_external_ref` floor).

## Context

The ingestion dedup floor consults **live rows only**. `services/ledger.py:1393-1394` documents it
directly: a row is skipped when one of its `external_refs` pairs "already exists in the ledger
(live, non-deleted — mirrors `TransactionStore.has_external_ref`)". Our deletes are soft
(`{"deleted": True}`), so a deleted row is invisible to that check.

The consequence is a loop the owner cannot escape:

1. A sync or file import books a row the owner does not want (a wrong-account import, a
   duplicate, a row they deliberately removed).
2. They delete it, or roll back the batch — which works. `delete_transactions_where(
   import_batch_id=…)` and its preview twin exist (`services/ledger.py:3320-3409`), so **batch
   rollback is already supported**; it soft-deletes the batch.
3. The next sync, or a re-import of an overlapping export, re-delivers the same
   `external_ref`. The floor sees no live row. It creates it again.

So rollback is real but not durable, and the two problems people report as separate — "my deleted
transactions come back" and "I can't undo an import" — are one problem with one fix. Nothing in
the codebase records that removal was *intentional*; `deleted: True` is indistinguishable from
"not present".

We do already have the right pattern one layer up, for a different question. `match_review.
dismiss_review` (`services/match_review.py:789`) keeps both rows and **remembers the pair as
"different" so it is never re-surfaced**, via a decisions store with `list_decisions` /
`forget_decision`. That is exactly this shape — a remembered negative decision with an escape
hatch — applied to duplicate pairs rather than to import identities.

Zerosum documents the equivalent guarantee explicitly ("Deleted imports do not come back"), scoped
per account, and pairs it with an import-batch undo that removes rows *without* marking them
rejected — so a bad column mapping can be corrected and re-imported. That distinction between
"undo my mistake" and "I don't want this row" is the part our single soft-delete cannot express.

## Decision

We will record an **intentional removal of an imported identity** as durable state, and consult
it in the ingestion dedup floor.

- Rejecting an in-review import, or deleting an approved imported row, writes a **rejection
  record** keyed by `(account, source, ref)` — the identity, not the row.
- `bulk_create_transactions(dedup_by_external_ref=True)` skips any incoming row whose identity
  carries a rejection record, and **reports the count** as a distinct outcome (`rejected`)
  alongside `skipped`, so an import summary can say "3 skipped as already present, 1 skipped as
  previously rejected" rather than conflating them.
- **Rejection is scoped to the account.** The same file imported into a different account is
  legitimately different history and must still import.
- **Batch rollback does NOT write rejection records.** Undoing an import is "I made a mistake
  entering this", not "I don't want these transactions" — the whole point is to fix the mapping
  and re-import. This is the distinction our current single soft-delete cannot express, and it is
  the reason the two must stay separate operations.
- The record is **reversible**: a forget/undo path mirroring `match_review.forget_decision`, so a
  mistaken rejection is not a permanent hole in the ledger.
- Rejection records are **checkpoint-included and portable** — they are user decisions, and a
  restore that dropped them would resurrect every rejected row at once.

## Consequences

### Positive

- Overlapping bank exports and repeated syncs become genuinely idempotent against the owner's
  decisions, not just against row presence.
- Batch rollback becomes durable, closing the two reported problems with one mechanism.
- An import summary can finally distinguish "already have it" from "you told me no", which is the
  difference between a reassuring number and a confusing one.
- It reuses a pattern already proven in `match_review`'s decisions store, including its
  forget/undo affordance.

### Negative

- **A new monotonically-growing store.** Rejection records accumulate forever and are never
  naturally collected — the same unbounded-growth note security review already raised against
  `email_snapshots`. It needs a bound or an accepted growth argument up front.
- Two removal verbs with different durable consequences is real conceptual surface. If the UI
  does not make "reject" versus "undo the import" obvious, the owner will pick the wrong one and
  the failure is silent in one direction (a row that keeps coming back) and destructive in the
  other (a row they can no longer re-import without finding the forget path).
- A rejected identity is invisible in the register by construction, so a wrongly-rejected row is
  discoverable only through a management screen that does not exist yet.
- The dedup floor gains a second lookup per row.

### Follow-ups

- Add the store (`db/`), the write path at both rejection points, and the floor consult; classify
  the store as checkpoint-included and add it to `core/portable.py`'s manifest.
- Regression test the exact loop: import → delete → re-deliver same ref → assert **not** created;
  then forget → re-deliver → assert created.
- Regression test that batch rollback leaves **no** rejection records and that re-import succeeds.
- Decide the growth bound (or record the accepted-growth argument) before merge.
- A management surface for rejected identities, mirroring `GET /api/matches/decisions`.
- Correct the misleading docstring on `DELETE /api/statements/{id}` (`api/import_api.py:508`) —
  it points at batch rollback without naming it.

## Alternatives considered

- **Make the dedup floor consult deleted rows too** — rejected: it conflates "deleted because I
  didn't want it" with "deleted for any other reason", and would make batch rollback permanently
  un-re-importable, breaking the fix-the-mapping-and-retry flow that is rollback's main use.
- **Hard-delete rejected rows and keep a tombstone on the row itself** — rejected: we soft-delete
  everywhere for audit reasons, and the record must survive the row.
- **Reuse the `match_review` decisions store** — rejected: its key is a *pair* of ledger rows; this
  key is a single external identity that may have no live row at all. Same pattern, different
  domain; a shared store would need both key shapes.
- **Have batch rollback also write rejection records** — rejected on the reasoning above; it
  would make the most common recovery path (wrong CSV mapping) unrecoverable.
- **Global rather than per-account scope** — rejected: the same statement legitimately imports
  into a different account as separate history.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner (in-session, 2026-08-04)
- **Lane / branch:** `feature/automations-redesign` (decision recorded only; no implementation)
- **Alternatives weighed:** consult deleted rows · row-level tombstone · reuse the match-review
  decisions store · rollback also rejects · global scope
- **Driving artifacts:** Zerosum help-centre teardown (*Importing Transactions* — "Deleted
  imports do not come back", 2026-08-04) · `services/ledger.py:1393` · `match_review.
  dismiss_review` as the precedent pattern
- **Why this way (one line):** a soft-delete records that a row is gone but not that the owner
  meant it, and the dedup floor needs the second fact.
