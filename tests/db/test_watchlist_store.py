"""WatchlistStore — a per-book SINGLETON on the `watchlists` table (plan 4b, task 3),
hand-rolled like PlanStateStore (db/plan_state_store.py), NOT a JsonStore/PayloadTableStore
subclass. PK is book_id; payload is the whole {watchlists, items, global_config} dict.

Moved here from the old JsonStore-era tests/test_watchlist_store.py (fixture rewired from a
tmp_path/monkeypatch-WATCHLIST_JSON file to `db_isolated` — every domain assertion below is
UNCHANGED) plus new coverage: the singleton round trip, SSRF validation preserved, the raw
row's true-singleton shape, and checkpoint-INCLUDED /rewind coverage (unlike asset_brief,
WATCHLIST_JSON was never in ai.checkpoint._EXCLUDED_PATHS — this migration preserves that).
"""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.watchlist_store import MY_HOLDINGS_ID, WatchlistStore, _ensure_shape

# ── Fixtures ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def store(db_isolated):
    """Isolated WatchlistStore backed by a fresh per-test database."""
    return WatchlistStore()


# ── _ensure_shape — normalization (no migration; single on-disk shape) ───────────


def test_ensure_shape_seeds_default_watchlist_on_empty():
    result = _ensure_shape({})
    assert result["watchlists"][0]["id"] == MY_HOLDINGS_ID
    assert result["watchlists"][0]["name"] == "My holdings"
    assert result["items"] == []
    assert result["global_config"] == {}


def test_ensure_shape_total_on_non_dict():
    result = _ensure_shape(None)  # type: ignore[arg-type]
    assert "watchlists" in result and result["items"] == []


def test_ensure_shape_idempotent_on_seeded_data():
    import copy

    seeded = {
        "watchlists": [{"id": MY_HOLDINGS_ID, "name": "My holdings"}],
        "items": [{"id": "i1", "watchlist_ids": [MY_HOLDINGS_ID]}],
        "global_config": {"enabled": True},
    }
    before = copy.deepcopy(seeded)
    assert _ensure_shape(seeded) == before


# ── WatchlistStore — fresh-store seeding ─────────────────────────────────────────


def test_fresh_store_seeds_default_and_persists_once(db_isolated, monkeypatch):
    """A brand-new (no row) store seeds the my_holdings watchlist and persists exactly
    once — proven against the raw table (the DB replacement for the old raw-file read)."""
    saves: list = []
    original_save = WatchlistStore._save_raw

    def counting_save(self, raw):
        saves.append(1)
        original_save(self, raw)

    monkeypatch.setattr(WatchlistStore, "_save_raw", counting_save)

    s = WatchlistStore()
    assert [w["id"] for w in s.list_watchlists()] == [MY_HOLDINGS_ID]
    s.list_watchlists()  # already seeded → no further save
    s.get_config()
    assert saves == [1]

    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import DEFAULT_BOOK_ID, watchlists

    with get_engine().connect() as conn:
        row = conn.execute(
            select(watchlists).where(watchlists.c.book_id == DEFAULT_BOOK_ID)
        ).first()
    assert row is not None
    assert "watchlists" in row.payload and "collections" not in row.payload


# ── WatchlistStore — collection CRUD ─────────────────────────────────────────────


def test_list_watchlists_default_exists(store):
    colls = store.list_watchlists()
    assert any(c["id"] == MY_HOLDINGS_ID for c in colls)


def test_create_watchlist_roundtrip(store):
    coll = store.create_watchlist("High Conviction", description="Best ideas")
    assert coll["name"] == "High Conviction"
    assert coll["id"] != MY_HOLDINGS_ID
    assert coll["kind"] == "manual"
    colls = store.list_watchlists()
    ids = [c["id"] for c in colls]
    assert coll["id"] in ids


def test_create_watchlist_order_increments(store):
    c1 = store.create_watchlist("List A")
    c2 = store.create_watchlist("List B")
    assert c2["order"] > c1["order"]


def test_update_watchlist_name(store):
    coll = store.create_watchlist("Old Name")
    updated = store.update_watchlist(coll["id"], {"name": "New Name"})
    assert updated["name"] == "New Name"
    # Persisted.
    colls = {c["id"]: c for c in store.list_watchlists()}
    assert colls[coll["id"]]["name"] == "New Name"


def test_collection_muted_defaults_false_and_round_trips(store):
    coll = store.create_watchlist("Geopolitics")
    assert coll["muted"] is False
    updated = store.update_watchlist(coll["id"], {"muted": True})
    assert updated["muted"] is True
    # Persisted across a fresh read.
    colls = {c["id"]: c for c in store.list_watchlists()}
    assert colls[coll["id"]]["muted"] is True
    # Default collection (and any legacy row) always surfaces the field.
    assert all("muted" in c for c in store.list_watchlists())


