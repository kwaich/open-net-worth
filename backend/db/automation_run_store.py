"""Mutable, compare-and-set Automation run records.

PLAN 4C TASK 1: DB-backed (Tier-2 logs, table ``automation_runs``) via ``PayloadTableStore``.
The ONE table in this wave that stays checkpoint-INCLUDED (``ai/checkpoint._EXCLUDED_TABLES``
deliberately does NOT list it) — already portable/captured today ("durable run history"),
behavior-preserved. ``patch``/``transition`` are the CAS-gated primitives (Design Note 3, plan
4c): read the row, decide, write — atomically, under the row's own lock, via
``PayloadTableStore``'s ``_use_conn``/``_row``/``_fire_hook`` seam.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import automation_runs

VALID_TRANSITIONS = {
    # ``cancelled`` is the user's own Stop. Only ``running`` reaches it: a ``waiting`` run
    # has no task left to cancel, so stopping one is a no-op, not a transition.
    "running": {"waiting", "completed", "failed", "cancelled"},
    "waiting": {"running", "completed", "failed"},
    # A durable Automation conversation remains reopenable after its first turn. A user
    # follow-up re-enters ``running`` on the same run row, then settles again.
    "completed": {"running"},
    "failed": {"running"},
    "cancelled": {"running"},
}
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_PATCH_FIELDS = frozenset(
    {
        "session_id",
        "trigger",
        "execution",
        "model",
        "effort",
        "summary",
        "error",
        "finished_at",
        "steps",
        "plan",
        "proposed_changes",
        "pending_tool_ids",
        "context",
        "files",
        "working_dir",
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "cost_usd",
        "stats",
    }
)


class TransitionConflict(RuntimeError):
    """A run changed state before the caller's compare-and-set could commit."""

    def __init__(self, run_id: str):
        super().__init__(f"automation run transition conflict for {run_id}")
        self.run_id = run_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy(value: object, default: object) -> object:
    if isinstance(default, dict):
        return dict(value) if isinstance(value, dict) else {}
    if isinstance(default, list):
        return list(value) if isinstance(value, list) else []
    return value if value is not None else default


def _make_run(data: dict) -> dict:
    started_at = data.get("started_at") or _now()
    return {
        "id": data.get("id") or new_id("run"),
        "automation_id": str(data.get("automation_id") or ""),
        "session_id": data.get("session_id"),
        "status": "running",
        "trigger": _copy(data.get("trigger"), {}),
        "execution": _copy(data.get("execution"), {}),
        "model": str(data.get("model") or ""),
        "effort": str(data.get("effort") or "medium") or "medium",
        "started_at": started_at,
        "finished_at": None,
        "summary": str(data.get("summary") or ""),
        "error": data.get("error"),
        "steps": _copy(data.get("steps"), []),
        "proposed_changes": _copy(data.get("proposed_changes"), []),
        "pending_tool_ids": _copy(data.get("pending_tool_ids"), []),
        "context": _copy(data.get("context"), {}),
        "files": _copy(data.get("files"), []),
        "working_dir": data.get("working_dir"),
        "input_tokens": data.get("input_tokens"),
        "output_tokens": data.get("output_tokens"),
        "cache_creation_tokens": data.get("cache_creation_tokens"),
        "cache_read_tokens": data.get("cache_read_tokens"),
        "cost_usd": data.get("cost_usd"),
        "stats": _copy(data.get("stats"), {}),
    }


class AutomationRunStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(automation_runs, id_field="id", engine=engine)

    def start(self, data: dict) -> dict:
        return self.insert(_make_run(data))

    def patch(self, run_id: str, expected: set[str], patch: dict) -> dict:
        """Atomically persist progress without changing lifecycle state."""
        with self._use_conn(None) as conn:
            existing = self._row(conn, run_id)
            if existing is None or existing["payload"].get("status") not in expected:
                raise TransitionConflict(run_id)
            updates = {k: v for k, v in patch.items() if k in _PATCH_FIELDS}
            merged = {**existing["payload"], **updates}
            self._fire_hook(conn, [(run_id, existing)])
            conn.execute(
                sa_update(self._table).where(self._table.c.id == run_id).values(payload=merged)
            )
        return merged

    def transition(
        self, run_id: str, expected: set[str], status: str, patch: dict
    ) -> tuple[dict, list[str]]:
        """Atomically transition a run without pruning its durable conversation owner."""
        with self._use_conn(None) as conn:
            existing = self._row(conn, run_id)
            if existing is None:
                raise TransitionConflict(run_id)
            current = existing["payload"]
            old_status = current.get("status")
            if (
                old_status not in expected
                or old_status not in VALID_TRANSITIONS
                or status not in VALID_TRANSITIONS[old_status]
            ):
                raise TransitionConflict(run_id)
            updates = {k: v for k, v in patch.items() if k in _PATCH_FIELDS}
            updated = {**current, **updates, "status": status}
            if status in _TERMINAL_STATUSES and not updated.get("finished_at"):
                updated["finished_at"] = _now()
            self._fire_hook(conn, [(run_id, existing)])
            conn.execute(
                sa_update(self._table).where(self._table.c.id == run_id).values(payload=updated)
            )
        return dict(updated), []

    def find_by_session_id(self, session_id: str) -> dict | None:
        if not session_id:
            return None
        rows = self.query(session_id=session_id)
        return rows[-1] if rows else None  # base query() is ascending — last = most recent

    def count_for_automation(self, automation_id: str) -> int:
        """How many runs this automation has — a SQL COUNT, not a materialised scan.

        ``GET /api/automations/{id}/runs`` reports ``total`` beside its page, and the
        Automations tab polls that endpoint ONCE A SECOND while a run is live. Computing the
        total as ``sum(1 for row in store.all() ...)`` deserialised every run payload in the
        table (262 on the owner's book) to produce one integer, every tick.

        ``payload["automation_id"].as_string()`` renders as ``json_extract`` on SQLite and
        ``->>`` on Postgres, so this stays correct on both engines.
        """
        with self._eng().connect() as conn:
            return int(
                conn.execute(
                    select(func.count())
                    .select_from(self._table)
                    .where(self._table.c.payload["automation_id"].as_string() == automation_id)
                ).scalar_one()
            )

    def outcomes(self) -> list[dict]:
        """Every run's outcome fields, oldest first (insertion order) — only ``id``,
        ``automation_id``, ``session_id``, ``status``, ``started_at``, ``finished_at`` and
        ``error``, never the steps/plan/context bulk ``all()`` would deserialise. The cheap
        read behind "which runs failed": the conversation list's run status and the Lucius
        home's Needs attention."""
        payload = self._table.c.payload
        fields = ("id", "automation_id", "session_id", "status", "started_at", "finished_at")
        columns = [payload[f].as_string().label(f) for f in (*fields, "error")]
        with self._eng().connect() as conn:
            rows = conn.execute(select(*columns).order_by(self._table.c.seq)).mappings().all()
        return [dict(row) for row in rows]

    def latest_completed(self, automation_id: str, *, before: str, exclude_id: str) -> dict | None:
        """The newest COMPLETED run of ``automation_id`` that started before ``before`` (an
        ISO timestamp; empty for no bound), other than ``exclude_id``."""
        for row in reversed(self.query(automation_id=automation_id)):  # ascending → newest first
            if row.get("id") == exclude_id or row.get("status") != "completed":
                continue
            if before and str(row.get("started_at") or "") >= before:
                continue
            return row
        return None

    def for_automation(self, automation_id: str, limit: int, offset: int) -> list[dict]:
        rows = self.query(automation_id=automation_id)
        rows.reverse()  # UNCHANGED — already correct against ascending order (Design Note 2)
        start = max(0, offset)
        return rows[start : start + max(0, limit)]

    def list_by_statuses(self, statuses: list[str], limit: int) -> list[dict]:
        """Runs across EVERY automation whose status is one of ``statuses``, most-recently
        STARTED first — the source for the Lucius landing screen's Activity › Active
        section (running/waiting runs, no ``automation_id`` scope). Filtered post-``all()``
        in Python rather than a JSON-path ``WHERE`` (the module docstring's dialect rule):
        this is a capped, occasional landing-screen read, not a once-a-second poll loop like
        ``for_automation``/``count_for_automation``, so it doesn't earn its own indexed
        column."""
        wanted = set(statuses)
        rows = [row for row in self.all() if row.get("status") in wanted]
        rows.sort(key=lambda row: row.get("started_at") or "", reverse=True)
        return rows[: max(0, limit)]

    def delete_for_automation(self, automation_id: str) -> list[str]:
        with self._use_conn(None) as conn:
            # order_by(seq.desc()) — matches for_automation's newest-first contract (the
            # ORIGINAL JsonStore version built `removed` via `reversed(rows)` over its
            # oldest-first file order; a bare `select()` here has no defined row order to
            # begin with, so the sort must be explicit, not incidental).
            rows = [
                r
                for r in conn.execute(select(self._table).order_by(self._table.c.seq.desc()))
                if r.payload.get("automation_id") == automation_id
            ]
            if not rows:
                return []
            self._fire_hook(conn, [(r.id, dict(r._mapping)) for r in rows])
            conn.execute(sa_delete(self._table).where(self._table.c.id.in_([r.id for r in rows])))
        return [r.id for r in rows]
