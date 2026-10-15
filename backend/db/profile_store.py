"""ProfileStore — server-side persistence for the owner's financial profile.

Row shape (table ``profile``, backend/db/tables.py): ONE row per book, keyed
``book_id = DEFAULT_BOOK_ID`` — a per-book singleton (plan_state's exact pattern). ``payload`` is
the whole profile dict (base_currency, risk prefs, FIRE inputs, liabilities, briefing_preferences,
monthly_budgets, category_targets/notes, …) stored VERBATIM. Profile amounts are user-entered
projection/config inputs, never ledger money summed into net worth, so they stay raw in
``payload`` — no decimal-Text promotion (plan_state's rule).

WAVE 5 (JSON->DB): replaces the hand-rolled ``open(profile.json)`` / ``atomic_write_text`` I/O in
services/profile.py + services/budgets.py. Every read-modify-write happens inside ONE locked
transaction (``_use_conn`` + ``_read_payload`` + ``_persist``), replacing budgets.py's
process-local ``threading.Lock`` — so two concurrent profile writers can no longer clobber each
other, even across uvicorn workers (the lost-update race budgets.py documented). Writes fire
``core.io.fire_db_write_hook`` with the before-image, so a profile edit is checkpoint-covered
(``profile`` is NOT in ``ai.checkpoint._EXCLUDED_TABLES`` — durable user config, like plan_state).

The ``_eng``/``_use_conn``/``_lock``/``_persist`` plumbing is copied VERBATIM from
``db/plan_state_store.py`` — same singleton-table contract, same lazy-engine-resolution reasoning
(this store is ALSO used as a long-lived MODULE singleton, ``services.profile._store``,
constructed once at import time).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import DEFAULT_BOOK_ID, profile


class ProfileStore:
    """Persists the owner's financial profile — one row per book (PK ``book_id``)."""

    def __init__(self, engine: Engine | None = None):
        self._table = profile
        self._engine = engine
        self._lock = _lock_for_table(profile.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently; the DEFAULT
        (``engine=None``) is resolved fresh via ``get_engine()`` on every call, never cached —
        mirrors ``PlanStateStore._eng``/``AccountStore._eng`` for the same module-singleton reason.
        """
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``PlanStateStore._use_conn`` exactly — a caller-supplied ``conn`` composes into
        an outer unit-of-work; ``None`` opens an owned, lock-held transaction."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    def _read_payload(self, conn: Connection) -> dict:
        row = conn.execute(
            select(self._table).where(self._table.c.book_id == DEFAULT_BOOK_ID)
        ).first()
        return dict(row.payload) if row and isinstance(row.payload, dict) else {}

    def _persist(self, conn: Connection, data: dict) -> None:
        existing = conn.execute(
            select(self._table).where(self._table.c.book_id == DEFAULT_BOOK_ID)
        ).first()
        fire_db_write_hook(
            self._table.name,
            [(DEFAULT_BOOK_ID, dict(existing._mapping) if existing else None)],
            conn=conn,
        )
        if existing:
            conn.execute(
                sa_update(self._table)
                .where(self._table.c.book_id == DEFAULT_BOOK_ID)
                .values(payload=data)
            )
        else:
            conn.execute(sa_insert(self._table).values(book_id=DEFAULT_BOOK_ID, payload=data))

    def get_raw(self, *, conn: Connection | None = None) -> dict:
        """The stored profile dict VERBATIM, or ``{}`` when no row exists yet (fresh install — the
        service layer merges DEFAULT_PROFILE on top, exactly as the old file-absent path did)."""
        if conn is not None:
            return self._read_payload(conn)
        with self._eng().connect() as c:
            return self._read_payload(c)

    def set_raw(self, data: dict, *, conn: Connection | None = None) -> None:
        """Replace the whole profile row with ``data`` (the caller owns the merge). One
        transaction; fires the write hook with the before-image, so the edit is checkpoint-covered."""
        with self._use_conn(conn) as conn:
            self._persist(conn, data)

    def set_key(self, key: str, value, *, conn: Connection | None = None) -> None:
        """Atomic read-modify-write of ONE key — the whole cycle runs in a single transaction, so a
        concurrent key write or a full-profile PUT can no longer lost-update it (the exact race the
        old ``_profile_write_lock`` guarded, now enforced by the DB instead of a per-process lock)."""
        with self._use_conn(conn) as conn:
            data = self._read_payload(conn)
            data[key] = value
            self._persist(conn, data)
