"""API smoke tests for GET /api/mentionables.

Auth: follows the pattern from test_activity_api.py — build a bare FastAPI
with just the mentionables router (no auth gate) + monkeypatched stores/services.

Covers:
- 200 + correct {items, next_cursor} shape
- surface + types filtering (delegated correctly)
- cursor round-trip
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.services.mentions.registry as _reg
from backend.api.mentionables import router
from backend.services.mentions.types import MentionProvider

# ── Fixture ───────────────────────────────────────────────────────────────────


def _fake_rows(type_: str, n: int = 3) -> list[dict]:
    return [
        {
            "type": type_,
            "id": f"{type_}-{i}",
            "label": f"{type_.title()} {i}",
            "sublabel": "sub",
            "value": f"{type_}-{i}",
            "icon": "◆",
            "surface": "entity",
        }
        for i in range(n)
    ]


def _fake_provider(type_: str, rows: list[dict]) -> MentionProvider:
    return MentionProvider(
        type=type_,
        surfaces=frozenset({"entity"}),
        icon="◆",
        resolve=lambda ref: None,
        search=lambda q: rows,
    )


def _make_client(monkeypatch) -> TestClient:
    # Isolate: replace registry with two predictable fake providers.
    monkeypatch.setitem(
        _reg.REGISTRY, "account", _fake_provider("account", _fake_rows("account", 5))
    )
    monkeypatch.setitem(
        _reg.REGISTRY, "instrument", _fake_provider("instrument", _fake_rows("instrument", 5))
    )
    # Remove all other providers so results are predictable.
    for key in list(_reg.REGISTRY.keys()):
        if key not in ("account", "instrument"):
            monkeypatch.delitem(_reg.REGISTRY, key)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_mentionables_returns_200_and_shape(monkeypatch):
    client = _make_client(monkeypatch)
    resp = client.get("/api/mentionables")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "next_cursor" in data
    assert isinstance(data["items"], list)


def test_mentionables_rows_carry_surface_field(monkeypatch):
    # The 7th row field (P3) must survive the search → API round-trip so the picker can
    # route a pick to the right zone (chip vs responder vs verb).
    client = _make_client(monkeypatch)
    resp = client.get("/api/mentionables")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    for item in items:
        assert "surface" in item, "each row must carry a 'surface' field"
        assert item["surface"] in ("entity", "agent", "verb", "dashboard")


def test_mentionables_types_filter(monkeypatch):
    client = _make_client(monkeypatch)
    resp = client.get("/api/mentionables?types=account")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items  # at least one
    assert all(item["type"] == "account" for item in items)


def test_mentionables_surface_filter_excludes_non_matching(monkeypatch):
    # Add a provider with surface "dashboard" only
    dashboard_provider = MentionProvider(
        type="widget",
        surfaces=frozenset({"dashboard"}),
        icon="◆",
        resolve=lambda ref: None,
        search=lambda q: [
            {
                "type": "widget",
                "id": "w1",
                "label": "Widget",
                "sublabel": "",
                "value": "w1",
                "icon": "◆",
                "surface": "dashboard",
            }
        ],
    )
    monkeypatch.setitem(_reg.REGISTRY, "widget", dashboard_provider)
    client = _make_client(monkeypatch)

    # surface=entity should NOT include dashboard-only providers
    resp = client.get("/api/mentionables?surface=entity")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(item["type"] != "widget" for item in items)


def test_mentionables_q_filter(monkeypatch):
    client = _make_client(monkeypatch)
    # providers are fakes that return rows regardless of q, but the shape must still be correct
    resp = client.get("/api/mentionables?q=account")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data


def test_mentionables_cursor_round_trip(monkeypatch):
    client = _make_client(monkeypatch)
    # With limit=3 and 10 total rows (5 account + 5 instrument), expect next_cursor
    resp1 = client.get("/api/mentionables?limit=3")
    assert resp1.status_code == 200
    d1 = resp1.json()
    assert len(d1["items"]) == 3
    cursor = d1["next_cursor"]
    assert cursor is not None

    # Following cursor gives the next 3
    resp2 = client.get(f"/api/mentionables?limit=3&cursor={cursor}")
    assert resp2.status_code == 200
    d2 = resp2.json()
    assert len(d2["items"]) == 3
    # No overlap
    ids1 = {item["id"] for item in d1["items"]}
    ids2 = {item["id"] for item in d2["items"]}
    assert ids1.isdisjoint(ids2)


def test_mentionables_last_page_no_next_cursor(monkeypatch):
    client = _make_client(monkeypatch)
    # 10 total rows; fetch all at once
    resp = client.get("/api/mentionables?limit=50")
    assert resp.status_code == 200
    data = resp.json()
    assert data["next_cursor"] is None


def test_mentionables_limit_default(monkeypatch):
    # Add enough fake rows to need pagination
    many_rows = [
        {
            "type": "account",
            "id": f"a-{i}",
            "label": f"Acc {i}",
            "sublabel": "",
            "value": f"a-{i}",
            "icon": "⬡",
            "surface": "entity",
        }
        for i in range(30)
    ]
    monkeypatch.setitem(_reg.REGISTRY, "account", _fake_provider("account", many_rows))
    for key in list(_reg.REGISTRY.keys()):
        if key != "account":
            monkeypatch.delitem(_reg.REGISTRY, key)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/api/mentionables")  # default limit=20
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 20
    assert data["next_cursor"] is not None
