---
title: ReceiptStore's link-table shape, checkpoint exclusion, and promotion idempotency keys
status: Accepted
date: 2026-08-16
deciders: implementing Claude agent
---

# ReceiptStore's link-table shape, checkpoint exclusion, and promotion idempotency keys

## Context

`docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md` already decided the
big question — three independent stores, explicit provenance-preserving promotion, no
merged artifact record. Implementing Phase 4 of
`.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md` (items 21-26) still
required several concrete, load-bearing calls that ADR left open:

1. What table shape backs the transaction<->receipt many-to-many relationship, and whether
   it earns promoted/indexed columns.
2. Whether `transaction_receipts` (the link table) is checkpoint-INCLUDED, like
   `transaction_splits`/`transaction_external_refs` (structural parts of a transaction), or
   checkpoint-EXCLUDED, like `attachments`/`artifacts` (document catalogs).
3. What makes re-promoting the same chat attachment as a receipt idempotent, since receipts
   have no pre-existing dedup precedent the way `StatementStore.create`'s content-hash dedup
   already did for statements.
4. How a composed transactional delete (receipt row + its links, in one
   `locked_unit_of_work`) avoids losing a blob file if the transaction rolls back.

## Decision

**1. Both `receipts` and `transaction_receipts` are bare `payload_table()` shapes — no
promoted `transaction_id`/`receipt_id`/`content_hash` columns.** `TransactionReceiptStore.
by_transaction`/`by_receipt` filter Python-side over `all()`, matching `payload_store.py`'s
own documented default ("a subclass with a real hot filter earns its own indexed column
instead"). At this app's scale (a handful to a few hundred receipts per install) this is
free; a future hot lookup earns its own column without a data migration, since `payload` is
schemaless JSON and already carries every field.

**2. Both tables are checkpoint-EXCLUDED**, not just `receipts`. A `transaction_splits`/
`transaction_external_refs` row is checkpoint-INCLUDED because it is a structural part of
ONE transaction's own snapshot — restoring an older transaction state correctly restores its
splits with it. A `transaction_receipts` link is not part of a transaction's own economic
snapshot; it points at a document-catalog row (`receipts`) that is ITSELF
checkpoint-EXCLUDED (mirroring `attachments`/`artifacts` — a Lucius restore must never
resurrect a user-deleted document). Making the link checkpoint-INCLUDED while its target is
excluded would let a `/rewind` resurrect a link to a receipt whose own deletion the rewind
never reverses, or silently relink a receipt the user explicitly and intentionally unlinked.
Document-catalog lifecycle stays outside the money-checkpoint system end to end; both tables
are still portable (BRIDGED), so export/import and `/api/reset` still see them.

**3. Receipt-promotion idempotency keys off `source_attachment_id`, not content hash.**
`StatementStore.create`'s dedup is BY CONTENT HASH because a statement genuinely IS its
bytes — the same file re-imported from any source is the same statement. A receipt is
different: the RFC's own m:n design means the identical bytes MAY legitimately back two
independently-promoted receipt rows (e.g. the same physical bill photographed twice from
different chats, or forwarded and re-uploaded). `services.receipts._promote_from_attachment`
therefore checks "has THIS attachment id already been promoted" before minting a new row —
promoting the SAME attachment twice is a no-op; promoting two different attachments that
happen to share bytes is not deduped, and that is intentional, not an oversight.

**4. `ReceiptStore.delete_receipt` defers the blob-file unlink to the caller when composed
inside a transaction** (`conn` given), mirroring `db/portable_bridge.py`'s `wipe_all`/
`_capture_then_delete` rollback-safety contract exactly: the DB row delete happens inside
the transaction; the filesystem delete happens only after that transaction commits
(`services.receipts._delete` calls `receipt_store.blob_path(...).unlink(...)` AFTER its
`with locked_unit_of_work(...)` block exits). A standalone call (`conn=None`, no outer
transaction to roll back around) deletes the blob immediately, matching
`AttachmentStore.delete_attachment`'s existing (looser, because never composed) convention.

## Consequences

### Positive

- No data migration needed if a future hot-path lookup earns a promoted column — the
  schemaless `payload` already carries every field.
- The checkpoint boundary is consistent end to end: every document-catalog concern
  (attachments, receipts, and now the link between them) sits outside `/rewind`'s reach as
  one coherent unit, rather than half in / half out.
- A rolled-back receipt delete/create transaction never destroys a blob file that the DB
  still (or again) claims exists.

### Negative

- `by_transaction`/`by_receipt` are O(n) over the whole `transaction_receipts` table today —
  acceptable at this app's real scale, but a future "thousands of receipts" install would
  need the promoted-column follow-up this decision explicitly defers rather than forecloses.
- A byte-identical receipt promoted from two different attachments is stored twice (two
  rows, two blob copies) — accepted storage cost, consistent with the parent ADR's "Why
  copying is intentional" section 2.4.

## Alternatives considered

- Promoted `transaction_id`/`receipt_id` columns on `transaction_receipts` from day one —
  rejected as premature optimization for the documented current scale; the schemaless
  payload keeps the door open without cost now.
- Checkpoint-INCLUDING `transaction_receipts` (matching `transaction_splits`) — rejected: it
  points at an EXCLUDED table, and a `/rewind` that resurrects a link but not the receipt it
  points to (or fails to reverse an explicit unlink) is a worse, inconsistent half-restore.
- Content-hash dedup for receipt promotion (matching `StatementStore`) — rejected: it would
  silently collapse two independently-relevant receipt records into one whenever their bytes
  happened to match, which the m:n design's own "one artifact may support several
  transactions" premise treats as a valid, distinct promotion each time.

## Agent provenance

- **Decided by:** implementing Claude agent, executing
  `.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md` Phase 4
  (items 21-26) on branch `feature/cross-channel-attachments-epic`.
- **Alternatives weighed:** promoted link-table columns, checkpoint-including the link
  table, content-hash receipt dedup — all rejected, reasoning above.
- **Driving artifacts:** `docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md`
  (the accepted parent decision this one implements), `backend/db/portable_bridge.py`'s
  `wipe_all` rollback-safety contract (the precedent the blob-deferral pattern mirrors).
- **Why this way (one line):** the link table and its target share one lifecycle boundary
  (outside `/rewind`, inside export/import), and promotion idempotency must key on WHAT was
  promoted (the specific attachment), not merely what bytes resulted.

## Amendment (2026-08-21) — Decision 2 reversed: both tables are now checkpoint-INCLUDED

**The owner has reversed Decision 2 above — its reasoning doesn't hold up.** Decision 2
argued a `/rewind` "must never resurrect a user-deleted document" or "silently relink a
receipt the user explicitly and intentionally unlinked", and rejected checkpoint-inclusion
on exactly that basis ("Alternatives considered" above). That argument implicitly assumes
a WHOLE-TABLE checkpoint model, where restoring an old snapshot really could bring back
rows a later, unrelated write removed. That is not how this app's checkpoint mechanism
works and has not been since before receipts shipped: `ai/checkpoint/` is ROW-LEVEL
(ADR `2026-08-07-write-log-row-level-checkpoints`) — it records a before-image per
`(table, pk)`, the FIRST time that exact row is touched within ONE turn, and a restore
replays only those recorded `(table, pk)` pairs (delete if the pk didn't exist before the
turn, re-insert the before-image otherwise). A rewind of turn N is structurally incapable
of reaching a row turn N never wrote — it cannot resurrect a receipt some OTHER turn
deleted, and it cannot relink a receipt some OTHER turn unlinked, for the same reason
restoring an old `transactions` checkpoint cannot silently revert a category edit made by
a later, unrelated turn: the blast radius is the writing turn's own rows, full stop.

Once that premise is corrected, `receipts` was carrying a MORE conservative posture than
every other row-level-checkpointed table in this app for a risk the mechanism already
contains — `transactions`, `accounts`, `categories`, every money-core and Planning table
face the identical "could a later edit theoretically be reverted" shape and stay
checkpoint-INCLUDED. `transaction_receipts` pointing at `receipts` (Decision 2's stated
worry — "the link points at something itself excluded") is now moot: `receipts` is no
longer excluded either, so there is no orphaned-link case left to guard against; the two
tables are still composed in ONE `locked_unit_of_work` per write (unchanged), so a turn
that both creates a receipt and links it captures/restores both rows together, exactly
like any other multi-table choke (ADR
`2026-08-09-trade-choke-points-compose-in-one-transaction`'s pattern).

**Decision (reversed):** `receipts` and `transaction_receipts` are removed from
`ai.checkpoint._policy._EXCLUDED_TABLES`. No change was needed to
`services.receipts.apply_receipt_change` itself beyond its own now-stale docstring — the
choke already writes through `PayloadTableStore`, which fires
`core.io.fire_db_write_hook` unconditionally on every write; `record_db_before` starts
recording before-images (and a turn that touches only these two tables now correctly
mints a checkpoint instead of `finalise()` silently discarding it) purely because the
table is no longer in the excluded set — no new explicit `record_write`/"snapshot" call
was added. Decision 1, 3, and 4 above are UNCHANGED (link-table shape, `source_
attachment_id` promotion idempotency, deferred blob-unlink-on-composed-delete) — none of
them depended on checkpoint status. Portability (BRIDGED, not secret,
not `NON_RESTORABLE`) is likewise unchanged — an independent axis from checkpoint
inclusion.

Test coverage: `tests/test_receipts_checkpoint.py` (attach inside a turn → restore →
receipt+link gone; unlink inside a turn → restore → link reappears, receipt untouched) —
the "checkpoint-excluded, pinned by `tests/test_receipts_service.py`" framing this ADR's
own Decision 2 referenced is gone; that file's write-receipt-CHIP test (a separate,
unrelated mechanism sharing the word "receipt") is retained with a corrected docstring.

### Agent provenance (amendment)

- **Decided by:** repo owner, executed by a Claude agent on `fix/receipts-checkpoint-included`
- **Alternatives weighed:** none re-litigated — this amendment corrects a factual premise
  (whole-table vs. row-level checkpoint blast radius) rather than re-balancing a genuine
  trade-off; Decision 2's original "Alternatives considered" entry rejecting
  checkpoint-inclusion is superseded by this amendment, not still live.
- **Driving artifacts:** `docs/adr/2026-08-07-write-log-row-level-checkpoints.md` (the
  mechanism whose actual scoping this amendment leans on), the amendment note on
  `docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md` (the parent ADR's
  own pointer here).
- **Why this way (one line):** row-level checkpointing already bounds a restore to the
  rows one turn wrote, so excluding receipts bought no safety the money-core tables don't
  already get for free — it only cost `/rewind` coverage for one of Lucius's write chokes.
