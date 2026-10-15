"""
CorrectionStore — category-correction signal log (P2-SP2).

Records a lightweight event every time the user re-categorizes a transaction at
the single edit choke point (``api/ledger.update_transaction``), so the detector
(``services/corrections.py``) can spot a repeated correction (e.g. "STARBUCKS"
moved to ``food`` three times) and *propose* a categorization rule the user can
accept with one click. This store is **dumb CRUD only** — no grouping / threshold
logic here (that lives in the service).

This is a **runtime/behavioral signal, NOT user-authored data**, so the table is added to
the checkpoint EXCLUSION list (``ai.checkpoint._EXCLUDED_TABLES``, mirroring ``txn_audit``):
a Lucius restore must not resurrect a stale correction history. This replaces the old
file-level exclusion (``ai/checkpoint._EXCLUDED_PATHS``'s ``CORRECTIONS_JSON`` entry, kept
for the legacy file). Append-only, capped (drop oldest) like ``NotificationLogStore``.

PLAN 4A TASK 2: DB-backed (Tier-1 planning, table ``corrections`` — backend/db/tables.py)
via ``BookPayloadTableStore``. Two item shapes share the table, distinguished by ``type``:

  Correction event (``type`` absent / "correction"):
      id            : str   prefixed id ("corr_…", core.ids.new_id)
      payee_key     : str   normalized payee (services.corrections._payee_key)
      payee_sample  : str   the raw description, for display ("STARBUCKS @ ION")
      payee_id      : str | None  resolved payee entity (ADR 0010); None on legacy/unresolved
      txn_id        : str
      from_category : str | None
      to_category   : str   the user's chosen category — the learning target
      kind          : str   "expense" | "income"
      created_at    : str   ISO 8601

  Dismissal (``type`` == "dismissal"):
      id            : str   prefixed id ("dis_…", core.ids.new_id)
      type          : "dismissal"
      payee_key     : str
      target_category : str
      dismissed_at  : str   ISO 8601

A dismissal permanently suppresses the ``(payee_key → target_category)``
suggestion.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import corrections

MAX_CORRECTIONS = 1000  # total items retained (events + dismissals); oldest dropped


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CorrectionStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(corrections, id_field="id", engine=engine)

    def _append_capped(self, row: dict) -> dict:
        """Insert ``row`` then drop the oldest (lowest-seq) rows past MAX_CORRECTIONS, all
        inside ONE base transaction — the read-modify-write cycle the removed
        _write_lock/_load_items/_save_items plumbing used to provide."""
        with self._use_conn(None) as conn:  # base lock + one transaction
            self._fire_hook(conn, [(row["id"], None)])  # no-op via _EXCLUDED_TABLES
            seq = self._next_seq(conn)
            # _insert_row injects book_id=DEFAULT_BOOK_ID (BookPayloadTableStore) — never
            # hand-build the values dict here, or the book_id column NOT NULL constraint fails.
            conn.execute(sa_insert(self._table).values(**self._insert_row(seq, row["id"], row)))
            over = (
                conn.execute(select(func.count()).select_from(self._table)).scalar_one()
                - MAX_CORRECTIONS
            )
            if over > 0:
                stale = [
                    r.id
                    for r in conn.execute(
                        select(self._table.c.id).order_by(self._table.c.seq).limit(over)
                    )
                ]
                conn.execute(sa_delete(self._table).where(self._table.c.id.in_(stale)))
        return row

    # ── Writes ────────────────────────────────────────────────────────────────

    def record(self, event: dict) -> dict:
        """Append a correction event (with a prefixed id + created_at), capping the table
        at MAX_CORRECTIONS by dropping the oldest rows (by seq). Returns the stored event."""
        stored = {
            "id": new_id("corr"),
            "payee_key": event.get("payee_key", ""),
            "payee_sample": event.get("payee_sample", ""),
            "payee_id": event.get(
                "payee_id"
            ),  # resolved entity (ADR 0010); None on legacy/unresolved
            "txn_id": event.get("txn_id", ""),
            "from_category": event.get("from_category"),
            "to_category": event.get("to_category", ""),
            "kind": event.get("kind", "expense"),
            "created_at": event.get("created_at") or _now(),
        }
        return self._append_capped(stored)

    def add_dismissal(self, payee_key: str, target_category: str) -> dict:
        """Persist a dismissal so the ``(payee_key → target_category)`` suggestion
        never re-surfaces. Idempotent: a duplicate dismissal is not re-added."""
        for it in self.query(type="dismissal"):
            if it.get("payee_key") == payee_key and it.get("target_category") == target_category:
                return it
        stored = {
            "id": new_id("dis"),
            "type": "dismissal",
            "payee_key": payee_key,
            "target_category": target_category,
            "dismissed_at": _now(),
        }
        return self._append_capped(stored)

    # ── Queries ───────────────────────────────────────────────────────────────

    def events(self) -> list[dict]:
        """All correction events (excludes dismissals)."""
        return [it for it in self.all() if it.get("type") != "dismissal"]

    def recent(self, since_iso: str) -> list[dict]:
        """Correction events with ``created_at`` >= ``since_iso`` (string compare,
        ISO-8601 sorts lexicographically). Excludes dismissals."""
        return [e for e in self.events() if e.get("created_at", "") >= since_iso]

    def dismissals(self) -> list[dict]:
        """All dismissal records."""
        return [it for it in self.all() if it.get("type") == "dismissal"]
