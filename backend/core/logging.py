"""Centralized logging setup (RFC P1-A1 §3.5).

The backend already routes every module through ``logging.getLogger(__name__)``
and ``main.py`` copies uvicorn's handler onto the ``backend`` logger in its
lifespan. This module makes that one canonical entry point so scripts, tests, and
connectors get the same setup without copy-paste.

A1 only lands the module (additive); ``main.py`` keeps its inline block until A2
swaps it for these helpers. Imports nothing internal.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import stat
from pathlib import Path

_DEFAULT_LEVEL = logging.INFO
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# ISO-8601-ish timestamp in the file — same format as the stream handler but
# always UTC-agnostic local time (readable without parsing).
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_FILE_DATE_FMT = "%Y-%m-%dT%H:%M:%S"

# Rotate at 5 MB, keep 3 backups  →  at most ~20 MB on disk.
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

# All backend.* loggers are children of this logger, so configuring it once
# governs the whole tree.
ROOT_LOGGER_NAME = "backend"

# Tag attached to the RotatingFileHandler so we can detect it on re-entry.
_FILE_HANDLER_TAG = "_onw_file_handler"


def get_logger(name: str) -> logging.Logger:
    """The one canonical way to obtain a logger.

    A thin wrapper over ``logging.getLogger`` today; the indirection lets us add
    structured context later without touching call sites.
    """
    return logging.getLogger(name)


def configure_logging(level: int | str | None = None) -> None:
    """Idempotent setup of the ``backend`` logger for non-uvicorn contexts.

    Used by scripts/tests and as the fallback when not running under uvicorn
    (which installs its own handlers — see ``attach_uvicorn_handlers``). Safe to
    call multiple times: it never double-adds a handler.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level if level is not None else _DEFAULT_LEVEL)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
        logger.addHandler(handler)
    logger.propagate = False


def attach_uvicorn_handlers() -> None:
    """Route ``backend.*`` logs through uvicorn's handler (the ``main.py`` lifespan logic).

    uvicorn installs its StreamHandler on the ``uvicorn`` logger (not
    ``uvicorn.error``); copy those handlers onto ``backend`` so all child loggers
    share uvicorn's output stream and format.

    A NO-OP when uvicorn has no handlers of its own — see the guard below.
    """
    uv_logger = logging.getLogger("uvicorn")
    handlers = list(uv_logger.handlers)
    if not handlers:
        # NOTHING TO ROUTE THROUGH — leave the logger exactly as configure_logging() left it.
        #
        # Copying an empty list and then setting propagate=False left ``backend`` with no
        # handlers AND no parent to fall back on, so every backend.* record was silently
        # dropped for the life of the process. Under a real uvicorn boot that never happens
        # (uvicorn's dictConfig has run by the time lifespan starts), but ``with
        # TestClient(app)`` runs lifespan in-process with uvicorn's dictConfig never called —
        # which is why entering it once poisoned caplog for every later test in the SESSION,
        # and why two test files carry hand-written save/restore dances around it
        # (tests/db/test_boot_migrates.py, tests/test_recurring_api.py).
        return
    backend_logger = logging.getLogger(ROOT_LOGGER_NAME)
    backend_logger.handlers = handlers
    backend_logger.setLevel(logging.INFO)
    backend_logger.propagate = False


def add_file_handler(log_file: Path | None = None) -> None:
    """Add a RotatingFileHandler to the ``backend`` logger (idempotent).

    Called once from ``main.py``'s lifespan, after ``attach_uvicorn_handlers``,
    so every backend.* record is also written to disk. Safe to call multiple
    times — a second call is a no-op (detected via ``_FILE_HANDLER_TAG``).

    ``log_file`` defaults to ``core.paths.LOG_FILE`` (imported lazily so this
    module remains a stdlib-only leaf at import time, matching the A1 contract;
    the lazy import resolves after the lifespan has set ``DATA_DIR``/``CACHE_DIR``
    from the environment).
    """
    backend_logger = logging.getLogger(ROOT_LOGGER_NAME)

    # Idempotency: skip if we already installed the file handler.
    if any(getattr(h, _FILE_HANDLER_TAG, False) for h in backend_logger.handlers):
        return

    if log_file is None:
        from backend.core.paths import LOG_FILE  # noqa: PLC0415 — lazy import is intentional

        log_file = LOG_FILE

    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Set the log directory to 0700 so other OS users cannot list or read files
    # inside it — matches the secret-store permission convention.
    try:
        os.chmod(log_file.parent, stat.S_IRWXU)
    except OSError:
        pass  # best-effort; non-POSIX platforms may not support chmod

    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    # Set the log file itself to 0600 — operator-readable only, not world-readable.
    # We do this after opening the handler (which creates the file) but before we
    # add it to the logger so no log records are emitted between open and chmod.
    try:
        os.chmod(log_file, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # best-effort
    fh.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_FILE_DATE_FMT))
    fh.setLevel(logging.DEBUG)  # capture everything; callers control the logger level
    setattr(fh, _FILE_HANDLER_TAG, True)

    backend_logger.addHandler(fh)
