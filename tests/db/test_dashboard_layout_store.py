"""DashboardLayoutStore — DB-backed per-book singleton (plan 4d, task 2), mirrors
PlanStateStore's own test shape: singleton round trip, view/widget id stamping, and the
MAX_VIEWS/MAX_WIDGETS_PER_VIEW caps still enforced on save.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.db.dashboard_layout_store import (
    MAX_VIEWS,
    MAX_WIDGETS_PER_VIEW,
    DashboardLayoutStore,
)
from backend.db.tables import DEFAULT_BOOK_ID, dashboard_layouts


def _raw_rows(engine):
    with engine.connect() as conn:
        return list(conn.execute(select(dashboard_layouts)))


# ── Singleton round trip (the brief's own named test) ───────────────────────────────────────


def test_dashboard_layout_singleton_round_trip(db_engine):
    store = DashboardLayoutStore(engine=db_engine)
    assert store.get_state() == {"activeViewId": None, "views": []}

    views = [{"name": "Home", "widgets": [{"defId": "movers", "x": 0, "y": 0, "w": 2, "h": 2}]}]
    saved = store.save_state(None, views)
    assert saved["views"][0]["viewId"].startswith("view_")
    assert saved["views"][0]["widgets"][0]["instanceId"].startswith("widg_")
    assert saved["activeViewId"] == saved["views"][0]["viewId"]  # defaults to first view

    assert store.get_state() == saved


def test_row_is_a_true_per_book_singleton(db_engine):
    store = DashboardLayoutStore(engine=db_engine)
    store.save_state(None, [{"name": "A", "widgets": []}])
    store.save_state(None, [{"name": "B", "widgets": []}])
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert rows[0].book_id == DEFAULT_BOOK_ID
    assert rows[0].payload["views"][0]["name"] == "B"


def test_active_view_id_falls_back_when_not_among_views(db_engine):
    store = DashboardLayoutStore(engine=db_engine)
    views = [{"name": "Home", "widgets": []}]
    saved = store.save_state("view_does_not_exist", views)
    assert saved["activeViewId"] == saved["views"][0]["viewId"]


def test_client_supplied_ids_are_trusted_not_reminted(db_engine):
    store = DashboardLayoutStore(engine=db_engine)
    views = [
        {
            "viewId": "view_client_supplied",
            "name": "Home",
            "widgets": [{"instanceId": "widg_client", "defId": "movers", "x": 0, "y": 0}],
        }
    ]
    saved = store.save_state("view_client_supplied", views)
    assert saved["views"][0]["viewId"] == "view_client_supplied"
    assert saved["views"][0]["widgets"][0]["instanceId"] == "widg_client"


def test_max_views_and_max_widgets_per_view_caps_enforced(db_engine):
    store = DashboardLayoutStore(engine=db_engine)
    many_views = [{"name": f"v{i}", "widgets": []} for i in range(MAX_VIEWS + 10)]
    saved = store.save_state(None, many_views)
    assert len(saved["views"]) == MAX_VIEWS

    many_widgets = [{"defId": "movers", "x": 0, "y": i} for i in range(MAX_WIDGETS_PER_VIEW + 10)]
    saved2 = store.save_state(None, [{"name": "crowded", "widgets": many_widgets}])
    assert len(saved2["views"][0]["widgets"]) == MAX_WIDGETS_PER_VIEW
