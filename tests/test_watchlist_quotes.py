"""GET /api/watchlist/quotes + watchlist_quotes.build_watchlist_quotes — synthetic, no network."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import prices, watch_context, watch_history, watchlist, watchlist_quotes

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _item(value, ids, **kw) -> dict:
    return {
        "id": f"wli_{value}_{'_'.join(ids)}",
        "type": "ticker",
        "value": value,
        "label": value,
        "asset_class": "equity",
        "watchlist_ids": ids,
        **kw,
    }


def _book(items, lists=None) -> dict:
    return {
        "items": items,
        "watchlists": lists
        or [
            {"id": "wl_b", "name": "AI", "order": 1},
            {"id": "wl_a", "name": "Favorites", "order": 0},
        ],
        "config": {},
    }


@pytest.fixture
def stub(monkeypatch):
    history: dict = {}
    requested: list = []

    def install(book, snaps, positions=None, hist=None):
        history.update(hist or {})
        monkeypatch.setattr(watchlist, "read_watchlist", lambda: book)
        monkeypatch.setattr(prices, "fetch_quote_snapshots", lambda syms: snaps)
        monkeypatch.setattr(watchlist_quotes, "_positions_by_ticker", lambda: positions or {})
        monkeypatch.setattr(watch_context, "history_for", lambda pid, now: history.get(pid, []))
        monkeypatch.setattr(watch_history, "request", lambda items: requested.extend(items))

    install.requested = requested
    return install


def test_empty_watchlist_returns_lists_and_no_rows(stub):
    stub(_book([]), {})
    out = watchlist_quotes.build_watchlist_quotes(NOW)
    assert out["rows"] == []
    assert [w["id"] for w in out["watchlists"]] == ["wl_a", "wl_b"]


def test_row_carries_change_volume_and_ranges(stub):
    snaps = {
        "NVDA": {
            "price": 110.0,
            "prev_close": 100.0,
            "volume": 3_000_000,
            "avg_volume": 1_500_000,
            "week52_low": 60.0,
            "week52_high": 160.0,
            "day_low": 99.0,
            "day_high": 111.0,
            "market_cap": 2.7e12,
            "exchange": "NasdaqGS",
            "currency": "USD",
        }
    }
    stub(_book([_item("NVDA", ["wl_a"])]), snaps)
    [row] = watchlist_quotes.build_watchlist_quotes(NOW)["rows"]
    assert row["change"] == pytest.approx(10.0)
    assert row["change_pct"] == pytest.approx(10.0)
    assert row["volume_ratio"] == 2.0
    assert row["range_pos"] == pytest.approx(0.5)
    assert row["market_cap"] == 2.7e12 and row["exchange"] == "NasdaqGS"
    assert row["position"] is None


def test_unquoted_ticker_is_still_listed_with_null_market_fields(stub):
    stub(_book([_item("ZZZZ", ["wl_a"])]), {})
    [row] = watchlist_quotes.build_watchlist_quotes(NOW)["rows"]
    assert row["ticker"] == "ZZZZ"
    assert row["price"] is None and row["change"] is None and row["change_pct"] is None


def test_ticker_on_two_lists_is_one_row_with_both_ids(stub):
    stub(_book([_item("AAPL", ["wl_a"]), _item("aapl", ["wl_b"])]), {})
    [row] = watchlist_quotes.build_watchlist_quotes(NOW)["rows"]
    assert row["watchlist_ids"] == ["wl_a", "wl_b"]


def test_topics_are_not_quoted(stub):
    topic = {**_item("SpaceX IPO", []), "type": "topic"}
    stub(_book([topic]), {})
    assert watchlist_quotes.build_watchlist_quotes(NOW)["rows"] == []


def test_held_name_carries_its_position_matched_by_instrument(stub):
    pos = {"quantity": 5.0, "value": 550.0, "day_pl": 12.5, "unrealized": 80.0}
    stub(
        _book([_item("KSTL.DE", ["wl_a"], instrument_id="ins_1")]),
        {"KSTL.DE": {"price": 36.0, "prev_close": 36.5}},
        positions={"iid:ins_1": pos, "KSTL": pos},
    )
    [row] = watchlist_quotes.build_watchlist_quotes(NOW)["rows"]
    assert row["position"] == pos
    assert row["change"] == pytest.approx(-0.5)


def test_position_fold_sums_legs_and_nulls_unknown_basis(monkeypatch):
    from backend.services import investments_book

    legs = [
        {
            "ticker": "MSFT",
            "quantity": 2,
            "base_value": 200.0,
            "daily_pl": 1.0,
            "pl": 10.0,
            "instrument_id": "ins_m",
        },
        {"ticker": "MSFT", "quantity": 3, "base_value": 300.0, "daily_pl": 2.0, "pl": None},
        {"ticker": "GOOG", "quantity": 0, "base_value": 0.0, "daily_pl": 0.0, "pl": 0.0},
    ]
    monkeypatch.setattr(investments_book, "project_invested", lambda rows, **kw: (legs, 500.0))
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])
    out = watchlist_quotes._positions_by_ticker()
    assert out["MSFT"] == {"quantity": 5.0, "value": 500.0, "day_pl": 3.0, "unrealized": None}
    assert out["iid:ins_m"] is out["MSFT"]
    assert "GOOG" not in out


def test_endpoint_serves_the_board(stub):
    stub(_book([_item("NVDA", ["wl_a"])]), {"NVDA": {"price": 1.0, "prev_close": 1.0}})
    res = TestClient(app).get("/api/watchlist/quotes")
    assert res.status_code == 200
    assert res.json()["rows"][0]["ticker"] == "NVDA"


def test_spark_ends_on_todays_price_and_skips_todays_bar(stub):
    hist = {"NVDA": [("2026-09-26", 100.0), ("2026-09-29", 104.0), ("2026-09-30", 999.0)]}
    snap = {"price": 106.0, "prev_close": 104.0, "session_date": "2026-09-30"}
    stub(_book([_item("NVDA", ["wl_a"])]), {"NVDA": snap}, hist=hist)
    [row] = watchlist_quotes.build_watchlist_quotes(NOW)["rows"]
    assert row["spark"] == [100.0, 104.0, 106.0]
    assert stub.requested == []


def test_no_history_means_no_spark_and_a_background_fetch(stub):
    stub(_book([_item("NVDA", ["wl_a"])]), {"NVDA": {"price": 1.0, "prev_close": 1.0}})
    [row] = watchlist_quotes.build_watchlist_quotes(NOW)["rows"]
    assert row["spark"] is None
    assert [i["value"] for i in stub.requested] == ["NVDA"]
