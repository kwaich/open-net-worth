"""Atomic file writes + a write-hook registry (RFC P1-A1 §3.7).

These are the low-level FS primitives that previously lived in
``backend/ai/checkpoint.py`` — pure (temp file + ``os.replace``), nothing AI
about them. They were the reason ``db/`` had to import ``ai/`` (the dependency-
rule violation this slice clears the first half of).

The write-hook registry is the *seam* that lets ``db/`` notify a higher layer
(the checkpoint module) before a write, by injection rather than import. A1 ships
the seam; A4 flips ``db/json_store`` to call ``fire_write_hook`` and registers
``ai/checkpoint`` as the hook. This module imports nothing internal.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Iterator, Optional

from backend.core import platform_fs
from backend.core.file_lock import lock_fd, unlock_fd

_log = logging.getLogger(__name__)

DATA_MAINTENANCE_LOCK = ".onw-data-maintenance.lock"
_held_maintenance_locks: ContextVar[tuple[tuple[str, bool, int], ...]] = ContextVar(
    "held_data_maintenance_locks", default=()
)
_local_lock_registry_guard = threading.Lock()
_local_lock_registry: dict[str, _LocalDataLock] = {}
_active_exclusive_roots: dict[str, int] = {}


class _LocalDataLock:
    """In-process reader/writer gate complementing the cross-process ``flock``."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextmanager
    def hold(self, *, exclusive: bool) -> Iterator[None]:
        with self._condition:
            if exclusive:
                self._waiting_writers += 1
                try:
                    while self._writer or self._readers:
                        self._condition.wait()
                finally:
                    self._waiting_writers -= 1
                self._writer = True
            else:
                while self._writer or self._waiting_writers:
                    self._condition.wait()
                self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                if exclusive:
                    self._writer = False
                else:
                    self._readers -= 1
                self._condition.notify_all()


def _local_data_lock(root: Path) -> _LocalDataLock:
    key = str(root)
    with _local_lock_registry_guard:
        if key not in _local_lock_registry:
            _local_lock_registry[key] = _LocalDataLock()
        return _local_lock_registry[key]


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _maintenance_root_for(path: Path) -> Path:
    """Choose the common data root whose advisory lock guards ``path``.

    Production data uses ``core.paths.DATA_DIR``. Isolated stores/migrations use the
    outermost ancestor already carrying the lock marker, falling back to the nearest
    existing parent. An active lock context always wins so migration-owned atomic
    writes never acquire a second descriptor and accidentally downgrade exclusivity.
    """
    candidate = path.absolute()
    thread_id = threading.get_ident()
    for root_text, _exclusive, owner_thread_id in reversed(_held_maintenance_locks.get()):
        if owner_thread_id != thread_id:
            continue
        root = Path(root_text)
        if _is_beneath(candidate, root):
            return root

    with _local_lock_registry_guard:
        active_roots = tuple(_active_exclusive_roots)
    matching_active_roots = [
        Path(root_text) for root_text in active_roots if _is_beneath(candidate, Path(root_text))
    ]
    if matching_active_roots:
        return max(matching_active_roots, key=lambda root: len(root.parts))

    try:
        from backend.core.paths import DATA_DIR

        configured = DATA_DIR.resolve()
        if _is_beneath(candidate.resolve(strict=False), configured):
            return configured
    except (ImportError, OSError):  # pragma: no cover - bootstrap/platform defensive
        pass

    existing = candidate.parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    marked = [
        parent
        for parent in (existing, *existing.parents)
        if (parent / DATA_MAINTENANCE_LOCK).exists()
    ]
    return marked[-1] if marked else existing


