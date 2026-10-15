"""GET /api/intel/assets/{id}/movements — status + shape smoke; the background trigger fires."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_movements_200_shape_and_limit_validation(client, monkeypatch):
    import backend.monitoring.price_movements as mon
    import backend.services.price_movements as svc

    payload = {
        "instrument_id": "i1",
        "ticker": "UBER",
        "label": "Uber",
        "currency": "USD",
        "market": {"state": "CLOSED"},
        "today": "2026-09-20",
        "as_of": "2026-09-20T04:21:00+00:00",
        "rows": [{"kind": "market_closed", "session_date": "2026-09-20"}],
    }
    seen = {}
    monkeypatch.setattr(
        svc, "read_timeline", lambda iid, limit: seen.setdefault("args", (iid, limit)) and payload
    )
    fired = []

    async def fake_trigger(iid, limit=10):
        fired.append((iid, limit))
        return True

    monkeypatch.setattr(mon, "trigger_on_demand_narration", fake_trigger)
    resp = client.get("/api/intel/assets/i1/movements?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert {"instrument_id", "currency", "market", "rows", "today"} <= set(body)
    assert body["rows"][0]["kind"] == "market_closed"
    assert seen["args"] == ("i1", 5)
    assert fired == [("i1", 5)]  # the on-demand pass covers the page the client asked for
    assert client.get("/api/intel/assets/i1/movements?limit=0").status_code == 422
    assert client.get("/api/intel/assets/i1/movements?limit=99").status_code == 422


def test_movements_404_for_unknown_or_no_ticker(client, monkeypatch):
    import backend.services.price_movements as svc

    monkeypatch.setattr(svc, "read_timeline", lambda iid, limit: None)
    assert client.get("/api/intel/assets/nope/movements").status_code == 404


def test_movements_flags_rows_whose_paragraph_is_being_rewritten(client, monkeypatch):
    """Owner 2026-09-21: "when the narrative is regenerating there is no way to tell — we are
    still showing the old narrative of today". The endpoint stamps `rewriting: true` on a
    row that already has a paragraph AND is either mid-narration or due for one, so the
    client can say "Updating…" next to the old text and poll faster; a row without a
    paragraph is already `pending` and is left alone."""
    import backend.monitoring.price_movements as mon
    import backend.services.price_movements as svc

    payload = {
        "instrument_id": "i1",
        "ticker": "UBER",
        "label": "Uber",
        "currency": "USD",
        "market": {"state": "REGULAR", "timezone": "America/New_York"},
        "today": "2026-09-21",
        "as_of": "2026-09-21T14:00:00+00:00",
        "rows": [
            {
                "kind": "live",
                "session_date": "2026-09-21",
                "summary": "old text",
                "narration": "ready",
            },
            {
                "kind": "session",
                "session_date": "2026-09-18",
                "summary": "done",
                "narration": "ready",
            },
            {
                "kind": "session",
                "session_date": "2026-09-17",
                "summary": None,
                "narration": "pending",
            },
        ],
    }
    monkeypatch.setattr(svc, "read_timeline", lambda iid, limit: payload)

    async def fake_trigger(iid, limit=10):
        return True

    monkeypatch.setattr(mon, "trigger_on_demand_narration", fake_trigger)
    monkeypatch.setattr(
        mon, "rows_being_rewritten", lambda iid, timeline: {"2026-09-21", "2026-09-17"}
    )
    rows = {
        r["session_date"]: r for r in client.get("/api/intel/assets/i1/movements").json()["rows"]
    }
    assert rows["2026-09-21"]["rewriting"] is True
    assert rows["2026-09-18"]["rewriting"] is False
    assert rows["2026-09-17"]["rewriting"] is False  # no paragraph to keep showing: it's pending
