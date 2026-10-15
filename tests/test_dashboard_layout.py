"""
Tests for the Overview dashboard-layout persistence layer: store CRUD, service
validation, and endpoint smoke tests. Isolated via db_isolated (DashboardLayoutStore
is DB-backed) — never touches the live database.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.db.dashboard_layout_store as store_mod
import backend.services.dashboard as svc_mod
from backend.api.dashboard import router as dashboard_router
from backend.core.errors import ValidationError
from backend.db.dashboard_layout_store import DashboardLayoutStore

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(db_isolated):
    return DashboardLayoutStore()


@pytest.fixture()
def svc(db_isolated):
    # services.dashboard._store is a module-level singleton constructed with engine=None,
    # and DashboardLayoutStore._eng() re-resolves get_engine() fresh on every call (never
    # cached) — so db_isolated's engine redirect already reaches it with no swap needed.
    return svc_mod


@pytest.fixture()
def client(db_isolated):
    app = FastAPI()
    app.include_router(dashboard_router)
    return TestClient(app)


def _widget(def_id="hero", **overrides):
    base = {"defId": def_id, "x": 0, "y": 0, "w": 12, "h": 224, "config": {}}
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# 1. Store CRUD tests
# ─────────────────────────────────────────────────────────────────────────────


class TestDashboardLayoutStore:
    def test_empty_on_first_load(self, store):
        assert store.get_state() == {"activeViewId": None, "views": []}

    def test_save_assigns_server_view_id(self, store):
        state = store.save_state(None, [{"name": "Overview", "widgets": [_widget()]}])
        assert len(state["views"]) == 1
        assert state["views"][0]["viewId"].startswith("view_")

    def test_save_assigns_server_widget_instance_id(self, store):
        state = store.save_state(None, [{"name": "Overview", "widgets": [_widget()]}])
        widgets = state["views"][0]["widgets"]
        assert widgets[0]["instanceId"].startswith("widg_")

    def test_duplicate_widget_gets_distinct_instance_id(self, store):
        """Two 'Movers' widgets on one view must not collide on identity."""
        state = store.save_state(
            None, [{"name": "Overview", "widgets": [_widget("movers"), _widget("movers")]}]
        )
        ids = [w["instanceId"] for w in state["views"][0]["widgets"]]
        assert len(set(ids)) == 2

    def test_caller_supplied_instance_id_preserved(self, store):
        state = store.save_state(
            None, [{"name": "Overview", "widgets": [_widget(instanceId="widg-keep")]}]
        )
        assert state["views"][0]["widgets"][0]["instanceId"] == "widg-keep"

    def test_persistence_across_store_reload(self, db_isolated):
        s1 = DashboardLayoutStore()
        s1.save_state(None, [{"name": "Overview", "widgets": [_widget()]}])
        s2 = DashboardLayoutStore()
        state = s2.get_state()
        assert len(state["views"]) == 1
        assert state["views"][0]["name"] == "Overview"

    def test_active_view_defaults_to_first_view(self, store):
        state = store.save_state(
            None,
            [
                {"name": "Overview", "widgets": []},
                {"name": "Reports", "widgets": []},
            ],
        )
        assert state["activeViewId"] == state["views"][0]["viewId"]

    def test_active_view_id_honored_when_valid(self, store):
        state = store.save_state(
            None,
            [
                {"name": "Overview", "widgets": []},
                {"name": "Reports", "widgets": []},
            ],
        )
        second_id = state["views"][1]["viewId"]
        state2 = store.save_state(second_id, state["views"])
        assert state2["activeViewId"] == second_id

    def test_section_widget_has_no_def_id(self, store):
        state = store.save_state(
            None,
            [
                {
                    "name": "Overview",
                    "widgets": [
                        {"kind": "section", "title": "Investing", "x": 0, "y": 0, "w": 12, "h": 30}
                    ],
                }
            ],
        )
        w = state["views"][0]["widgets"][0]
        assert w["kind"] == "section"
        assert "defId" not in w or w.get("defId") is None
        assert w["instanceId"].startswith("widg_")

    def test_view_and_widget_caps(self, db_isolated, monkeypatch):
        monkeypatch.setattr(store_mod, "MAX_VIEWS", 2)
        monkeypatch.setattr(store_mod, "MAX_WIDGETS_PER_VIEW", 1)
        s = DashboardLayoutStore()
        state = s.save_state(
            None,
            [
                {"name": "A", "widgets": [_widget(), _widget()]},
                {"name": "B", "widgets": []},
                {"name": "C", "widgets": []},
            ],
        )
        assert len(state["views"]) == 2
        assert len(state["views"][0]["widgets"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. Service validation tests
# ─────────────────────────────────────────────────────────────────────────────


class TestDashboardLayoutService:
    def test_missing_view_name_rejected(self, svc):
        with pytest.raises(ValidationError, match="name"):
            svc.save_layout(None, [{"widgets": []}])

    def test_missing_widgets_array_rejected(self, svc):
        with pytest.raises(ValidationError, match="widgets"):
            svc.save_layout(None, [{"name": "Overview"}])

    def test_widget_missing_def_id_rejected(self, svc):
        with pytest.raises(ValidationError, match="defId"):
            svc.save_layout(
                None, [{"name": "Overview", "widgets": [{"x": 0, "y": 0, "w": 4, "h": 100}]}]
            )

    def test_section_widget_does_not_require_def_id(self, svc):
        state = svc.save_layout(
            None,
            [
                {
                    "name": "Overview",
                    "widgets": [{"kind": "section", "x": 0, "y": 0, "w": 12, "h": 30}],
                }
            ],
        )
        assert state["views"][0]["widgets"][0]["kind"] == "section"

    def test_section_geometry_must_still_be_numeric(self, svc):
        with pytest.raises(ValidationError, match="x"):
            svc.save_layout(
                None,
                [
                    {
                        "name": "Overview",
                        "widgets": [{"kind": "section", "x": "abc", "y": 0, "w": 12, "h": 30}],
                    }
                ],
            )

    def test_widget_geometry_must_be_numeric(self, svc):
        with pytest.raises(ValidationError, match="x"):
            svc.save_layout(
                None,
                [
                    {
                        "name": "Overview",
                        "widgets": [{"defId": "hero", "x": "zero", "y": 0, "w": 12, "h": 224}],
                    }
                ],
            )

    def test_happy_path(self, svc):
        state = svc.save_layout(None, [{"name": "Overview", "widgets": [_widget()]}])
        assert state["views"][0]["name"] == "Overview"

    def test_normal_layout_saves(self, svc):
        """x=0, y=0, w=12 (full grid width), h=224 — a completely ordinary widget."""
        state = svc.save_layout(
            None, [{"name": "Overview", "widgets": [_widget(x=0, y=0, w=12, h=224)]}]
        )
        widget = state["views"][0]["widgets"][0]
        assert widget["x"] == 0
        assert widget["y"] == 0
        assert widget["w"] == 12
        assert widget["h"] == 224

    def test_nan_geometry_rejected(self, svc):
        with pytest.raises(ValidationError, match="x"):
            svc.save_layout(None, [{"name": "Overview", "widgets": [_widget(x=float("nan"))]}])

    def test_infinite_geometry_rejected(self, svc):
        with pytest.raises(ValidationError, match="w"):
            svc.save_layout(None, [{"name": "Overview", "widgets": [_widget(w=float("inf"))]}])

    def test_negative_x_rejected(self, svc):
        with pytest.raises(ValidationError, match="x"):
            svc.save_layout(None, [{"name": "Overview", "widgets": [_widget(x=-999999)]}])

    def test_zero_width_rejected(self, svc):
        with pytest.raises(ValidationError, match="w"):
            svc.save_layout(None, [{"name": "Overview", "widgets": [_widget(w=0)]}])

    def test_width_over_grid_columns_rejected(self, svc):
        with pytest.raises(ValidationError, match="w"):
            svc.save_layout(None, [{"name": "Overview", "widgets": [_widget(w=13)]}])

    def test_x_at_or_past_the_grid_edge_rejected(self, svc):
        with pytest.raises(ValidationError, match="x"):
            svc.save_layout(None, [{"name": "Overview", "widgets": [_widget(x=12)]}])
        with pytest.raises(ValidationError, match="x"):
            svc.save_layout(None, [{"name": "Overview", "widgets": [_widget(x=10_000_000)]}])

    def test_bool_geometry_rejected(self, svc):
        with pytest.raises(ValidationError, match="x"):
            svc.save_layout(None, [{"name": "Overview", "widgets": [_widget(x=True)]}])

    def test_duplicate_instance_id_rejected(self, svc):
        with pytest.raises(ValidationError, match="instanceId"):
            svc.save_layout(
                None,
                [
                    {
                        "name": "Overview",
                        "widgets": [
                            _widget(instanceId="widg-dup"),
                            _widget(instanceId="widg-dup"),
                        ],
                    }
                ],
            )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Endpoint smoke tests
# ─────────────────────────────────────────────────────────────────────────────


class TestDashboardLayoutEndpoints:
    def test_get_empty_layout(self, client):
        r = client.get("/api/dashboard/layout")
        assert r.status_code == 200
        assert r.json() == {"activeViewId": None, "views": []}

    def test_put_layout_roundtrip(self, client):
        payload = {
            "activeViewId": None,
            "views": [{"name": "Overview", "widgets": [_widget()]}],
        }
        r = client.put("/api/dashboard/layout", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert len(body["views"]) == 1
        view_id = body["views"][0]["viewId"]
        assert body["activeViewId"] == view_id

        r2 = client.get("/api/dashboard/layout")
        assert r2.json()["views"][0]["widgets"][0]["defId"] == "hero"

    def test_put_layout_rejects_bad_widget(self, client):
        r = client.put(
            "/api/dashboard/layout",
            json={"activeViewId": None, "views": [{"name": "Overview", "widgets": [{"x": 0}]}]},
        )
        assert r.status_code == 422

    def test_put_layout_rejects_raw_nan_geometry(self, client):
        # json= can't encode float('nan') as spec-legal JSON — send the raw NaN literal
        # body directly, the way a hand-crafted/malicious client actually would.
        body = (
            '{"activeViewId": null, "views": [{"name": "Overview", "widgets": '
            '[{"defId": "hero", "x": NaN, "y": -999999, "w": Infinity, "h": 224}]}]}'
        )
        r = client.put(
            "/api/dashboard/layout",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 422
