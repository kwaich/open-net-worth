---
title: A database ahead of the code is refused untouched
status: Accepted
date: 2026-09-06
deciders: orchestrating Claude agent + repo owner
---

# 2026-09-06. A database ahead of the code is refused untouched

## Context

`db.migrate.ensure_migrated()` decides whether a migration is needed with `_pending(url)`,
which reads the database's `alembic_version` and compares it to this checkout's
`ScriptDirectory.get_current_head()` with a plain `current != head`. That comparison cannot tell
"this database is BEHIND — there is real pending work" apart from "this database is AHEAD — a
newer checkout already stamped a revision this code has never heard of." Both read as `True`.

**The incident (2026-09-06).** A launchd `KeepAlive` service kept an OLDER checkout running
against the owner's live database while a NEWER server (already migrated past it, at
`202609061000_owned_assets`) was serving real traffic on the same file. Every ~10 seconds the
older process's boot called `ensure_migrated()`, `_pending()` returned `True` (its own head did
not match the live revision), it took a verified pre-migrate backup, and `command.upgrade`
raised `alembic.util.exc.CommandError: Can't locate revision identified by
'202609061000_owned_assets'` — it has no script for a revision written after it was built. The
`except` branch then "restored" the backup it had just taken: `_restore` unlinks the `-wal`/
`-shm` sidecars and `os.replace`s the database file. Nothing had actually been migrated, so
there was nothing to restore — the restore itself was the damage, discarding the live server's
open WAL and orphaning its file handles across five detached copies of the database over
repeated retries.

**Follow-up security review, same day.** The first fix above only closed the AHEAD door. A
reviewer reproduced the identical damage through three others: (1) a database merely BEHIND
head, with another process — even a same-port "sibling" `acquire()` itself would wave through —
still holding the file open, still hit `_restore`'s old copy+unlink+`os.replace` on an unrelated
upgrade failure; (2) a populated database with NO `alembic_version` stamp at all read as "fresh,
safe to migrate" and got backed up and upgraded; (3) `ScriptDirectory.get_revision` accepts a
unique PREFIX of a real revision (a truncated stamp misread as "known") and raises a bare
`AssertionError` — not `CommandError` — for an empty string, escaping `ensure_migrated`
uncaught. All three are fixed in the same change described below.

**Second follow-up security review, same day.** A second reviewer found the first follow-up's
own tests vacuous and several smaller holes, all closed in this same change:

- **HIGH — the three tests exercising `_restore` were vacuous.** All three built "pending" with
  `command.downgrade(cfg, "base")` + a same-test `sentinel` table — exactly a
  populated-but-unstamped database, so `_UNSTAMPED` (the fix just above) refused it BEFORE
  `_sqlite_backup`, and neither `run_upgrade` nor `_restore` ever ran. The reviewer proved the
  inode-preservation test passed even with the OLD, destructive `os.replace` `_restore`
  reinstated, because the test never reached it. Fixed by downgrading to a real, KNOWN
  intermediate revision instead of `base`; a mutation check (below) now proves all three
  genuinely exercise `_restore`.
- **MEDIUM — order.** The revision-unsafe check ran AFTER the lock guard, so a database that was
  both ahead-of-code AND behind a lock holder burned the lock guard's bounded wait before ever
  reporting the real, cheaper-to-diagnose problem.
- **MEDIUM — the lock-wait poll only re-checked `_pending`, not the lock itself.** A holder that
  exited without finishing its own migration (crash, `kill -9`) cost the waiter the FULL timeout
  even though nothing was stopping it from proceeding the moment the lock freed.
- **MEDIUM — `held_by_other`'s own probe (`mkdir`/`open`) could raise a raw `OSError`** (a file
  already sitting where the working folder should be; a read-only folder) and escape
  `ensure_migrated` unwrapped.
- **MEDIUM — in-place restore cannot recover a destination whose own header the failed upgrade
  corrupted** — `sqlite3.DatabaseError: file is not a database` on the destination connection,
  before `backup()` can copy anything in at all.
- **MEDIUM — `env.py` never checked the instance lock**, only `refuse_live_instance`; a raw
  `alembic -x allow_live_instance=1 upgrade head` bypassed `ensure_migrated()`'s lock guard
  entirely.
- **LOW — `_finish_cutovers`'s rename could race a sibling.** MEDIUM 7 means a waiting sibling
  now wakes exactly when the migration owner finishes, so two processes can reach
  `_finish_cutovers` for the same row; a source file the OTHER process already renamed made
  `src.rename()` raise `FileNotFoundError`, previously treated as a hard failure.

## Decision

`ensure_migrated` now checks, for a pre-existing sqlite database, whether the CURRENT revision
is one this checkout's `ScriptDirectory` recognizes — BEFORE `_sqlite_backup`, before
`dispose_engine()`, before `run_upgrade` ever runs.

