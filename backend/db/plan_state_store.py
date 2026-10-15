"""PlanStateStore — server-side persistence for Outlook saved scenarios and the FIRE baseline.

Row shape (table ``plan_state``, backend/db/tables.py): ONE row per book, keyed
``book_id = DEFAULT_BOOK_ID`` — a true per-book SINGLETON (never more than one row exists,
unlike every other planning store's flat list). ``payload`` is the whole
``{"scenarios": [...], "baseline": {...} | None, "budget"?: {"home_engine_id": ...}}`` dict,
stored verbatim. FIRE projection inputs are NOT ledger money (never summed into net worth),
so amount fields stay raw inside ``payload`` — no decimal-Text promotion, unlike
``budget_assignments``.

Scenarios are the user's "save & compare" assumption sets from the Outlook tab.
The baseline is the "My FIRE plan" lever override (null = fall back to FIRE profile settings).
``budget`` is the pluggable-budget-engines home-lens pointer (RFC
``2026-08-22-pluggable-budget-engines.md``, Decision E, Phase 2, ``set_home_engine_id``
below) — OMITTED, not defaulted to ``{}``, on a book that has never switched its home
lens, so the pre-Phase-2 ``{scenarios, baseline}`` shape is byte-identical until a write
actually adds the key.

Field keys are stored in camelCase verbatim (saving, ret, vol, monthlyRetirementSpending, swr, lifeExp,
retireAge, atNumber, streams, events, crash, weakSeq, lostDec, highInfl, shockCfg, …) —
the client reads/writes them without any transformation.

PLAN 4A TASK 4: DB-backed (Core). Hand-rolled, not a ``BookPayloadTableStore`` subclass — this
table has no ``id``/``seq`` column at all (the PRIMARY KEY IS ``book_id``; a keyed singleton,
not a flat list of independently-addressable rows). The ``conn=``/``_lock``/
``fire_db_write_hook`` plumbing (``_eng``/``_use_conn``) is copied VERBATIM from
``db/account_store.py`` — same contract, same lazy-engine-resolution reasoning (this store is
ALSO constructed as a long-lived MODULE singleton — ``services.plan_state._store`` — at import
time). ``_stamp_event_ids``/``_ensure_state``/``_empty_state``/``MAX_SCENARIOS`` and every
per-method VALIDATION/SHAPE rule are UNCHANGED from the JsonStore era — ONLY the persistence
layer changed (read one row / write one row instead of ``_load_raw``/``_save_raw``).

Every write reads-modifies-writes the single row inside ONE locked transaction (``_use_conn``),
mirroring the old ``_write_lock``-held read-modify-write exactly — the same protection against
two concurrent scenario/baseline edits clobbering each other. Writes fire
``core.io.fire_db_write_hook`` with the before-image (the whole prior ``{scenarios, baseline}``
row, or ``None`` for the first-ever write), so a scenario save or baseline edit is
checkpoint-covered and a Lucius turn that touches Outlook state is undoable (``plan_state`` is
NOT in ``ai.checkpoint._EXCLUDED_TABLES``).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.errors import ValidationError
from backend.core.ids import new_id
from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import DEFAULT_BOOK_ID, plan_state

MAX_SCENARIOS = 100


def _empty_state() -> dict:
    return {"scenarios": [], "baseline": None}


def _stamp_event_ids(obj: dict | None) -> dict | None:
    """Mint an id for every life event that lacks one, before it is persisted.

    A life event's ``id`` is a SERVER-ASSIGNED handle (like a scenario's, ADR 0011) — callers
    never supply it: the AI tool schema has no ``id`` property, and the UI's own creates only
    got one by accident of client-side minting. The client keys delete/drag/edit on it, and an
    id-less event matches every OTHER id-less event (``x.id !== undefined`` is false for each),
    so a pair of them deleted and dragged as one — live incident 2026-07-18.

    Stamped HERE rather than in the service so identity is guaranteed at the point bytes hit
    disk: no present or future write path can persist an event without one.
    """
    if not isinstance(obj, dict) or not isinstance(obj.get("events"), list):
        return obj
    events = [
        {**ev, "id": new_id("evt")} if isinstance(ev, dict) and not ev.get("id") else ev
        for ev in obj["events"]
    ]
    return {**obj, "events": events}


def _ensure_state(raw) -> dict:
    if not isinstance(raw, dict):
        return _empty_state()
    state = {
        "scenarios": raw["scenarios"] if isinstance(raw.get("scenarios"), list) else [],
        "baseline": raw.get("baseline"),
    }
    # `budget` (pluggable-budget-engines, RFC 2026-08-22-pluggable-budget-engines.md,
    # Decision E/Phase 2: `{"home_engine_id": ...}`) is carried through UNTOUCHED when
    # present — this store still knows nothing about its shape (that validation is
    # services.budget_engines.base's job), it just must not silently DROP a key some
    # other write already landed. Omitted (not defaulted to `{}`) when absent, so a book
    # that has never set a home engine keeps the exact `{scenarios, baseline}` shape every
    # existing test (tests/db/test_plan_state_store.py) already pins.
    if isinstance(raw.get("budget"), dict):
        state["budget"] = raw["budget"]
    return state


class PlanStateStore:
    """Persists Outlook saved scenarios and the FIRE baseline override.

    One row per book (PK ``book_id``) — see module docstring for the full shape/contract.
    """

    def __init__(self, engine: Engine | None = None):
        self._table = plan_state
        self._engine = engine
        self._lock = _lock_for_table(plan_state.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently. The
        DEFAULT (``engine=None``) is never cached here — resolved fresh via ``get_engine()``
        on every call. Mirrors ``AccountStore._eng()`` verbatim: this store is used as a
        long-lived MODULE singleton (``services.plan_state._store``), constructed once at
        import time and shared for the process's whole lifetime — caching the lazily-resolved
        default would pin it to whichever engine happened to be live the FIRST time it was
        ever used, silently ignoring every later ``db_isolated``-style redirect."""
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

    def _read_state(self, conn: Connection) -> dict:
        row = conn.execute(
            select(self._table).where(self._table.c.book_id == DEFAULT_BOOK_ID)
        ).first()
        return _ensure_state(row.payload) if row else _empty_state()

    def _persist(self, conn: Connection, state: dict) -> None:
        existing = conn.execute(
            select(self._table).where(self._table.c.book_id == DEFAULT_BOOK_ID)
        ).first()
        fire_db_write_hook(
            self._table.name,
            [(DEFAULT_BOOK_ID, dict(existing._mapping) if existing else None)],
            conn=conn,
        )
        if existing:
            conn.execute(
                sa_update(self._table)
                .where(self._table.c.book_id == DEFAULT_BOOK_ID)
                .values(payload=state)
            )
        else:
            conn.execute(sa_insert(self._table).values(book_id=DEFAULT_BOOK_ID, payload=state))

    def _load_state(self) -> dict:
        with self._eng().connect() as conn:
            return self._read_state(conn)

    # ── Scenarios ─────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        return self._load_state()

    def list_scenarios(self) -> list[dict]:
        return self._load_state()["scenarios"]

    def add_scenario(self, obj: dict, *, conn: Connection | None = None) -> dict:
        """Assign a server-side id, append, persist. Raises ValidationError when cap is reached."""
        with self._use_conn(conn) as conn:
            state = self._read_state(conn)
            if len(state["scenarios"]) >= MAX_SCENARIOS:
                raise ValidationError(
                    f"Scenario limit reached ({MAX_SCENARIOS}). Delete one before adding more."
                )
            scenario = {**_stamp_event_ids(obj), "id": new_id("scen")}
            state["scenarios"].append(scenario)
            self._persist(conn, state)
        return scenario

    def update_scenario(
        self, scenario_id: str, obj: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Replace a scenario in place. Returns the updated object or None if not found."""
        with self._use_conn(conn) as conn:
            state = self._read_state(conn)
            for i, s in enumerate(state["scenarios"]):
                if s.get("id") == scenario_id:
                    updated = {**_stamp_event_ids(obj), "id": scenario_id}
                    state["scenarios"][i] = updated
                    self._persist(conn, state)
                    return updated
        return None

    def delete_scenario(self, scenario_id: str, *, conn: Connection | None = None) -> bool:
        with self._use_conn(conn) as conn:
            state = self._read_state(conn)
            before = len(state["scenarios"])
            state["scenarios"] = [s for s in state["scenarios"] if s.get("id") != scenario_id]
            if len(state["scenarios"]) < before:
                self._persist(conn, state)
                return True
        return False

    # ── Baseline ──────────────────────────────────────────────────────────────

    def get_baseline(self) -> dict | None:
        return self._load_state()["baseline"]

    def set_baseline(self, obj: dict | None, *, conn: Connection | None = None) -> dict | None:
        with self._use_conn(conn) as conn:
            state = self._read_state(conn)
            stamped = _stamp_event_ids(obj)
            state["baseline"] = stamped
            self._persist(conn, state)
        return stamped

    # ── Budget-engine home lens (pluggable-budget-engines, RFC
    #    2026-08-22-pluggable-budget-engines.md, Decision E, Phase 2) ────────────────

    def set_home_engine_id(self, engine_id: str, *, conn: Connection | None = None) -> dict:
        """Set ``payload["budget"]["home_engine_id"]`` — the book's home budget-paradigm
        lens. Composes into the SAME locked read-modify-write as every other field on
        this singleton row, so a home-lens switch is checkpoint-covered and
        ``/rewind``-able exactly like a scenario or baseline edit (``plan_state`` is NOT
        in ``ai.checkpoint._EXCLUDED_TABLES``).

        Touches ONLY this one key (Decision A: switching home never touches another
        engine's own state — ``budget_engine_state`` rows, ``budget_assignments``, ...
        are never read or written by this method). Engine-id VALIDATION against the
        real ``ENGINES`` registry is the CALLER's job
        (``services.budget_engines.base.set_home_engine_id``) — a store is data access
        only, per CLAUDE.md."""
        with self._use_conn(conn) as conn:
            state = self._read_state(conn)
            state["budget"] = {**(state.get("budget") or {}), "home_engine_id": engine_id}
            self._persist(conn, state)
        return dict(state["budget"])

    # ── Fresh-start undo snapshot (one-shot, server-held) ─────────────────────

    def get_fresh_start_undo(self) -> dict | None:
        """The pending fresh-start before-image, or None. See ``set_fresh_start_undo``."""
        return (self._load_state().get("budget") or {}).get("fresh_start_undo")

    def set_fresh_start_undo(
        self, snapshot: dict | None, *, conn: Connection | None = None
    ) -> None:
        """Hold (or clear, with ``None``) the ONE pending fresh-start undo snapshot at
        ``payload["budget"]["fresh_start_undo"]`` — ``{token, month, rollover_start,
        assignments, created_at}``.

        It lives HERE, on the same singleton row and inside the same locked read-modify-write as
        ``set_home_engine_id``, for one reason: the undo must replay a before-image the SERVER
        wrote, never a map the client hands back. A process-local dict would not survive a
        restart mid-undo-window; a client round trip made the undo route an unauthenticated bulk
        assign that could smuggle negative amounts past ``_op_assign``'s guard (found in
        pre-merge review). Composing it into the fresh-start transaction means the snapshot and
        the writes it describes commit together or not at all.

        A store is data access only — token minting/matching and one-shot expiry are the
        caller's job (``services.planning.fresh_start``/``undo_fresh_start``)."""
        with self._use_conn(conn) as conn:
            state = self._read_state(conn)
            budget = {**(state.get("budget") or {})}
            if snapshot is None:
                budget.pop("fresh_start_undo", None)
            else:
                budget["fresh_start_undo"] = snapshot
            state["budget"] = budget
            self._persist(conn, state)

    # ── Envelope-delete undo snapshot (one-shot, server-held) ─────────────────

    def get_envelope_delete_undo(self) -> dict | None:
        """The pending envelope-delete before-image, or None. See ``set_envelope_delete_undo``."""
        return (self._load_state().get("budget") or {}).get("envelope_delete_undo")

    def set_envelope_delete_undo(
        self, snapshot: dict | None, *, conn: Connection | None = None
    ) -> None:
        """Hold (or clear, with ``None``) the ONE pending envelope-delete undo snapshot at
        ``payload["budget"]["envelope_delete_undo"]`` — mirrors ``set_fresh_start_undo`` exactly
        (same singleton row, same one-pending-at-a-time contract, same reason: the undo must
        replay a before-image the SERVER wrote, never one a client hands back). See
        ``services.envelopes.delete_envelope``/``undo_delete_envelope`` for the shape."""
        with self._use_conn(conn) as conn:
            state = self._read_state(conn)
            budget = {**(state.get("budget") or {})}
            if snapshot is None:
                budget.pop("envelope_delete_undo", None)
            else:
                budget["envelope_delete_undo"] = snapshot
            state["budget"] = budget
            self._persist(conn, state)
