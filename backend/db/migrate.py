"""Fail-closed boot migrations: verified backup -> upgrade head -> or refuse to boot.

Replaces the fail-open `except: _log.exception(... "non-fatal")` posture of the
legacy boot scripts for everything DB-owned. Spec D3; ADR
`docs/adr/2026-08-07-fail-closed-alembic-migrations-with-verified-backup.md`.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from backend.core.errors import MigrationError
from backend.core.instance_lock import held_by_other
from backend.core.paths import DATA_DIR

_log = logging.getLogger(__name__)
# Backups always live under the app's DATA_DIR, regardless of where
# ONW_DATABASE_URL points the database file itself.
BACKUP_DIR = DATA_DIR / "backups" / "db"
# Retention: an unbounded pre-migrate-*.db pile grows one file per boot that hits a
# pending migration. Keep the newest N (the new backup itself counts as one of them).
_BACKUP_KEEP = 5
# How long ensure_migrated waits for a database another process has open to reach
# head on its own before refusing — see the live-instance-lock guard below.
# Overridable by tests so the wait doesn't actually take 30 real seconds.
_LIVE_LOCK_POLL_TIMEOUT_S = 30.0
_LIVE_LOCK_POLL_INTERVAL_S = 0.5
# Sentinel returned by `_unknown_current_revision` for a database that already has
# real tables but no `alembic_version` stamp at all — never a real revision id
# (those are all "<timestamp>_<name>"), so it can't collide with one.
_UNSTAMPED = "<unstamped-existing-database>"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _redact_url(url: str) -> str:
    """A connection URL safe to put in an exception message or a log line —
    host/db/username kept (an operator needs those to act), password masked."""
    return make_url(url).render_as_string(hide_password=True)


def head_revision() -> str:
    """The Alembic head THIS checkout's scripts end at."""
    from alembic.script import ScriptDirectory

    from backend.db.alembic_config import make_config

    return ScriptDirectory.from_config(make_config()).get_current_head()


def _pending(url: str) -> bool:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    from backend.db.alembic_config import make_config

    head = ScriptDirectory.from_config(make_config(url)).get_current_head()
    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        eng.dispose()
    return current != head


def _await_pending_clears(url: str, folder: Path) -> None:
    """Poll while ``folder``'s instance lock is held by ANOTHER process AND the
    database is still pending — returns (without raising) the moment EITHER
    condition clears: the database reaches head (a sibling's owner finished
    migrating), or the lock holder exits without ever finishing its own migration
    (nobody is coming to do this for us, but nobody is stopping us either).
    ``held_by_other`` is re-checked every iteration, not just once up front
    (security-review finding MEDIUM 7) — a holder that exits early must not cost
    the caller the full poll window. Raises ``MigrationError`` only if BOTH are
    still true when ``_LIVE_LOCK_POLL_TIMEOUT_S`` elapses."""
    deadline = time.monotonic() + _LIVE_LOCK_POLL_TIMEOUT_S
    while _pending(url) and held_by_other(folder):
        if time.monotonic() >= deadline:
            raise MigrationError(
                f"another process holds {folder}; refusing to migrate a database another "
                "instance has open. If that instance is still migrating, let it finish — never "
                "kill a migration in progress; if it is idle or stale, stop it, then retry.",
                path=str(folder),
            )
        time.sleep(_LIVE_LOCK_POLL_INTERVAL_S)


def _refuse_if_locked_by_other(url: str) -> bool:
    """Live-instance-lock guard (security review finding, 2026-09-06): a database
    that is genuinely PENDING is not automatically safe to migrate — another process
    may still have the working folder's instance lock, whether a wholly separate
    live instance or a SIBLING worker of this one also racing to boot. ``acquire()``
    waves a same-port sibling through; this deliberately does not, because two
    workers racing to back up/upgrade/restore the same file is exactly what must be
    serialized. Waits for the lock holder to finish its own migration (a legitimate
    sibling); raises rather than ever touching the file if it doesn't finish in time.

    Returns True if the caller should treat the database as no longer pending and
    return (the lock holder finished while this call waited). False if it is safe to
    proceed with our OWN backup/upgrade — either nobody else ever held the lock, or
    the holder released it before finishing (MEDIUM 7: `_await_pending_clears`
    re-checks the lock every polling iteration, so this does not wait out the full
    timeout once the lock is free even though the database is still pending)."""
    folder = DATA_DIR.parent
    try:
        locked = held_by_other(folder)
    except OSError as exc:
        raise MigrationError(
            f"cannot probe the instance lock in {folder} ({exc}); refusing to migrate",
            path=str(folder),
        ) from exc
    if not locked:
        return False
    _await_pending_clears(url, folder)
    return not _pending(url)


