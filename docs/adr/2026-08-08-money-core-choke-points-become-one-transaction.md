---
title: Money-core write chokes compose through locked_unit_of_work (global alphabetical lock order); restore is fail-closed + staged-file hardened; Alembic connections now enforce real FKs; parsed-file deletes ride the commit, not the transaction
status: Accepted
date: 2026-08-08
deciders: Claude (implementer agents, storage plan 3 tasks 1/3/4/6/8 — choke seam, absorb, chokes, portable bridge, rehearsal)
---

# 2026-08-08. The money-core choke seam: one transaction per multi-store write, fail-closed restore, real FK enforcement in Alembic, and the parsed-file lifecycle scars

## Context

`JsonStore` never had cross-store atomicity — the P1-A gap the storage-engine spec's D2 names
directly: "the 4-store vest sequence becomes one `BEGIN … COMMIT`." Plan 3 needed this delivered
for the ledger's own multi-store money chokes (`create_transaction`, `bulk_create_transactions`,
`update_transaction`, `bulk_delete_transactions`, `merge_transactions`, `unmerge_transactions`,
`set_account_opening` — each composing writes across `transactions`/`accounts`/`txn_audit`/
`merge_journal`). Three more forces were live at the same time:

- Plan-2's final review (recommendation 5) required restore hardening: the pre-restore capture
  needed to be fail-closed (an image that cannot be recorded must abort the restore before any
  mutation, not swallow the failure), and file writes needed to avoid a long "some reverted, some
  not" window.
- Task 3 (the money absorb, revision 0007) needed its own Python-level cross-file consistency
  pre-checks (`verify_cross_file_consistency`) as the binding referential-unit guarantee — which
  turned out to be **necessary, not redundant**, because of a real gap found empirically: `PRAGMA
  foreign_keys=ON` is NOT actually active during a migration's own Alembic connection.
- Task 6 (the portable bridge / undoable money imports) needed a statement's fat parsed-rows
  payload to live outside the database (`DATA_DIR/statements_parsed/<id>.json`, referenced by a
  promoted `parsed_rows_path` column) — a filesystem artifact a SQL transaction cannot make
  atomic with the DB rows that reference it.

## Decision

We will:

1. **Every multi-store money choke composes through `backend.db.engine.locked_unit_of_work(*tables)`**
   — it acquires every composed table's per-table `threading.Lock` in ONE global order
   (alphabetical by table name, collapsing duplicates), then opens `unit_of_work()`. A `conn=`
   threaded into each store mutate tells it to skip its own lock/transaction, since the caller
   already holds both for the whole composed write. Every caller MUST sort the same way — one
   caller acquiring `(accounts, transactions)` while another acquires `(transactions, accounts)`
   directly (bypassing this seam) is the textbook lock-ordering deadlock this function exists to
   close; a two-thread, opposite-order test (`tests/db/test_choke_seam.py`) proved this
   deadlock-free before any real caller existed. Audit-trail appends (`txn_audit`) ride the SAME
   transaction as the money write they record, so a crash mid-choke can no longer leave a
   committed money write with no audit row, or vice versa.
2. **Restore is fail-closed on its DB capture half.** `_capture_db_pre_restore` now calls
   `record_db_before(..., fail_closed=True)` — an image that cannot be recorded aborts the WHOLE
   restore before `_replay_write_log` or any file write runs, instead of letting a silently
   incomplete undo snapshot go on to be mutated behind. (`record_db_before`'s ordinary in-turn
   capture stays best-effort — only the pre-restore safety snapshot's own capture is upgraded to
   fail-closed, since THAT snapshot is the one thing standing between a restore and losing the
   ability to undo the restore itself.)
3. **Restore's file half stages before it commits.** File bytes write to `<dest>.restore-tmp-<id>`
   BEFORE the DB replay's transaction commits; only after a successful commit does the loop become a
   fast rename (or unlink) per file. This shrinks the split-world window (DB reverted, files not yet
   reverted) from the whole read-blob→decompress→write sequence down to just that final rename
   loop — a crash inside it leaves an untouched destination beside its already-staged temp, and
   simply re-running `restore()` finishes the job (the rename loop is idempotent).
4. **Alembic's own migration connection now enforces real foreign keys on SQLite, closing a gap
   found empirically, not assumed.** `db/engine.py`'s FK pragma is wired via a `connect`-event
   listener scoped to ONE engine instance (`_make_engine`); Alembic's `env.py` was calling a bare
   `create_engine(url)` — a *different* engine instance, without that listener — so every SQLite
   migration (including the money absorb) ran with FK constraints silently unenforced at the DB
   level. This is exactly why task 3's absorb needed its own Python-level
   `verify_cross_file_consistency` as the REAL, dialect-independent enforcement mechanism during
   absorb (Postgres always enforces FKs regardless of any pragma, so there it was always
   defense-in-depth; on SQLite it was the ONLY enforcement). `env.py` now reuses
   `db.engine._make_engine` — the same function every real store uses via `get_engine()` — instead
   of the bare call, so every SQLite migration connection enforces real FKs exactly like the app's
   runtime engine does. A blast-radius experiment (forcing the pragma on and replaying revisions
   0001–0007 under it) proved this a pure hardening, not a behavior change, before it landed.