def test_update_watchlist_not_found_raises(store):
    with pytest.raises(NotFoundError):
        store.update_watchlist("nonexistent-id", {"name": "X"})


def test_delete_default_raises_conflict(store):
    with pytest.raises(ConflictError):
        store.delete_watchlist(MY_HOLDINGS_ID)


def test_delete_non_default_removes_items_from_list(store):
    coll = store.create_watchlist("Watch Later")
    # Insert i1 only in the new collection; i2 only in my_holdings (via sync, so skip direct insert);
    # i3 in both. We create i2 via a user watchlist since my_holdings is locked for direct inserts.
    user_wl = store.create_watchlist("Temp")
    store.insert({"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": [coll["id"]]})
    store.insert({"id": "i2", "value": "TSLA", "type": "ticker", "watchlist_ids": [user_wl["id"]]})
    store.insert(
        {
            "id": "i3",
            "value": "NVDA",
            "type": "ticker",
            "watchlist_ids": [user_wl["id"], coll["id"]],
        }
    )

    result = store.delete_watchlist(coll["id"])
    assert result["deleted"] == coll["id"]
    # i1 had only this list → deleted; i3 had two lists → trimmed (counted as reassigned).
    assert result["reassigned_count"] == 1

    items = store.query_items()
    by_id = {i["id"]: i for i in items}
    # i1 is gone (only belonged to the deleted collection).
    assert "i1" not in by_id
    # i2 is untouched.
    assert by_id["i2"]["watchlist_ids"] == [user_wl["id"]]
    # i3 has coll["id"] removed from its list_ids.
    assert coll["id"] not in by_id["i3"]["watchlist_ids"]
    assert user_wl["id"] in by_id["i3"]["watchlist_ids"]


def test_delete_not_found_raises(store):
    with pytest.raises(NotFoundError):
        store.delete_watchlist("nonexistent-id")


# ── WatchlistStore — collection name uniqueness ──────────────────────────────────


def test_create_watchlist_duplicate_name_raises(store):
    store.create_watchlist("High Conviction")
    with pytest.raises(ConflictError, match="already exists"):
        store.create_watchlist("High Conviction")


def test_create_watchlist_duplicate_name_case_insensitive_raises(store):
    store.create_watchlist("High Conviction")
    with pytest.raises(ConflictError, match="already exists"):
        store.create_watchlist("high conviction")


def test_create_watchlist_duplicate_name_whitespace_insensitive_raises(store):
    store.create_watchlist("High Conviction")
    with pytest.raises(ConflictError, match="already exists"):
        store.create_watchlist("  High Conviction  ")


def test_create_watchlist_colliding_with_default_name_raises(store):
    # The default collection is named "My holdings"; a second one is refused.
    with pytest.raises(ConflictError, match="already exists"):
        store.create_watchlist("My holdings")


def test_create_watchlist_distinct_names_ok(store):
    a = store.create_watchlist("List A")
    b = store.create_watchlist("List B")
    assert a["id"] != b["id"]


def test_update_watchlist_rename_to_existing_name_raises(store):
    store.create_watchlist("Alpha")
    beta = store.create_watchlist("Beta")
    with pytest.raises(ConflictError, match="already exists"):
        store.update_watchlist(beta["id"], {"name": "Alpha"})


def test_update_watchlist_rename_collision_case_insensitive(store):
    store.create_watchlist("Alpha")
    beta = store.create_watchlist("Beta")
    with pytest.raises(ConflictError, match="already exists"):
        store.update_watchlist(beta["id"], {"name": "alpha"})


def test_update_watchlist_rename_to_own_name_ok(store):
    """Renaming a collection to its own current name (or a case variant) is not a collision."""
    alpha = store.create_watchlist("Alpha")
    updated = store.update_watchlist(alpha["id"], {"name": "alpha"})
    assert updated["name"] == "alpha"


def test_update_watchlist_rename_no_collision_ok(store):
    alpha = store.create_watchlist("Alpha")
    updated = store.update_watchlist(alpha["id"], {"name": "Gamma"})
    assert updated["name"] == "Gamma"


def test_update_watchlist_description_only_does_not_trip_uniqueness(store):
    """A non-rename patch on an existing collection never trips the name guard."""
    alpha = store.create_watchlist("Alpha")
    updated = store.update_watchlist(alpha["id"], {"description": "best ideas"})
    assert updated["description"] == "best ideas"
    assert updated["name"] == "Alpha"


