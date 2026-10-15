---
title: Schema migrations are fail-closed behind a verified backup; `downgrade` is a dev/CI tool, never the production rollback
status: Accepted
date: 2026-08-07
deciders: Claude (implementer agent, storage plan 1) + owner ratification (spec 2026-08-05, Decision D3)
---

# Schema migrations are fail-closed behind a verified backup; `downgrade` is a dev/CI tool, never the production rollback

## Context

Before this plan, ~30 ad-hoc `backend/scripts/migrate_*.py` ran at boot from `main.py`, each
wrapped in `except: _log.exception(..., "non-fatal")`. There was no `schema_version`, no ordering
guarantee, no backup-before-migrate, and no way to know whether a given install had applied a
given fix — **fail-open on financial data**. This is the same class of defect
`2026-07-09-jsonstore-fail-closed-on-corrupt` already closed for a corrupt JSON file
(`JsonStore` quarantines and raises `DataIntegrityError` rather than silently reading a corrupt
file as empty); schema migrations had no equivalent posture at all.

The spec is explicit about why `downgrade` cannot be the real safety net: **"a data-destructive
downgrade is a lie."** If an `upgrade` drops a column, `downgrade` can recreate the column but not
its contents. Any rollback story that leans on `downgrade` alone is therefore lying to whoever
reads "rollback available" as "your data comes back."

Alembic gives ordered, versioned `upgrade`/`downgrade` revisions with an `alembic_version` stamp
inside the database itself — the mechanism to replace the fail-open scripts with. The question
this ADR answers is what *wraps* that mechanism so a migration failure on a live financial
database cannot silently corrupt or half-apply.

## Decision

We will make `backend/db/migrate.py::ensure_migrated()` the single boot-time gate — called first
in the FastAPI `lifespan`, before any store is read — with this posture:

1. **Verified backup is the actual rollback**, not `downgrade`. On SQLite (the shipped default):
   `sqlite3`'s native backup API (consistent under WAL) copies the database to
   `DATA_DIR/backups/db/pre-migrate-<ts>.db`, which is then **read back and `PRAGMA quick_check`ed
   before the migration is allowed to proceed** — a backup that fails its own integrity check
   aborts the migration before a single DDL statement runs. On Postgres, Plan 1 has no automatic
   equivalent: the operator must take their own `pg_dump` and explicitly set
   `ONW_MIGRATE_ASSUME_BACKUP=1`, or boot refuses outright (`_require_backup_or_refuse`).
2. **Any exception during `upgrade head` aborts and restores.** `ensure_migrated` disposes pooled
   connections, runs the upgrade, and on ANY exception restores the verified backup over the live
   database file and **raises `MigrationError`** (a `DataIntegrityError` subclass — the same family
   the JsonStore fail-closed posture already established) so the app **refuses to start**. There is
   no `except: "non-fatal"` anywhere on this path.
