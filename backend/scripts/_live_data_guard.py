"""Fail-closed live-data guard for the Phase 6 migration/diagnostic scripts (RFC
cross-channel-attachments item 31).

Every script in this family (``migrate_bare_model_refs.py``, ``migrate_inline_
transcript_images.py``, ``scan_legacy_proactive_strings.py``,
``scan_mixed_statement_rows.py``) reads — and, for the two migrations, can optionally
write — the DB-backed sessions/attachments/statements tables. Simply importing
``backend.db.engine`` picks up whatever ``DATA_DIR``/``ONW_DATABASE_URL`` the ambient
environment resolves to, which on this box (and any real self-hosted install) is the
operator's LIVE ``~/.open-net-worth`` tree by default — the same silent-default risk
``core.config``'s ``ONW_QA_MODE`` guard closes for the app's own boot path (see that
module's docstring). These scripts are not the app boot path, so that guard never fires
for them; this module is their own, script-specific equivalent, mirroring its fail-closed
posture: refuse by default, require an explicit, never-silently-inherited flag to touch
anything real.

Leaf: stdlib only at import time (mirrors ``_migration_io.py``) — ``backend.db.engine`` is
imported lazily, only once a caller has proven it wants the live database, so merely
IMPORTING this module (or running ``--help``) never touches any config or filesystem.

Usage in a script's ``main()``::

    ap = argparse.ArgumentParser(...)
    _live_data_guard.add_target_args(ap)
    ap.add_argument("--apply", action="store_true", ...)
    args = ap.parse_args()
    db_url = _live_data_guard.resolve_db_url(args)   # SystemExit if neither flag given
    _live_data_guard.bind_db_url(db_url)              # only after this may db/ be imported
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

LIVE_FLAG = "--i-accept-live-data-risk"

# Mirrors backend.core.config._REAL_DATA_DIRS / _real_instance_root — duplicated rather
# than imported so this module can stay a stdlib-only leaf usable before any DATA_DIR
# resolution happens. Any sqlite path under EITHER of these is refused unless the caller
# explicitly accepted the live-data risk, even when it arrived via --db-url (closes the
# loophole of "pass the real path as if it were a fixture").
_REAL_INSTANCE_ROOT = (Path.home() / ".open-net-worth").resolve()
_REPO_SEED_DATA = (Path(__file__).resolve().parents[2] / "backend" / "data").resolve()


def add_target_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument(
        "--db-url",
        default=None,
        help=(
            "SQLAlchemy DB URL to target, e.g. sqlite:////tmp/fixture/test.db — for a "
            "test/fixture database ONLY. Required unless " + LIVE_FLAG + " is given."
        ),
    )
    ap.add_argument(
        LIVE_FLAG,
        action="store_true",
        help=(
            "Target the app's normally-configured LIVE database (DATA_DIR/onw.db or "
            "ONW_DATABASE_URL). NEVER pass this against the operator's real "
            "~/.open-net-worth install from an automated/agent session — this flag exists "
            "for the operator to run the script themselves after reviewing a dry-run "
            "report produced with --db-url against a fixture."
        ),
    )


def _sqlite_path(db_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return None
    raw = db_url[len(prefix) :]
    if not raw or raw == ":memory:":
        return None
    return Path(raw).expanduser().resolve()


def _looks_live(db_url: str) -> bool:
    path = _sqlite_path(db_url)
    if path is None:
        return False
    try:
        path.relative_to(_REAL_INSTANCE_ROOT)
        return True
    except ValueError:
        pass
    try:
        path.relative_to(_REPO_SEED_DATA)
        return True
    except ValueError:
        return False


def resolve_db_url(args: argparse.Namespace) -> str:
    """The DB URL this run should target, or ``SystemExit`` with a clear fix.

    ``--db-url`` wins when given, EXCEPT it is still refused if it resolves inside the
    live instance tree or the repo's tracked seed data — those two paths are only ever
    legitimate via the explicit live-risk flag, never disguised as a "fixture" URL.
    """
    accept_live = bool(getattr(args, "i_accept_live_data_risk", False))
    db_url = getattr(args, "db_url", None)

    if db_url:
        if _looks_live(db_url) and not accept_live:
            raise SystemExit(
                f"refusing to run: --db-url {db_url!r} resolves inside the operator's live "
                f"install or the repo's tracked seed data. Point --db-url at an isolated "
                f"fixture/tmp_path database, or pass {LIVE_FLAG} if this really is meant."
            )
        return db_url
    if accept_live:
        from backend.db.engine import database_url

        return database_url()
    raise SystemExit(
        "refusing to run: no target given.\n"
        "  For a dry run against a fixture/tmp_path database:\n"
        "    --db-url sqlite:////tmp/x/test.db\n"
        f"  To target the real configured install (operator only — never from an agent):\n"
        f"    {LIVE_FLAG}\n"
    )


def bind_db_url(db_url: str) -> None:
    """Point ``backend.db.engine``'s global engine at ``db_url`` and drop any cached
    connection so every store instantiated AFTER this call sees the new target. Mirrors
    ``tests/db/conftest.py``'s own ``monkeypatch.setenv(\"ONW_DATABASE_URL\", ...)`` +
    ``dispose_engine()`` pairing — same mechanism, without the pytest fixture."""
    os.environ["ONW_DATABASE_URL"] = db_url
    from backend.db.engine import dispose_engine

    dispose_engine()
