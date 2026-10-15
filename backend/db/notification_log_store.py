"""NotificationLogStore — the disposable ring of things the app told the user about.

A flat list of notification rows, newest-first, hard-capped at ``MAX_NOTIFICATION_LOG_ROWS``
(oldest dropped). Disposable runtime cache — a poll/write produces these, the user
dismisses/reads them, nothing here is authored data.

Renamed from ``signals``/``SignalsStore`` (2026-08-20, revision 0044) — an audit of its
actual live content found it holds operational/system activity (automation run digests,
run-ledger failure/recovery alerts, unattended-write reviews, reminders), never market/news
signal data, which lives entirely in the unrelated, checkpoint-INCLUDED ``IntelSignalsStore``.
See the migration's docstring for the full rationale.

PLAN 4D TASK 2: DB-backed (Tier-2 cache, table ``notification_log``) via ``PayloadTableStore``.
Checkpoint-EXCLUDED (``ai.checkpoint._EXCLUDED_TABLES``, added in the same commit as this
rewrite) — a ``/rewind`` must not resurrect a dismissed notification or erase a fresh one,
mirroring the legacy ``SIGNALS_JSON`` file-level exclusion.

**Newest-first, preserved exactly** (the class of bug Wave C's ``logs_io`` cutover fix
addressed for the 4 prepend-order log stores — see ``docs/adr`` / that wave's report):
``append`` inserts each new row at the next ASCENDING ``seq`` (never a decreasing one —
``_next_seq``'s ``max(seq)+1`` invariant is never special-cased), and every read
(``query_signals`` via ``_filtered``) explicitly orders ``seq DESC``. That descending read —
not insertion order — is what makes the ring newest-first, exactly mirroring
``tool_call_log_store``/``mcp_write_log_store`` (Design Note 2, plan 4c). The cap drops the
OLDEST (lowest-seq) rows, all inside ONE base transaction (mirrors Wave A's
``CorrectionStore._append_capped``).

``fail_closed_on_corrupt=False`` was the JsonStore-era self-heal for a half-written file; a
DB row has no equivalent failure mode (there is no half-written row to quarantine — a write
either commits or it doesn't), so that flag has no DB counterpart and is not ported.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import notification_log

MAX_NOTIFICATION_LOG_ROWS = 200


class NotificationLogStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(notification_log, id_field="id", engine=engine)

    # ── Domain queries ────────────────────────────────────────────────────────

    def _filtered(
        self,
        type: str | None = None,
        unread_only: bool = False,
        ticker: str | None = None,
    ) -> list[dict]:
        """The shared, UNLIMITED filter pass behind query_signals/count_signals — newest
        first (``ORDER BY seq DESC``, see module docstring). One implementation so a count
        can never disagree with the page it describes."""
        with self._eng().connect() as conn:
            items = [
                r.payload
                for r in conn.execute(select(self._table).order_by(self._table.c.seq.desc()))
            ]
        if type is not None:
            items = [s for s in items if s.get("type") == type]
        if unread_only:
            items = [s for s in items if not s.get("read")]
        if ticker is not None:
            ticker_up = ticker.upper()
            items = [
                s for s in items if ticker_up in [t.upper() for t in s.get("affected_tickers", [])]
            ]
        return items

    def query_signals(
        self,
        type: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
        ticker: str | None = None,
    ) -> list[dict]:
        return self._filtered(type=type, unread_only=unread_only, ticker=ticker)[:limit]

    def count_signals(
        self,
        type: str | None = None,
        unread_only: bool = False,
        ticker: str | None = None,
    ) -> int:
        """How many notifications match, IGNORING any limit — the denominator a capped read
        needs so "20 signals" can't be mistaken for "all the signals". Cheap: the store is
        hard-capped at MAX_NOTIFICATION_LOG_ROWS rows."""
        return len(self._filtered(type=type, unread_only=unread_only, ticker=ticker))

    def unread_count(self) -> int:
        return sum(1 for s in self.all() if not s.get("read"))

    # ── Writes ────────────────────────────────────────────────────────────────

    def append(self, data: dict) -> dict:
        """Insert a new notification at the next (ascending) seq, capping at
        MAX_NOTIFICATION_LOG_ROWS by dropping the OLDEST (lowest-seq) rows — all inside ONE
        base transaction, the read-modify-write cycle the removed ``_write_lock``/
        ``_load_items``/``_save_items`` plumbing used to provide."""
        new = _make_signal(data)
        with self._use_conn(None) as conn:
            self._fire_hook(conn, [(new["id"], None)])  # no-op via _EXCLUDED_TABLES
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**self._insert_row(seq, new["id"], new)))
            over = (
                conn.execute(select(func.count()).select_from(self._table)).scalar_one()
                - MAX_NOTIFICATION_LOG_ROWS
            )
            if over > 0:
                stale = [
                    r.id
                    for r in conn.execute(
                        select(self._table.c.id).order_by(self._table.c.seq).limit(over)
                    )
                ]
                conn.execute(sa_delete(self._table).where(self._table.c.id.in_(stale)))
        return new

    def mark_read(self, signal_id: str) -> dict | None:
        return self.update_by_id(signal_id, {"read": True})

    def mark_all_read(self) -> None:
        """Mark every notification read, in ONE transaction (mirrors the removed
        ``_write_lock``-held read-modify-write cycle exactly — no window where a
        concurrently-appended notification could be missed by reading ids outside the
        lock)."""
        with self._use_conn(None) as conn:
            rows = [dict(r._mapping) for r in conn.execute(select(self._table))]
            unread = [r for r in rows if not r["payload"].get("read")]
            if not unread:
                return
            self._fire_hook(conn, [(r["id"], r) for r in unread])
            for r in unread:
                conn.execute(
                    sa_update(self._table)
                    .where(self._table.c.id == r["id"])
                    .values(payload={**r["payload"], "read": True})
                )

    def clear(self) -> None:
        """Drop all notifications."""
        self.replace_all([])


def _make_signal(data: dict) -> dict:
    raw_sources = data.get("sources") or []
    source_urls = data.get("source_urls") or [
        s.get("url", "") for s in raw_sources if isinstance(s, dict)
    ]
    sources = raw_sources if raw_sources else [{"title": "", "url": u} for u in source_urls if u]
    return {
        "id": new_id("sig"),
        "created_at": datetime.now().isoformat(),
        "type": data.get("type", "news"),
        "severity": data.get("severity", "info"),
        "title": data.get("title", ""),
        "summary": data.get("summary", ""),
        "portfolio_impact": data.get("portfolio_impact", ""),
        "action_items": data.get("action_items", []),
        "affected_tickers": data.get("affected_tickers", []),
        "sources": sources,
        "source_urls": [s.get("url", "") for s in sources if s.get("url")],
        "read": False,
        "trigger": data.get("trigger", "scheduled"),
    }
