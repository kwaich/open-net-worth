"""Dispatcher-level tests for create/update_watchlists name-uniqueness + batch behavior.

A watchlist name is a handle both the UI and Lucius resolve by (resolve_watchlist_id / _strict),
so duplicate names would make name resolution ambiguous. These pin that the tools surface a
clean ok:False on a colliding create or rename rather than silently creating a shadow watchlist.

Batch-pluralized 2026-07-19: create_watchlist -> create_watchlists, update_watchlist ->
update_watchlists, delete_watchlist -> delete_watchlists. The former singular names are kept
as registry aliases (a tool name is persisted data).

Param-arity follow-up (2026-07-19d): delete_watchlists' batch param renamed
watchlist_id -> watchlist_ids (the old key still resolves — read-side fallback, no alias
table needed for a param rename).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool


@pytest.fixture
def wl(db_isolated):
    import backend.db.watchlist_store as wls_mod

    return wls_mod.WatchlistStore()


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── create_watchlists: single-target call ───────────────────────────────────────────


def test_create_collection_tool_rejects_duplicate_name(wl):
    res, _ = call("create_watchlists", name="Space Sector")
    assert res["ok"] is True
    res2, _ = call("create_watchlists", name="space sector")
    assert res2["ok"] is False
    assert "already exists" in res2["error"].lower()
    # Only the one watchlist was created (plus the default).
    assert sum(c["name"].lower() == "space sector" for c in wl.list_watchlists()) == 1


def test_create_collection_tool_distinct_names_ok(wl):
    a, _ = call("create_watchlists", name="Space Sector")
    b, _ = call("create_watchlists", name="Defense")
    assert a["ok"] is True and b["ok"] is True


def test_create_watchlist_alias_resolves(wl):
    """The pre-2026-07-19 singular name must still resolve (via registry alias)."""
    res, _ = call("create_watchlists", name="Alias Sector")
    assert res["ok"] is True
    assert res["watchlist"]["name"] == "Alias Sector"


# ── create_watchlists: batch call ───────────────────────────────────────────────────


def test_create_watchlists_batch_creates_all_independently(wl):
    res, proposed = call(
        "create_watchlists",
        watchlists=[{"name": "High conviction"}, {"name": "Watch later", "description": "d"}],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["created"] == 2
    names = {r["name"] for r in res["results"] if r.get("ok")}
    assert names == {"High conviction", "Watch later"}


def test_create_watchlists_batch_one_collision_does_not_block_the_rest(wl):
    call("create_watchlists", name="Existing")
    res, proposed = call(
        "create_watchlists",
        watchlists=[{"name": "Existing"}, {"name": "Brand New"}],
    )
    assert proposed is None
    assert res["ok"] is False
    assert res["created"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True


# ── update_watchlists: single-target call ───────────────────────────────────────────


def test_update_collection_tool_rejects_rename_collision(wl):
    call("create_watchlists", name="Alpha")
    beta = wl.create_watchlist("Beta")
    res, _ = call("update_watchlists", watchlist_id=beta["id"], name="Alpha")
    assert res["ok"] is False
    assert "already exists" in res["error"].lower()
    # Beta keeps its name.
    names = {c["id"]: c["name"] for c in wl.list_watchlists()}
    assert names[beta["id"]] == "Beta"


def test_update_watchlist_alias_resolves(wl):
    beta = wl.create_watchlist("Beta")
    res, _ = call("update_watchlists", watchlist_id=beta["id"], name="Beta Renamed")
    assert res["ok"] is True
    assert res["watchlist"]["name"] == "Beta Renamed"


# ── update_watchlists: batch call ───────────────────────────────────────────────────


def test_update_watchlists_batch_updates_each_independently(wl):
    a = wl.create_watchlist("A")
    b = wl.create_watchlist("B")
    res, proposed = call(
        "update_watchlists",
        updates=[
            {"watchlist_id": a["id"], "name": "A Renamed"},
            {"watchlist_id": b["id"], "order": 5},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 2
    names = {c["id"]: c["name"] for c in wl.list_watchlists()}
    assert names[a["id"]] == "A Renamed"


def test_update_watchlists_batch_one_not_found_does_not_block_the_rest(wl):
    a = wl.create_watchlist("A")
    res, proposed = call(
        "update_watchlists",
        updates=[
            {"watchlist_id": "ghost", "name": "Nope"},
            {"watchlist_id": a["id"], "name": "A Renamed"},
        ],
    )
    assert proposed is None
    assert res["ok"] is False
    assert res["updated"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True


# ── delete_watchlists: single-target + batch + alias ────────────────────────────────


def test_delete_collection_description_is_accurate():
    """Regression: the delete description must not falsely promise 'nothing is lost' (items that
    lived only in the deleted watchlist ARE removed), and must name the default correctly."""
    from backend.ai.tools import TOOL_SCHEMAS

    desc = {t["name"]: t for t in TOOL_SCHEMAS}["delete_watchlists"]["description"].lower()
    assert "nothing is lost" not in desc
    assert "'watchlist'" not in desc  # the default is named "My holdings", not "Watchlist"
    assert "my holdings" in desc


def test_delete_watchlist_alias_resolves(wl):
    coll = wl.create_watchlist("Throwaway")
    res, _ = call("delete_watchlists", watchlist_id=coll["id"])
    assert res["ok"] is True
    assert res["deleted"] == coll["id"]


def test_delete_watchlists_batch_deletes_each_independently(wl):
    """Also exercises the pre-2026-07-19d singular ``watchlist_id`` key — must keep
    resolving as a read-side fallback (see the module docstring's param-arity note)."""
    a = wl.create_watchlist("A")
    b = wl.create_watchlist("B")
    res, proposed = call("delete_watchlists", watchlist_id=[a["id"], b["id"]])
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted_count"] == 2
    remaining_ids = {c["id"] for c in wl.list_watchlists()}
    assert a["id"] not in remaining_ids and b["id"] not in remaining_ids


def test_delete_watchlists_batch_accepts_the_plural_key(wl):
    """The current schema key (2026-07-19d param-arity fixup): ``watchlist_ids``."""
    a = wl.create_watchlist("A")
    b = wl.create_watchlist("B")
    res, proposed = call("delete_watchlists", watchlist_ids=[a["id"], b["id"]])
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted_count"] == 2
    remaining_ids = {c["id"] for c in wl.list_watchlists()}
    assert a["id"] not in remaining_ids and b["id"] not in remaining_ids


def test_delete_watchlists_single_id_via_plural_key(wl):
    coll = wl.create_watchlist("Throwaway")
    res, _ = call("delete_watchlists", watchlist_ids=coll["id"])
    assert res["ok"] is True
    assert res["deleted"] == coll["id"]


def test_delete_watchlists_batch_refuses_my_holdings_without_blocking_the_rest(wl):
    from backend.db.watchlist_store import MY_HOLDINGS_ID

    a = wl.create_watchlist("A")
    res, proposed = call("delete_watchlists", watchlist_ids=[MY_HOLDINGS_ID, a["id"]])
    assert proposed is None
    assert res["ok"] is False
    assert res["deleted_count"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True
