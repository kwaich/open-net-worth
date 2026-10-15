"""custom_feed_urls validation at the WatchlistStore write choke (owner directive 2026-07-15,
open-source generalization amendment to news-ingestion-latency.md) — covers the store layer
(the single choke both REST and every AI-tool write pass through) AND the REST/AI surfaces.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db.watchlist_store import WatchlistStore


@pytest.fixture
def store(db_isolated):
    return WatchlistStore()


def _insert(store, watchlist_id, **overrides):
    item_type = overrides.pop("type", "topic")
    item = {
        "id": overrides.pop("id", "wli-1"),
        "type": item_type,
        "value": overrides.pop("value", "x"),
        # A topic is followed, in no watchlist (ADR 2026-09-27); a ticker needs its list.
        "watchlist_ids": [] if item_type in ("topic", "company") else [watchlist_id],
        **overrides,
    }
    return store.insert(item)


# ── store-level validation ────────────────────────────────────────────────────────


def test_insert_accepts_valid_https_urls(store):
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"], custom_feed_urls=["https://example.com/feed.xml"])
    assert item["custom_feed_urls"] == ["https://example.com/feed.xml"]


def test_insert_rejects_more_than_five_custom_feeds(store):
    wl = store.create_watchlist("Watch Later")
    urls = [f"https://example.com/feed{i}.xml" for i in range(6)]
    with pytest.raises(ValidationError):
        _insert(store, wl["id"], custom_feed_urls=urls)


def test_insert_rejects_javascript_scheme(store):
    wl = store.create_watchlist("Watch Later")
    with pytest.raises(ValidationError):
        _insert(store, wl["id"], custom_feed_urls=["javascript:alert(1)"])


def test_insert_rejects_private_ip_target(store):
    wl = store.create_watchlist("Watch Later")
    with pytest.raises(ValidationError):
        _insert(store, wl["id"], custom_feed_urls=["http://127.0.0.1/feed.xml"])


def test_insert_rejects_link_local_metadata_target(store):
    wl = store.create_watchlist("Watch Later")
    with pytest.raises(ValidationError):
        _insert(store, wl["id"], custom_feed_urls=["http://169.254.169.254/latest/meta-data"])


def test_insert_strips_blank_entries_without_erroring(store):
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"], custom_feed_urls=["", "  ", "https://example.com/a.xml"])
    assert item["custom_feed_urls"] == ["https://example.com/a.xml"]


def test_insert_no_custom_feed_urls_key_is_a_noop(store):
    """An item without the key at all must insert cleanly (existing pre-INT5 items)."""
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"])
    assert "custom_feed_urls" not in item


def test_update_by_id_validates_custom_feed_urls(store):
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"])
    with pytest.raises(ValidationError):
        store.update_by_id(item["id"], {"custom_feed_urls": ["javascript:alert(1)"]})


def test_update_by_id_accepts_valid_replacement(store):
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"], custom_feed_urls=["https://example.com/old.xml"])
    updated = store.update_by_id(item["id"], {"custom_feed_urls": ["https://example.com/new.xml"]})
    assert updated["custom_feed_urls"] == ["https://example.com/new.xml"]


def test_update_by_id_clears_custom_feed_urls_with_empty_list(store):
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"], custom_feed_urls=["https://example.com/a.xml"])
    updated = store.update_by_id(item["id"], {"custom_feed_urls": []})
    assert updated["custom_feed_urls"] == []


def test_insert_linked_tickers_passthrough(store):
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"], linked_tickers=["ONWAI.DE"])
    assert item["linked_tickers"] == ["ONWAI.DE"]


def test_insert_rejects_more_than_ten_linked_tickers(store):
    """Nit (code review): linked_tickers gets the same length-cap discipline as
    custom_feed_urls — an unbounded list would bloat every hot-tier entity/term list."""
    wl = store.create_watchlist("Watch Later")
    tickers = [f"T{i}" for i in range(11)]
    with pytest.raises(ValidationError):
        _insert(store, wl["id"], linked_tickers=tickers)


def test_insert_accepts_exactly_ten_linked_tickers(store):
    wl = store.create_watchlist("Watch Later")
    tickers = [f"T{i}" for i in range(10)]
    item = _insert(store, wl["id"], linked_tickers=tickers)
    assert item["linked_tickers"] == tickers


def test_update_by_id_rejects_more_than_ten_linked_tickers(store):
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"])
    with pytest.raises(ValidationError):
        store.update_by_id(item["id"], {"linked_tickers": [f"T{i}" for i in range(11)]})


def test_insert_linked_tickers_strips_blank_entries(store):
    wl = store.create_watchlist("Watch Later")
    item = _insert(store, wl["id"], linked_tickers=["", "  ", "ONWAI.DE"])
    assert item["linked_tickers"] == ["ONWAI.DE"]


# ── REST surface ──────────────────────────────────────────────────────────────────


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.api.watchlist as wl_api

    app = FastAPI()
    app.include_router(wl_api.router)
    return TestClient(app)


def test_rest_add_item_rejects_oversize_custom_feed_list(db_isolated):
    client = _client()
    wl_resp = client.post("/api/watchlists", json={"name": "Watch Later"})
    assert wl_resp.status_code == 201
    resp = client.post(
        "/api/watchlist/items",
        json={
            "type": "topic",
            "value": "macro",
            "label": "Macro",
            "watchlist": "Watch Later",
            "custom_feed_urls": [f"https://example.com/{i}.xml" for i in range(6)],
        },
    )
    assert resp.status_code == 422


def test_rest_add_item_rejects_unsafe_custom_feed_url(db_isolated):
    client = _client()
    client.post("/api/watchlists", json={"name": "Watch Later"})
    resp = client.post(
        "/api/watchlist/items",
        json={
            "type": "topic",
            "value": "macro",
            "label": "Macro",
            "watchlist": "Watch Later",
            "custom_feed_urls": ["http://127.0.0.1/feed.xml"],
        },
    )
    assert resp.status_code == 422


def test_rest_patch_item_rejects_unsafe_custom_feed_url(db_isolated):
    client = _client()
    client.post("/api/watchlists", json={"name": "Watch Later"})
    add_resp = client.post(
        "/api/watchlist/items",
        json={"type": "topic", "value": "macro", "label": "Macro", "watchlist": "Watch Later"},
    )
    item_id = add_resp.json()["id"]
    resp = client.patch(
        f"/api/watchlist/items/{item_id}", json={"custom_feed_urls": ["javascript:alert(1)"]}
    )
    assert resp.status_code == 422


def test_rest_patch_item_accepts_linked_tickers(db_isolated):
    client = _client()
    client.post("/api/watchlists", json={"name": "Watch Later"})
    add_resp = client.post(
        "/api/watchlist/items",
        json={
            "type": "topic",
            "value": "ONWAI M&A",
            "label": "ONWAI M&A",
            "watchlist": "Watch Later",
        },
    )
    item_id = add_resp.json()["id"]
    resp = client.patch(f"/api/watchlist/items/{item_id}", json={"linked_tickers": ["ONWAI.DE"]})
    assert resp.status_code == 200
    assert resp.json()["linked_tickers"] == ["ONWAI.DE"]