3. **Compound-failure reporting.** If the restore itself ALSO fails (e.g. the disk that produced
   the migration failure also breaks the restore's `os.replace`), both failures are named in one
   `MigrationError` — the highest-stakes moment in the whole flow gets the most explicit guidance
   (the exact backup path to restore manually), never a silently-swallowed second exception.
4. **`downgrade` stays implemented and tested for every revision** — it is the dev-loop and
   CI-reversibility tool (`tests/db/test_migrations.py::test_upgrade_downgrade_upgrade_cycle` runs
   `upgrade head → downgrade base → upgrade head` on both dialects), not the production safety net.
5. **Expand/contract for anything destructive.** A destructive schema change ships as: add column →
   backfill → dual-read → switch reads → drop the old column, with the drop landing in a *later*
   release. Any single shipped release therefore stays losslessly reversible by its own
   `downgrade`; the lossy step only ships once the version that needed the old shape is dead. This
   is a review-time discipline this ADR records, not (yet) a mechanically enforced one.
6. **Per-row cutover commits.** `_finish_cutovers` renames each absorbed source JSON file to
   `<name>.json.migrated-<ts>` (never deletes it) and marks its `json_cutovers` row `renamed=1` in
   its **own** transaction, one row at a time — found necessary during task 5's review: a single
   shared transaction let a LATER store's absorption failure roll back an EARLIER store's already
   -completed, already-renamed cutover's bookkeeping, desynchronizing the DB's belief about what
   was renamed from the filesystem's actual state.
7. **Source-hash guard on rename.** The cutover ledger stores the source file's sha256 at the
   moment it was absorbed; `_finish_cutovers` recomputes the hash right before renaming and
   **refuses the rename** if it no longer matches — naming the exact recovery step (restore the
   newest `backups/db/pre-migrate-*.db`, reboot, let the migration re-absorb the newer file) rather
   than silently discarding bytes written to the source after it was already read into the
   database.
8. **The ~30 legacy fail-open `migrate_*.py` scripts are retired by absorption into Alembic
   revisions over Plans 2–4** — not this plan. Existing installs get `alembic stamp`-ed at the
   revision representing "everything up to the cutover has already run"; new installs run the
   equivalent revisions in order via ordinary `upgrade head`. Neither path can silently skip a fix
   again, once absorbed.

## Consequences

### Positive
- A financial app can no longer boot on a half-migrated schema — closes the exact "fail-open on
  financial data" gap the spec's Problem section opens with.
- The backup is *proven*, not merely attempted: a corrupt or incomplete backup is caught by
  `PRAGMA quick_check` before it is ever trusted as the rollback path.
- This extends, rather than reinvents, the repo's existing fail-closed philosophy —
  `MigrationError` is literally a subclass of `DataIntegrityError`
  (`2026-07-09-jsonstore-fail-closed-on-corrupt`) — so the mental model for an agent or reviewer is
  "the same rule, one layer up," not a second bespoke safety mechanism to learn.
- On SQLite (the common case today), this is fully automatic: zero operator action required for
  the backup-verify-migrate-or-refuse cycle to run correctly.

### Negative
- **Postgres gets no automatic backup in Plan 1.** An operator on Postgres who has not taken a
  `pg_dump`, or who sets `ONW_MIGRATE_ASSUME_BACKUP=1` without actually having a current one, has
  opted out of the real rollback path entirely — accepted for now because Postgres is CI-only in
  Plan 1 (no self-hoster runs it in production yet), but this is a documented gap that MUST close
  before Postgres is offered as a real deployment target (tracked in the companion engine ADR).
- **Expand/contract is a review discipline, not a mechanical gate.** Nothing today stops an author
  from writing a one-shot destructive `upgrade` with no staged backfill/dual-read — this ADR
  records the rule; `/code-review` and `/security-review` are what actually enforce it on a given
  PR.
- **The 30 legacy boot scripts are not retired by this plan.** They keep running, still fail-open,
  alongside the new fail-closed DB path until Plans 2–4 absorb them one by one — two migration
  philosophies coexist in the interim, and an agent reading `main.py` today will see both.
- The per-row cutover commit (point 6) means a crash partway through absorbing several stores in
  one boot leaves some `json_cutovers` rows `renamed=1` and others not — this is by design
  (idempotent: the next boot's `_finish_cutovers` simply continues from where it left off) but it
  does mean "the migration" is not always one atomic all-or-nothing unit once more than one store
  is being absorbed in the same release.

### Follow-ups
- Plans 2–4 absorb the remaining legacy `migrate_*.py` scripts into Alembic revisions; each
  absorption removes one `except: "non-fatal"` from the boot path.
- A Postgres-symmetric automatic verified-backup mechanism is required before Postgres moves from
  "CI-tested second dialect" to "supported production target."
- CI's `alembic check` (autogenerate against live `MetaData` must diff empty) and the
  `upgrade → downgrade → upgrade` cycle on both dialects are the two mechanical gates that exist
  today; a lint rule that flags a destructive op (`drop_column`, `alter_column` narrowing a type)
  without an accompanying expand/contract comment would close the remaining review-only gap named
  above, if it becomes a repeated mistake.

## Alternatives considered

- **Treat `downgrade` as the real rollback** — rejected: the spec's own "honest part about down."
  A destructive `upgrade`'s `downgrade` can restore the schema shape but not data it dropped;
  presenting that as the safety net would be a lie. It stays valuable as a dev-loop and
  CI-reversibility tool, just never the production guarantee.
- **Keep the existing fail-open `except: "non-fatal"` boot-script pattern for schema changes** —
  rejected outright: this is the exact defect the spec's Problem section names as its motivating
  example. A financial app that boots on a half-migrated database is worse than one that refuses
  to boot.
- **One shared transaction across all pending cutover renames** — rejected, and reproduced as a
  real bug during task 5's review: a later store's absorption failure inside one shared
  transaction rolled back an earlier store's already-successful, non-transactional filesystem
  rename's bookkeeping, leaving the DB's `renamed` flag disagreeing with what was actually on
  disk. Per-row commits (point 6) fixed this because the DB write and the filesystem rename have
  different failure domains and cannot be reconciled by batching them into one transaction.
- **Skip the source-hash check and always rename after a successful absorb** — rejected: without
  it, bytes written to the source file after it was read into the database (a stale writer, a
  manual edit, a future concurrent-store scenario) would be silently discarded the instant the
  rename fires. The hash guard turns a silent-data-loss risk into a loud, specific refusal.
- **Immediately migrate every legacy `migrate_*.py` script into this plan** — rejected as
  out of scope for Plan 1: the spec sequences their absorption across Plans 2–4, alongside the
  stores each script actually touches, rather than a big-bang rewrite disconnected from the
  cutover order.

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 1 (`lane/storage-plan1` worktree),
  ratifying spec decision D3 into a formal ADR at task 10, after `backend/db/migrate.py` had
  shipped, been fixed twice under review (tasks 5 and 9), and been exercised end-to-end by the
  live-data dress rehearsal (a real 86,145-row, ~106 MB `evidence.json` migrated in 3.4s with a
  clean round-trip verify and cutover rename).
- **Alternatives weighed:** see above — treating `downgrade` as the rollback and keeping the
  fail-open script pattern were the spec's own named rejections; the shared-transaction cutover
  and the source-hash guard were concrete defects found and fixed mid-implementation (task 5 and
  task 6 reviews respectively), folded in here as the decisions they actually were.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D3, "The honest part about down", "Fail-closed wrapper", "CI gates"); `backend/db/migrate.py`;
  `backend/core/errors.py`'s `MigrationError(DataIntegrityError)`; `2026-07-09-jsonstore-fail-
  closed-on-corrupt.md`; `.superpowers/sdd/2026-08-07-storage-plan1-substrate-and-evidence/
  progress.md` task 5 and task 9 review notes.
- **Why this way (one line):** the backup you verified before touching anything is the only
  rollback promise that is never a lie; everything else in this design exists to make sure that
  promise is kept automatically, and to say so loudly the one time it cannot be.

