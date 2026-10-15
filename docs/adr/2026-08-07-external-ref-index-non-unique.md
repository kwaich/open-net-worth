---
title: transaction_external_refs(book_id, source, ref) index is non-unique — merges legitimately duplicate a pair across two live rows
status: Accepted
date: 2026-08-07
deciders: Claude (implementer agent, storage plan 3 task 4 — money core on Core)
supersedes: docs/adr/2026-07-21-collapse-external-refs-to-one-list.md (extends it — no change to that ADR's decision, only to a schema constraint task 2 layered on top of it that turned out to contradict it)
---

# 2026-08-07. `transaction_external_refs`'s (book_id, source, ref) index is non-unique by design

## Context

Storage Plan 3 task 2 introduced `transaction_external_refs` — a child table that explodes each
transaction's embedded `external_refs: [{source, ref}]` list (ADR 2026-07-21) into one row per
pair, so `TransactionStore.has_external_ref`/`by_external_ref` (the dedup engine's idempotency
check) could hit an index instead of scanning every transaction's payload in Python. Task 2
declared the natural-seeming index `Index("ix_transaction_external_refs_book_source_ref",
"book_id", "source", "ref", unique=True)` — reasoning, per its own fix-round-1 note, about the
already-known gap that SQL `NULL <> NULL` lets two `(book_id, None, ref)` rows coexist even
under a unique index, and treating that as the only case worth a comment.

Task 4 (this task) rewrote `TransactionStore` to be DB-backed and, in doing so, wrote the first
tests that actually insert two DIFFERENT transactions sharing a non-null `(source, ref)` pair —
the shape produced by an ordinary merge, not an edge case. `services.ledger.merge_transactions`
step 6 ("Append the absorbed's provenance pairs ... into the canonical's `external_refs`")
unions the absorbed row's pairs onto the canonical row; step 7 tombstones the absorbed row
(`deleted=True`, `merged_into=<canonical>`) but — deliberately, per ADR 2026-07-21 and
`by_external_ref`'s own docstring ("the canonical row already carries the absorbed pair in its
own `external_refs[]`") — never clears the absorbed row's own `external_refs`. After an ordinary
merge, the SAME non-null `(book_id, source, ref)` tuple is therefore live in TWO rows'
`external_refs` at once: the canonical (not deleted) and the absorbed (soft-deleted, not hard-
deleted). `transaction_external_refs` explodes both rows unconditionally — deletion status is a
column on `transactions`, not a filter this child table applies to itself — so both rows'
children carry the identical triple.

A plain `Index(..., unique=True)` has no `WHERE deleted = 0` clause (SQLAlchemy Core's `Index`
does not support a partial/filtered unique constraint portably across SQLite and Postgres without
dialect-specific DDL, which the task's cross-dialect binding rules out as a first move), so this
is not a NULL-source-only gap — it is a direct collision with the dedup engine's core merge
operation. Reproduced directly, with no live data needed, by this task's own
`tests/db/test_transaction_store_db.py::test_by_external_ref_excludes_deleted_but_merge_tombstone_covered_by_canonical`
(a two-row merge scenario) and independently corroborated by a concurrently-running sweep-fix
agent against `tests/dedup/test_e2e_flows.py::test_e2e_transfer_pair_accept` /
`test_e2e_deleted_row_reimport_restore`, both of which hit the identical
`IntegrityError: UNIQUE constraint failed` before this fix landed.

The app-level dedup check was never actually delegated to the SQL constraint: both
`has_external_ref`/`by_external_ref` and the L0 floor in `matching.check` have always compared
`(source, ref)` tuples in Python (`core.txn_sources.ext_ref_pairs`) — the store's own docstring
states the index exists "as a lookup accelerant instead of a full Python scan," never as the
authority. `unique=True` added an enforcement layer under a query the domain logic never asked to
be enforced at the SQL layer, and that layer was WRONG for real, intended domain state.

## Decision

Change the index to `unique=False` in both `backend/db/tables.py` (the live schema) and the
already-landed migration `backend/db/alembic/versions/0006_money_core_schema.py` (edited in
place, not superseded by a new revision) — the index stays as a plain composite index (still
serves `by_external_ref`'s equality lookup at the same O(log n) cost; only the constraint is
removed). Editing revision `0006` directly follows storage plan 3 task 2's own established
precedent (its fix round 1 edited `0006` in place for the NULL-source finding) and its stated
justification still holds: `0006` has never absorbed a real user's data outside this dev branch,
so there is no shipped/applied state whose compatibility an in-place edit could break. `alembic
check` was re-verified clean against a fresh scratch database after the edit.

The app-level Python tuple-compare in `by_external_ref`/`has_external_ref` remains, unchanged,
the SOLE dedup authority — this decision removes an incorrect SQL-level constraint, it does not
add or change any application-level dedup logic.

Two existing tests in `tests/db/test_money_tables.py` (task 2's own) were updated to match:
`test_transaction_external_refs_null_source_duplicate_not_caught_by_index`'s docstring now frames
the NULL-source gap as one instance of the general non-unique rule rather than a standalone SQL
quirk; `test_transaction_external_refs_source_ref_unique` (which asserted the now-corrected
`IntegrityError` behavior) was renamed to
`test_transaction_external_refs_source_ref_index_is_non_unique_by_design` and rewritten to assert
that both inserts succeed.

## Consequences

### Positive

- The dedup engine's core merge operation (two transactions sharing an authoritative,
  non-null-source external ref, one of them merged into the other) works again — it was broken
  the moment task 2's schema landed, but nothing before this task's tests exercised the DB-backed
  store deeply enough to reproduce it against real insert semantics (task 2/3 tests exercised the
  table shape and the cutover/absorb path, not this write pattern via the live store).
- No change to the dedup contract itself: the Python tuple-compare was always the authority, so
  removing the redundant/incorrect SQL constraint changes nothing about which rows are considered
  duplicates from the application's point of view.
- `by_external_ref`'s lookup stays index-backed (same query plan, same complexity) — this is a
  constraint removal, not a performance regression.

### Negative

- `transaction_external_refs(book_id, source, ref)` can no longer be relied on, AT THE SCHEMA
  LEVEL, to prove a pair is singly-owned — any future code that queries this table directly
  (bypassing `by_external_ref`) must not assume at most one row matches a given triple. This is
  already true in practice (a merge produces exactly two), but the schema no longer even
  ASPIRATIONALLY promises otherwise.
- A future engineer proposing a partial/filtered unique index (`WHERE deleted = 0` scoped to
  `transactions`, which would require a join or a denormalized `deleted` column on the child
  table itself) needs to re-derive this reasoning; this ADR is that record.

### Follow-ups

- None required — the fix is complete and self-contained (schema + migration + two test files).
  If a future change wants schema-level dedup enforcement, it must be scoped correctly (per-
  canonical-row, filtered by `transactions.deleted = 0`, likely via a partial index or a trigger)
  rather than a bare composite unique index, and should re-examine whether that's worth the
  cross-dialect complexity given the Python layer already does the job.

## Alternatives considered

- **Keep `unique=True`, strip the absorbed row's `external_refs` on merge instead.** Rejected —
  this would reverse ADR 2026-07-21's explicit design (`by_external_ref`'s docstring: "the
  canonical row already carries the absorbed pair in its own `external_refs[]`" is stated as the
  no-op-guarantee mechanism, not an incidental detail) and `unmerge_transactions`' byte-exact undo
  contract snapshots and restores the absorbed row's PRE-merge state verbatim — scrubbing its
  `external_refs` at merge time would mean undo no longer restores the row it actually was.
- **A partial/filtered unique index (`WHERE deleted = 0` on the joined `transactions` row).**
  Deferred, not rejected outright — SQLAlchemy Core's `Index` does not express a portable partial
  index with a condition on a DIFFERENT table across both SQLite and Postgres without dialect-
  specific DDL (`sqlite_where=`/Postgres partial-index syntax diverge), and the task's binding
  scope is a targeted correctness fix, not a new cross-dialect index mechanism. Left as a
  documented follow-up, not pursued because the Python-layer dedup already does the job the index
  would otherwise exist to backstop.
- **Leave the bug and route around it in the store** (e.g. catch `IntegrityError` in
  `_sync_children` and skip the colliding child row). Rejected — that would silently DROP a
  ref pair from whichever row's insert lost the race, corrupting the round-trip guarantee
  `_explode_children`/`verify_transaction_children` exist to protect (a child table must always
  be an exact, complete explosion of its parent's payload — task 3's own binding).

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 3 task 4 (money core on Core — the six
  live JsonStore-backed money stores' DB cutover). Found via this task's own
  `tests/db/test_transaction_store_db.py` while proving `TransactionStore`'s rewrite against a
  real merge scenario; independently corroborated the same session by a parallel sweep-fix agent
  hitting the identical `IntegrityError` in `tests/dedup/test_e2e_flows.py`. Lane/branch:
  `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** scrubbing the absorbed row's refs at merge time was rejected against
  ADR 2026-07-21's explicit text and `unmerge_transactions`' undo contract; a partial/filtered
  unique index was deferred as unnecessary cross-dialect complexity given the Python layer is
  already the dedup authority; swallowing the collision in the store was rejected as silent data
  loss against task 3's round-trip-explosion binding.
- **Driving artifacts:** `docs/adr/2026-07-21-collapse-external-refs-to-one-list.md` (the list-
  union merge design this schema constraint contradicted); `backend/services/ledger.py`'s
  `merge_transactions` (step 6, the union-without-clearing behavior) and `unmerge_transactions`
  (the byte-exact undo contract that rules out scrubbing); `backend/db/transaction_store.py`'s
  `by_external_ref` docstring (states the covering-canonical-row guarantee directly); the plan-3
  task-4 report (`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-4-report.md`) for the
  full reproduction trace.
- **Why this way (one line):** a SQL constraint layered on top of a list-union merge design must
  match what that design actually produces on disk — two live rows sharing a pair is intended
  state, not corruption, so the schema must not forbid it.
