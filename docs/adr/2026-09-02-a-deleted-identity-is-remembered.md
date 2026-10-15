---
title: A deleted identity is remembered — every write choke refuses it, and only an explicit decision brings it back
status: Accepted
date: 2026-09-02
deciders: orchestrating agent (Claude) + repo owner
---

# A deleted identity is remembered — every write choke refuses it, and only an explicit decision brings it back

> Implements, and amends, [2026-08-04-a-rejected-import-identity-is-remembered](2026-08-04-a-rejected-import-identity-is-remembered.md),
> which recorded the decision without building it. Three things moved: the write point is the
> **delete choke** (any delete, not "an imported row"); the key is **movement-scoped**, not
> per-account; and the consult is at **every** create choke, outside the `force` /
> `allow_duplicate` gate, not inside the opt-in dedup floor.

## Context

The owner deletes a transaction. The next automation run fetches the same email, sees no live
row holding its ref, and books it again. This happened on the live book with a voided charge:
deleted once by hand, re-logged by the next scan under the same `email:` ref, and the run's own
transcript shows the pre-flight reporting the row unmatched — Lucius booked it in good faith.

Every dedup layer we have reads **live rows only**. `TransactionStore.by_external_ref` excludes
deleted rows by design (the "unblock after an accidental delete" guarantee), the L0 floor in
`create_transaction` and the opt-in `dedup_by_external_ref` floor in `bulk_create_transactions`
both sit on it, and the tool pre-flight (`matching.check`) scores against the live ledger. A soft
delete records that a row is *gone*; nothing records that the owner *meant it*. `deleted: True`
is indistinguishable from "never present", so a delete is undone by the very next delivery of the
same event.

The 2026-08-04 ADR named this loop and decided the shape of the fix — a durable rejection record,
reversible, checkpoint-included, with batch rollback deliberately writing none — but recorded it
"decision only; no implementation". Building it surfaced three places where that sketch was
wrong for this codebase:

- **Per-account scope is the wrong scope.** A Wise confirmation books two legs on two routes
  from ONE email, so one ref legitimately lives on two rows. Keyed on `(account, source, ref)`
  the two legs collide on the same account; keyed on the bare ref, deleting one leg rejects the
  other. The scope that already distinguishes them is the one `matching._batch_l0` uses: the
  **movement**, `(from_account_id, to_account_id)`.
- **The opt-in floor is the wrong consult point.** `dedup_by_external_ref` is off by default
  and `force=True` / `allow_duplicate` skip every duplicate check. The owner's framing was
  exact: *force should allow duplicates for sure* — but overriding a duplicate and overriding
  the owner's own delete are two different consents, and a flag that grants the first must not
  silently grant the second.
- **The write point is any delete, not "reject an import".** The statement-review "reject" verb
  does not exist as a ledger write today; the delete choke does, and every delete path the owner
  can reach goes through it.

## Decision

We will remember the external identity of every transaction the owner deletes, and refuse to
book it again at every write choke until the owner explicitly asks for it back.

- **The record.** `rejected_identities` (revision `202609021600_rejected_identities`, a
  `BookPayloadTableStore`), one row per `(source, ref, from_account_id, to_account_id)` of the
  deleted transaction — the movement-scoped identity `_batch_l0` uses — carrying the deleted
  `txn_id`, date, description, amount/currency for display, and `rejected_at`. A Wise-chain
  delete writes two records; deleting one leg never rejects the other.
- **The write point is the delete choke.** `ledger.bulk_delete_transactions` (and
  `delete_transactions_where` for every filter except `batch_id`) writes the records inside the
  same unit of work as the delete. Merge tombstones (`merged_into`) are skipped — a merge is a
  fold, and the ref legitimately lives on the canonical row.
- **Rollback ≠ reject** (unchanged from 2026-08-04). Batch rollback — `bulk_soft_delete`,
  `delete_transactions_where(batch_id=…)` — writes nothing: undoing a bad column mapping must
  leave the statement re-importable. Trade undo (`equity`/`lots`) composes the delete with
  `reject_identities=False` for the same reason.