def _refuse_if_revision_unsafe(unknown_rev: str | None, db_path: Path) -> None:
    """Raise the MigrationError matching `_unknown_current_revision`'s result — the
    populated-but-unstamped sentinel, an ahead-of-code revision, or (``None``) do
    nothing, it is safe to proceed."""
    if unknown_rev is _UNSTAMPED:
        raise MigrationError(
            "existing database has no alembic_version stamp — refusing to touch "
            "it. No backup, no upgrade, no restore.",
            path=str(db_path),
        )
    if unknown_rev is not None:
        raise MigrationError(
            f"database is at revision {unknown_rev!r}, which this checkout's "
            "Alembic scripts do not recognize — it was migrated by NEWER code "
            "than this build. Refusing to touch it: no backup, no upgrade, no "
            "restore. Run the checkout that owns that revision, or upgrade this "
            "one so it includes it.",
            path=str(db_path),
        )


def _unknown_current_revision(url: str) -> str | None:
    """The database's current alembic revision, IF this checkout's ScriptDirectory
    does not recognize it — meaning the database was migrated by NEWER code than
    this build has — OR the sentinel ``_UNSTAMPED`` if the database already has real
    tables but no alembic stamp at all. ``None`` means safe to proceed: the revision
    is known, or the database is genuinely fresh (no stamp AND no tables). Assumes
    ``url`` is sqlite — only called for a pre-existing sqlite file.

    2026-09-06 incident: ``_pending()`` above only compares ``current != head``, so
    a database stamped by newer code — or one with real data but no stamp at all —
    reads as ordinary pending work. A stale checkout's boot then took a pre-migrate
    backup, ``command.upgrade`` failed against a revision/state it cannot
    understand, and the failure handler "restored" the backup anyway, unlinking a
    running newer server's live ``-wal``/``-shm`` sidecars and swapping its open
    database file out from under it. This check — and the identical hazard for a
    populated-but-unstamped database — must run BEFORE any backup, before
    ``dispose_engine()``, before ``run_upgrade``: see ADR
    ``2026-09-06-database-ahead-of-code-is-refused-untouched``.

    The "known" check is an EXACT match against every real revision id
    (``ScriptDirectory.walk_revisions()``), not ``get_revision()``: that call
    accepts a unique PREFIX of a real id and resolves it (a truncated/corrupted
    stamp would misread as "known"), and raises a bare ``AssertionError`` — not
    ``CommandError`` — for an empty string. Set membership has neither failure mode.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text

    from backend.db.alembic_config import make_config

    eng = create_engine(url)
    table_count = 0
    try:
        with eng.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
            if current is None:
                # `alembic_version` itself survives a downgrade to "base" (alembic
                # deletes its ROW, not the table) — exclude it, or a previously
                # migrated-then-downgraded db would misread as "populated but
                # unstamped" and get permanently refused instead of re-migrated.
                table_count = conn.execute(
                    text(
                        "SELECT count(*) FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
                    )
                ).scalar()
    finally:
        eng.dispose()
    if current is None:
        return _UNSTAMPED if table_count else None
    script = ScriptDirectory.from_config(make_config(url))
    try:
        known = current in {r.revision for r in script.walk_revisions()}
    except Exception:
        known = False
    return None if known else current


def _sqlite_backup(db_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # Owner-only (FR-I3): pre-migrate-*.db backups are full copies of the financial
    # database — they must not sit world-readable at 0755.
    os.chmod(BACKUP_DIR, 0o700)
    stamp = _stamp()
    dest = BACKUP_DIR / f"pre-migrate-{stamp}.db"
    suffix = 2
    while dest.exists():
        # same-second boots must never overwrite a verified backup
        dest = BACKUP_DIR / f"pre-migrate-{stamp}-{suffix}.db"
        suffix += 1
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)  # consistent under WAL
        finally:
            dst.close()
    finally:
        src.close()
    check = sqlite3.connect(dest)
    try:
        ok = check.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        raise MigrationError(f"pre-migrate backup failed verification: {ok}", path=str(dest))
    _prune_old_backups(dest)
    return dest


def _unlink_with_sidecars(db_path: Path) -> None:
    """Best-effort unlink of ``db_path`` plus its ``-wal``/``-shm`` sidecars (a source
    database backed up while in WAL mode carries that journal mode into the destination
    file's own header, so a later open of it — even just this module's own
    ``PRAGMA quick_check`` — can leave a sidecar behind beside it). Each suffix is
    independent: a missing sidecar (the common case) is not an error."""
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{db_path}{suffix}").unlink()
        except OSError:
            continue


def _prune_orphan_sidecars() -> None:
    """Remove a ``pre-migrate-*.db-wal``/``-shm`` sidecar whose own ``.db`` no longer
    exists — left behind by a prune that ran before ``_unlink_with_sidecars`` existed, or
    any other path that removed the ``.db`` without it. Best-effort, same as every other
    removal in this module."""
    for suffix in ("-wal", "-shm"):
        for sidecar in BACKUP_DIR.glob(f"pre-migrate-*.db{suffix}"):
            db_path = Path(str(sidecar)[: -len(suffix)])
            if db_path.exists():
                continue
            try:
                sidecar.unlink()
            except OSError:
                continue


def _prune_old_backups(keep: Path) -> None:
    """Keep only the newest _BACKUP_KEEP pre-migrate-*.db files. ``keep`` — the backup
    ``_sqlite_backup`` just created and verified — is excluded from deletion candidates
    UNCONDITIONALLY, before any sort, and counts as one of the _BACKUP_KEEP survivors:
    an mtime TIE among the other files (coarse-mtime filesystems, two boots in the same
    second) must never be able to rank it past the keep boundary by accident.
    Best-effort otherwise: a file that fails to stat (e.g. it vanished between glob and
    stat) or unlink (e.g. a concurrent reader) is skipped, never allowed to fail the
    backup that just succeeded. Each pruned ``.db``'s own ``-wal``/``-shm`` sidecars are
    removed with it, and any pre-existing orphan sidecar (its ``.db`` already gone) is
    swept too — see ``_unlink_with_sidecars``/``_prune_orphan_sidecars``."""
    dated: list[tuple[float, Path]] = []
    for p in BACKUP_DIR.glob("pre-migrate-*.db"):
        if p == keep:
            continue
        try:
            dated.append((p.stat().st_mtime, p))
        except OSError:
            continue  # vanished between glob and stat — nothing to prune
    dated.sort(key=lambda pair: pair[0], reverse=True)
    for _, stale in dated[_BACKUP_KEEP - 1 :]:
        _unlink_with_sidecars(stale)
        _log.info("pruned old pre-migrate backup: %s", stale)
    _prune_orphan_sidecars()


def _require_backup_or_refuse(url: str) -> None:
    if os.environ.get("ONW_MIGRATE_ASSUME_BACKUP") == "1":
        _log.warning("ONW_MIGRATE_ASSUME_BACKUP=1 — proceeding without an automatic backup")
        return
    safe_url = _redact_url(url)
    raise MigrationError(
        f"pending schema migrations on a non-SQLite database ({safe_url}); automatic "
        "backup is SQLite-only in plan 1. Take a backup (pg_dump) and set "
        "ONW_MIGRATE_ASSUME_BACKUP=1 to proceed.",
        path=safe_url,
    )


def _restore(backup: Path, db_path: Path) -> None:
    """Restore ``backup`` INTO ``db_path`` IN PLACE, through SQLite's own online
    backup API — never copy + unlink sidecars + ``os.replace`` as the PRIMARY path.
    That sequence swaps the INODE out from under any other process that still has
    ``db_path`` open (unlinking its live ``-wal``/``-shm`` sidecars in the process),
    which is exactly the damage the 2026-09-06 incident's restore did to a live
    server. ``sqlite3.Connection.backup()`` takes SQLite's own locks and writes
    through the DESTINATION's existing file, so a reader with the file already open
    sees the restored content on its NEXT read, with nothing unlinked out from
    under it — see ADR ``2026-09-06-database-ahead-of-code-is-refused-untouched``.

    Security review, MEDIUM 2: in-place restore has ONE hole — it cannot recover a
    destination whose own HEADER the failed upgrade corrupted (e.g. truncated or
    zeroed mid-DDL). Opening ``db_path`` as a destination connection then raises
    ``sqlite3.DatabaseError`` before ``backup()`` can copy anything in at all, so
    in-place restore is not merely damage-preserving there, it is IMPOSSIBLE. Only
    for that specific failure — never for any other ``sqlite3.Error`` — this falls
    back to the old copy-to-temp -> unlink sidecars -> ``os.replace`` path: a fresh
    file is strictly better than refusing to restore. This fallback trades the
    inode-preservation property for actually recovering the data; it is the
    documented exception, not the common case."""
    try:
        src = sqlite3.connect(backup)
        try:
            dst = sqlite3.connect(db_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        _log.info("restored %s in place via sqlite3.Connection.backup()", db_path)
    except sqlite3.DatabaseError:
        _log.warning(
            "in-place restore of %s failed (destination header unreadable) — "
            "falling back to copy+replace",
            db_path,
            exc_info=True,
        )
        import shutil

        tmp = db_path.with_name(db_path.name + ".restore-tmp")
        shutil.copy2(backup, tmp)
        for suffix in ("-wal", "-shm"):
            stale = Path(str(db_path) + suffix)
            if stale.exists():
                stale.unlink()
        os.replace(tmp, db_path)
        _log.info("restored %s via copy+replace fallback", db_path)


def _cutover_recovery_hint() -> str:
    """Recovery text for the post-absorb drift guard in ``_finish_cutovers``.

    The FIRST boot (a fresh database) is the most likely moment for this drift
    — the old server may still be appending to the JSON source right up until
    the new one absorbs it — and it is exactly the boot with NO backup yet, so
    pointing the operator at "restore the backup" would strand them in a
    boot-refusal loop. Branch on whether a backup actually exists.
    """
    from backend.db.engine import database_url, resolved_sqlite_path

    if sorted(BACKUP_DIR.glob("pre-migrate-*.db")):
        return (
            "restore the newest backups/db/pre-migrate-*.db over the database "
            "file and re-boot — the migration will re-absorb the newer file."
        )
    db_path = resolved_sqlite_path(database_url())
    target = (
        str(db_path) if db_path is not None else "DATA_DIR/onw.db or your ONW_DATABASE_URL target"
    )
    return (
        "no pre-migrate backup exists (first-boot absorb); delete the database "
        f"file ({target}) and re-boot — the migration will re-absorb the "
        "current, newer file."
    )


def _finish_cutovers(engine) -> None:
    import hashlib

    from backend.db.tables import json_cutovers

    with engine.connect() as conn:
        rows = conn.execute(
            select(json_cutovers)
            .where(json_cutovers.c.renamed == 0)
            .order_by(json_cutovers.c.store)
        ).fetchall()

    for row in rows:
        if not row.source_hash:
            # No-compat cleanup (owner ruling, plan 2 task 6 spec Addendum): every
            # real cutover revision (0002/0004/0005) stamps a sha256 hash on absorb
            # — a row with none is malformed data, not a legacy format to tolerate.
            raise MigrationError(
                f"cutover ledger row for store {row.store!r} has no source_hash — "
                "every real cutover revision stamps one on absorb, so this row is "
                "malformed; refusing to rename its source file.",
                path=row.source_path,
            )
        src = Path(row.source_path)
        if src.exists():
            digest = hashlib.sha256(src.read_bytes()).hexdigest()
            if digest != row.source_hash:
                raise MigrationError(
                    f"{src} changed AFTER it was absorbed into the database "
                    f"(hash {digest[:12]}… != absorbed {row.source_hash[:12]}…) — "
                    "refusing to rename it; rows written since the absorb would be "
                    f"silently lost. Recover: {_cutover_recovery_hint()}",
                    path=str(src),
                )
            try:
                src.rename(src.with_name(f"{src.name}.migrated-{_stamp()}"))
            except FileNotFoundError:
                # LOW 8 (security review): a waiting sibling now wakes exactly when
                # the migration owner finishes (MEDIUM 7's re-check), so two
                # processes can race into `_finish_cutovers` for the SAME row. If
                # `src` vanished between our `exists()` check above and this rename,
                # another process already renamed it — that is success, not a
                # failure to raise on.
                _log.info("cutover: %s already renamed by another process", src)
            except OSError as exc:
                raise MigrationError(
                    f"could not rename absorbed source file {src}: {exc}", path=str(src)
                ) from exc
            else:
                _log.info("cutover: renamed %s", src)
        # Per-row commit: the rename above is NOT transactional, so its DB
        # bookkeeping must land immediately — a later row's failure must never
        # roll back an earlier row's renamed=1.
        with engine.begin() as conn:
            conn.execute(
                update(json_cutovers).where(json_cutovers.c.store == row.store).values(renamed=1)
            )


def refuse_live_instance(url: str, *, allowed: bool) -> None:
    """Refuse to run migrations against a live instance unless the caller opted in.

    Only the app boot and the operator's own ``make setup`` / ``make db-upgrade`` /
    ``make seed`` pass ``allowed=True``; the alembic CLI opts in with
    ``-x allow_live_instance=1``. Nothing else has business migrating real data — the
    2026-09-02 incident was a scratch smoke-test whose isolation variable was misspelled,
    so ``DATA_DIR`` fell through to the default and an unmerged revision stamped the
    owner's database (ADR ``2026-09-03-live-instance-migrations-require-an-opt-in``).
    Judged on BOTH the database file and ``DATA_DIR`` (where the backup lands), and before
    any side effect: ``_pending()``'s probe alone creates the file.
    """
    if allowed:
        return
    from backend.core.config import is_live_instance_path
    from backend.db.engine import resolved_sqlite_path

    db_path = resolved_sqlite_path(url)
    live = [p for p in (db_path, DATA_DIR) if p is not None and is_live_instance_path(p)]
    if not live:
        return
    raise MigrationError(
        "refusing to run migrations against a LIVE instance from a caller that did not opt "
        f"in ({', '.join(str(p) for p in live)}). Only the app boot, `make setup`, "
        "`make db-upgrade` and `make seed` migrate real data — they pass "
        "allow_live_instance=True; raw alembic passes `-x allow_live_instance=1`. A scratch "
        "run must point ONW_ROOT (or DATA_DIR + CACHE_DIR + LOGS_DIR) at a throwaway folder "
        "and set ONW_QA_MODE=1 so a misspelled variable is a refusal, not a write here.",
        path=str(live[0]),
    )


def ensure_migrated(*, upgrade=None, allow_live_instance: bool = False) -> None:
    """Bring the database to head, or refuse to let the app boot. Called ONCE,
    first thing in the FastAPI lifespan, before any store is read.

    ``allow_live_instance`` is the caller saying "this is the operator's own instance and I
    am meant to be here". Any other caller on a live path is refused before the database
    file is touched — see ``refuse_live_instance``."""
    from alembic import command

    from backend.db.alembic_config import make_config
    from backend.db.engine import database_url, dispose_engine, get_engine, resolved_sqlite_path

    url = database_url()
    refuse_live_instance(url, allowed=allow_live_instance)
    cfg = make_config(url)
    # env.py re-judges every Alembic run; this call has already passed (or was let through).
    cfg.attributes["allow_live_instance"] = True
    run_upgrade = upgrade or (lambda c: command.upgrade(c, "head"))
    # Resolve ONCE, against the ACTUAL url — not the default DATA_DIR/onw.db
    # path — so a sqlite ONW_DATABASE_URL override backs up / restores the
    # right file instead of silently missing it. A url that resolves to None
    # here (only sqlite://:memory: in practice — no filesystem path exists to
    # back up) is treated as non-sqlite below and hits
    # _require_backup_or_refuse: refusing without ONW_MIGRATE_ASSUME_BACKUP is
    # the fail-closed choice for it, not a bug.
    db_path = resolved_sqlite_path(url)
    is_sqlite = db_path is not None
    if is_sqlite:
        db_path.parent.mkdir(
            parents=True, exist_ok=True
        )  # _pending()'s probe below creates the file
    # Captured BEFORE _pending()'s probe connection: for a not-yet-existing sqlite
    # path, sqlite3 creates an empty file as a side effect of merely connecting (to
    # read the current alembic revision), so re-checking .exists() AFTER that probe
    # would misread a fresh install as "there is a file to back up" and back up (and
    # later verify) a zero-byte, header-less file.
    db_existed = is_sqlite and db_path.exists()

    if not _pending(url):
        _finish_cutovers(get_engine())
        return

    if is_sqlite and db_existed:
        # A pre-existing sqlite file is exactly the case a backup-then-restore cycle
        # can damage — check BEFORE `_sqlite_backup`/`dispose_engine`/`run_upgrade`,
        # and BEFORE the (possibly 30s-long) lock-holder wait below, that this
        # checkout's Alembic scripts actually recognize the current revision. Cheap,
        # read-only, and diagnostic: the 2026-09-06 incident's own scenario (this
        # checkout is OLDER than the database) must report that fact immediately,
        # not burn the lock guard's bounded wait first and blame "another process"
        # for a problem that is really "wrong checkout." A fresh (not-yet-existing)
        # db has no current revision to be unknown, and a non-sqlite target never
        # takes an automatic backup to restore from (`_require_backup_or_refuse`
        # below), so neither needs this extra connection — the latter matters
        # concretely: a test doubles `_pending` to keep a fake postgres URL from
        # ever being dialed.
        _refuse_if_revision_unsafe(_unknown_current_revision(url), db_path)

    if _refuse_if_locked_by_other(url):
        _finish_cutovers(get_engine())
        return

    backup: Path | None = None
    if is_sqlite and db_existed:
        backup = _sqlite_backup(db_path)
        _log.info("pre-migrate backup verified: %s", backup)
    elif not is_sqlite:
        _require_backup_or_refuse(url)

    dispose_engine()  # no live pooled connections while DDL runs
    try:
        run_upgrade(cfg)
    except Exception as exc:
        dispose_engine()
        restore_note = ""
        if backup is not None:
            try:
                _restore(backup, db_path)
                restore_note = "Database restored from the pre-migrate backup. "
                _log.error("migration failed — restored %s from %s", db_path, backup)
            except Exception as restore_exc:
                # Compound failure — the highest-stakes moment for guidance: still
                # raise MigrationError, reporting BOTH failures + the path. Broad on
                # purpose: `_restore` now goes through `sqlite3.Connection.backup()`
                # (finding 1b), so a corrupt backup file or a locked destination
                # raises `sqlite3.Error`, not `OSError` — either way this branch is
                # the one place that turns "the restore also failed" into guidance
                # instead of a raw, unhandled traceback.
                restore_note = (
                    f"AND the automatic restore ALSO failed ({restore_exc}); "
                    f"restore manually from {backup}. "
                )
                _log.error("migration failed AND restore failed", exc_info=True)
        raise MigrationError(
            f"schema migration failed ({type(exc).__name__}: {exc}). "
            + restore_note
            + "Refusing to boot.",
            path=str(db_path) if is_sqlite else _redact_url(url),
        ) from exc
    dispose_engine()
    _finish_cutovers(get_engine())
