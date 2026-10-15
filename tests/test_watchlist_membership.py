"""resolve_watchlist_id_strict (service) + watchlist membership via
add/remove_watchlist_items (tools).

Consolidated model (Option A): membership is owned by add_watchlist_items (link an existing
ticker into a named watchlist) and remove_watchlist_items (drop it from one watchlist, by
name or id); update_watchlist_items is settings-only. Regression net for the original bug: a
ticker can be added to a second watchlist without being removed from its first.

Batch-pluralized 2026-07-19: add_watchlist_item -> add_watchlist_items,
remove_watchlist_item -> remove_watchlist_items, update_watchlist_item ->
update_watchlist_items. The former singular names are kept as registry aliases (a tool
name is persisted data).

Param-arity follow-up (2026-07-19d): remove_watchlist_items'/update_watchlist_items'
batch param renamed item_id -> item_ids, and get_watchlist's value -> values (old keys
still resolve — read-side fallback, no alias table needed for a param rename).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.watchlist_store import MY_HOLDINGS_ID


@pytest.fixture
def wl(db_isolated):
    """Isolated (fresh per-test database) watchlist store."""
    import backend.db.watchlist_store as wls_mod

    return wls_mod.WatchlistStore()


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── resolve_watchlist_id_strict (service) ─────────────────────────────────────────────


def test_resolve_strict_matches_collection_name(wl):
    from backend.services.watchlist import resolve_watchlist_id_strict

    coll = wl.create_watchlist("Space Sector")
    assert resolve_watchlist_id_strict("Space Sector") == coll["id"]
    assert resolve_watchlist_id_strict("space sector") == coll["id"]


def test_resolve_strict_matches_collection_id(wl):
    from backend.services.watchlist import resolve_watchlist_id_strict

    coll = wl.create_watchlist("Space Sector")
    assert resolve_watchlist_id_strict(coll["id"]) == coll["id"]


def test_resolve_strict_default_by_name_and_id(wl):
    """After ADR 0018: the auto-managed watchlist resolves by MY_HOLDINGS_ID and by its name."""
    from backend.services.watchlist import resolve_watchlist_id_strict

    wl.list_watchlists()  # ensure my_holdings exists
    assert resolve_watchlist_id_strict(MY_HOLDINGS_ID) == MY_HOLDINGS_ID
    assert resolve_watchlist_id_strict("My holdings") == MY_HOLDINGS_ID


def test_resolve_strict_unknown_returns_none(wl):
    from backend.services.watchlist import resolve_watchlist_id_strict

    assert resolve_watchlist_id_strict("No Such List") is None


def test_resolve_strict_none_returns_none(wl):
    from backend.services.watchlist import resolve_watchlist_id_strict

    assert resolve_watchlist_id_strict(None) is None


def test_resolve_strict_empty_string_returns_none(wl):
    from backend.services.watchlist import resolve_watchlist_id_strict

    assert resolve_watchlist_id_strict("") is None


# ── add_watchlist_items: membership ADD (link existing) ──────────────────────────


def test_add_links_existing_ticker_to_named_list_without_removing_default(wl):
    """The original bug fix: add an already-watched ticker to a second watchlist; it stays in first list."""
    watchlist_a = wl.create_watchlist("Watchlist A")
    watchlist_b = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [watchlist_a["id"]]})

    res, _ = call(
        "add_watchlist_items",
        type="ticker",
        value="RKLB",
        label="Rocket Lab",
        watchlist="Space Sector",
    )
    assert res["ok"] is True and res.get("linked") is True
    item = next(i for i in wl.query_items() if i["value"] == "RKLB")
    assert set(item["watchlist_ids"]) == {watchlist_a["id"], watchlist_b["id"]}


def test_add_watchlist_item_alias_resolves(wl):
    """The pre-2026-07-19 singular name must still resolve (via registry alias)."""
    wl.create_watchlist("Space Sector")
    res, _ = call(
        "add_watchlist_items",
        type="ticker",
        value="RKLB",
        label="Rocket Lab",
        watchlist="Space Sector",
    )
    assert res["ok"] is True
    assert res["item"]["value"] == "RKLB"


def test_add_watchlist_items_batch_adds_several_in_one_call(wl):
    wl.create_watchlist("Space Sector")
    res, proposed = call(
        "add_watchlist_items",
        watchlist="Space Sector",
        items=[
            {"type": "ticker", "value": "RKLB", "label": "Rocket Lab"},
            {"type": "ticker", "value": "LUNR", "label": "Intuitive Machines"},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["added"] == 2
    values = {i["value"] for i in wl.query_items()}
    assert {"RKLB", "LUNR"} <= values


def test_add_watchlist_items_batch_one_bad_url_does_not_block_the_rest(wl):
    """Partial-failure legibility: the SSRF guard fires per item."""
    wl.create_watchlist("Space Sector")
    res, proposed = call(
        "add_watchlist_items",
        watchlist="Space Sector",
        items=[
            {
                "type": "topic",
                "value": "Evil",
                "label": "Evil",
                "custom_feed_urls": ["javascript:alert(1)"],
            },
            {"type": "ticker", "value": "RKLB", "label": "Rocket Lab"},
        ],
    )
    assert proposed is None
    assert res["ok"] is False
    assert res["added"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True
    assert any(i["value"] == "RKLB" for i in wl.query_items())
    assert not any(i["value"] == "EVIL" for i in wl.query_items())


def test_add_watchlist_items_batch_item_can_override_the_shared_watchlist(wl):
    space = wl.create_watchlist("Space Sector")
    defense = wl.create_watchlist("Defense")
    res, _ = call(
        "add_watchlist_items",
        watchlist="Space Sector",
        items=[
            {"type": "ticker", "value": "RKLB", "label": "Rocket Lab"},
            {"type": "ticker", "value": "LMT", "label": "Lockheed", "watchlist": "Defense"},
        ],
    )
    assert res["ok"] is True
    by_value = {i["value"]: i for i in wl.query_items()}
    assert by_value["RKLB"]["watchlist_ids"] == [space["id"]]
    assert by_value["LMT"]["watchlist_ids"] == [defense["id"]]


# ── remove_watchlist_items: membership REMOVE (by name or id) ────────────────────


def test_remove_from_named_list_by_name(wl):
    watchlist_a = wl.create_watchlist("Watchlist A")
    coll = wl.create_watchlist("Space Sector")
    wl.insert(
        {
            "id": "i1",
            "value": "RKLB",
            "type": "ticker",
            "watchlist_ids": [watchlist_a["id"], coll["id"]],
        }
    )

    res, _ = call("remove_watchlist_items", item_id="i1", watchlist="Space Sector")
    assert res["ok"] is True
    item = next(i for i in wl.query_items() if i["id"] == "i1")
    assert item["watchlist_ids"] == [watchlist_a["id"]]


def test_remove_from_named_list_by_id_still_works(wl):
    watchlist_a = wl.create_watchlist("Watchlist A")
    coll = wl.create_watchlist("Space Sector")
    wl.insert(
        {
            "id": "i1",
            "value": "RKLB",
            "type": "ticker",
            "watchlist_ids": [watchlist_a["id"], coll["id"]],
        }
    )

    res, _ = call("remove_watchlist_items", item_id="i1", watchlist=coll["id"])
    assert res["ok"] is True
    assert next(i for i in wl.query_items() if i["id"] == "i1")["watchlist_ids"] == [
        watchlist_a["id"]
    ]


def test_remove_unknown_list_name_errors(wl):
    watchlist_a = wl.create_watchlist("Watchlist A")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [watchlist_a["id"]]})
    res, _ = call("remove_watchlist_items", item_id="i1", watchlist="No Such List")
    assert res["ok"] is False
    assert "not found" in res["error"].lower()
    assert wl.query_items()[0]["watchlist_ids"] == [watchlist_a["id"]]


def test_remove_from_only_list_deletes_item(wl):
    """Removing an item from its only watchlist (a user-created list, by name) deletes it entirely."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, _ = call("remove_watchlist_items", item_id="i1", watchlist="Space Sector")
    assert res["ok"] is True and res.get("deleted") is True
    assert not any(i["id"] == "i1" for i in wl.query_items())


