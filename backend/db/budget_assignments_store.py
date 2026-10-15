"""
BudgetAssignmentsStore — YNAB envelope ASSIGNED amounts, keyed month -> category (PLAN).

The one genuinely-new store the Planning feature needs: per-(YYYY-MM, category) "Assigned"
amount, stored **in base currency** at write time. Envelopes are base-only, so the stored
value is authoritative (no FX re-derivation for Assigned itself — unlike Activity, which the
service always recomputes from the native txn leg).

PLAN 4A TASK 4: DB-backed (Core, table ``budget_assignments`` — backend/db/tables.py). This is
the ONE planning store whose money is promoted to a typed column: ``amount`` is ``Text``
holding a ``repr(float(...))``-exact decimal string (the revision-0008 pattern
``money_io._money_text`` established for the money core, reused here — DRY, the ONE
``repr(float)`` helper) — NEVER a native ``Float`` column. Reads do ``float(amount_text)``,
bit-identical to the old JsonStore-era float reads. One row per ``(book, month, category)``;
``id`` is the deterministic, book-prefixed surrogate ``f"{book_id}:{month}:{category}"`` (all
three components colon-free) so two books can never collide on the same (month, category)
primary key.

Hand-rolled, not a ``BookPayloadTableStore`` subclass (unlike the other eleven planning
stores): this table has no ``seq``/insertion-order column (a keyed relation has none) and its
one money field is promoted rather than living inside a payload blob — the shape genuinely
differs, not just the call surface. The ``conn=``/``_lock``/``fire_db_write_hook`` plumbing
(``_eng``/``_use_conn``) is copied VERBATIM from ``db/account_store.py`` — same contract, same
lazy-engine-resolution reasoning: this store is ALSO constructed as a long-lived MODULE
singleton (``services.envelopes.assignments_store``, ``services.planning.assignments_store``)
at import time, so ``_eng()`` must never cache a lazily-resolved default (see that method's
own docstring for the exact hazard this avoids).

Dumb storage + shape coercion ONLY — all rollover / available / ready-to-assign math lives in
``services.planning`` (never here). ``set_assigned`` upserts and DELETES the row when
``amount == 0`` (preserves the sparse-file invariant a Ready-to-Assign scan over ``months()``/
``all_map()`` depends on — a zero assignment is ABSENT, not a stored zero). Only an EXACT zero
removes the entry: a NEGATIVE amount is stored as-is (FEB-02 fix, 2026-07-04 — moving money OUT
of an envelope is capped at its AVAILABLE, not this-month assigned, so a move can legitimately
drive assigned negative; the availability identity ``prev + assigned + activity``
already absorbs it, the same way an in-month overspend does).

Writes fire ``core.io.fire_db_write_hook`` with the before-image (the whole prior row, or
``None`` for a fresh insert) — checkpoint-covered like every other DB store, so a Lucius turn
that assigns money is undoable (``budget_assignments`` is NOT in
``ai.checkpoint._EXCLUDED_TABLES``). The per-table ``_lock`` (``_lock_for_table`` — a
module-wide registry keyed by table name, mirrors ``json_store``'s own per-path registry)
replaces the old per-path ``_write_lock`` and preserves the exact concurrency guarantee
BUD-SWEEP-01 (2026-07-04) fixed: the read-modify-write in ``set_assigned`` holds the lock
across the WHOLE read+write, so concurrent assigns (the auto-assign fan-out, a Lucius turn
racing the UI) can never last-writer-wins clobber each other.

See docs/prd/ynab-planning.md.
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
from backend.db.money_io import _assert_finite_money, _money_text
from backend.db.tables import DEFAULT_BOOK_ID, budget_assignments


def _round(value) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _row_id(month: str, category: str) -> str:
    # book-prefixed surrogate: two books can never collide on the same (month, category) PK.
    return f"{DEFAULT_BOOK_ID}:{month}:{category}"


class BudgetAssignmentsStore:
    def __init__(self, engine: Engine | None = None):
        self._table = budget_assignments
        self._engine = engine
        self._lock = _lock_for_table(budget_assignments.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently. The
        DEFAULT (``engine=None``) is never cached here — resolved fresh via ``get_engine()``
        on every call. Mirrors ``AccountStore._eng()`` verbatim, for the identical reason:
        this store is used as a long-lived MODULE singleton (``services.envelopes.
        assignments_store``, ``services.planning.assignments_store``), constructed once at
        import time and shared for the process's whole lifetime — caching the lazily-resolved
        default would pin it to whichever engine happened to be live the FIRST time it was
        ever used, silently ignoring every later ``db_isolated``-style redirect (env var +
        ``engine.dispose_engine()``) — exactly the bug this avoids."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``AccountStore._use_conn``/``PayloadTableStore._use_conn`` exactly — see
        either module's CONN= COMPOSITION docstring section for the full contract."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    # ── Reads (lock-free, unfiltered-by-book — single book today, mirrors the money stores) ──

    def month_map(self, month: str) -> dict[str, float]:
        """Assigned amounts for one month: {category: amount}. Empty when the month is absent."""
        with self._eng().connect() as conn:
            rows = conn.execute(select(self._table).where(self._table.c.month == month))
            return {r.category: _round(float(r.amount)) for r in rows}

    def assigned(self, month: str, category: str) -> float:
        """Assigned (base) for one (month, category); 0.0 when unset."""
        return _round(self.month_map(month).get(category, 0.0))

    def months(self) -> list[str]:
        """Sorted list of months that carry any assignment (YYYY-MM)."""
        with self._eng().connect() as conn:
            return sorted({r.month for r in conn.execute(select(self._table.c.month))})

    def all_map(self) -> dict[str, dict[str, float]]:
        """The whole {month: {category: amount}} map in ONE load — for callers that need to
        fold assignments across many months (e.g. cumulative-contributions for a by_date
        set-aside target) without re-reading the table per month."""
        out: dict[str, dict[str, float]] = {}
        with self._eng().connect() as conn:
            for r in conn.execute(select(self._table)):
                out.setdefault(r.month, {})[r.category] = _round(float(r.amount))
        return out

    # ── Write ──────────────────────────────────────────────────────────────────────────

    def set_assigned(
        self, month: str, category: str, amount: float, *, conn: Connection | None = None
    ) -> float:
        """Set the assigned amount for one (month, category) and persist. Returns the stored
        (rounded) amount. See the module docstring for the sparse-delete-on-exact-zero and
        negative-amount-preserved rules. ``conn`` composes this write into a caller's own open
        transaction (choke composition, plan 4a task 5) — ``None`` opens its own, lock-held
        one.

        M1 fix (plan 4a security review): asserts ``amount`` finite via the SAME guard the
        money core uses (``money_io._assert_finite_money``, FR-M7) — the one store-level
        chokepoint every caller funnels through, so a live ``set_assigned(inf)`` is rejected
        instead of poisoning budget/RTA display math (inf - inf -> nan)."""
        rid = _row_id(month, category)
        _assert_finite_money(amount, field="amount", row_id=rid)
        amt = _round(amount)
        with self._use_conn(conn) as conn:
            existing = conn.execute(select(self._table).where(self._table.c.id == rid)).first()
            fire_db_write_hook(
                self._table.name,
                [(rid, dict(existing._mapping) if existing else None)],
                conn=conn,
            )
            if amt != 0:
                if existing:
                    conn.execute(
                        sa_update(self._table)
                        .where(self._table.c.id == rid)
                        .values(amount=_money_text(amt))
                    )
                else:
                    conn.execute(
                        sa_insert(self._table).values(
                            id=rid,
                            book_id=DEFAULT_BOOK_ID,
                            month=month,
                            category=category,
                            amount=_money_text(amt),
                        )
                    )
            elif existing:
                conn.execute(sa_delete(self._table).where(self._table.c.id == rid))
        return amt