# ── WatchlistStore — item validation ─────────────────────────────────────────────


def test_insert_item_with_empty_watchlist_ids_raises_validation_error(store):
    """ADR 0018: insert with no watchlist_ids raises ValidationError (no silent default)."""
    item = {"id": "i1", "value": "AAPL", "type": "ticker"}
    with pytest.raises(ValidationError, match="watchlist_ids is required"):
        store.insert(item)


def test_insert_item_into_my_holdings_raises_conflict(store):
    """ADR 0018: inserting directly into my_holdings raises ConflictError (it is locked)."""
    item = {"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": [MY_HOLDINGS_ID]}
    with pytest.raises(ConflictError, match="auto-managed"):
        store.insert(item)


def test_insert_item_unknown_list_id_in_list_ids_raises(store):
    with pytest.raises(ValidationError, match="watchlist_id"):
        store.insert(
            {"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": ["nonexistent"]}
        )


def test_update_by_id_unknown_list_ids_raises(store):
    # Insert into a user watchlist first (my_holdings is locked).
    coll = store.create_watchlist("My List")
    store.insert({"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": [coll["id"]]})
    with pytest.raises(ValidationError, match="watchlist_id"):
        store.update_by_id("i1", {"watchlist_ids": ["nonexistent"]})


# ── WatchlistStore — custom_feed_urls / linked_tickers SSRF + shape validation ───


def test_insert_custom_feed_urls_rejects_private_ip_target(store):
    """assert_public_url still guards the store's write choke (owner directive 2026-07-15) —
    the single choke both REST and every AI-tool write pass through, now DB-backed."""
    tech = store.create_watchlist("Tech")
    with pytest.raises(ValidationError):
        store.insert(
            {
                "id": "wli_x",
                "value": "AAPL",
                "type": "ticker",
                "watchlist_ids": [tech["id"]],
                "custom_feed_urls": ["http://127.0.0.1/feed"],
            }
        )


def test_insert_custom_feed_urls_accepts_public_https(store):
    tech = store.create_watchlist("Tech")
    item = store.insert(
        {
            "id": "wli_y",
            "value": "AAPL",
            "type": "ticker",
            "watchlist_ids": [tech["id"]],
            "custom_feed_urls": ["https://example.com/feed.xml"],
        }
    )
    assert item["custom_feed_urls"] == ["https://example.com/feed.xml"]


# ── WatchlistStore — sync_from_holdings stamps my_holdings ───────────────────────


def test_sync_from_holdings_stamps_list_ids_my_holdings(store):
    """sync_from_holdings adds owned tickers to my_holdings."""
    rows = [
        {
            "ticker": "NVDA",
            "quantity": 10,
            "label": "NVIDIA",
            "asset_class": "equity",
            "instrument_id": "inst-nvda",
        }
    ]
    added = store.sync_from_holdings(rows)
    assert "NVDA" in added
    items = store.query_items()
    assert items[0]["watchlist_ids"] == [MY_HOLDINGS_ID]
    assert items[0]["instrument_id"] == "inst-nvda"


def test_sync_from_holdings_adds_my_holdings_to_existing_multi_list_item(store):
    """Existing item in a non-default list gets 'my_holdings' added to its list_ids."""
    coll = store.create_watchlist("High Conviction")
    store.insert({"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": [coll["id"]]})
    rows = [
        {
            "ticker": "AAPL",
            "quantity": 5,
            "label": "Apple",
            "asset_class": "equity",
            "instrument_id": "",
        }
    ]
    added = store.sync_from_holdings(rows)
    assert "AAPL" not in added  # not newly added
    items = store.query_items()
    aapl = next(i for i in items if i["value"] == "AAPL")
    assert MY_HOLDINGS_ID in aapl["watchlist_ids"]
    assert coll["id"] in aapl["watchlist_ids"]


def test_sync_from_holdings_stamps_instrument_id_on_existing(store):
    # Insert existing item WITHOUT instrument_id into a user watchlist (my_holdings is locked).
    coll = store.create_watchlist("Tech")
    store.insert({"id": "i-nvda", "value": "NVDA", "type": "ticker", "watchlist_ids": [coll["id"]]})
    # Now sync with instrument_id and positive quantity
    rows = [
        {
            "ticker": "NVDA",
            "quantity": 5,
            "label": "NVIDIA",
            "asset_class": "equity",
            "instrument_id": "inst-nvda",
        }
    ]
    added = store.sync_from_holdings(rows)
    # NVDA already exists, not re-added
    assert "NVDA" not in added
    # But instrument_id should be stamped
    items = store.query_items()
    nvda = next(i for i in items if i["value"] == "NVDA")
    assert nvda["instrument_id"] == "inst-nvda"