@contextmanager
def data_maintenance_lock(root: Path, *, exclusive: bool) -> Iterator[None]:
    """Hold the cross-process data lock in shared (writer) or exclusive (maintenance) mode.

    Locks are nest-aware. An atomic write already inside the migration's exclusive
    context reuses that lock instead of opening the marker again, which would risk
    converting/downgrading the lock on platforms with process-associated ``flock``.
    """
    resolved = Path(root).resolve(strict=True)
    held = _held_maintenance_locks.get()
    thread_id = threading.get_ident()
    for held_root, held_exclusive, owner_thread_id in reversed(held):
        if owner_thread_id != thread_id:
            continue
        if held_root != str(resolved):
            continue
        if exclusive and not held_exclusive:
            raise RuntimeError("cannot upgrade a shared data-maintenance lock")
        yield
        return

    with _local_data_lock(resolved).hold(exclusive=exclusive):
        flags = platform_fs.open_flags(os.O_RDWR, os.O_CREAT)
        descriptor = os.open(resolved / DATA_MAINTENANCE_LOCK, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("data-maintenance lock is not a regular file")
            if not platform_fs.is_current_owner(metadata):
                raise OSError("data-maintenance lock has the wrong owner")
            platform_fs.fchmod(descriptor, 0o600)
            lock_fd(descriptor, exclusive=exclusive)
            if exclusive:
                with _local_lock_registry_guard:
                    key = str(resolved)
                    _active_exclusive_roots[key] = _active_exclusive_roots.get(key, 0) + 1
            token = _held_maintenance_locks.set((*held, (str(resolved), exclusive, thread_id)))
            try:
                yield
            finally:
                _held_maintenance_locks.reset(token)
                if exclusive:
                    with _local_lock_registry_guard:
                        remaining = _active_exclusive_roots[str(resolved)] - 1
                        if remaining:
                            _active_exclusive_roots[str(resolved)] = remaining
                        else:
                            del _active_exclusive_roots[str(resolved)]
                unlock_fd(descriptor)
        finally:
            os.close(descriptor)


@contextmanager
def data_write_lock(path: Path) -> Iterator[None]:
    """Guard one ordinary data read-modify-write cycle with the shared lock."""
    with data_maintenance_lock(_maintenance_root_for(path), exclusive=False):
        yield


# ── Atomic write helpers ─────────────────────────────────────────────────────


def fsync_directory(path: Path) -> None:
    """Durably flush directory-entry changes, propagating any failure."""
    platform_fs.fsync_directory(path)


def fsync_file(path: Path) -> None:
    """Durably flush an existing regular file without following its final symlink."""
    platform_fs.fsync_file(path)


def durable_replace(source: Path, destination: Path) -> None:
    """Replace and fsync every affected parent before reporting success."""
    platform_fs.replace(source, destination)
    fsync_directory(destination.parent)
    if source.parent != destination.parent:
        fsync_directory(source.parent)


def _atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with data_write_lock(path):
        tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        try:
            flags = platform_fs.open_flags(os.O_WRONLY, os.O_CREAT, os.O_EXCL)
            descriptor = os.open(tmp, flags, 0o600 if mode is not None else 0o666)
            try:
                if mode is not None:
                    platform_fs.fchmod(descriptor, mode)
                with os.fdopen(descriptor, "wb", closefd=False) as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
            finally:
                os.close(descriptor)
            durable_replace(tmp, path)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
    # Post-write (file now durably in place): bump the file-write epoch so a data_version-keyed
    # read cache invalidates on file-store changes too — profile.json (base currency), fx_cache,
    # skills — which SQLite's PRAGMA data_version cannot see. Strictly AFTER durable_replace, so
    # the new bytes are visible before the epoch moves (no stale-under-new-epoch window).
    bump_file_write_epoch()


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write ``text`` to ``path`` atomically so a crash mid-write can't truncate a file.

    Used by every remaining file-backed store (``SkillStore``, ``AuthStore``, ``budgets``'s
    ``profile.json``, ``portability``'s session bundles) and the checkpoint restore path.
    """
    _atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Write raw ``data`` to ``path`` atomically (temp file + ``os.replace``).

    Public companion to ``atomic_write_text`` for binary caches (e.g. the favicon proxy).
    Does NOT fire the write hook — these are local cache/asset bytes, not checkpointed data.
    """
    _atomic_write_bytes(path, data, mode=mode)


# Exact suffix shape `_atomic_write_bytes` produces: `.tmp-<pid digits>-<8 lowercase
# hex chars>` (os.getpid() is always decimal digits; uuid.uuid4().hex[:8] is always 8
# lowercase hex chars). Anchored full-match, not a `.tmp-` substring test — see
# sweep_stale_tmp_files's docstring for why that distinction is load-bearing.
_ATOMIC_TMP_RE = re.compile(r".+\.tmp-\d+-[0-9a-f]{8}\Z")


def sweep_stale_tmp_files(root: Path, *, older_than_seconds: int = 24 * 3600) -> int:
    """Remove orphaned atomic-write temp files older than the horizon.

    They are only ever left by a crash BETWEEN temp-write and os.replace; anything
    older than a day is garbage by construction. Matching is ANCHORED to the exact
    `.tmp-<pid>-<8hex>` suffix `_atomic_write_bytes` produces (``_ATOMIC_TMP_RE``,
    a full-string match) — NOT a `.tmp-` substring test. A real data file whose name
    merely contains `.tmp-` (e.g. `x.tmp-1.json`) must never match, or a stale-enough
    real file would be silently and permanently deleted. Returns count removed.
    """
    import time

    cutoff = time.time() - older_than_seconds
    removed = 0
    for p in root.glob("*.tmp-*"):
        if not _ATOMIC_TMP_RE.fullmatch(p.name):
            continue
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# ── File-write epoch (read-cache invalidation for file-backed stores) ─────────

# A monotonic counter bumped AFTER every atomic file write lands (see
# _atomic_write_bytes). The data_version-keyed read cache (backend/db/read_cache.py) reads
# this alongside SQLite's PRAGMA data_version: data_version covers DB-store writes, this covers
# the remaining file-backed stores (profile/base-currency, fx_cache, skills). core/ stays a
# stdlib leaf — this is a plain int + lock, no db/ import.
_file_write_epoch = 0
_file_write_epoch_lock = threading.Lock()


def bump_file_write_epoch() -> None:
    """Increment the file-write epoch. Called post-write by _atomic_write_bytes."""
    global _file_write_epoch
    with _file_write_epoch_lock:
        _file_write_epoch += 1


def file_write_epoch() -> int:
    """The current file-write epoch (a plain int read — atomic under the GIL)."""
    return _file_write_epoch


# ── Write-hook registry ──────────────────────────────────────────────────────

# A single optional callback invoked before each data-file write. The checkpoint
# module registers itself here (in A4) so the storage layer can snapshot pre-write
# bytes without importing ``ai/``.
_write_hook: Optional[Callable[[Path], None]] = None


def register_write_hook(fn: Optional[Callable[[Path], None]]) -> None:
    """Install (or clear, with ``None``) the pre-write hook. Last registration wins."""
    global _write_hook
    _write_hook = fn


def fire_write_hook(path: Path) -> None:
    """Invoke the registered pre-write hook, if any. Best-effort — never raises.

    A failing hook must not block the data write itself, so exceptions are
    swallowed (and logged at debug level).
    """
    hook = _write_hook
    if hook is None:
        return
    try:
        hook(path)
    except Exception:  # pragma: no cover - defensive; hook must never block a write
        _log.debug("write hook failed for %s", path, exc_info=True)


# ── DB write-hook registry (spec D7 — write_log) ─────────────────────────────

# The DB-store mirror of ``_write_hook``: a single optional callback invoked before
# each DB-store write, so ``ai.checkpoint`` can record row-level before-images
# (``write_log``) without ``db/`` importing ``ai/``. Policy (which tables are
# captured vs excluded) lives entirely in the registered hook — this seam only
# carries the call. The payload stays plain str/dict/None: core/ is a stdlib leaf;
# ``conn`` is an opaque object (never typed as a DB-driver type here) passed
# through untouched — see ``fire_db_write_hook``'s docstring for the contract.
_db_write_hook: Optional[Callable[[str, list[tuple[str, dict | None]], object], None]] = None


def register_db_write_hook(
    fn: Optional[Callable[[str, list[tuple[str, dict | None]], object], None]],
) -> None:
    """Install (or clear, with ``None``) the pre-write DB hook. Mirrors
    ``register_write_hook``. Last registration wins.

    The registered ``fn`` must accept ``(table_name, rows, conn)`` — ``conn`` is
    whatever ``fire_db_write_hook`` was called with (``None``, or the caller's own
    open transaction/connection, passed through opaque and unexamined). See
    ``fire_db_write_hook``'s docstring for the transaction contract this implies:
    a hook fired with ``conn=None`` while the caller holds its own open write
    transaction can stall and then silently drop the image (best-effort).
    """
    global _db_write_hook
    _db_write_hook = fn


def fire_db_write_hook(
    table_name: str, rows: list[tuple[str, dict | None]], conn: object | None = None
) -> None:
    """Invoke the registered pre-write DB hook, if any. Best-effort — never raises.

    ``rows`` is a list of ``(pk, before_row_or_None)`` pairs — None means the row did
    not exist before this write. Mirrors ``fire_write_hook``: a failing hook must not
    block the write itself, so exceptions are swallowed (and logged at debug level).
    Called unconditionally by every DB store write — the checkpoint layer decides
    captured-vs-excluded, not this seam (mirrors today's file-hook split).

    TRANSACTION CONTRACT (review-hardened — do not weaken without re-reading the
    incident this closes): a caller holding an OPEN WRITE TRANSACTION MUST pass its
    connection as ``conn`` — the before-image then commits atomically with the
    caller's own write (a crash lands neither or both, never a half-captured turn).
    ``conn=None`` opens a short INDEPENDENT transaction and is safe ONLY when the
    caller has NO in-flight write transaction of its own: firing conn-less from
    inside an already-open write transaction stalls on SQLite's single-writer slot
    until ``busy_timeout``, and because this function is best-effort, that failure
    is SWALLOWED — the before-image silently never lands and the turn can mint no
    checkpoint at all for a real data change. ``conn`` is an opaque object — this
    module never imports a DB driver to type it; it is passed through to the
    registered hook untouched.
    """
    hook = _db_write_hook
    if hook is None:
        return
    try:
        hook(table_name, rows, conn)
    except Exception:  # pragma: no cover - defensive; hook must never block a write
        _log.debug("db write hook failed for table %s", table_name, exc_info=True)