- **The consult is at every create choke, outside the force gate.** `create_transaction` and
  `bulk_create_transactions` check the record after L0 and the semantic guard regardless of
  `force` / `dedup_by_external_ref`. A live row holding the ref still wins as idempotent
  `already_recorded` — a rejection can only fire when no live row holds the ref. An edit of a
  live row (`update_transaction`'s inner rebuild) bypasses the check: a stale record must never
  refuse an edit.
- **The outcome is its own word, never silent.** Service: `PreviouslyDeletedError` (409,
  `previously_deleted`). Tool `add_transactions`: per-row status `previously_deleted`, a
  `previously_deleted` list with the deleted id and date, a summary tally, and a note that
  says NOT BOOKED — excluded from `undecided_matches` because there is no decision to make.
  Statement commit: `refused_duplicates[i].previously_deleted = true`. REST: 409 with
  `retry_with: {resurrect: true}`.
- **Resurrection is a separate consent.** `TransactionCreate.resurrect` and the tool's
  `decisions: [{index, action: "create"}]` are read OUTSIDE the `force` / `allow_duplicate`
  gate. A resurrected row books, stamps `meta["resurrected_from"]`, and forgets its record in
  the same unit of work — deleting it again remembers it again.
- **Forget paths.** `GET /api/matches/rejected` lists; `DELETE /api/matches/rejected/{id}`
  forgets one; un-deleting a row (`decisions._restore_deleted`) forgets its records.
- **Checkpoint-included and portable** (`PORTABLE_FILES` + `portable_bridge.BRIDGED`) — a
  restore that dropped the records would resurrect every deleted row at once.
- **No backfill.** The migration creates an empty table; the 224 already-deleted ref'd rows on
  the live book are not remembered. A record asserts the owner *meant* the delete, and history
  cannot say which of those were rollbacks. The memory starts at this revision.

## Consequences

### Positive

- Closes the live loop for every path that re-delivers an event — automation runs, chat
  bulk-adds, REST, statement re-imports — with one record and one consult, instead of a
  per-adapter fix.
- `force` keeps its meaning. Nothing that used to book under `force=True` stops booking except
  a row the owner deleted, and that row is refused by name with the deleted id and date.
- The record is reversible three ways (resurrect, forget, un-delete) so a mistaken delete is
  never a permanent hole.
- Growth bound, accepted rather than enforced: one record per deleted ref'd row, ~200 bytes,
  bounded by the number of deletes the owner performs. Measured on the live book: 335 deleted
  rows over eight months, 224 of them ref'd, against 2,180 rows in total. That is a fraction of
  the ledger and grows only with the owner's own decisions — nothing like `email_snapshots`'
  unreferenced rows, which grow with every fetch.

### Negative

- **Deleting a mis-logged row and re-importing must now be asked for.** The 2026-08-12 board
  note called this out and the owner accepted it: the automation will report the row
  `previously_deleted` and stop, and the owner says "book it anyway". This is the intended
  cost — the alternative was the loop.
- The pre-flight in the tool is a second read of the store per batch (one `index()` read, never
  per row). The choke enforces; the pre-flight exists only so the model sees the refusal before
  it writes, and it fails open.
- A rejected identity is invisible in the register. The REST list exists; a settings surface
  for it does not yet.
- Two more tables in the standalone ledger lock set (`transactions`, `txn_audit`,
  `rejected_identities`) — composed callers that already hold the first two must pass
  `reject_identities=False` rather than re-enter the lock. A guard test sweeps `equity.py` and
  `lots.py` for a composed delete that forgot to.
- Statement preview does not yet flag a previously-deleted row before commit — it surfaces in
  the commit outcome instead.

### Follow-ups

- A settings surface for the rejected list (REST is in place).
- Flag previously-deleted rows in the statement preview, not only at commit.
- The statement-review "reject" verb as a second write point, once it exists as a ledger write.
- `/security-review` + `/code-review` on the ledger write-path changes.

## Alternatives considered

- **Consult deleted rows directly (`deleted_by_external_ref`, the 2026-08-12 board proposal)** —
  rejected: it cannot distinguish a rollback from a delete, so a bad column mapping becomes
  un-re-importable; and a bare-ref lookup collides the two legs of a Wise chain.
- **Per-account key `(account, source, ref)` (the 2026-08-04 sketch)** — rejected: the two legs
  of one Wise email share the account; only the movement separates them, and `_batch_l0` already
  keys on it.
- **Consult inside the opt-in `dedup_by_external_ref` floor only** — rejected: the floor is off
  by default and every duplicate override skips it, so the automation path that produced the
  live bug would never have reached it.
- **Let `force=True` / `allow_duplicate` also resurrect** — rejected: skipping duplicate
  detection and overriding the owner's own delete are two consents; the tool already reaches for
  `allow_duplicate` on any refusal, which would make the memory decorative.
- **Reuse the `match_review` decisions store** — rejected (as in 2026-08-04): its key is a pair
  of live rows; this key is a single identity with no live row.
- **Hard-delete plus a row tombstone** — rejected: soft delete is the audit convention, and the
  record must outlive the row.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner (owner drove the framing: "force should
  allow adding duplication for sure … the only thing here is, how we can ensure that if something
  was deleted, it will be skipped")
- **Lane / branch:** `main`, single lane
- **Alternatives weighed:** consult deleted rows · per-account key · opt-in floor only · force
  resurrects · reuse the match-review store · hard-delete + tombstone
- **Driving artifacts:** ADR `2026-08-04-a-rejected-import-identity-is-remembered` (amended);
  board items IMP3 / DUP8; a live automation run that re-booked a hand-deleted charge;
  `tests/test_deleted_identity_is_remembered.py`, `tests/db/test_rejected_identity_store.py`
- **Why this way (one line):** a soft delete says the row is gone, not that the owner meant it —
  so the delete choke writes the second fact, every create choke reads it, and only an explicit
  decision overrides it.
