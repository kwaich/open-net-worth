"""BudgetEngineStateStore — the default per-(book, engine) configuration store for a
pluggable budget engine that owns no bespoke store of its own (.claude/rfcs/
2026-08-22-pluggable-budget-engines.md, Decision A/E).

Table ``budget_engine_state`` (backend/db/tables.py, revision 0049): one row per
``(book_id, engine_id)``, ``id`` the deterministic surrogate ``f"{book_id}:{engine_id}"``.
``payload`` is opaque, engine-owned JSON — this store never inspects or validates its
contents (an engine's own business rules, e.g. "targets sum to 100%", live in
``services/budget_engines/``, not here). Envelope and category-caps keep their existing
bespoke stores and never land a row here — see the table's own comment for the full roster.

Hand-rolled, not a ``BookPayloadTableStore`` subclass — like ``budget_assignments`` (its
direct shape precedent, ``db/budget_assignments_store.py``), this table has no ``seq``
column (a keyed relation has no insertion order). The ``conn=``/``_lock``/
``fire_db_write_hook`` plumbing (``_eng``/``_use_conn``) is copied VERBATIM from
``db/account_store.py`` — same contract, same lazy-engine-resolution reasoning: a caller is
free to hold this store as a long-lived module singleton, so ``_eng()`` must never cache a
lazily-resolved default (see that method's own docstring for the exact hazard this avoids).

Every write does a read-modify-write inside ONE locked transaction (``_use_conn``) and fires
``core.io.fire_db_write_hook`` with the before-image (the whole prior row, or ``None`` for a
fresh insert) — checkpoint-covered like every other DB store, so switching lenses or editing a
lens's config is undoable (``budget_engine_state`` is NOT in
``ai.checkpoint._policy._EXCLUDED_TABLES``). The per-table ``_lock`` (``_lock_for_table``)
serializes same-row read-modify-write races, mirroring ``BudgetAssignmentsStore``'s own
concurrency guarantee exactly.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import DEFAULT_BOOK_ID, budget_engine_state


def _row_id(engine_id: str) -> str:
    # book-prefixed surrogate: two books can never collide on the same engine's row.
    return f"{DEFAULT_BOOK_ID}:{engine_id}"


class BudgetEngineStateStore:
    def __init__(self, engine: Engine | None = None):
        self._table = budget_engine_state
        self._engine = engine
        self._lock = _lock_for_table(budget_engine_state.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently. The
        DEFAULT (``engine=None``) is never cached here — resolved fresh via ``get_engine()``
        on every call. Mirrors ``AccountStore._eng()``/``BudgetAssignmentsStore._eng()``
        verbatim, for the identical reason: a caller may hold this store as a long-lived
        module singleton, constructed once at import time — caching the lazily-resolved
        default would pin it to whichever engine happened to be live the FIRST time it was
        ever used, silently ignoring every later ``db_isolated``-style redirect."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``AccountStore._use_conn``/``BudgetAssignmentsStore._use_conn`` exactly —
        see either module's CONN= COMPOSITION docstring section for the full contract."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    # ── Reads (lock-free) ────────────────────────────────────────────────────────────

    def get(self, engine_id: str) -> dict | None:
        """One engine's own config, or ``None`` when it has never set one."""
        with self._eng().connect() as conn:
            row = conn.execute(
                select(self._table).where(self._table.c.id == _row_id(engine_id))
            ).first()
        return dict(row.payload) if row else None

    def all_for_book(self) -> dict[str, dict]:
        """Every configured engine's payload, keyed by ``engine_id`` — "which engines are
        configured" for the lens switcher's "set up" vs "switch to" distinction."""
        with self._eng().connect() as conn:
            rows = conn.execute(select(self._table).where(self._table.c.book_id == DEFAULT_BOOK_ID))
            return {r.engine_id: dict(r.payload) for r in rows}

    # ── Write ────────────────────────────────────────────────────────────────────────

    def set(self, engine_id: str, payload: dict, *, conn: Connection | None = None) -> dict:
        """Upsert one engine's config in place. ``conn`` composes this write into a caller's
        own open transaction (choke composition) — ``None`` opens its own, lock-held one."""
        rid = _row_id(engine_id)
        with self._use_conn(conn) as conn:
            existing = conn.execute(select(self._table).where(self._table.c.id == rid)).first()
            fire_db_write_hook(
                self._table.name,
                [(rid, dict(existing._mapping) if existing else None)],
                conn=conn,
            )
            if existing:
                conn.execute(
                    sa_update(self._table).where(self._table.c.id == rid).values(payload=payload)
                )
            else:
                conn.execute(
                    sa_insert(self._table).values(
                        id=rid, book_id=DEFAULT_BOOK_ID, engine_id=engine_id, payload=payload
                    )
                )
        return payload

    def delete(self, engine_id: str, *, conn: Connection | None = None) -> bool:
        """Remove one engine's config. Returns False when it had none (a no-op, not an
        error) — mirrors every other keyed store's delete contract in this codebase."""
        rid = _row_id(engine_id)
        with self._use_conn(conn) as conn:
            existing = conn.execute(select(self._table).where(self._table.c.id == rid)).first()
            if existing is None:
                return False
            fire_db_write_hook(self._table.name, [(rid, dict(existing._mapping))], conn=conn)
            conn.execute(sa_delete(self._table).where(self._table.c.id == rid))
        return True
