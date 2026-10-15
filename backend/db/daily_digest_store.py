"""DailyDigestStore — one portfolio digest row per date (RFC intel-signals-data-model, split
of the old ``daily_digest``-typed ``intel_signals`` rows into their own table).

``date`` ('YYYY-MM-DD') is the LITERAL primary key, not a synthetic id — ``digest.py``'s
``_write_inapp_digest`` already treated the date as the real key
(``id: f"daily-digest-{date}"``, always upserted, never a list); this table makes that an
honest storage shape instead of an encoded id string. Promoted query-surface columns + a
verbatim ``payload`` — the ``accounts``/``evidence`` shape (``payload.period``/``pl_base``/
``pl_pct`` are a rendered snapshot, not promoted — nothing queries by them).

WIPE-ONLY, NOT bridged (``db/tables.py``'s own comment on this table has the full reasoning)
— but still checkpoint-INCLUDED (not in ``ai.checkpoint._EXCLUDED_TABLES``), unchanged from
its old home inside ``intel_signals``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import daily_digests

_DEFAULTS: dict[str, Any] = {
    "severity": "info",
    "title": "Daily Digest",
    "message": "",
    "affected_tickers": [],
    "sources": [],
    "source_urls": [],
}


def _row_values(digest: dict) -> dict:
    """Column dict for one ``daily_digests`` row. ``date``/``created_at``/``trigger`` carry
    no default — a digest missing one of those hits the table's own NOT NULL/CHECK
    constraint, a loud failure (same posture as every other promoted-column store here)."""
    row: dict = {
        "date": digest.get("date"),
        "created_at": digest.get("created_at"),
        "trigger": digest.get("trigger"),
        "read": bool(digest.get("read")),
        "payload": digest,
    }
    for field, default in _DEFAULTS.items():
        row[field] = digest.get(field) if digest.get(field) is not None else default
    return row


class DailyDigestStore:
    def __init__(self, engine=None):
        self._engine = engine
        self._lock = _lock_for_table(daily_digests.name)

    def _eng(self):
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    def all(self) -> list[dict]:
        with self._eng().connect() as conn:
            return [
                r.payload
                for r in conn.execute(select(daily_digests).order_by(daily_digests.c.date))
            ]

    def get(self, date: str) -> dict | None:
        with self._eng().connect() as conn:
            row = conn.execute(select(daily_digests).where(daily_digests.c.date == date)).first()
        return row.payload if row else None

    def upsert(self, digest: dict, *, conn: Connection | None = None) -> dict:
        """Insert or replace by ``date`` — the SAME id shape ``_write_inapp_digest`` has
        always used (an EOD write on a day the premarket digest already fired overwrites
        it; see ``db/tables.py``'s comment on this table for why that is preserved, not
        silently fixed)."""
        date = digest.get("date")
        if conn is not None:
            self._upsert_locked(conn, date, digest)
            return digest
        with self._lock, self._eng().begin() as owned_conn:
            self._upsert_locked(owned_conn, date, digest)
        return digest

    def _upsert_locked(self, conn: Connection, date: str, digest: dict) -> None:
        existing = conn.execute(select(daily_digests).where(daily_digests.c.date == date)).first()
        fire_db_write_hook(
            daily_digests.name, [(date, dict(existing._mapping) if existing else None)], conn=conn
        )
        values = _row_values(digest)
        if existing is not None:
            values.pop("date")
            conn.execute(
                sa_update(daily_digests).where(daily_digests.c.date == date).values(**values)
            )
        else:
            conn.execute(sa_insert(daily_digests).values(**values))

    def delete(self, date: str) -> bool:
        with self._lock, self._eng().begin() as conn:
            existing = conn.execute(
                select(daily_digests).where(daily_digests.c.date == date)
            ).first()
            if existing is None:
                return False
            fire_db_write_hook(daily_digests.name, [(date, dict(existing._mapping))], conn=conn)
            conn.execute(sa_delete(daily_digests).where(daily_digests.c.date == date))
        return True