5. **Statement parsed-file deletes ride the commit, never the open transaction — closing two
   related, sequentially-discovered scars.**
   - **Scar 1 (mid-transaction unlink):** the original money-bridge wipe path called `.unlink()` on
     a statement's parsed-rows file WHILE the enclosing transaction was still open. Reproduced
     directly: seed a statement with a real parsed file, force a LATER failure in the same money
     import (an unrelated `IntegrityError`) — the DB row correctly rolls back, but the FILE it
     pointed at was already deleted outside the SQL transaction's control, and
     `StatementStore.all()`'s unconditional re-inline of every row then raised on that one row and
     **permanently bricked reads of the whole store**. Fixed by making every `_wipe_*` function
     return the `list[Path]` it WOULD delete instead of deleting inline; the real `.unlink()` calls
     moved to a new `_unlink_best_effort` in `services/portability.py`, called strictly OUTSIDE the
     `with unit_of_work()` / `with locked_unit_of_work()` block — a delete is now reachable only
     after a real commit, symmetric with the transaction's own rollback-on-exception by
     construction, not by timing luck. Defense-in-depth alongside it: `_reinline_statement` now
     tolerates a missing/corrupt parsed file (logs a warning, returns `parsed_rows=None`) instead of
     raising and bricking every other row's read.
   - **Scar 2 (the deferral's own regression — live-set subtraction):** Scar 1's fix introduced a
     NEW bug, reproduced independently by the review: a bundle that reimports the SAME statement id
     writes NEW parsed content to the SAME deterministic path (`statements_parsed/<id>.json`,
     derived from the id) inside the SAME transaction whose wipe phase had already QUEUED that exact
     path for post-commit deletion. The post-commit unlink then deleted the freshly-rewritten file —
     and Scar 1's own tolerant-reinline fix masked the resulting `FileNotFoundError` as a silent
     `parsed_rows=None`, so the failure was invisible. Fixed by having `apply_money_bundle` RE-QUERY
     the LIVE `parsed_rows_path` set (on the SAME connection, so it sees its own uncommitted writes)
     after the import phase completes, and SUBTRACTING that live set from the collected delete list
     before returning. The invariant is now checked against the table's actual final state — "a path
     is deleted only if it is orphaned by the wipe AND not referenced by any row alive at commit" —
     rather than inferred from which ids happened to repeat, so it is robust against any future
     writer that lands a file at a previously-collected path, not just the reproduced same-id-
     reimport shape.

**Verified at production scale (2026-08-08 dress rehearsal 3):** this task's own `/rewind` proof is
a direct, real-data exercise of items 1–3 above — one non-opening transaction's `memo` field was
edited via `services.ledger.update_transaction` inside a `locked_unit_of_work`-composed choke,
wrapped in a real `begin_turn`/`end_turn` pair, then reverted via `ai.checkpoint.restore(mode=
"data")`. The transaction's stored payload and its full raw table row were captured before the edit
and after the restore and found **byte-identical** (sha256 match); a snapshot of
`statements_parsed/` taken at both points was also identical, confirming the file-side of the
checkpoint mechanism stayed correctly inert for a choke that never touches files. Full mechanism:
`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-8-report.md`.

## Consequences

### Positive

- The vest-era atomicity promise ("the 4-store sequence becomes one `BEGIN … COMMIT`") is now
  delivered generically for every multi-store money choke, not hand-rolled per call site.
- Restore is provably fail-closed on its DB half and has a bounded, recoverable split-world window
  on its file half — a crash mid-restore no longer requires manual recovery beyond re-running
  `restore()`.
- The money absorb's referential-unit guarantee is real on SQLite (previously it looked enforced by
  a pragma that silently wasn't active during the one operation that mattered most).
- The statement parsed-file lifecycle is now transaction-safe under both failure shapes actually
  found (mid-transaction delete on rollback; deferred-delete racing a same-transaction rewrite) —
  not just the first one discovered.

### Negative

- `locked_unit_of_work`'s global-alphabetical-lock-ordering contract is a discipline, not a
  compiler-enforced rule: a future multi-table money choke that composes its writes WITHOUT going
  through this seam can silently reintroduce the exact deadlock class it exists to close. Nothing
  in the type system stops that mistake.
- The FK-pragma fix touches `env.py`, a file every Alembic revision shares — its blast radius had to
  be proven experimentally (replaying 0001–0007 under the newly-enforced pragma) rather than being
  obviously safe from reading the diff alone.
- The parsed-file lifecycle needed two fix rounds to close (Scar 1's own fix introduced Scar 2) —
  this is recorded as a real cost, not smoothed over: a file path derived deterministically from a
  row id, shared across a wipe/import/delete sequence within one transaction, is a genuinely sharp
  edge, and no further gap is currently known but the pattern (not just this instance of it) is
  worth remembering.

### Follow-ups

- Any future multi-table money write must be reviewed for `locked_unit_of_work` composition at
  review time — this is a code-review checklist item, not something CI currently catches
  structurally.
- Any future filesystem-sidecar pattern (a DB row's promoted column pointing at a deterministically
  named file — the same shape `statements_meta.parsed_rows_path` uses) should be checked against
  all THREE parsed-file scars found in this area before shipping: (a) never unlink inside an open
  transaction; (b) never trust a collected delete-set without re-checking it against final live
  state when the same transaction can also REWRITE a path it collected; (c) the invariant covers
  file WRITES too, not just deletes — never write to a LIVE path inside an open transaction either.
  Scar (c) was found empirically, not hypothetically: `money_io._statement_row` wrote a statement's
  sidecar straight to its live final path DURING `apply_money_bundle`'s transaction, so a LATER file
  in `_MONEY_IMPORT_ORDER` failing (`merge_journal.json` is last) rolled the DB row back to its
  pre-import content while the file stayed at the failed import's content — silently served under
  the restored row's original identity, with no warning. Fixed the same way as (a)/(b): stage the
  write to a temp path (mirroring `ai.checkpoint`'s own restore-staging pattern,
  `_stage_restore_files`/`_commit_staged_restore_files`) and rename it into place only after commit,
  with the same transaction cleaning up its own staged temps on rollback. See the task-6 report's
  final-review-fix section for the reproduction and regression test.

## Alternatives considered

- **Fix the FK-pragma gap by touching every migration file individually** — rejected: `env.py` is
  the one shared connection-construction seam; fixing it there closes the gap for every existing
  revision (0001–0007) retroactively with one change, verified once via the blast-radius replay,
  instead of seven separate edits each needing their own justification.
- **Fix Scar 2 by subtracting only the same-id-reimport case** (the narrower fix matching the
  exact reproduction) — rejected in favor of the general live-set re-query: the narrow fix would
  only close the ONE reproduced shape, while the general form ("never delete a path a live row still
  references, checked directly") is robust against any future writer landing a file at a previously
  collected path, not just a reimport.
- **An engine-level `BEGIN IMMEDIATE`-style lock instead of per-table Python locks** — rejected,
  consistent with the sibling `EvidenceStore` write-serialization ADR's reasoning: broader than any
  single task's scope and a regression for WAL read concurrency; per-table locks composed through
  one ordering function give the same deadlock-freedom without that cost.
- **Leave the pre-restore capture best-effort, matching the ordinary in-turn capture** — rejected:
  the pre-restore snapshot is categorically different (it is the ONE thing that lets a restore
  itself be undone); a best-effort failure there can silently leave a restore with no way back,
  which the ordinary in-turn capture's best-effort failure does not risk to the same degree.

## Agent provenance

- **Decided by:** Claude (implementer agents across storage plan 3 tasks 1 [choke seam + restore
  hardening], 3 [FK-pragma finding], 4 [FK-pragma fix inclusion + choke composition], 6 [parsed-file
  lifecycle, both fix rounds]; recorded by the task 8 implementer agent, which independently
  re-verified the choke seam and restore mechanism via the production-scale rehearsal cited above).
  Lane/branch: `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** per-migration FK-pragma fixes were rejected in favor of the one shared
  `env.py` seam; a narrow same-id-only fix for Scar 2 was rejected in favor of the general live-set
  re-query; an engine-level lock was rejected on the same WAL-concurrency grounds the sibling
  evidence-store ADR already established; a best-effort pre-restore capture was rejected as
  categorically riskier than the ordinary in-turn capture it was modeled on.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D2, D7); `backend/db/engine.py`'s `locked_unit_of_work` docstring; `backend/db/alembic/env.py`'s
  own inline writeup of the FK-pragma finding; storage-plan3 task 1, 3, 4, and 6 reports (the latter
  covering both parsed-file fix rounds) under
  `.superpowers/sdd/2026-08-07-storage-plan3-money-core/`; ADR
  `2026-08-07-write-log-row-level-checkpoints` (the restore mechanism this ADR hardens); ADR
  `2026-08-07-evidence-store-write-serialization` (the precedent for rejecting an engine-level lock);
  this task's own rehearsal 3 report
  (`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-8-report.md`).
- **Why this way (one line):** a multi-store money write is only as atomic as its LEAST atomic
  component — the DB transaction, the lock order, the migration's own FK enforcement, and any
  filesystem side effect all had to be independently hardened and independently proven, because a
  single strong link (the DB transaction) does not make the whole chain strong.

