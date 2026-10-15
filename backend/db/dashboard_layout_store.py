"""DashboardLayoutStore — persistence for the Overview widget-dashboard layout.

Row shape: {"activeViewId": str | None, "views": [{"viewId", "name", "widgets": [...]}]}

A "widget" here is either a real widget instance (``defId`` + grid position/size +
``config``) or a full-width section divider (``kind: "section"``). This store is
deliberately dumb: it knows nothing about ``WIDGET_DEFINITIONS``, the grid/compaction
math, or what a widget renders — that is 100% client-side (the handoff's own "keep
the schema minimal... never a derived figure" guidance). It only guarantees every
view and every widget instance carries a server-assigned, collision-proof id.

PLAN 4D TASK 2: DB-backed (Tier-1, per-book SINGLETON, table ``dashboard_layouts`` — PK
``book_id``) — mirrors ``PlanStateStore`` verbatim: ``_eng``/``_use_conn``/``_read_state``/
``_persist`` over ONE row per book, keyed ``book_id=DEFAULT_BOOK_ID``. ``get_state``/
``save_state`` + ``_ensure_state``/``_stamp_view_ids``/``_stamp_widget_ids``/``MAX_VIEWS``/
``MAX_WIDGETS_PER_VIEW`` are UNCHANGED from the JsonStore era — only the persistence layer
changed (read one row / write one row instead of ``_load_raw``/``_save_raw``). Every write
fires ``core.io.fire_db_write_hook`` with the before-image, so a layout edit stays
checkpoint-covered (``dashboard_layouts`` is NOT in ``ai.checkpoint._EXCLUDED_TABLES``).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import DEFAULT_BOOK_ID, dashboard_layouts

MAX_VIEWS = 50
MAX_WIDGETS_PER_VIEW = 200


def _empty_state() -> dict:
    return {"activeViewId": None, "views": []}


def _stamp_widget_ids(widgets: list) -> list:
    """Mint instanceId for any widget item that lacks one — see module docstring.

    A widget instance's identity must never collide (two "Movers" widgets on one
    view are legitimate). Client-generated ids are trusted when present (duplicate
    / drag / resize round-trip the same instanceId) but never required.
    """
    out = []
    for w in widgets:
        if not isinstance(w, dict):
            continue
        if not w.get("instanceId"):
            w = {**w, "instanceId": new_id("widg")}
        out.append(w)
    return out


def _stamp_view_ids(views: list) -> list:
    out = []
    for v in views:
        if not isinstance(v, dict):
            continue
        if not v.get("viewId"):
            v = {**v, "viewId": new_id("view")}
        widgets = v.get("widgets") if isinstance(v.get("widgets"), list) else []
        out.append({**v, "widgets": _stamp_widget_ids(widgets)})
    return out


def _ensure_state(raw) -> dict:
    if not isinstance(raw, dict):
        return _empty_state()
    views = _stamp_view_ids(raw["views"] if isinstance(raw.get("views"), list) else [])
    active = raw.get("activeViewId")
    if active is None and views:
        active = views[0]["viewId"]
    return {"activeViewId": active, "views": views}


class DashboardLayoutStore:
    """Per-book singleton — see module docstring for the full shape/contract."""

    def __init__(self, engine: Engine | None = None):
        self._table = dashboard_layouts
        self._engine = engine
        self._lock = _lock_for_table(dashboard_layouts.name)

    def _eng(self) -> Engine:
        """``engine=`` given at construction (test injection) is honored permanently. The
        DEFAULT (``engine=None``) is never cached here — resolved fresh via ``get_engine()``
        on every call, mirroring ``PlanStateStore._eng`` verbatim: this store is ALSO used as
        a long-lived MODULE singleton (``services.dashboard._store``)."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        """Mirrors ``PlanStateStore._use_conn``/``PayloadTableStore._use_conn`` exactly — see
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

    def get_state(self) -> dict:
        return self._load_state()

    def save_state(self, active_view_id: str | None, views: list) -> dict:
        with self._use_conn(None) as conn:
            stamped_views = _stamp_view_ids(views)[:MAX_VIEWS]
            for v in stamped_views:
                v["widgets"] = v["widgets"][:MAX_WIDGETS_PER_VIEW]
            ids = {v["viewId"] for v in stamped_views}
            active = (
                active_view_id
                if active_view_id in ids
                else (stamped_views[0]["viewId"] if stamped_views else None)
            )
            state = {"activeViewId": active, "views": stamped_views}
            self._persist(conn, state)
            return state