# ── WatchlistStore — remove_from_watchlist ───────────────────────────────────────────


def test_remove_from_watchlist_drops_one_list_keeps_item(store):
    """remove_from_watchlist with one of two lists removes that list but keeps the item."""
    coll = store.create_watchlist("High Conviction")
    user_wl = store.create_watchlist("Other")
    store.insert(
        {
            "id": "i1",
            "value": "AAPL",
            "type": "ticker",
            "watchlist_ids": [user_wl["id"], coll["id"]],
        }
    )
    result = store.remove_from_watchlist("i1", coll["id"])
    assert result == {"removed": True, "deleted": False}
    items = store.query_items()
    aapl = next((i for i in items if i["id"] == "i1"), None)
    assert aapl is not None
    assert aapl["watchlist_ids"] == [user_wl["id"]]
    assert coll["id"] not in aapl["watchlist_ids"]


def test_remove_from_watchlist_last_list_deletes_item(store):
    """remove_from_watchlist when it's the only list deletes the item entirely."""
    coll = store.create_watchlist("Only List")
    store.insert({"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": [coll["id"]]})
    result = store.remove_from_watchlist("i1", coll["id"])
    assert result == {"removed": True, "deleted": True}
    items = store.query_items()
    assert not any(i["id"] == "i1" for i in items)


def test_remove_from_watchlist_my_holdings_raises_conflict(store):
    """ADR 0018: remove_from_watchlist with my_holdings raises ConflictError."""
    # Seed a my_holdings item via sync.
    store.sync_from_holdings(
        [
            {
                "ticker": "AAPL",
                "quantity": 5,
                "label": "Apple",
                "asset_class": "equity",
                "instrument_id": "",
            }
        ]
    )
    items = store.query_items()
    aapl = next(i for i in items if i["value"] == "AAPL")
    with pytest.raises(ConflictError, match="auto-managed"):
        store.remove_from_watchlist(aapl["id"], MY_HOLDINGS_ID)


def test_remove_from_watchlist_not_in_list_returns_not_removed(store):
    """remove_from_watchlist when item isn't in the specified list returns removed=False."""
    coll = store.create_watchlist("Other")
    other = store.create_watchlist("Source")
    store.insert({"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": [other["id"]]})
    result = store.remove_from_watchlist("i1", coll["id"])
    assert result == {"removed": False, "deleted": False}
    # Item is unchanged.
    items = store.query_items()
    assert any(i["id"] == "i1" for i in items)


# ── WatchlistStore — is_locked_watchlist ─────────────────────────────────────────


def test_is_locked_watchlist_true_for_my_holdings(store):
    """ADR 0018: my_holdings is locked."""
    assert store.is_locked_watchlist(MY_HOLDINGS_ID) is True


def test_is_locked_watchlist_false_for_user_watchlist(store):
    """ADR 0018: user-created watchlists are not locked."""
    coll = store.create_watchlist("High Conviction")
    assert store.is_locked_watchlist(coll["id"]) is False


# ── WatchlistStore — sync prune semantics (ADR 0018) ─────────────────────────────


def test_sync_prune_removes_non_owned_from_my_holdings(store):
    """A ticker in my_holdings with quantity=0 (not owned) is pruned on sync."""
    # Seed a my_holdings item via an initial sync.
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
    assert any(i["value"] == "GRAB" for i in items)

    # Now sync with GRAB not owned (empty rows = sold).
    store.sync_from_holdings([])
    items = store.query_items()
    assert not any(i["value"] == "GRAB" for i in items)


def test_sync_prune_preserves_user_watchlist_membership(store):
    """A pruned my_holdings ticker that also belongs to a user watchlist keeps user membership."""
    user_wl = store.create_watchlist("Keep Me")
    # Seed with GRAB owned and in a user list.
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
    # Manually link GRAB to user watchlist too.
    items = store.query_items()
    grab = next(i for i in items if i["value"] == "GRAB")
    store.update_by_id(grab["id"], {"watchlist_ids": [MY_HOLDINGS_ID, user_wl["id"]]})

    # Sell GRAB: sync with empty rows.
    store.sync_from_holdings([])
    items = store.query_items()
    grab_after = next((i for i in items if i["value"] == "GRAB"), None)
    # Item survives because it's still in user_wl.
    assert grab_after is not None
    # my_holdings membership is removed.
    assert MY_HOLDINGS_ID not in grab_after["watchlist_ids"]
    assert user_wl["id"] in grab_after["watchlist_ids"]


