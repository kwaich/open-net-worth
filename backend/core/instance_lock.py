"""One instance per working folder.

Two processes opening one folder does not corrupt SQLite — WAL serialises writes across
processes — but everything above it diverges: two turn registries, two caches, interleaved
write-log checkpoints, and a ``/rewind`` in one reverting rows the other just wrote. The
symptom is an app that behaves oddly for no visible reason, which is the worst kind of bug to
own a money ledger.

MULTIPLE WORKERS OF ONE INSTANCE ARE STILL FINE, and that is the whole subtlety. This app
supports several uvicorn workers sharing a folder (``main.py``'s scheduler lock exists
precisely because they do), so a plain exclusive lock would break a legitimate deployment. The
lock therefore identifies the INSTANCE, not the process: the holder records the port it serves,
and a second process on the SAME port is a sibling worker (allowed) while a different port is a
second instance (refused).

Desktop boot instead records an opaque owner id and holds the folder exclusively. It never
accepts a same-port worker. Both claims use native process locks on POSIX and Windows.

Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import IO, Optional

from backend.core.file_lock import lock_exclusive

logger = logging.getLogger(__name__)

LOCK_NAME = ".instance.lock"


class InstanceAlreadyRunning(RuntimeError):
    """Another instance already owns this working folder."""


# Held for the life of the process. A closed handle releases the lock, so this must not be
# garbage-collected — the same reason main.py keeps the scheduler lock handle on a global.
_handle: Optional[IO[str]] = None


def _describe(path: Path) -> str:
    """Whatever the current holder recorded, for the refusal message."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return f"pid {raw.get('pid', '?')} on port {raw.get('port', '?')}"
    except (OSError, ValueError, TypeError, AttributeError):
        return "another process"


def _recorded_server_port(path: Path) -> Optional[int]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return None if record.get("instance_id") else int(record["port"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def acquire(folder: Path, port: int, *, instance_id: str | None = None) -> bool:
    """Claim ``folder`` for this instance. True if claimed, False if a sibling worker holds it.

    Raises ``InstanceAlreadyRunning`` when a DIFFERENT instance holds the folder. Desktop
    owners are exclusive even on the same port: a desktop never has sibling workers.
    """
    global _handle
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOCK_NAME
    handle = open(path, "a+", encoding="utf-8")
    try:
        lock_exclusive(handle)
    except OSError:
        # Someone holds it. A sibling worker of THIS instance serves the same port; anything
        # else is a second instance and must not share the folder.
        holder_port = _recorded_server_port(path)
        handle.close()
        if instance_id is None and holder_port == port:
            logger.info("instance lock: held by a sibling worker on port %s", port)
            return False
        raise InstanceAlreadyRunning(
            f"{folder} is already in use by {_describe(path)}. Two instances must not share a "
            f"working folder. Start this one with ONW_ROOT pointing somewhere else, or stop "
            f"the other first."
        )

    handle.seek(0)
    handle.truncate()
    record: dict[str, int | str] = {"pid": os.getpid(), "port": port}
    if instance_id is not None:
        record["instance_id"] = instance_id
    handle.write(json.dumps(record))
    handle.flush()
    _handle = handle
    logger.info("instance lock: this process owns %s", folder)
    return True


def held_by_other(folder: Path) -> bool:
    """True if some process OTHER than this one currently holds ``folder``'s instance
    lock. Used by ``db.migrate.ensure_migrated`` to refuse migrating a database another
    running instance still has open — even a SIBLING worker of THIS same instance.
    Unlike ``acquire()``, this probe does NOT accept the same-port sibling excuse:
    two workers of one instance racing to migrate at boot is exactly the case that
    must still be serialized, not let through. The caller polls ``_pending`` instead,
    so a legitimate sibling just waits for its owner to finish.

    False when THIS process already owns the lock (``_handle`` is set — the ordinary
    boot order is ``acquire()`` then ``ensure_migrated()``, so no probe is needed)."""
    if _handle is not None:
        return False
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / LOCK_NAME, "a+", encoding="utf-8")
    try:
        lock_exclusive(handle)
    except OSError:
        return True
    else:
        return False
    finally:
        handle.close()


def release() -> None:
    """Drop the lock. Closing the handle is what releases it; the file is left behind on
    purpose so a crashed instance's pid/port is still readable for the next refusal message."""
    global _handle
    if _handle is not None:
        try:
            _handle.close()
        except OSError:
            pass
        _handle = None