- **`_unknown_current_revision(url)`** reads the current revision (a plain, read-only
  connection, exactly `_pending()`'s own probe). A `None` current (fresh database) is trivially
  known; otherwise the check is EXACT-MATCH set membership —
  `current in {r.revision for r in script.walk_revisions()}` — not `ScriptDirectory.get_revision`,
  which accepts a unique PREFIX of a real id and resolves it (misreading a truncated/corrupted
  stamp as "known") and raises a bare `AssertionError`, not `CommandError`, for an empty string.
  A revision not in that set is one this build has never heard of, and the function returns it.
- **Called only when a backup is even possible** — a pre-existing sqlite file. A fresh database
  has no current revision to be unknown, and a non-sqlite target never takes an automatic backup
  to restore from in the first place (`_require_backup_or_refuse` covers it); scoping the check
  this way also means the extra connection is never opened at all on a URL a test or an operator
  has deliberately kept unreachable.
- **Refusal, not repair** — an unknown current revision raises `MigrationError` naming the
  revision, stating it was migrated by newer code, and refusing to touch the file: no backup, no
  upgrade attempt, no restore. The message points the operator at running the checkout that owns
  that revision, or upgrading this one.
- **Defense in depth left as-is** — `_restore` only ever runs inside the `except` around
  `run_upgrade(cfg)` itself; this decision adds a refusal BEFORE that block is ever reached for
  the ahead-of-code case, rather than widening what `_restore` is allowed to do.

**The three follow-up fixes, same posture (refuse before touching, never repair in place):**

- **A live-instance-lock guard, independent of revision comparison** — `ensure_migrated` now
  calls `core.instance_lock.held_by_other(DATA_DIR.parent)` right after `_pending()` returns
  `True`, for EVERY pending case, not just the ahead-of-code one. Unlike `acquire()`,
  `held_by_other` grants no same-port sibling exception — a migration is exactly the operation
  two workers of one instance must not race on. If another process holds the folder,
  `ensure_migrated` polls `_pending` for a bounded window (`_LIVE_LOCK_POLL_TIMEOUT_S` /
  `_LIVE_LOCK_POLL_INTERVAL_S`, both test-overridable): a legitimate sibling whose OWNER is
  mid-migration simply waits for the database to reach head and returns; if it is still pending
  when the window closes, `ensure_migrated` raises `MigrationError` naming the folder, having
  touched nothing. The check is run BEFORE the (possibly 30s) wait — the revision check above
  runs first, so a database that is both ahead-of-code and lock-held reports the cheaper,
  more-diagnostic fact immediately (second follow-up review, MEDIUM 5) — and the wait itself
  re-checks the lock every polling iteration, not just once up front, so a holder that exits
  without finishing its own migration frees the waiter immediately rather than costing it the
  full timeout (MEDIUM 7).
- **`_restore` writes IN PLACE by default, never copy + unlink sidecars + `os.replace` as the
  PRIMARY path** — `sqlite3.connect(backup).backup(sqlite3.connect(db_path))` (SQLite's own
  online backup API) copies through the DESTINATION's existing file and its own locks,
  preserving the inode. A reader that already has `db_path` open keeps working and sees the
  restored content on its next read, with no `-wal`/`-shm` sidecar unlinked out from under it.
  **This is not unconditionally better than the old path** (second follow-up review, MEDIUM 2):
  in-place restore cannot recover a destination whose own HEADER the failed upgrade corrupted —
  opening it as a destination connection raises `sqlite3.DatabaseError` before `backup()` can
  copy anything in at all. `_restore` falls back to the old copy-to-temp → unlink sidecars →
  `os.replace` path ONLY for that specific failure: a fresh file that actually recovers the data
  is strictly better than refusing to restore, and this is the one case where the inode-
  preservation property is deliberately traded away.
- **A populated database with no alembic stamp is refused, not treated as fresh** —
  `_unknown_current_revision` now distinguishes "no stamp because genuinely new" (zero user
  tables) from "no stamp on a database that already has real tables" (the `_UNSTAMPED` sentinel,
  refused with its own message) by counting `sqlite_master` rows — excluding `alembic_version`
  itself, which survives a downgrade to `base` with an empty row set and must not misread as
  "populated."
- **The "known" check is exact-match set membership, not `get_revision`** —
  `current in {r.revision for r in script.walk_revisions()}` replaces
  `script.get_revision(current) is not None`. `get_revision` accepts a unique PREFIX and resolves
  it (a truncated stamp read as "known") and raises a bare `AssertionError`, not `CommandError`,
  for an empty string (escaping `ensure_migrated` as an unhandled crash instead of a
  `MigrationError`). Set membership has neither failure mode.

**The second follow-up review's remaining fixes:**

- **`held_by_other`'s probe failure is a `MigrationError`, not a raw `OSError`** —
  `_refuse_if_locked_by_other` wraps the `held_by_other(folder)` call itself: a `mkdir`/`open`
  failure (a plain file already sitting where the working folder should be; a read-only folder)
  now raises `MigrationError(f"cannot probe the instance lock in {folder} ({exc}); refusing to
  migrate", path=str(folder))` instead of escaping as an unhandled crash.
- **`env.py`'s door gets the same lock check, without the wait** — a raw
  `alembic -x allow_live_instance=1 upgrade head` bypasses `ensure_migrated()` entirely, so its
  bounded-wait guard never ran either. `run_migrations_online()` now calls
  `held_by_other(DATA_DIR.parent)` itself, before `context.run_migrations()`, and raises its own
  clear `MigrationError` if another process holds the folder — no polling, since a raw CLI
  invocation is an operator/script action, not a boot racing sibling workers.
- **`_finish_cutovers` tolerates a sibling that already renamed the source** — MEDIUM 7 means a
  waiting sibling now wakes exactly when the migration owner finishes, so two processes can reach
  `_finish_cutovers` for the same row. If `src.rename()` raises `FileNotFoundError` (the file
  vanished between our own `exists()` check and the rename — another process got there first),
  that is logged and treated as success, not raised as `MigrationError`; every OTHER `OSError`
  is still a hard failure.

## Consequences

### Positive

- The incident is unrepresentable by the same sequence: an old checkout polling a live,
  newer database now refuses on the first check, every time, with no file ever touched — no
  backup written, no restore attempted, no `-wal`/`-shm` sidecar unlinked.
- The refusal message is actionable: it names the exact revision and the two ways out (run the
  checkout that owns it, or upgrade this one).
- Genuinely pending migrations (the database is BEHIND) are unaffected — `_unknown_current_revision`
  returns `None` for any revision this checkout's scripts recognize, which is every revision on a
  real upgrade path, so backup → upgrade → restore-on-failure still runs exactly as before.
- The BEHIND-and-another-process-has-it-open gap is closed the same way: a `_restore` that ever
  DOES run (a genuinely pending, genuinely unlocked migration whose upgrade fails for an
  unrelated reason) can no longer swap the inode or unlink a live sidecar, because it no longer
  does either — the fix is unconditional, not scoped to the ahead-of-code case.
- A legitimate sibling worker of one instance is unaffected in the common case (one worker
  actually owns the migration; the others wait and return once it clears) — only a lock holder
  that never finishes trips the bounded-wait refusal.

### Negative

- One extra read-only connection to the database on every genuinely pending migration
  (existing sqlite file case only) — negligible next to the backup that already follows it.
- A `KeepAlive`/autostart service running a stale checkout against a live, newer database will
  now fail its boot loudly and repeatedly instead of corrupting the file — this is the intended
  failure mode, but it does mean a stale service must still be found and stopped or upgraded by
  an operator; the guard does not do that part for them.
- A boot that races a slow sibling worker's migration now blocks for up to
  `_LIVE_LOCK_POLL_TIMEOUT_S` (30s in production) before either proceeding or refusing, instead
  of returning immediately — an intended cost of not letting two workers touch the file at once.
- `ensure_migrated` gained two more read paths (`held_by_other`'s flock probe, the
  `sqlite_master` table count) on every genuinely pending sqlite migration; both are cheap
  relative to the backup that already follows them.
- **On a platform without `fcntl` (Windows), the lock guard is a no-op** — `held_by_other`
  returns `False` unconditionally there, exactly matching `acquire()`'s own posture (logged and
  skipped rather than refusing to boot over a missing OS feature). The ahead-of-code and
  populated-unstamped checks are unaffected — they do not depend on `fcntl` at all — so the
  original incident stays closed on every platform; only the NEW behind-head-and-locked
  scenario (finding 1a) has no protection there.
