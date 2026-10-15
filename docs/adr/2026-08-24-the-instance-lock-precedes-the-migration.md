# The instance lock precedes the migration

- **Date:** 2026-08-24
- **Status:** Accepted
- **Amends:** `2026-08-05` fail-closed boot posture (spec D3), and the CLAUDE.md line
  "Boot migrations are fail-closed via `db/migrate.ensure_migrated()` (first in `lifespan`,
  verified-backup-or-refuse)".

## Context

`ensure_migrated()` was the literal first statement in `lifespan`, guarded by an AST-position
test (`tests/db/test_boot_migrates.py`) so the ordering could not silently drift. The intent:
nothing that touches data or serves a request may happen before the schema is known good.

Adding a single-instance lock (`core/instance_lock.py`, 2026-08-24) forced the question of
where the claim goes, because there are only two options and they are not equivalent:

- **Lock, then migrate.** If another instance owns the folder, refuse before touching the
  database at all.
- **Migrate, then lock.** Run a backup-and-modify migration against a database another live
  instance is actively serving, and discover the conflict afterwards.

The second is the failure the lock exists to prevent, arriving one statement too late. A
migration is the single most destructive thing this app does to its own data; doing it to a
file another process owns is worse than any ordering concern the original invariant addressed.

## Decision

The instance-lock preamble — its imports and the single `_acquire_instance(...)` call — may
precede `ensure_migrated()` in `lifespan`. **Nothing else may.**

The guarding test is not relaxed to "somewhere near the top": it skips exactly the allowed
preamble by name and then asserts the schema check is the next statement. The allowance is a
closed list in that test, so a third thing cannot join by accident.

## Why this does not weaken the original invariant

The invariant's purpose is that no data is read, written, or served before the schema check.
The lock does none of those. It opens a lock file, takes `flock`, and either proceeds or
aborts the boot — it is a guard, not a step. A boot that fails the lock never reaches the
migration, which is the desired outcome and strictly more fail-closed than before.

## Agent provenance

Found by the full suite: `tests/db/test_boot_migrates.py::test_ensure_migrated_is_first_statement_in_lifespan`
failed as the only regression across 16,199 tests after the lock landed. The failure was
correct — a documented invariant had been changed without saying so. Rather than move the lock
after the migration (which would have made the lock nearly pointless) or quietly edit the
assertion, the invariant is amended here with its reason, and the test now encodes the
allowance explicitly.