def test_sync_zero_quantity_does_not_count_as_owned(store):
    """A row with quantity=0 is not treated as owned and won't be added to my_holdings."""
    rows = [
        {
            "ticker": "SOLD",
            "quantity": 0,
            "label": "Sold Stock",
            "asset_class": "equity",
            "instrument_id": "",
        }
    ]
    added = store.sync_from_holdings(rows)
    assert "SOLD" not in added
    items = store.query_items()
    assert not any(i["value"] == "SOLD" for i in items)


def test_sync_newly_owned_ticker_is_added(store):
    """A ticker with quantity > 0 that doesn't have a watchlist item yet is newly added."""
    rows = [
        {
            "ticker": "NEW",
            "quantity": 10,
            "label": "New Corp",
            "asset_class": "equity",
            "instrument_id": "inst-new",
        }
    ]
    added = store.sync_from_holdings(rows)
    assert "NEW" in added
    items = store.query_items()
    new_item = next((i for i in items if i["value"] == "NEW"), None)
    assert new_item is not None
    assert MY_HOLDINGS_ID in new_item["watchlist_ids"]


# ── WatchlistStore — as_dict back-compat ─────────────────────────────────────────


def test_as_dict_includes_collections_and_config(store):
    d = store.as_dict()
    assert "items" in d
    assert "config" in d  # back-compat key
    assert "watchlists" in d  # new additive key
    assert isinstance(d["watchlists"], list)


# ── Singleton shape (plan 4b task 3 — the brief's own worked example) ────────────


def test_watchlist_singleton_round_trip_and_default_seed(db_isolated):
    from backend.db.watchlist_store import MY_HOLDINGS_ID, WatchlistStore

    s = WatchlistStore()
    assert any(wl["id"] == MY_HOLDINGS_ID for wl in s.list_watchlists())  # seeded on first read
    wl = s.create_watchlist("Tech")
    assert s.update_watchlist(wl["id"], {"description": "d"})["description"] == "d"


def test_watchlist_ssrf_validation_preserved(db_isolated):
    from backend.core.errors import ValidationError
    from backend.db.watchlist_store import WatchlistStore

    s = WatchlistStore()
    tech = s.create_watchlist("Tech")
    with pytest.raises(ValidationError):  # assert_public_url still guards
        s.insert(
            {
                "id": "wli_x",
                "value": "AAPL",
                "watchlist_ids": [tech["id"]],
                "custom_feed_urls": ["http://127.0.0.1/feed"],
            }
        )


def test_watchlist_row_is_a_true_per_book_singleton(db_isolated):
    """Regardless of how many watchlist/item writes happen, exactly ONE row ever exists,
    keyed book_id=DEFAULT_BOOK_ID — proven against the raw table, mirroring
    tests/db/test_plan_state_store.py::test_plan_state_row_is_a_true_per_book_singleton."""
    from sqlalchemy import select

    from backend.db.tables import DEFAULT_BOOK_ID, watchlists

    s = WatchlistStore()
    a = s.create_watchlist("A")
    s.create_watchlist("B")
    s.insert({"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": [a["id"]]})

    from backend.db.engine import get_engine

    with get_engine().connect() as conn:
        rows = list(conn.execute(select(watchlists)))
    assert len(rows) == 1
    assert rows[0].book_id == DEFAULT_BOOK_ID
    assert len(rows[0].payload["watchlists"]) == 3  # my_holdings + A + B
    assert len(rows[0].payload["items"]) == 1


# ── Checkpoint-INCLUDED /rewind coverage (preserves today's behavior) ────────────


def test_watchlist_edit_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    """WATCHLIST_JSON was never in ai.checkpoint._EXCLUDED_PATHS — a /rewind reverting a
    watchlist edit is EXISTING behavior this migration must preserve (unlike asset_brief,
    which is deliberately excluded). Mirrors
    tests/db/test_plan_state_store.py::test_baseline_edit_is_checkpoint_covered_and_rewind_reverts."""
    from backend.ai import checkpoint as cp

    store = WatchlistStore()
    coll = store.create_watchlist("Before turn")  # pre-turn, outside any checkpoint

    ctx, token = cp.begin_turn("s1", 0, "rename watchlist")
    try:
        store.update_watchlist(coll["id"], {"name": "After turn"})
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "watchlists" in entry["db_tables"]
    assert store.list_watchlists()[-1]["name"] == "After turn"

    cp.restore(entry["id"], mode="data")
    reverted = {wl["id"]: wl for wl in store.list_watchlists()}
    assert reverted[coll["id"]]["name"] == "Before turn"
