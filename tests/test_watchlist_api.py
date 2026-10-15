"""TestClient smoke tests for watchlist + watchlists REST endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient  # noqa: F401 (used by fixture)

from backend.db.watchlist_store import MY_HOLDINGS_ID

# ── Fixtures ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolate(tmp_path, monkeypatch, db_isolated):
    """Isolate all watchlist-touching modules to a fresh per-test database.

    WatchlistStore is DB-backed (plan 4b task 3) — db_isolated redirects it (and every
    other DB store) to a per-test sqlite file; there is no more raw watchlist.json path to
    monkeypatch (the router already delegates all I/O to WatchlistStore via wl_service, so
    only the DB-adjacent stubs below remain relevant)."""
    import backend.api.watchlist as wl_api

    # Stub out portfolio rows (avoid hitting real DB).
    monkeypatch.setattr(wl_api, "read_rows", lambda: [])

    # Stub instrument resolution (avoid Yahoo/CoinGecko network): echo the symbol as a
    # valid equity. Tests that need an "unknown ticker" re-patch this to return None.
    monkeypatch.setattr(
        wl_api,
        "resolve_watchlist_symbol",
        lambda q: {"value": (q or "").upper(), "label": q, "asset_class": "equity"},
    )

    # Stub connectors (used by alert_channels endpoint).
    import backend.services.connectors as conn_svc

    monkeypatch.setattr(conn_svc, "notifiable_channels", lambda: [])
    monkeypatch.setattr(conn_svc, "known_channel_ids", lambda: set())

    return tmp_path


@pytest.fixture
def client(isolate):
    from backend.api.watchlist import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _make_user_watchlist(client, name="My List"):
    """Helper: create a user watchlist and return its id."""
    res = client.post("/api/watchlists", json={"name": name})
    assert res.status_code == 201
    return res.json()["id"]


# ── GET /api/watchlist ────────────────────────────────────────────────────────────


def test_get_watchlist_200_includes_collections(client):
    res = client.get("/api/watchlist")
    assert res.status_code == 200
    body = res.json()
    assert "items" in body
    assert "config" in body
    assert "watchlists" in body
    assert isinstance(body["watchlists"], list)
    # The auto-managed watchlist must always be present (ADR 0018: key is my_holdings).
    ids = [c["id"] for c in body["watchlists"]]
    assert MY_HOLDINGS_ID in ids


# ── GET /api/watchlists ───────────────────────────────────────────────────────────


def test_get_collections_200(client):
    res = client.get("/api/watchlists")
    assert res.status_code == 200
    body = res.json()
    assert "watchlists" in body
    assert any(c["id"] == MY_HOLDINGS_ID for c in body["watchlists"])


# ── POST /api/watchlists ───────────────────────────────────────────────────────────


def test_create_collection_201(client):
    res = client.post("/api/watchlists", json={"name": "High Conviction"})
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "High Conviction"
    assert "id" in body
    assert body["id"] != MY_HOLDINGS_ID


def test_create_collection_with_optional_fields(client):
    res = client.post(
        "/api/watchlists",
        json={"name": "Watch Later", "description": "For later review", "is_auto_sync": False},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["description"] == "For later review"
    assert body["is_auto_sync"] is False


def test_created_collection_appears_in_list(client):
    client.post("/api/watchlists", json={"name": "My Picks"})
    res = client.get("/api/watchlists")
    names = [c["name"] for c in res.json()["watchlists"]]
    assert "My Picks" in names


# ── PATCH /api/watchlists/{id} ─────────────────────────────────────────────────────


def test_patch_collection_200(client):
    coll_res = client.post("/api/watchlists", json={"name": "Original"})
    coll_id = coll_res.json()["id"]

    res = client.patch(f"/api/watchlists/{coll_id}", json={"name": "Renamed"})
    assert res.status_code == 200
    assert res.json()["name"] == "Renamed"


def test_patch_collection_not_found_404(client):
    res = client.patch("/api/watchlists/nonexistent", json={"name": "X"})
    assert res.status_code == 404


def test_create_collection_duplicate_name_409(client):
    client.post("/api/watchlists", json={"name": "Space Sector"})
    res = client.post("/api/watchlists", json={"name": "space sector"})
    assert res.status_code == 409


def test_patch_collection_rename_collision_409(client):
    client.post("/api/watchlists", json={"name": "Alpha"})
    beta_id = client.post("/api/watchlists", json={"name": "Beta"}).json()["id"]
    res = client.patch(f"/api/watchlists/{beta_id}", json={"name": "Alpha"})
    assert res.status_code == 409


# ── DELETE /api/watchlists/{id} ────────────────────────────────────────────────────


def test_delete_my_holdings_collection_409(client):
    """ADR 0018: deleting my_holdings is refused with 409."""
    res = client.delete(f"/api/watchlists/{MY_HOLDINGS_ID}")
    assert res.status_code == 409


def test_delete_non_default_removes_solo_items(client):
    """Items that only belong to the deleted watchlist are removed from the store."""
    coll_res = client.post("/api/watchlists", json={"name": "Temp"})
    coll_id = coll_res.json()["id"]

    # Add one item only to this watchlist.
    client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "AAPL", "label": "Apple", "watchlist": coll_id},
    )

    res = client.delete(f"/api/watchlists/{coll_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == coll_id

    # The item had only this watchlist → it is deleted from the store.
    wl = client.get("/api/watchlist").json()
    assert not any(i["value"] == "AAPL" for i in wl["items"])


# ── POST /api/watchlist/items — watchlist validation ─────────────────────────────


def test_add_item_with_valid_list_id(client):
    coll_res = client.post("/api/watchlists", json={"name": "My List"})
    coll_id = coll_res.json()["id"]

    res = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "TSLA", "label": "Tesla", "watchlist": coll_id},
    )
    assert res.status_code == 201
    assert coll_id in res.json()["watchlist_ids"]


def test_add_item_bad_list_id_422(client):
    res = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "TSLA", "label": "Tesla", "watchlist": "nonexistent"},
    )
    assert res.status_code == 422


def test_add_item_no_list_id_returns_422(client):
    """ADR 0018: adding with no watchlist is rejected (My holdings is auto-managed)."""
    res = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "NVDA", "label": "NVIDIA"},
    )
    assert res.status_code == 422


def test_add_item_to_my_holdings_returns_409(client):
    """ADR 0018: explicitly targeting my_holdings for an add is refused with 409."""
    res = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "AAPL", "label": "Apple", "watchlist": MY_HOLDINGS_ID},
    )
    assert res.status_code == 409


def test_add_unknown_ticker_422(client, monkeypatch):
    """A symbol that resolves to no real instrument is rejected, not stored."""
    import backend.api.watchlist as wl_api

    monkeypatch.setattr(wl_api, "resolve_watchlist_symbol", lambda q: None)
    coll_id = _make_user_watchlist(client, "My List")
    res = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "NOTATICKER", "watchlist": coll_id},
    )
    assert res.status_code == 422
    # Nothing was stored.
    wl = client.get("/api/watchlist").json()
    assert not any(i["value"] == "NOTATICKER" for i in wl["items"])


def test_add_ticker_canonicalises_resolved_symbol(client, monkeypatch):
    """A loose name resolves to the canonical symbol (ADOBE -> ADBE)."""
    import backend.api.watchlist as wl_api

    monkeypatch.setattr(
        wl_api,
        "resolve_watchlist_symbol",
        lambda q: {"value": "ADBE", "label": "Adobe Inc.", "asset_class": "equity"},
    )
    coll_id = _make_user_watchlist(client, "My List")
    res = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "ADOBE", "watchlist": coll_id},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["value"] == "ADBE"
    assert body["label"] == "Adobe Inc."


# ── PATCH /api/watchlist/items/{id} — label/alerts only (no watchlist move) ────────


def test_patch_item_updates_label(client):
    coll_id = _make_user_watchlist(client, "Patching List")
    add_res = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "META", "label": "Meta", "watchlist": coll_id},
    )
    assert add_res.status_code == 201
    item_id = add_res.json()["id"]

    res = client.patch(f"/api/watchlist/items/{item_id}", json={"label": "Meta Platforms"})
    assert res.status_code == 200
    assert res.json()["label"] == "Meta Platforms"


# ── Deadlock regression (P0) ────────────────────────────────────────────────────────
# A prior version wrapped each handler in `with _watchlist_lock:` while the store methods
# re-acquired the SAME non-reentrant per-path lock → a guaranteed deadlock in production
# (invisible to tests because isolation rebinds the path, splitting the locks). These
# tests run every write endpoint under a watchdog timeout so any reintroduced hang fails
# loudly instead of blocking forever.


def _run_with_timeout(fn, seconds=10):
    """Run fn() in a daemon thread; return its result or raise on timeout (hang)."""
    import threading

    box = {}

    def _target():
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001 — surface in the assertion below
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "watchlist endpoint hung (possible lock deadlock)"
    if "error" in box:
        raise box["error"]
    return box["result"]


def test_write_endpoints_do_not_deadlock(client):
    # GET (auto-sync path), POST item, PATCH config, POST sync, DELETE item — each must complete.
    assert _run_with_timeout(lambda: client.get("/api/watchlist")).status_code == 200

    # Create a user watchlist first (ADR 0018: no silent default to my_holdings).
    coll_res = _run_with_timeout(
        lambda: client.post("/api/watchlists", json={"name": "Deadlock Test"})
    )
    assert coll_res.status_code == 201
    coll_id = coll_res.json()["id"]

    add = _run_with_timeout(
        lambda: client.post(
            "/api/watchlist/items",
            json={"type": "ticker", "value": "AAPL", "label": "Apple", "watchlist": coll_id},
        )
    )
    assert add.status_code == 201
    item_id = add.json()["id"]

    assert (
        _run_with_timeout(
            lambda: client.patch(
                "/api/watchlist/config", json={"price_check_interval_seconds": 600}
            )
        ).status_code
        == 200
    )
    assert _run_with_timeout(lambda: client.post("/api/watchlist/sync")).status_code == 200
    assert (
        _run_with_timeout(lambda: client.delete(f"/api/watchlist/items/{item_id}")).status_code
        == 200
    )


def test_add_ticker_link_semantics(client):
    """Adding AAPL to a user list then a second watchlist yields ONE item with both in watchlist_ids
    (link semantics). ADR 0018: no silent default to my_holdings; must name a watchlist."""
    item_id: str

    # Create two user watchlists.
    wl1_id = _make_user_watchlist(client, "Primary")

    # First add → new item in wl1.
    r1 = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "AAPL", "label": "Apple", "watchlist": wl1_id},
    )
    assert r1.status_code == 201
    item_id = r1.json()["id"]
    assert r1.json()["watchlist_ids"] == [wl1_id]

    # Same ticker, SAME watchlist → 409.
    r2 = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "AAPL", "watchlist": wl1_id},
    )
    assert r2.status_code == 409
    # Case-insensitive, same watchlist → still 409.
    r3 = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "aapl", "watchlist": wl1_id},
    )
    assert r3.status_code == 409

    # Same ticker, DIFFERENT watchlist → links (returns existing item with both watchlist_ids).
    # (Price alerts are decoupled from the item per ADR 0014 — link semantics no longer touch them.)
    coll = client.post("/api/watchlists", json={"name": "Themes"}).json()
    r4 = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "AAPL", "watchlist": coll["id"]},
    )
    assert r4.status_code == 201
    body4 = r4.json()
    # Must be the SAME item (same id), not a new one.
    assert body4["id"] == item_id
    # watchlist_ids now contains BOTH watchlists.
    assert wl1_id in body4["watchlist_ids"]
    assert coll["id"] in body4["watchlist_ids"]
    # The item carries no price_alerts field anymore (alerts live in /api/alerts).
    assert "price_alerts" not in body4

    # Store should have exactly ONE item for AAPL.
    wl = client.get("/api/watchlist").json()
    aapl_items = [i for i in wl["items"] if i["value"] == "AAPL"]
    assert len(aapl_items) == 1


def test_delete_per_list_removes_from_one_list_keeps_item(client):
    """DELETE ?watchlist_id= removes from that watchlist only; item survives if still in others."""
    wl1_id = _make_user_watchlist(client, "Primary")

    # Add AAPL to wl1, then link to a second watchlist.
    r1 = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "AAPL", "label": "Apple", "watchlist": wl1_id},
    )
    item_id = r1.json()["id"]
    coll = client.post("/api/watchlists", json={"name": "Extra"}).json()
    client.post(
        "/api/watchlist/items", json={"type": "ticker", "value": "AAPL", "watchlist": coll["id"]}
    )

    # Remove from Extra only.
    res = client.delete(f"/api/watchlist/items/{item_id}?watchlist_id={coll['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["deleted"] is False  # not fully deleted

    # Item still exists in wl1.
    wl = client.get("/api/watchlist").json()
    aapl = next((i for i in wl["items"] if i["value"] == "AAPL"), None)
    assert aapl is not None
    assert aapl["watchlist_ids"] == [wl1_id]


def test_delete_per_list_removes_last_list_deletes_item(client):
    """DELETE ?watchlist_id= when it's the last watchlist deletes the item entirely."""
    wl1_id = _make_user_watchlist(client, "Only List")
    r1 = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "TSLA", "label": "Tesla", "watchlist": wl1_id},
    )
    item_id = r1.json()["id"]

    res = client.delete(f"/api/watchlist/items/{item_id}?watchlist_id={wl1_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["deleted"] is True

    wl = client.get("/api/watchlist").json()
    assert not any(i["value"] == "TSLA" for i in wl["items"])


def test_delete_scoped_noop_when_item_not_in_that_watchlist_returns_ok_false(client):
    """I7 regression: DELETE ?watchlist_id=<a list the item ISN'T a member of> must not lie —
    the frontend's activeListId can point at a watchlist unrelated to the tapped item, and the
    old response always returned ok:true even when the item was untouched."""
    wl1_id = _make_user_watchlist(client, "Primary")
    other_id = _make_user_watchlist(client, "Themes")

    r1 = client.post(
        "/api/watchlist/items",
        json={"type": "ticker", "value": "TSLA", "label": "Tesla", "watchlist": wl1_id},
    )
    item_id = r1.json()["id"]

    res = client.delete(f"/api/watchlist/items/{item_id}?watchlist_id={other_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["removed"] is False
    assert body["deleted"] is False

    # Item is untouched — still present, still only in wl1.
    wl = client.get("/api/watchlist").json()
    item = next((i for i in wl["items"] if i["id"] == item_id), None)
    assert item is not None
    assert item["watchlist_ids"] == [wl1_id]


# ── Topics are followed, in no watchlist (ADR 2026-09-27-topics-are-followed-not-listed) ──


def test_a_topic_is_followed_without_a_watchlist(client):
    res = client.post(
        "/api/watchlist/items", json={"type": "topic", "value": "SpaceX IPO", "label": "SpaceX IPO"}
    )
    assert res.status_code == 201
    assert res.json()["watchlist_ids"] == []
    assert "notice" not in res.json()


def test_a_watchlist_sent_with_a_topic_is_ignored_and_said_so(client):
    wl1_id = _make_user_watchlist(client, "Themes")
    res = client.post(
        "/api/watchlist/items",
        json={"type": "topic", "value": "SpaceX IPO", "label": "SpaceX IPO", "watchlist": wl1_id},
    )
    assert res.status_code == 201
    assert res.json()["watchlist_ids"] == []
    assert "followed everywhere" in res.json()["notice"]


def test_following_the_same_topic_twice_is_refused(client):
    body = {"type": "topic", "value": "SpaceX IPO", "label": "SpaceX IPO"}
    assert client.post("/api/watchlist/items", json=body).status_code == 201
    again = client.post("/api/watchlist/items", json={**body, "value": " spacex ipo "})
    assert again.status_code == 409
    assert "already following" in again.json()["detail"]


def test_deleting_a_topic_scoped_to_a_watchlist_unfollows_it(client):
    other_id = _make_user_watchlist(client, "Themes")
    item_id = client.post(
        "/api/watchlist/items", json={"type": "topic", "value": "Fed", "label": "Fed"}
    ).json()["id"]
    res = client.delete(f"/api/watchlist/items/{item_id}?watchlist_id={other_id}")
    assert res.json() == {"ok": True, "deleted": True}
    wl = client.get("/api/watchlist").json()
    assert not any(i["id"] == item_id for i in wl["items"])


def test_delete_full_no_query_param_removes_a_topic(client):
    """Full DELETE (no watchlist_id) of a topic removes it entirely."""
    r1 = client.post(
        "/api/watchlist/items",
        json={"type": "topic", "value": "SpaceX IPO", "label": "SpaceX IPO"},
    )
    item_id = r1.json()["id"]

    res = client.delete(f"/api/watchlist/items/{item_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["deleted"] is True

    wl = client.get("/api/watchlist").json()
    assert not any(i["id"] == item_id for i in wl["items"])


def test_delete_item_watchlist_id_my_holdings_returns_409(client):
    """ADR 0018: DELETE ?watchlist_id=my_holdings is refused with 409."""
    # Seed a my_holdings item via sync (monkeypatched read_rows returns []).
    # We can't add directly, so use the sync endpoint with a stubbed position.
    # Instead, just call the endpoint directly with a fake item id — the store will raise.
    res = client.delete(f"/api/watchlist/items/fake-id?watchlist_id={MY_HOLDINGS_ID}")
    assert res.status_code == 409


def test_delete_item_in_my_holdings_without_watchlist_id_returns_409(client, monkeypatch):
    """ADR 0018: full DELETE of an item in my_holdings (no watchlist_id param) returns 409."""
    import backend.db.watchlist_store as wl_store

    # Inject a my_holdings item directly into the store via sync.
    store = wl_store.WatchlistStore()
    store.sync_from_holdings(
        [
            {
                "ticker": "GRAB",
                "quantity": 5,
                "label": "Grab",
                "asset_class": "equity",
                "instrument_id": "",
            }
        ]
    )
    items = store.query_items()
    grab = next(i for i in items if i["value"] == "GRAB")

    res = client.delete(f"/api/watchlist/items/{grab['id']}")
    assert res.status_code == 409


# ── web_search_enabled round-trips through the real read path (regression) ──────────


def test_normalize_config_preserves_web_search_enabled():
    from backend.services.watchlist import _normalize_config

    assert _normalize_config({"web_search_enabled": False})["web_search_enabled"] is False
    assert _normalize_config({})["web_search_enabled"] is True  # default on


def test_web_search_enabled_persists_through_read_watchlist(client, isolate):
    """The intel scan reads config via read_watchlist → _normalize_config; a saved
    web_search_enabled=False MUST survive that path (else paid search silently re-enables)."""
    from backend.services.watchlist import read_watchlist

    assert read_watchlist()["config"].get("web_search_enabled") is True  # default
    res = client.patch("/api/watchlist/config", json={"web_search_enabled": False})
    assert res.status_code == 200 and res.json().get("web_search_enabled") is False
    assert read_watchlist()["config"]["web_search_enabled"] is False  # not stripped on read


def test_outlet_feeds_enabled_patch_roundtrips(client, isolate):
    """ConfigPatch must declare outlet_feeds_enabled or the PATCH is silently dropped by
    model_dump(exclude_none=True) (INT11)."""
    from backend.services.watchlist import read_watchlist

    assert read_watchlist()["config"].get("outlet_feeds_enabled") is True  # default on

    res = client.patch("/api/watchlist/config", json={"outlet_feeds_enabled": False})
    assert res.status_code == 200 and res.json().get("outlet_feeds_enabled") is False
    assert read_watchlist()["config"]["outlet_feeds_enabled"] is False

    res = client.patch("/api/watchlist/config", json={"outlet_feeds_enabled": True})
    assert res.status_code == 200 and res.json().get("outlet_feeds_enabled") is True
    assert read_watchlist()["config"]["outlet_feeds_enabled"] is True


# ── watch intent (buy zone / fair value / thesis) ────────────────────────────────


def _add_ticker(client, value="AMD"):
    wid = _make_user_watchlist(client, name=f"List {value}")
    res = client.post("/api/watchlist/items", json={"value": value, "watchlist": wid})
    assert res.status_code in (200, 201)
    return res.json()["id"]


def test_intent_round_trips_and_is_normalised(client):
    item_id = _add_ticker(client)
    res = client.patch(
        f"/api/watchlist/items/{item_id}",
        json={
            "intent": {
                "thesis": "  AI capex leader  ",
                "buy_below": 90,
                "buy_above": 70,
                "fair_value": 150,
            }
        },
    )
    assert res.status_code == 200
    assert res.json()["intent"] == {
        "thesis": "AI capex leader",
        "buy_below": 90.0,
        "buy_above": 70.0,
        "fair_value": 150.0,
    }


def test_an_empty_intent_clears_it(client):
    item_id = _add_ticker(client)
    client.patch(f"/api/watchlist/items/{item_id}", json={"intent": {"buy_below": 90}})
    res = client.patch(f"/api/watchlist/items/{item_id}", json={"intent": {}})
    assert res.json()["intent"] is None


@pytest.mark.parametrize(
    "intent",
    [
        {"buy_below": -5},
        {"buy_above": 70},  # a floor with no top
        {"buy_below": 70, "buy_above": 90},  # inverted zone
        {"thesis": "x" * 281},
    ],
)
def test_bad_intent_is_refused_at_the_store(client, intent):
    item_id = _add_ticker(client)
    res = client.patch(f"/api/watchlist/items/{item_id}", json={"intent": intent})
    assert res.status_code == 422
    assert "intent" in res.json()["detail"]


def test_ai_settings_default_and_round_trip_through_the_read(client):
    """Settings › Intelligence: the AI keys default sensibly, survive the service read, and are
    clamped the same way the Lucius tool clamps them."""
    from backend.services.watchlist import read_watchlist

    cfg = read_watchlist()["config"]
    assert cfg["explained_alerts_enabled"] is True and cfg["watch_reads_enabled"] is True
    assert cfg["watch_reads_per_day"] == 20 and cfg["ai_opinion_level"] == "information"
    assert cfg["holdings_brief_verbosity"] == 3 and cfg["watchlist_brief_verbosity"] == 3

    res = client.patch(
        "/api/watchlist/config",
        json={
            "explained_alerts_enabled": False,
            "watch_reads_enabled": False,
            "watch_reads_per_day": 500,
            "ai_opinion_level": "suggest",
            "holdings_brief_verbosity": 9,
            "watchlist_brief_verbosity": 0,
        },
    )
    assert res.status_code == 200
    cfg = read_watchlist()["config"]
    assert cfg["explained_alerts_enabled"] is False and cfg["watch_reads_enabled"] is False
    assert cfg["watch_reads_per_day"] == 50
    assert cfg["ai_opinion_level"] == "suggest"
    assert (cfg["holdings_brief_verbosity"], cfg["watchlist_brief_verbosity"]) == (5, 1)


def test_an_unknown_opinion_level_is_refused(client):
    res = client.patch("/api/watchlist/config", json={"ai_opinion_level": "yolo"})
    assert res.status_code == 422


def test_the_lucius_tool_sets_the_ai_settings(client):
    import asyncio
    import json

    from backend.ai.tools import execute_tool
    from backend.services.watchlist import read_watchlist

    text, _ = asyncio.run(
        execute_tool(
            "update_monitoring_config",
            {"ai_opinion_level": "lean", "watch_reads_per_day": 5, "holdings_brief_verbosity": 5},
        )
    )
    assert json.loads(text).get("ok") is not False
    cfg = read_watchlist()["config"]
    assert (cfg["ai_opinion_level"], cfg["watch_reads_per_day"]) == ("lean", 5)
    assert cfg["holdings_brief_verbosity"] == 5