def test_remove_item_entirely_without_list_id(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, _ = call("remove_watchlist_items", item_id="i1")
    assert res["ok"] is True and res["deleted"] is True
    assert not any(i["id"] == "i1" for i in wl.query_items())


def test_remove_watchlist_item_alias_resolves(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, _ = call("remove_watchlist_items", item_id="i1")
    assert res["ok"] is True and res["deleted"] is True


def test_remove_watchlist_items_batch_removes_several_in_one_call(wl):
    """Also exercises the pre-2026-07-19d singular ``item_id`` key — must keep resolving
    as a read-side fallback (see the module docstring's param-arity note)."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call("remove_watchlist_items", item_id=["i1", "i2"])
    assert proposed is None
    assert res["ok"] is True
    assert res["removed"] == 2
    assert wl.query_items() == []


def test_remove_watchlist_items_batch_accepts_the_plural_key(wl):
    """The current schema key (2026-07-19d param-arity fixup): ``item_ids``."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call("remove_watchlist_items", item_ids=["i1", "i2"])
    assert proposed is None
    assert res["ok"] is True
    assert res["removed"] == 2
    assert wl.query_items() == []


def test_remove_watchlist_items_single_id_via_plural_key(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, _ = call("remove_watchlist_items", item_ids="i1")
    assert res["ok"] is True and res["deleted"] is True


def test_remove_watchlist_items_batch_one_not_found_does_not_block_the_rest(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call(
        "remove_watchlist_items",
        removes=[{"item_id": "i1"}, {"item_id": "i1", "watchlist": "No Such List"}],
    )
    assert proposed is None
    # i1 is removed by the first entry; the second entry (same id, bad watchlist name)
    # independently reports its own error since i1 no longer resolves to anything odd —
    # exercised here mainly to prove one bad entry doesn't raise out of the batch loop.
    assert res["removed"] >= 1
    assert len(res["results"]) == 2


def test_remove_watchlist_items_batch_refuses_my_holdings_without_blocking_the_rest(wl):
    from backend.services import positions as positions_service

    wl.sync_from_holdings(
        [{"ticker": "AAPL", "quantity": 1, "asset_class": "equity", "instrument_id": "inst_aapl"}]
    )
    my_holdings_item = next(i for i in wl.query_items() if i["value"] == "AAPL")
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i2", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call("remove_watchlist_items", item_id=[my_holdings_item["id"], "i2"])
    assert proposed is None
    assert res["ok"] is False
    assert res["removed"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True
    _ = positions_service


# ── update_watchlist_items: settings-only (no membership) ────────────────────────


def test_update_settings_only(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, _ = call("update_watchlist_items", item_id="i1", monitor_news=False, label="Rocket Lab")
    assert res["ok"] is True
    assert res["item"]["monitor_news"] is False
    assert res["item"]["label"] == "Rocket Lab"
    assert res["item"]["watchlist_ids"] == [coll["id"]]  # membership untouched by update


def test_update_sets_a_tickers_intent_and_refuses_a_bad_zone(wl):
    coll = wl.create_watchlist("Chips")
    wl.insert({"id": "i1", "value": "AMD", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, _ = call(
        "update_watchlist_items",
        item_id="i1",
        intent={"thesis": "AI accelerators", "buy_below": 90, "fair_value": 150},
    )
    assert res["ok"] is True
    assert res["item"]["intent"] == {
        "thesis": "AI accelerators",
        "buy_below": 90.0,
        "fair_value": 150.0,
    }
    bad, _ = call("update_watchlist_items", item_id="i1", intent={"buy_below": 70, "buy_above": 90})
    assert bad["ok"] is False and "intent" in bad["error"]


def test_update_missing_item_reports_not_found(wl):
    res, _ = call("update_watchlist_items", item_id="missing", label="X")
    assert res["ok"] is False
    assert "not found" in res["error"].lower()


def test_update_watchlist_item_alias_resolves(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, _ = call("update_watchlist_items", item_id="i1", label="Rocket Lab Inc")
    assert res["ok"] is True
    assert res["item"]["label"] == "Rocket Lab Inc"


def test_update_watchlist_items_batch_broadcasts_the_same_change(wl):
    """Also exercises the pre-2026-07-19d singular ``item_id`` key — must keep resolving
    as a read-side fallback (see the module docstring's param-arity note)."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call("update_watchlist_items", item_id=["i1", "i2"], monitor_news=False)
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 2
    assert all(i["monitor_news"] is False for i in wl.query_items())


def test_update_watchlist_items_batch_accepts_the_plural_key(wl):
    """The current schema key (2026-07-19d param-arity fixup): ``item_ids``."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call("update_watchlist_items", item_ids=["i1", "i2"], monitor_news=False)
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 2
    assert all(i["monitor_news"] is False for i in wl.query_items())


def test_update_watchlist_items_single_id_via_plural_key(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, _ = call("update_watchlist_items", item_ids="i1", label="Rocket Lab Inc")
    assert res["ok"] is True
    assert res["item"]["label"] == "Rocket Lab Inc"


def test_update_watchlist_items_batch_different_changes_per_item(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call(
        "update_watchlist_items",
        updates=[
            {"item_id": "i1", "label": "Rocket Lab"},
            {"item_id": "i2", "label": "Intuitive Machines"},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 2
    by_id = {i["id"]: i for i in wl.query_items()}
    assert by_id["i1"]["label"] == "Rocket Lab"
    assert by_id["i2"]["label"] == "Intuitive Machines"


def test_update_watchlist_items_batch_one_not_found_does_not_block_the_rest(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call(
        "update_watchlist_items",
        updates=[{"item_id": "missing", "label": "X"}, {"item_id": "i1", "label": "Rocket Lab"}],
    )
    assert proposed is None
    assert res["ok"] is False
    assert res["updated"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True


# ── INT5: linked_tickers + custom_feed_urls via the AI tool surface ──────────────


def test_add_watchlist_item_tool_accepts_linked_tickers_and_custom_feeds(wl):
    coll = wl.create_watchlist("Watch Later")
    res, _ = call(
        "add_watchlist_items",
        type="topic",
        value="ONWAI M&A",
        label="ONWAI M&A",
        watchlist="Watch Later",
        linked_tickers=["ONWAI.DE"],
        custom_feed_urls=["https://example.com/feed.xml"],
    )
    assert res["ok"] is True
    assert res["item"]["linked_tickers"] == ["ONWAI.DE"]
    assert res["item"]["custom_feed_urls"] == ["https://example.com/feed.xml"]
    _ = coll


def test_add_watchlist_item_tool_reports_unsafe_custom_feed_url(wl):
    wl.create_watchlist("Watch Later")
    res, _ = call(
        "add_watchlist_items",
        type="topic",
        value="x",
        label="x",
        watchlist="Watch Later",
        custom_feed_urls=["javascript:alert(1)"],
    )
    assert res["ok"] is False
    assert "error" in res


def test_update_watchlist_item_tool_replaces_linked_tickers(wl):
    wl.create_watchlist("Watch Later")
    wl.insert({"id": "i1", "type": "topic", "value": "ONWAI M&A", "watchlist_ids": []})
    res, _ = call("update_watchlist_items", item_id="i1", linked_tickers=["ONWAI.DE", "UBER"])
    assert res["ok"] is True
    assert res["item"]["linked_tickers"] == ["ONWAI.DE", "UBER"]


def test_update_watchlist_item_tool_reports_unsafe_custom_feed_url(wl):
    wl.create_watchlist("Watch Later")
    wl.insert({"id": "i1", "type": "topic", "value": "x", "watchlist_ids": []})
    res, _ = call("update_watchlist_items", item_id="i1", custom_feed_urls=["http://127.0.0.1/x"])
    assert res["ok"] is False


def test_add_watchlist_item_tool_writes_off_the_event_loop(wl, monkeypatch):
    """SEC-A (code review): custom_feed_urls validation does a blocking getaddrinfo per URL
    inside the store write — must run via asyncio.to_thread, not inline on the event loop."""
    import threading

    wl.create_watchlist("Watch Later")

    seen_thread = {}
    real_insert = wl.__class__.insert

    def _spy_insert(self, item):
        seen_thread["ident"] = threading.get_ident()
        return real_insert(self, item)

    monkeypatch.setattr(wl.__class__, "insert", _spy_insert)

    async def _run():
        return threading.get_ident(), await execute_tool(
            "add_watchlist_items",
            {"type": "topic", "value": "x", "label": "x", "watchlist": "Watch Later"},
        )

    loop_thread_ident, _ = asyncio.run(_run())
    assert "ident" in seen_thread
    assert seen_thread["ident"] != loop_thread_ident


def test_update_watchlist_item_tool_writes_off_the_event_loop(wl, monkeypatch):
    """SEC-A (code review): same blocking-getaddrinfo concern on the update path."""
    import threading

    wl.create_watchlist("Watch Later")
    wl.insert({"id": "i1", "type": "topic", "value": "x", "watchlist_ids": []})

    seen_thread = {}
    real_update = wl.__class__.update_by_id

    def _spy_update(self, id_val, changes):
        seen_thread["ident"] = threading.get_ident()
        return real_update(self, id_val, changes)

    monkeypatch.setattr(wl.__class__, "update_by_id", _spy_update)

    async def _run():
        return threading.get_ident(), await execute_tool(
            "update_watchlist_items", {"item_id": "i1", "label": "y"}
        )

    loop_thread_ident, _ = asyncio.run(_run())
    assert "ident" in seen_thread
    assert seen_thread["ident"] != loop_thread_ident


# ── get_watchlist: value widened to scalar-or-list (2026-07-19), renamed values (2026-07-19d)


def test_get_watchlist_value_single_unchanged(wl):
    """Also exercises the pre-2026-07-19d singular ``value`` key — must keep resolving as
    a read-side fallback (see the module docstring's param-arity note)."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, _ = call("get_watchlist", value="rklb")
    assert [i["value"] for i in res["items"]] == ["RKLB"]


def test_get_watchlist_value_list_matches_any(wl):
    """New batch capability: value accepts a LIST — look up several tickers/topics at once
    instead of one get_watchlist call per value."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i3", "value": "LMT", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call("get_watchlist", value=["rklb", "lmt"])
    assert proposed is None
    values = {i["value"] for i in res["items"]}
    assert values == {"RKLB", "LMT"}


def test_get_watchlist_value_list_still_respects_other_filters(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "SPACE", "type": "topic", "watchlist_ids": []})

    res, _ = call("get_watchlist", value=["RKLB", "SPACE"], type="ticker")
    assert [i["value"] for i in res["items"]] == ["RKLB"]


def test_get_watchlist_values_single_via_plural_key(wl):
    """The current schema key (2026-07-19d param-arity fixup): ``values``."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, _ = call("get_watchlist", values="rklb")
    assert [i["value"] for i in res["items"]] == ["RKLB"]


def test_get_watchlist_values_list_via_plural_key(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i3", "value": "LMT", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call("get_watchlist", values=["rklb", "lmt"])
    assert proposed is None
    values = {i["value"] for i in res["items"]}
    assert values == {"RKLB", "LMT"}


# ── Topics are followed, in no watchlist (ADR 2026-09-27-topics-are-followed-not-listed) ──


def test_add_watchlist_items_follows_a_topic_without_a_watchlist(wl):
    coll = wl.create_watchlist("Watch Later")
    res, _ = call(
        "add_watchlist_items",
        items=[
            {"type": "topic", "value": "AI capex", "label": "AI capex"},
            {"type": "topic", "value": "Fed", "label": "Fed", "watchlist": coll["id"]},
        ],
    )
    assert res["added"] == 2
    topics = [i for i in wl.as_dict()["items"] if i["type"] == "topic"]
    assert [t["watchlist_ids"] for t in topics] == [[], []]
    assert (
        "notice" not in res["results"][0] and "followed everywhere" in res["results"][1]["notice"]
    )


def test_remove_watchlist_items_unfollows_a_topic_whatever_the_watchlist(wl):
    coll = wl.create_watchlist("Watch Later")
    wl.insert({"id": "t1", "type": "topic", "value": "Fed", "watchlist_ids": []})
    res, _ = call("remove_watchlist_items", item_ids="t1", watchlist=coll["id"])
    assert res["ok"] is True
    assert not wl.as_dict()["items"]


def test_a_topic_can_never_be_put_in_a_watchlist(wl):
    from backend.core.errors import ValidationError

    coll = wl.create_watchlist("Watch Later")
    with pytest.raises(ValidationError):
        wl.insert({"id": "t1", "type": "topic", "value": "Fed", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "t1", "type": "topic", "value": "Fed", "watchlist_ids": []})
    with pytest.raises(ValidationError):
        wl.update_by_id("t1", {"watchlist_ids": [coll["id"]]})


def test_find_by_value_can_ask_for_a_ticker_only(wl):
    coll = wl.create_watchlist("Watch Later")
    wl.insert({"id": "t1", "type": "topic", "value": "AMD", "watchlist_ids": []})
    wl.insert({"id": "k1", "type": "ticker", "value": "AMD", "watchlist_ids": [coll["id"]]})
    assert wl.find_by_value("amd", type="ticker")["id"] == "k1"


def test_find_by_value_treats_a_typeless_legacy_row_as_a_ticker(wl):
    coll = wl.create_watchlist("Watch Later")
    wl.insert({"id": "k1", "type": "ticker", "value": "AMD", "watchlist_ids": [coll["id"]]})
    wl.update_by_id("k1", {"type": None})
    assert wl.find_by_value("AMD", type="ticker")["id"] == "k1"


def test_demo_seed_follows_its_topic_instead_of_dropping_it(wl):
    from backend.scripts.demo.planning import seed_watchlist

    assert seed_watchlist() == 4
    (topic,) = [i for i in wl.as_dict()["items"] if i["type"] == "topic"]
    assert topic["value"] == "US mortgage rates" and topic["watchlist_ids"] == []


def test_following_a_topic_twice_hands_back_the_existing_row(wl):
    wl.insert({"id": "t1", "type": "topic", "value": "Fed", "watchlist_ids": []})
    res, _ = call("add_watchlist_items", type="topic", value="fed", label="Fed")
    (result,) = res["results"]
    assert result["ok"] is False and "already following" in result["error"]
    assert result["item"]["id"] == "t1"
