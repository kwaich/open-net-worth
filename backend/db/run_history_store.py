"""
RunHistoryStore — the append-only run-history store for agents and scheduled tasks.

A capped ring (newest first, bounded to MAX_RUNS_PER_ENTITY per entity). It is **runtime /
behavioral output state → checkpoint-EXCLUDED** (mirrors ``agent_findings.json`` /
``signals.json`` — reverting a run log would erase a failure record the user is relying on;
the run is re-derived on the next execution).

The store is shared by agents AND tasks. Records are keyed by ``(kind, entity_id)``
where ``kind`` is ``"agent"`` or ``"task"`` (also ``"system_job"`` — a built-in
scheduler job with no store-backed entity of its own, e.g. the intel scan). Streak-dedup
companion records live in the same table with ``kind="__streak__"`` and a deterministic id
so they are upserted, not appended (never counted against the cap).

Run record schema:
    id            : str        uuid4 hex
    kind          : str        "agent" | "task" | "system_job"
    entity_id     : str        agent_id or task_id
    entity_name   : str        denormalised name/title (no second lookup at read time)
    started_at    : str ISO
    finished_at   : str ISO
    duration_ms   : int|None   finished-started in ms; None if unparseable
    status        : str        "ok" | "error" | "skipped"
    error         : str|None   FULL unredacted error text (local-only, auth-gated)
    summary       : str        one-line _summarize() of output
    trigger       : str        "manual" | "dispatch" | "schedule" | "schedule:<task_id>"
    finding_id    : str|None   agent runs only — deep-link to the finding surface

Streak companion schema (kind="__streak__", id=f"__streak__:{kind}:{entity_id}"):
    failing            : bool
    fail_count         : int
    first_error_at     : str|None   ISO of streak start (systemic-window grouping)
    last_error_redacted: str|None   redacted root-cause (systemic match)
    updated_at         : str ISO

PLAN 4C TASK 1: DB-backed (Tier-2 logs, table ``run_history``) via ``PayloadTableStore``.
``for_entity`` reads via an explicit ``ORDER BY seq DESC`` (Design Note 2, plan 4c) — the
ORIGINAL list-prepend order; streak rows ride the base ``upsert``, unchanged shape.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import run_history

MAX_RUNS_PER_ENTITY = 50
VALID_STATUSES = {"ok", "error", "skipped"}
_STREAK_KIND = "__streak__"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started_at: str, finished_at: str) -> int | None:
    try:
        s = datetime.fromisoformat(started_at)
        f = datetime.fromisoformat(finished_at)
        return max(0, int((f - s).total_seconds() * 1000))
    except Exception:
        return None


def _make_run(data: dict) -> dict:
    finished_at = data.get("finished_at") or _now()
    started_at = data.get("started_at") or finished_at
    return {
        "id": data.get("id") or new_id("run"),
        "kind": data.get("kind", "agent"),
        "entity_id": data.get("entity_id", ""),
        "entity_name": data.get("entity_name", ""),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": data.get("duration_ms")
        if data.get("duration_ms") is not None
        else _duration_ms(started_at, finished_at),
        "status": data.get("status") if data.get("status") in VALID_STATUSES else "ok",
        "error": data.get("error"),
        "summary": data.get("summary", ""),
        "trigger": data.get("trigger", "manual"),
        "finding_id": data.get("finding_id"),
        "steps": data.get("steps") or [],
        "cost_usd": data.get("cost_usd"),
    }


def _streak_id(kind: str, entity_id: str) -> str:
    return f"{_STREAK_KIND}:{kind}:{entity_id}"


def _make_streak(kind: str, entity_id: str, patch: dict) -> dict:
    base = {
        "id": _streak_id(kind, entity_id),
        "kind": _STREAK_KIND,
        "entity_id": entity_id,
        "entity_name": None,  # denormalised at write time by run_ledger
        "failing": False,
        "fail_count": 0,
        "first_error_at": None,
        "last_error_redacted": None,
        "updated_at": _now(),
    }
    base.update(patch)
    base["updated_at"] = _now()
    return base


class RunHistoryStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(run_history, id_field="id", engine=engine)

    # ── Domain queries ────────────────────────────────────────────────────────

    def for_entity(self, kind: str, entity_id: str, limit: int = 50) -> list[dict]:
        """Run records for one entity, newest-first. Excludes __streak__ companions."""
        with self._eng().connect() as conn:
            rows = conn.execute(select(self._table).order_by(self._table.c.seq.desc()))
            runs = [
                r.payload
                for r in rows
                if r.payload.get("kind") == kind and r.payload.get("entity_id") == entity_id
            ]
        return runs[: max(0, limit)]

    def streak_state(self, kind: str, entity_id: str) -> dict | None:
        """The streak companion record for an entity, or None if not set."""
        return self.find_by_id(_streak_id(kind, entity_id))

    def set_streak_state(self, kind: str, entity_id: str, patch: dict) -> dict:
        """Upsert the streak companion record (deterministic id, never capped)."""
        sid = _streak_id(kind, entity_id)
        existing = self.find_by_id(sid)
        if existing:
            updates = {**existing, **patch, "updated_at": _now()}
            return self.update_by_id(sid, updates) or updates
        return self.upsert(_make_streak(kind, entity_id, patch))

    def recent_failing(self, within_iso: str) -> list[dict]:
        """Streak records with failing=True whose first_error_at >= within_iso."""
        return [
            r
            for r in self.all()
            if r.get("kind") == _STREAK_KIND
            and r.get("failing") is True
            and (r.get("first_error_at") or "") >= within_iso
        ]

    # ── Writes ────────────────────────────────────────────────────────────────

    def append(self, data: dict) -> dict:
        """Append a new run record, capping to MAX_RUNS_PER_ENTITY for this entity.

        Streak companion records (kind=__streak__) are never touched by this path —
        use set_streak_state() for those. Only this entity's run rows are trimmed;
        all other entities' rows and all streak records are left untouched.
        """
        new = _make_run(data)
        kind, entity_id = new["kind"], new["entity_id"]
        with self._use_conn(None) as conn:
            self._fire_hook(conn, [(new["id"], None)])
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**self._insert_row(seq, new["id"], new)))
            # Per-entity cap ONLY (kind, entity_id) — streak rows (kind="__streak__") live in a
            # different entity_id-space entirely and are never touched by this trim.
            rows = [
                r
                for r in conn.execute(
                    select(self._table.c.id, self._table.c.seq, self._table.c.payload)
                )
                if r.payload.get("kind") == kind and r.payload.get("entity_id") == entity_id
            ]
            if len(rows) > MAX_RUNS_PER_ENTITY:
                stale = sorted(rows, key=lambda r: r.seq)[: len(rows) - MAX_RUNS_PER_ENTITY]
                conn.execute(
                    sa_delete(self._table).where(self._table.c.id.in_([r.id for r in stale]))
                )
        return new
