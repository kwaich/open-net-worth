"""PendingPushStore — durable retry queue for zero-delivery external notify() pushes.

Production incident (2026-07-16): two analyst pushes were silently dropped when the
Telegram bot was mid-reconnect (``notify()`` returned ``{"sent": 0}`` for every
channel) — nothing logged it, nothing retried, the analysis row was marked done. A
zero-delivery push is now queued here (``backend.monitoring.push_retry``) instead of
being swallowed, and retried with bounded backoff by the ~60s analysis-sweep tick.
Durable (a DB table, not an in-memory asyncio timer) — a dev-server hot-reload or any
process restart must not cancel an in-flight retry.

PLAN 4E TASK 1: DB-backed (Tier-2 — table ``pending_pushes``, backend/db/tables.py) via
the generic ``PayloadTableStore`` (seq/id/payload) shape. ``checkpointed=False`` at
construction (a construction-time opt-out of ever firing the write-log hook — the table
is already registered in ``ai.checkpoint._EXCLUDED_TABLES``, which would make the
capture a structural no-op anyway; skipping the call entirely is cheaper than a
guaranteed no-op for a table that writes on every scheduler tick). ``due()`` filters and
sorts in Python over ``self.all()`` — the table is small and bounded by design, a retry
queue, not a ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import pending_pushes


class PendingPushStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(pending_pushes, id_field="id", engine=engine, checkpointed=False)

    def add(
        self,
        message: str,
        channels: list[str],
        label: str,
        ticker: str,
        next_attempt_at: str,
    ) -> dict:
        """Append a new retry row for a push that delivered to zero channels."""
        row: dict = {
            "id": new_id("ppush"),
            "message": message,
            "channels": channels,
            "label": label,
            "ticker": ticker,
            "attempts": 0,
            "next_attempt_at": next_attempt_at,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.insert(row)
        return row

    def due(self, now_iso: str) -> list[dict]:
        """All rows whose next_attempt_at has arrived, earliest first."""
        rows = [r for r in self.all() if (r.get("next_attempt_at") or "") <= now_iso]
        rows.sort(key=lambda r: r.get("next_attempt_at") or "")
        return rows

    def reschedule(self, row_id: str, attempts: int, next_attempt_at: str) -> None:
        self.update_by_id(row_id, {"attempts": attempts, "next_attempt_at": next_attempt_at})

    def remove(self, row_id: str) -> None:
        self.delete(row_id)
