"""Honest no-op guard for watchlist.py's update tools (2026-07-21 harness reliability pass).

update_watchlists / update_watchlist_items / update_monitoring_config all rewrite their
backing store UNCONDITIONALLY once the target id resolves (or, for update_monitoring_config,
whenever the tool is called at all) — an id-only call with no other changeable field used to
return a plain ok:true success shape byte-indistinguishable from a real edit, so the model
would report "Done" for a write that changed nothing (the update_transactions incident this
mirrors, see ai/tools/_shared.py::no_op_write_result).

Each tool below is checked for:
  * a single-target call with no changeable field -> no_op:true, entity unchanged, no store
    write actually landed
  * an unknown id with no changeable field -> the EXISTING not-found error (a no-op guard
    must never paper over "this doesn't exist")
  * a batch mixing a no-op item with a real edit -> the real edit still lands, the no-op is
    flagged per-row, and the batch's own count excludes the no-op
  * a REAL single-field edit -> unchanged behavior/shape (regression pin)
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── update_watchlists ────────────────────────────────────────────────────────────────


@pytest.fixture
def wl(db_isolated):
    import backend.db.watchlist_store as wls_mod

    return wls_mod.WatchlistStore()


def test_update_watchlists_id_only_is_no_op(wl):
    beta = wl.create_watchlist("Beta")
    res, proposed = call("update_watchlists", watchlist_id=beta["id"])
    assert proposed is None
    assert res["ok"] is True
    assert res["no_op"] is True
    assert "message" in res
    assert res["watchlist"]["name"] == "Beta"
    # Nothing was rewritten.
    assert wl.list_watchlists()[-1]["name"] == "Beta"


def test_update_watchlists_id_only_unknown_id_still_not_found(wl):
    res, _ = call("update_watchlists", watchlist_id="ghost")
    assert res["ok"] is False
    assert "no_op" not in res
    assert "not found" in res["error"].lower()


def test_update_watchlists_batch_no_op_item_excluded_from_updated_count(wl):
    a = wl.create_watchlist("A")
    b = wl.create_watchlist("B")
    res, proposed = call(
        "update_watchlists",
        updates=[
            {"watchlist_id": a["id"]},  # no-op: only the id
            {"watchlist_id": b["id"], "name": "B Renamed"},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 1  # the no-op does NOT count as "updated"
    assert res["no_op_count"] == 1
    assert res["results"][0]["no_op"] is True
    assert res["results"][1]["ok"] is True and "no_op" not in res["results"][1]
    names = {c["id"]: c["name"] for c in wl.list_watchlists()}
    assert names[b["id"]] == "B Renamed"
    assert names[a["id"]] == "A"  # untouched


def test_update_watchlists_real_single_edit_unchanged_shape(wl):
    """Regression: a real single-field edit keeps its pre-fix shape/behavior."""
    beta = wl.create_watchlist("Beta")
    res, proposed = call("update_watchlists", watchlist_id=beta["id"], name="Beta Renamed")
    assert proposed is None
    assert res["ok"] is True
    assert "no_op" not in res
    assert res["watchlist"]["name"] == "Beta Renamed"
    assert wl.list_watchlists()[-1]["name"] == "Beta Renamed"


# ── update_watchlist_items ───────────────────────────────────────────────────────────


def test_update_watchlist_items_id_only_is_no_op(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert(
        {
            "id": "i1",
            "value": "RKLB",
            "type": "ticker",
            "label": "Rocket Lab",
            "watchlist_ids": [coll["id"]],
        }
    )
    res, proposed = call("update_watchlist_items", item_ids="i1")
    assert proposed is None
    assert res["ok"] is True
    assert res["no_op"] is True
    assert res["item"]["label"] == "Rocket Lab"
    # No store write landed.
    assert wl.find_by_id("i1")["label"] == "Rocket Lab"


def test_update_watchlist_items_id_only_unknown_id_still_not_found(wl):
    res, _ = call("update_watchlist_items", item_ids="missing")
    assert res["ok"] is False
    assert "no_op" not in res
    assert "not found" in res["error"].lower()


def test_update_watchlist_items_batch_no_op_item_excluded_from_updated_count(wl):
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    wl.insert({"id": "i2", "value": "LUNR", "type": "ticker", "watchlist_ids": [coll["id"]]})

    res, proposed = call(
        "update_watchlist_items",
        updates=[
            {"item_id": "i1"},  # no-op: only the id
            {"item_id": "i2", "label": "Intuitive Machines"},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 1
    assert res["no_op_count"] == 1
    assert res["results"][0]["no_op"] is True
    assert res["results"][1]["ok"] is True and "no_op" not in res["results"][1]
    by_id = {i["id"]: i for i in wl.query_items()}
    assert by_id["i2"]["label"] == "Intuitive Machines"


def test_update_watchlist_items_real_single_edit_unchanged_shape(wl):
    """Regression: a real single-field edit keeps its pre-fix shape/behavior."""
    coll = wl.create_watchlist("Space Sector")
    wl.insert({"id": "i1", "value": "RKLB", "type": "ticker", "watchlist_ids": [coll["id"]]})
    res, proposed = call("update_watchlist_items", item_ids="i1", label="Rocket Lab Inc")
    assert proposed is None
    assert res["ok"] is True
    assert "no_op" not in res
    assert res["item"]["label"] == "Rocket Lab Inc"


# ── update_monitoring_config ─────────────────────────────────────────────────────────


@pytest.fixture
def cfg_isolate(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr("backend.scheduler.reschedule", lambda cfg: None, raising=False)
    return tmp_path


def test_update_monitoring_config_empty_input_is_no_op(cfg_isolate, monkeypatch):
    import backend.db.watchlist_store as wl_store_mod

    def _boom(self, changes):
        raise AssertionError("update_config must not be called for an empty patch")

    monkeypatch.setattr(wl_store_mod.WatchlistStore, "update_config", _boom)

    from backend.ai.tools.watchlist import update_monitoring_config

    result_json, proposed = asyncio.run(update_monitoring_config({}))
    result = json.loads(result_json)
    assert proposed is None
    assert result["ok"] is True
    assert result["no_op"] is True
    assert "config" in result


def test_update_monitoring_config_unknown_keys_only_is_no_op(cfg_isolate, monkeypatch):
    """No if-key-in-input gate matches -> changes stays empty even though input_data is
    non-empty (a nonsense/unrecognized key)."""
    import backend.db.watchlist_store as wl_store_mod

    def _boom(self, changes):
        raise AssertionError("update_config must not be called for an empty patch")

    monkeypatch.setattr(wl_store_mod.WatchlistStore, "update_config", _boom)

    from backend.ai.tools.watchlist import update_monitoring_config

    result_json, _ = asyncio.run(update_monitoring_config({"bogus_key": 123}))
    result = json.loads(result_json)
    assert result["ok"] is True
    assert result["no_op"] is True


def test_update_monitoring_config_real_field_unchanged_shape(cfg_isolate):
    """Regression: a real field still writes and returns its normal shape."""
    from backend.ai.tools.watchlist import update_monitoring_config

    result_json, proposed = asyncio.run(update_monitoring_config({"web_search_enabled": False}))
    result = json.loads(result_json)
    assert proposed is None
    assert result["ok"] is True
    assert "no_op" not in result
    assert result["config"]["web_search_enabled"] is False
