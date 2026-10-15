"""MergeJournalStore — the undo journal for the dedup/match engine's merge primitive.

A merge folds an *absorbed* transaction into a *canonical* one (RFC transaction-dedup-engine
§merge primitive). To make every merge reversible byte-for-byte — even a ``become_transfer``
merge that rebuilt the canonical row's legs — we snapshot BOTH rows' full pre-merge state here,
keyed by the absorbed id. ``ledger.unmerge_transactions`` reads the snapshot, restores both rows
exactly, and drops the journal entry.

One entry per active (un-undone) merge:

    absorbed_id              : str   (the tombstoned row's id — the journal key)
    canonical_id             : str   (the surviving row that absorbed it)
    canonical_before         : dict  (full canonical row snapshot, pre-merge)
    absorbed_before          : dict  (full absorbed row snapshot, pre-merge)
    post_canonical_snapshot  : dict | absent
        Expected state of the canonical row AFTER the merge was applied. When present,
        ``unmerge_transactions`` compares the live canonical against this snapshot and
        REFUSES the undo if they differ (i.e. the row was edited/deleted post-merge).
        Back-compat: entries without this field (created before the hardening) are
        accepted as-is and proceed with the old byte-exact restore behaviour.
    become_transfer : bool  (whether the canonical was rebuilt into a transfer)
    reason          : str   (why the merge happened — audit)
    created_at      : str   (ISO 8601 UTC)

PLAN 3 TASK 4: DB-backed (Core, table ``merge_journal`` — backend/db/tables.py). Rows are
promoted-columns-over-verbatim-payload, like ``accounts``: ``payload`` is the round-trip
source of truth (the entry dict exactly as ``record()`` was given), ``canonical_id``/
``created_at`` are the derived query surface, recomputed from ``payload`` on every write via
``money_io._merge_journal_row`` (never duplicated here).

Hand-rolled, not a ``payload_store.PayloadTableStore`` subclass: this table's primary-key
column is ``absorbed_id``, not the generic ``id`` every ``PayloadTableStore`` method hardcodes
(``self._table.c.id``) — and even a same-named PK wouldn't help, since every mutate here also
writes ``canonical_id``/``created_at`` beyond the bare ``{seq, id, payload}`` shape
``PayloadTableStore`` writes. The conn=/lock/hook plumbing below mirrors
``PayloadTableStore``'s own (see that module's docstring for the CONN= COMPOSITION and
CHECKPOINTS contracts — honored identically here) — duplicated, not imported, because neither
module may import the other's private surface across this specific mismatch.

Checkpoints: this table is NOT in ``ai.checkpoint._EXCLUDED_TABLES`` — a file-level check
found ``merge_journal.json`` was never in ``ai.checkpoint._EXCLUDED_PATHS`` either (unlike
``txn_audit.json``), so it has always been an ordinary, restorable file; the DB table mirrors
that today-true behavior exactly (plan 3 task 4 finding — see the task's report).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, cast

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.money_io import _merge_journal_row
from backend.db.tables import merge_journal


class MergeJournalStore:
    def __init__(self, engine: Engine | None = None):
        self._table = merge_journal
        self._engine = engine
        self._lock = _lock_for_table(merge_journal.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently.
        The DEFAULT (``engine=None``) is never cached here — resolved fresh via
        ``get_engine()`` on every call. This store can be used as a long-lived MODULE
        singleton (e.g. ``services.ledger.transactions``/``services.audit._audit``),
        constructed once at import time and shared for the process's whole lifetime;
        caching the lazily-resolved default would pin it to whichever engine happened
        to be live the FIRST time it was ever used, silently ignoring every later
        ``db_isolated``-style redirect (env var + ``engine.dispose_engine()``) —
        exactly the bug this avoids."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``PayloadTableStore._use_conn`` exactly — see that module's CONN=
        COMPOSITION docstring section for the full contract."""
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    def _row(self, conn: Connection, absorbed_id: str) -> dict | None:
        row = conn.execute(
            select(self._table).where(self._table.c.absorbed_id == absorbed_id)
        ).first()
        return dict(row._mapping) if row else None

    def _next_seq(self, conn: Connection) -> int:
        return conn.execute(select(func.coalesce(func.max(self._table.c.seq), -1))).scalar_one() + 1

    def _write_row(self, conn: Connection, absorbed_id: str, row: dict) -> None:
        conn.execute(
            sa_update(self._table)
            .where(self._table.c.absorbed_id == absorbed_id)
            .values(**{k: v for k, v in row.items() if k != "absorbed_id"})
        )

    # ── Query API (lock-free, mirrors JsonStore's read methods) ─────────────────

    def all(self) -> list[dict]:
        with self._eng().connect() as conn:
            return [
                r.payload for r in conn.execute(select(self._table).order_by(self._table.c.seq))
            ]

    def find_by_id(self, absorbed_id: str) -> dict | None:
        with self._eng().connect() as conn:
            row = self._row(conn, absorbed_id)
        return row["payload"] if row else None

    def query(self, **filters: Any) -> list[dict]:
        items = self.all()
        for key, value in filters.items():
            if value is None:
                continue
            items = [i for i in items if i.get(key) == value]
        return items

    def count(self, **filters: Any) -> int:
        return len(self.query(**filters))

    # ── Write API (JsonStore parity) ─────────────────────────────────────────────

    def insert(self, item: dict, *, conn: Connection | None = None) -> dict:
        absorbed_id = cast(str, item.get("absorbed_id"))
        with self._use_conn(conn) as conn:
            fire_db_write_hook(self._table.name, [(absorbed_id, None)], conn=conn)
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**_merge_journal_row(seq, item)))
        return item

    def bulk_insert(self, new_items: list[dict], *, conn: Connection | None = None) -> list[dict]:
        if not new_items:
            return new_items
        with self._use_conn(conn) as conn:
            fire_db_write_hook(
                self._table.name,
                [(item.get("absorbed_id"), None) for item in new_items],
                conn=conn,
            )
            seq = self._next_seq(conn)
            conn.execute(
                sa_insert(self._table),
                [_merge_journal_row(seq + i, item) for i, item in enumerate(new_items)],
            )
        return new_items

    def upsert(self, item: dict, *, conn: Connection | None = None) -> dict:
        """Insert or replace by absorbed_id. A replace KEEPS the row's seq (matches
        JsonStore's in-place ``items[i] = item``); a new absorbed_id is appended (next seq)."""
        absorbed_id = cast(str, item.get("absorbed_id"))
        with self._use_conn(conn) as conn:
            existing = self._row(conn, absorbed_id)
            fire_db_write_hook(self._table.name, [(absorbed_id, existing)], conn=conn)
            if existing is not None:
                self._write_row(conn, absorbed_id, _merge_journal_row(existing["seq"], item))
            else:
                seq = self._next_seq(conn)
                conn.execute(sa_insert(self._table).values(**_merge_journal_row(seq, item)))
        return item

    def update_by_id(
        self, absorbed_id: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        with self._use_conn(conn) as conn:
            existing = self._row(conn, absorbed_id)
            if existing is None:
                return None
            merged = {**existing["payload"], **changes}
            fire_db_write_hook(self._table.name, [(absorbed_id, existing)], conn=conn)
            self._write_row(conn, absorbed_id, _merge_journal_row(existing["seq"], merged))
        return merged

    def bulk_patch(
        self, updates: dict[str, dict], *, conn: Connection | None = None
    ) -> dict[str, dict]:
        if not updates:
            return {}
        with self._use_conn(conn) as conn:
            existing_rows = {
                aid: row for aid in updates if (row := self._row(conn, aid)) is not None
            }
            patched: dict[str, dict] = {}
            hook_rows: list[tuple[str, dict | None]] = []
            for absorbed_id, changes in updates.items():
                if absorbed_id in existing_rows:
                    patched[absorbed_id] = {**existing_rows[absorbed_id]["payload"], **changes}
                    hook_rows.append((absorbed_id, existing_rows[absorbed_id]))
            if patched:
                fire_db_write_hook(self._table.name, hook_rows, conn=conn)
                for absorbed_id, merged in patched.items():
                    self._write_row(
                        conn,
                        absorbed_id,
                        _merge_journal_row(existing_rows[absorbed_id]["seq"], merged),
                    )
        return patched

    def delete(self, absorbed_id: str, *, conn: Connection | None = None) -> bool:
        with self._use_conn(conn) as conn:
            existing = self._row(conn, absorbed_id)
            if existing is None:
                return False
            fire_db_write_hook(self._table.name, [(absorbed_id, existing)], conn=conn)
            conn.execute(sa_delete(self._table).where(self._table.c.absorbed_id == absorbed_id))
        return True

    def replace_all(self, items: list[dict], *, conn: Connection | None = None) -> None:
        with self._use_conn(conn) as conn:
            existing_map = {
                r.absorbed_id: dict(r._mapping) for r in conn.execute(select(self._table))
            }
            incoming_ids = {item.get("absorbed_id") for item in items}
            hook_rows = list(existing_map.items())
            hook_rows += [(iid, None) for iid in incoming_ids if iid not in existing_map]
            fire_db_write_hook(self._table.name, hook_rows, conn=conn)
            conn.execute(self._table.delete())
            if items:
                conn.execute(
                    sa_insert(self._table),
                    [_merge_journal_row(i, item) for i, item in enumerate(items)],
                )

    # ── Domain writes ─────────────────────────────────────────────────────────

    def record(self, entry: dict, *, conn: Connection | None = None) -> dict:
        """Persist (or replace) the undo snapshot for one merge, keyed by absorbed_id."""
        return self.upsert(entry, conn=conn)

    def pop(self, absorbed_id: str, *, conn: Connection | None = None) -> dict | None:
        """Return and remove the journal entry for ``absorbed_id`` (consumed by undo). Reads
        and deletes through the SAME connection when ``conn`` is given — atomic with the
        caller's transaction, and correct against any earlier-in-transaction change to this
        same row the caller made (a fresh, independent read could miss it on a dialect with
        real transaction isolation)."""
        with self._use_conn(conn) as conn:
            existing = self._row(conn, absorbed_id)
            if existing is None:
                return None
            payload = existing["payload"]
            fire_db_write_hook(self._table.name, [(absorbed_id, existing)], conn=conn)
            conn.execute(sa_delete(self._table).where(self._table.c.absorbed_id == absorbed_id))
        return payload