- `_restore`'s copy+replace fallback (MEDIUM 2) reintroduces the inode-swap/sidecar-unlink shape
  for the one narrow case it exists for (a corrupted destination header) — a deliberate,
  documented trade: a fresh, readable file beats refusing to restore, but a reader with the file
  open at that exact moment sees the same disruption the in-place path was built to avoid.

### Follow-ups

- `CLAUDE.md`'s boot-migrations bullet and `docs/INVARIANTS.md`'s long-form entry updated in the
  same change.
- Whether an autostart/launchd service should itself detect "I am older than the database I am
  pointed at" and stop retrying (rather than boot-looping on a refusal) is left for a future
  change to the autostart tooling, not this fix.
- Recording the checkout's own version/path in the lock file, and changing how `main.py` acts on
  `acquire()`'s return value, were both explicitly left out of this change's scope.

## Alternatives considered

- **Make `_pending()` itself raise on an unknown revision** — rejected as the initial shape: it
  would also change behavior for callers (e.g. a monkeypatched `_pending` in existing tests) that
  only care about the pending/not-pending boolean, and `_pending` is called on every boot,
  including the non-sqlite path, which never takes a backup to protect in the first place. A
  narrower, separately-named check called only where the backup/restore hazard actually exists
  keeps `_pending`'s contract unchanged.
