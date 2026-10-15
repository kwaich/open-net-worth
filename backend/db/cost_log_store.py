"""CostLogStore / CostLogDailyStore — persistent cost tracking for every AI call.

``cost_log``: one row per LLM turn, written by ``ai.cost_tracker.record()``. ``cost_log_daily``:
the ``"YYYY-MM-DD|model|source"``-keyed rollup that ``record()`` folds a raw row into once it
ages out of the retention window (see that module for the retention/backstop policy — unchanged
by this migration). Both are AI-cost TELEMETRY, never summed into net worth or any financial
total (``logs_io.py``'s own "NO MONEY GUARD" rule). Runtime/behavioral → checkpoint-EXCLUDED
(``ai/checkpoint._EXCLUDED_TABLES``; a Lucius restore must not rewrite spend history).

WAVE 5B (JSON->DB): replaces the hand-rolled ``json.loads``/``atomic_write_text`` I/O in
``ai/cost_tracker.py`` (both files) and the direct reads in ``services/cost.py``.

Raw cost records have NO natural id field (``ai/cost_tracker.py`` never used a ``JsonStore``) —
``append`` mints one fresh per row via ``new_id``, living ONLY in the row's ``id`` column, never
mixed into ``payload`` (matches ``logs_io.import_payload_list_minted``'s cutover-side precedent,
so a historical row and a row appended live afterward carry the exact same payload shape).
Because the eviction/rollup decision needs to know WHICH row is which (a bare payload carries no
id to delete by), ``all_with_ids``/``delete_many`` expose that — the two primitives
``all()``/``replace_all()`` (inherited from ``PayloadTableStore``) cannot serve this store's
eviction path, since both assume an item's id lives inside its own payload dict.

``CostLogDailyStore`` mirrors ``ModelTelemetryStore``'s hand-rolled shape exactly: it subclasses
``PayloadTableStore`` for its plumbing only (``_use_conn``/``_row``/``_fire_hook``/``_next_seq``/
``_insert_row``) — never its generic domain methods, since the bucket key lives in the ``id``
COLUMN, not inside the payload dict (the payload's own "date"/"model"/"source" fields
redundantly encode the same three components, but the key is never re-derived from them).
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import cost_log, cost_log_daily


class CostLogStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(cost_log, id_field="id", engine=engine)

    def append(self, rec: dict) -> str:
        """Append one raw cost record. Returns the newly minted row id (never embedded
        in ``payload`` — see module docstring)."""
        with self._use_conn(None) as conn:
            row_id = new_id("cost")
            self._fire_hook(conn, [(row_id, None)])
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**self._insert_row(seq, row_id, rec)))
        return row_id

    def retention_probe(self) -> tuple[int, str]:
        """``(row count, oldest row's ts)`` — the two facts ``ai.cost_tracker.record``
        needs to decide whether ANY eviction work is due, without materialising the log.

        ``record`` runs on every single LLM call and its comment promises a fast path
        ("only scan when the oldest record has aged out"), but it called ``all_with_ids``
        FIRST and then read exactly ``rows[0]`` and ``len(rows)`` off the result — so the
        fast path loaded the whole table anyway. That put a full scan on the event loop
        once per turn (live 2026-08-11 18:47, caught by ``core.loop_watch`` under
        ``news_screen`` → ``complete_model``). Two indexed reads answer the same question.

        Oldest is ``min(seq)``, not ``min(ts)`` — deliberately identical to the ``rows[0]``
        it replaces, which took the first row of a ``seq``-ordered scan.
        """
        with self._eng().connect() as conn:
            total = conn.execute(select(func.count()).select_from(self._table)).scalar_one()
            oldest = conn.execute(
                select(self._table.c.payload).order_by(self._table.c.seq).limit(1)
            ).scalar_one_or_none()
        ts = oldest.get("ts") if isinstance(oldest, dict) else None
        return int(total), ts or ""

    def all_with_ids(self) -> list[tuple[str, dict]]:
        """Every row as ``(id, payload)``, oldest first — the shape the retention/backstop
        eviction in ``ai.cost_tracker.record`` needs to identify exactly which rows to
        delete (a bare payload, from ``all()``, carries no id to delete by)."""
        with self._eng().connect() as conn:
            rows = conn.execute(select(self._table).order_by(self._table.c.seq))
            return [(r.id, r.payload) for r in rows]

    def delete_many(self, ids: list[str]) -> None:
        """Bulk-delete specific rows by id — the eviction/rollup and one-shot-dedup
        paths. A no-op for an empty list (mirrors ``bulk_insert``'s own empty-input
        contract)."""
        if not ids:
            return
        with self._use_conn(None) as conn:
            existing = self._rows_map(conn, ids)
            hook_rows = [(i, existing[i]) for i in ids if i in existing]
            if hook_rows:
                self._fire_hook(conn, hook_rows)
            conn.execute(sa_delete(self._table).where(self._table.c.id.in_(ids)))


class CostLogDailyStore(PayloadTableStore):
    def __init__(self, engine=None):
        # id_field="key" is documentation only — every method below is hand-rolled and
        # never touches self._id_field (mirrors ModelTelemetryStore's own comment).
        super().__init__(cost_log_daily, id_field="key", engine=engine)

    def merge_records(self, records: list[dict]) -> None:
        """Fold raw cost records into their ``date|model|source`` daily buckets — the
        exact aggregation the pre-migration ``_rollup_to_daily`` performed against a
        whole-dict file rewrite, now one row per bucket. Existing buckets accumulate;
        ``session_id`` is intentionally dropped (daily aggregates are summaries only).
        One transaction: later records in the SAME call see earlier ones' updates to the
        same bucket, exactly like the original's single in-memory dict pass."""
        if not records:
            return
        with self._use_conn(None) as conn:
            for r in records:
                raw_ts: str = r.get("ts") or ""
                date_key = raw_ts[:10] if len(raw_ts) >= 10 else "unknown"
                key = f"{date_key}|{r.get('model', 'unknown')}|{r.get('source', 'unknown')}"
                existing = self._row(conn, key)
                agg = (
                    dict(existing["payload"])
                    if existing
                    else {
                        "date": date_key,
                        "model": r.get("model", "unknown"),
                        "source": r.get("source", "unknown"),
                        "turns": 0,
                        "input_tokens": 0,
                        "cache_creation_tokens": 0,
                        "cache_read_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": 0.0,
                    }
                )
                agg["turns"] += 1
                agg["input_tokens"] += r.get("input_tokens", 0)
                agg["cache_creation_tokens"] += r.get("cache_creation_tokens", 0)
                agg["cache_read_tokens"] += r.get("cache_read_tokens", 0)
                agg["output_tokens"] += r.get("output_tokens", 0)
                agg["cost_usd"] += r.get("cost_usd", 0.0)
                self._fire_hook(conn, [(key, existing)])
                if existing is None:
                    seq = self._next_seq(conn)
                    conn.execute(sa_insert(self._table).values(**self._insert_row(seq, key, agg)))
                else:
                    conn.execute(
                        sa_update(self._table).where(self._table.c.id == key).values(payload=agg)
                    )

    def all_as_dict(self) -> dict[str, dict]:
        """Every bucket, keyed by its ``date|model|source`` string."""
        with self._eng().connect() as conn:
            return {r.id: r.payload for r in conn.execute(select(self._table))}

    def delete_keys(self, keys: list[str]) -> None:
        """Bulk-delete specific buckets by key — the one-shot-dedup path
        (``scripts/dedup_cost_log.py``'s narration-phantom sweep)."""
        if not keys:
            return
        with self._use_conn(None) as conn:
            existing = self._rows_map(conn, keys)
            hook_rows = [(k, existing[k]) for k in keys if k in existing]
            if hook_rows:
                self._fire_hook(conn, hook_rows)
            conn.execute(sa_delete(self._table).where(self._table.c.id.in_(keys)))
