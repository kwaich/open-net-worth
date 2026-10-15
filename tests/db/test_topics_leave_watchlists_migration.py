"""Revisions 202609271200 (now a no-op) + 202609271300 — topics leave watchlists
(ADR 2026-09-27-topics-are-followed-not-listed).

Runs the chain for REAL (``command.upgrade`` from 1200's parent through 1300 on an isolated
tmp-path DB) against hand-seeded pre-migration payloads. Synthetic data only.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_BEFORE = "202609231130_pending_position_fmv_basis"
_AFTER = "202609271300_topics_leave_watchlists"


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _BEFORE)
    engine = create_engine(url)
    yield url, engine
    engine.dispose()


def _list(wid: str, name: str, *, auto: bool = False) -> dict:
    return {"id": wid, "name": name, "is_auto_sync": auto, "kind": "manual", "order": 0}


def _item(iid: str, type_: str, value: str, lists: list[str]) -> dict:
    return {"id": iid, "type": type_, "value": value, "watchlist_ids": lists, "note": "keep me"}


def _migrate(db, lists: list[dict], items: list[dict]) -> dict:
    return _migrate_raw(
        db, {"watchlists": lists, "items": items, "global_config": {"enabled": True}}
    )


def _migrate_raw(db, payload: object) -> dict:
    url, engine = db
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM watchlists WHERE book_id = 'default'"))
        conn.execute(
            text("INSERT INTO watchlists (book_id, payload) VALUES ('default', :p)"),
            {"p": json.dumps(payload)},
        )
    command.upgrade(make_config(url), _AFTER)
    with engine.begin() as conn:
        raw = conn.execute(
            text("SELECT payload FROM watchlists WHERE book_id = 'default'")
        ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def test_topics_are_detached_and_tickers_keep_their_lists(db):
    after = _migrate(
        db,
        [_list("my_holdings", "My holdings", auto=True), _list("wl_us", "US")],
        [
            _item("t1", "topic", "US mortgage rates", ["wl_us"]),
            _item("c1", "company", "SpaceX", ["wl_us"]),
            _item("k1", "ticker", "AMD", ["wl_us"]),
        ],
    )
    by_id = {i["id"]: i for i in after["items"]}
    assert by_id["t1"]["watchlist_ids"] == [] and by_id["c1"]["watchlist_ids"] == []
    assert by_id["t1"]["note"] == "keep me"
    assert by_id["k1"]["watchlist_ids"] == ["wl_us"]
    assert [w["id"] for w in after["watchlists"]] == ["my_holdings", "wl_us"]
    assert after["global_config"] == {"enabled": True}


@pytest.mark.parametrize("name", ["Macro & topics", "Intelligence", " macro and topics "])
def test_the_topics_only_list_is_deleted_once_empty(db, name):
    after = _migrate(
        db,
        [_list("wl_us", "US"), _list("wl_t", name)],
        [_item("t1", "topic", "Fed", ["wl_t"])],
    )
    assert [w["id"] for w in after["watchlists"]] == ["wl_us"]
    assert after["items"][0]["watchlist_ids"] == []


def test_a_topics_named_list_that_holds_tickers_is_kept(db):
    after = _migrate(
        db,
        [_list("wl_t", "Intelligence")],
        [_item("t1", "topic", "Fed", ["wl_t"]), _item("k1", "ticker", "PLTR", ["wl_t"])],
    )
    assert [w["name"] for w in after["watchlists"]] == ["Intelligence"]


def test_an_empty_ordinary_list_is_untouched(db):
    after = _migrate(db, [_list("wl_new", "Semis")], [])
    assert [w["id"] for w in after["watchlists"]] == ["wl_new"]


def test_a_macro_and_topics_list_that_holds_tickers_gets_its_old_name_back(db):
    """1200 once renamed "Intelligence" → "Macro & topics"; a kept ticker list under that name
    would sit beside the Topics & macro tab looking like it."""
    after = _migrate(
        db,
        [_list("wl_t", "Macro & topics")],
        [_item("t1", "topic", "Fed", ["wl_t"]), _item("k1", "ticker", "PLTR", ["wl_t"])],
    )
    assert [w["name"] for w in after["watchlists"]] == ["Intelligence"]


def test_duplicate_topics_from_two_lists_merge_into_one_followed_row(db):
    first = _item("t1", "topic", "US mortgage rates", ["wl_a"])
    first["search_queries"] = ["30y fixed rate"]
    dup = _item("t2", "topic", " us mortgage RATES ", ["wl_b"])
    dup.update(search_queries=["30y fixed rate", "Fed mortgage"], note="refi watch")
    after = _migrate(db, [_list("wl_a", "US"), _list("wl_b", "SG")], [first, dup])
    (topic,) = after["items"]
    assert topic["id"] == "t1" and topic["watchlist_ids"] == []
    assert topic["search_queries"] == ["30y fixed rate", "Fed mortgage"]
    assert topic["note"] == "keep me\nrefi watch"


def test_a_payload_that_is_not_a_dict_never_blocks_the_upgrade(db):
    assert _migrate_raw(db, ["not", "a", "book"]) == ["not", "a", "book"]