- **Widen `_restore`'s own guard instead** — rejected because by the time `_restore` would run,
  the backup has already been taken and `command.upgrade` has already been attempted against a
  database it cannot understand; the damage-shaped operations need to never start, not merely
  fail safely once started.
- **Treat this as identical to the live-instance guard (2026-09-03)** — rejected: that guard is
  about WHO is allowed to migrate a real book at all; this one is about a database that is
  already ahead of the code trying to migrate it, which can happen on a fully legitimate,
  intentionally-isolated instance too (e.g. a rollback to an older checkout during a bad
  deploy). The fix belongs at the revision-recognition check, independent of live/scratch.
- **Let `held_by_other` accept the same-port sibling exception, like `acquire()` does** —
  rejected: two workers of ONE instance racing to migrate at boot is exactly the case that must
  still be serialized (one backs up and upgrades; the other must wait, not also start its own
  backup). The polling wait already gives a legitimate sibling a normal, fast path through — it
  never needed the exception `acquire()` grants for an entirely different purpose (letting
  several workers SERVE requests over one folder).
- **Refuse immediately when another process holds the lock, no polling** — rejected: this would
  turn ordinary multi-worker boot (several uvicorn workers starting together, one of them
  legitimately migrating) into a boot failure for every worker but the first. The bounded wait
  keeps that common case working and only refuses a holder that genuinely never finishes.

## Agent provenance

- **Decided by:** orchestrating Claude agent, working from the owner's incident report, then TWO
  same-day security reviews — the first reproduced the incident's damage through three other
  doors, the second found the first review's own restore tests vacuous plus several smaller
  holes (order, lock re-checking, probe error handling, a corrupted-header restore case, the
  env.py door, a cutover-rename race).
- **Lane / branch:** worktree `fix-db-ahead-and-approval-card`, off `main` at `b12f162d6`.
- **Alternatives weighed:** raising inside `_pending()`; widening `_restore`'s own guard;
  folding into the 2026-09-03 live-instance-opt-in guard; a same-port sibling exception on the
  lock guard; refusing immediately instead of polling when another process holds the lock.
- **Driving artifacts:** the 2026-09-06 incident report (stale launchd `KeepAlive` service vs. a
  live, newer server); the first follow-up security review's four findings; the second follow-up
  review's HIGH/MEDIUM/LOW findings (vacuous restore tests proven and fixed via a mutation
  check — swapping `_restore` for the old implementation and for a no-op both now correctly fail
  the relevant tests). `tests/db/test_migrate_wrapper.py`'s
  `test_database_ahead_of_this_codes_alembic_scripts_is_refused_untouched`,
  `test_restore_after_failed_upgrade_preserves_the_inode_for_another_open_reader`,
  `test_restore_falls_back_to_copy_replace_when_destination_header_is_corrupted`,
  `test_ahead_of_code_revision_is_refused_before_the_lock_wait`,
  `test_locked_by_other_probe_failure_is_a_migration_error_not_a_raw_oserror`,
  `test_pending_migration_refuses_while_another_process_holds_the_working_folder`,
  `test_pending_migration_proceeds_once_the_lock_holder_finishes_migrating`,
  `test_pending_migration_proceeds_once_the_lock_holder_releases_mid_wait`,
  `test_existing_populated_database_with_no_alembic_stamp_is_refused_untouched`,
  `test_empty_string_version_num_is_refused_not_a_bare_assertion_error`,
  `test_truncated_revision_prefix_is_refused_not_treated_as_known`,
  `test_finish_cutovers_treats_a_missing_source_as_already_renamed_by_a_sibling`;
  `tests/test_instance_lock.py`'s `held_by_other` tests;
  `tests/db/test_live_instance_migration_guard.py`'s
  `test_a_raw_alembic_upgrade_is_refused_while_another_process_holds_the_folder`.
- **Why this way (one line):** the database being AHEAD of the code — or BEHIND but still open
  elsewhere, or populated without a stamp, or stamped with something malformed — is a fact
  `_pending()` alone cannot distinguish from ordinary pending work, so each fix names its fact
  explicitly and refuses on it before any side effect, rather than trying to make the existing
  backup/restore cycle safer to run against a database it was never meant to run against; and
  where a fix could not be perfectly clean (a corrupted destination header; a platform with no
  `fcntl`), that trade-off is named rather than glossed over as "strictly better."
