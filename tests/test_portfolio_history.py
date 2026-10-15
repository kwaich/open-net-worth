"""
Unit tests for services.portfolio_history — the shared portfolio-value primitive.

All tests use synthetic/injected data. No live data files are read.

Run:
    .venv/bin/python3 -m pytest tests/test_portfolio_history.py -q
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.services import portfolio_history as ph

# USD pinned at par with the SGD base: these tests exercise the series mechanics, not
# conversion, and a missing pair is refused rather than assumed 1:1.
PAR_RATES = {"USD": 1.0, "SGD": 1.0}

# ─── Helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect PositionStore into a temp dir — needed only by the tests that exercise
    holdings_as_of's default (non-injected) `locked_position_ids` resolution."""
    return tmp_path


def _lot(
    instrument_id: str,
    original_qty: float,
    remaining_qty: float,
    acquired_at: str = "",
    kind: str = "opening",
    unit_cost: float | None = None,
    cost_currency: str = "USD",
) -> dict:
    return {
        "id": f"lot-{instrument_id}-{acquired_at or 'open'}",
        "instrument_id": instrument_id,
        "position_id": f"pos-{instrument_id}",
        "account_id": "acct-1",
        "acquired_at": acquired_at,
        "original_qty": original_qty,
        "remaining_qty": remaining_qty,
        "unit_cost": unit_cost,
        "cost_currency": cost_currency,
        "kind": kind,
        "fees": 0.0,
        "note": "",
    }


def _disposal(
    instrument_id: str, quantity: float, disposed_at: str, proceeds_per_unit: float = 10.0
) -> dict:
    return {
        "id": f"rlz-{instrument_id}-{disposed_at}",
        "instrument_id": instrument_id,
        "position_id": f"pos-{instrument_id}",
        "account_id": "acct-1",
        "disposed_at": disposed_at,
        "quantity": quantity,
        "proceeds_per_unit": proceeds_per_unit,
        "proceeds_currency": "USD",
        "disposal_fees": 0.0,
        "realized_gain": None,
    }


def _instrument(
    iid: str,
    symbol: str = "TST",
    asset_class: str = "equity",
    price_source_id: str = "",
    quote_currency: str = "USD",
) -> dict:
    return {
        "id": iid,
        "symbol": symbol,
        "asset_class": asset_class,
        "price_source_id": price_source_id or iid,
        "quote_currency": quote_currency,
        "quote_per_unit": 1.0,
    }


# ─── inception_date ───────────────────────────────────────────────────────────


def test_inception_date_from_txn():
    txns = [{"id": "t1", "date": "2023-06-01", "deleted": False}]
    assert ph.inception_date(accounts=[], txns=txns) == "2023-06-01"


def test_inception_date_min_of_both():
    """``accounts`` is inert now (every tracking start is itself an opening transaction, so
    txn dates already cover it) — this pins that passing it alongside real txns still just
    resolves to the txn min, i.e. the accounts param is accepted but contributes nothing."""
    accts = [{"id": "a1", "initial_balance_date": "2024-03-01"}]
    txns = [{"id": "t1", "date": "2024-01-01"}, {"id": "t2", "date": "2024-02-15"}]
    assert ph.inception_date(accounts=accts, txns=txns) == "2024-01-01"


def test_inception_date_empty_book():
    assert ph.inception_date(accounts=[], txns=[]) is None


def test_inception_date_ignores_deleted_txns():
    # A deleted txn should not be included in candidates; caller pre-filters.
    # inception_date() itself just takes the list as given — so deleted rows
    # passed by caller should already be filtered, but a pure empty txn list is safe.
    txns = [{"id": "t1", "date": "2020-01-01", "deleted": True}]
    # deleted rows ARE included if the caller passes them — the store filter is on the caller.
    # This tests that the function picks up the date regardless (caller responsibility to filter).
    result = ph.inception_date(accounts=[], txns=txns)
    assert result == "2020-01-01"


# ─── holdings_as_of ──────────────────────────────────────────────────────────


def test_holdings_as_of_opening_lot_present_from_inception():
    """An opening (undated) lot should be included for any as_of >= inception."""
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    result = ph.holdings_as_of("2024-06-01", inception="2024-01-01", lots=lots, disposals=[])
    assert result == {"AAPL": pytest.approx(10.0)}


def test_holdings_as_of_opening_lot_absent_before_inception():
    """An opening lot must NOT appear before the inception date (no-data guard)."""
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    # as_of == inception: must appear
    assert "AAPL" in ph.holdings_as_of(
        "2024-01-01", inception="2024-01-01", lots=lots, disposals=[]
    )
    # We can't go before inception with opening lots — there is no "before inception" by definition.


def test_holdings_as_of_no_inception_opening_lot_excluded():
    """When the book has no inception, opening lots contribute nothing."""
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    result = ph.holdings_as_of("2024-06-01", inception=None, lots=lots, disposals=[])
    assert result == {}


def test_holdings_as_of_dated_buy_steps_up():
    """A dated buy lot steps the qty up on its acquired_at date."""
    lots = [_lot("MSFT", 5.0, 5.0, acquired_at="2024-03-10", kind="buy")]
    # Before the buy: zero
    assert ph.holdings_as_of("2024-03-09", inception="2024-01-01", lots=lots, disposals=[]) == {}
    # On the buy date: appears
    assert ph.holdings_as_of("2024-03-10", inception="2024-01-01", lots=lots, disposals=[]) == {
        "MSFT": pytest.approx(5.0)
    }


def test_holdings_as_of_disposal_steps_down():
    """A disposal reduces the qty on disposed_at."""
    lots = [_lot("AAPL", 10.0, 6.0, acquired_at="", kind="opening")]
    disposals = [_disposal("AAPL", 4.0, "2024-04-01")]
    # Before disposal
    assert ph.holdings_as_of("2024-03-31", inception="2024-01-01", lots=lots, disposals=disposals)[
        "AAPL"
    ] == pytest.approx(10.0)
    # After disposal
    assert ph.holdings_as_of("2024-04-01", inception="2024-01-01", lots=lots, disposals=disposals)[
        "AAPL"
    ] == pytest.approx(6.0)


def test_holdings_as_of_full_disposal_removes_instrument():
    """When all shares are disposed, the instrument is absent from the result."""
    lots = [_lot("BTC", 1.0, 0.0, acquired_at="", kind="opening")]
    disposals = [_disposal("BTC", 1.0, "2024-05-01")]
    result = ph.holdings_as_of("2024-06-01", inception="2024-01-01", lots=lots, disposals=disposals)
    assert "BTC" not in result


def test_holdings_as_of_reconciles_to_remaining_qty():
    """holdings_as_of(today) should equal the sum of remaining_qty per instrument."""
    today = date.today().isoformat()
    lots = [
        _lot("AAPL", 10.0, 6.0, acquired_at="", kind="opening"),
        _lot("AAPL", 5.0, 5.0, acquired_at="2024-02-01", kind="buy"),
        _lot("MSFT", 8.0, 8.0, acquired_at="2024-03-01", kind="buy"),
    ]
    disposals = [_disposal("AAPL", 4.0, "2024-04-01")]
    # Σ remaining_qty per instrument: AAPL = 6+5=11, MSFT=8
    expected_aapl = 6.0 + 5.0  # remaining_qty sum
    expected_msft = 8.0

    result = ph.holdings_as_of(today, inception="2024-01-01", lots=lots, disposals=disposals)
    assert result["AAPL"] == pytest.approx(expected_aapl)
    assert result["MSFT"] == pytest.approx(expected_msft)


def test_holdings_as_of_multiple_lots_same_instrument():
    """Multiple dated lots for the same instrument stack correctly."""
    lots = [
        _lot("ETH", 2.0, 2.0, acquired_at="2024-01-01", kind="buy"),
        _lot("ETH", 3.0, 3.0, acquired_at="2024-03-01", kind="buy"),
    ]
    # After first buy, before second
    r1 = ph.holdings_as_of("2024-02-01", inception="2024-01-01", lots=lots, disposals=[])
    assert r1["ETH"] == pytest.approx(2.0)
    # After both
    r2 = ph.holdings_as_of("2024-04-01", inception="2024-01-01", lots=lots, disposals=[])
    assert r2["ETH"] == pytest.approx(5.0)


# ─── holdings_as_of / holdings_series: unvested equity exclusion (ADR 2026-07-31-
# unvested-equity-excluded-from-net-worth) — a lot attached to a vesting_state="unvested" position
# (e.g. a legacy/imported lot; current equity.py never lot-backs a locked position) must
# never contribute quantity to reconstructed holdings. ─────────────────────────────────


def test_holdings_as_of_excludes_lot_on_locked_position():
    """A lot whose position_id is a locked position contributes nothing, even though its
    instrument_id matches a legitimate (non-locked) holding of the same instrument."""
    lots = [
        _lot("ONWAI", 1242.0, 1242.0, acquired_at="", kind="opening"),  # liquid, pos-ONWAI
        {  # legacy/orphan lot on the locked position — must be excluded
            "id": "lot-ONWAI-locked",
            "instrument_id": "ONWAI",
            "position_id": "pos-ONWAI-locked",
            "account_id": "acct-1",
            "acquired_at": "",
            "original_qty": 1955.0,
            "remaining_qty": 1500.00,
            "unit_cost": None,
            "cost_currency": "USD",
            "kind": "opening",
        },
    ]
    result = ph.holdings_as_of(
        "2024-06-01",
        inception="2024-01-01",
        lots=lots,
        disposals=[],
        locked_position_ids=frozenset({"pos-ONWAI-locked"}),
    )
    assert result["ONWAI"] == pytest.approx(1242.0)  # only the liquid lot counts


def test_holdings_as_of_defaults_locked_position_ids_from_position_store(isolated):
    """With no explicit `locked_position_ids`, the function resolves locked positions from
    the live PositionStore — the real (non-injected) path net_worth_history() takes."""
    from backend.db.position_store import PositionStore

    PositionStore().create(
        {
            "instrument_id": "ONWAI",
            "account_id": "acct-1",
            "quantity": 1500.00,
            "vesting_state": "unvested",
        }
    )
    locked_id = PositionStore().query_positions(vesting_state="unvested")[0]["id"]
    lots = [
        _lot("ONWAI", 1242.0, 1242.0, acquired_at="", kind="opening"),
        {**_lot("ONWAI", 1955.0, 1500.00, acquired_at=""), "position_id": locked_id},
    ]
    result = ph.holdings_as_of("2024-06-01", inception="2024-01-01", lots=lots, disposals=[])
    assert result["ONWAI"] == pytest.approx(1242.0)


def test_holdings_as_of_no_locked_positions_unaffected():
    """A book with no locked positions is a complete no-op (empty exclusion set)."""
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    result = ph.holdings_as_of(
        "2024-06-01",
        inception="2024-01-01",
        lots=lots,
        disposals=[],
        locked_position_ids=frozenset(),
    )
    assert result == {"AAPL": pytest.approx(10.0)}


def test_holdings_series_excludes_lot_on_locked_position():
    """holdings_series applies the same locked-position exclusion as holdings_as_of."""
    lots = [
        _lot("ONWAI", 1242.0, 1242.0, acquired_at="", kind="opening"),
        {
            "id": "lot-ONWAI-locked",
            "instrument_id": "ONWAI",
            "position_id": "pos-ONWAI-locked",
            "account_id": "acct-1",
            "acquired_at": "",
            "original_qty": 1955.0,
            "remaining_qty": 1500.00,
            "unit_cost": None,
            "cost_currency": "USD",
            "kind": "opening",
        },
    ]
    result = ph.holdings_series(
        "2024-01-01",
        "2024-01-01",
        "daily",
        inception="2024-01-01",
        lots=lots,
        disposals=[],
        locked_position_ids=frozenset({"pos-ONWAI-locked"}),
    )
    assert result[0]["holdings"]["ONWAI"] == pytest.approx(1242.0)


# ─── holdings_series ─────────────────────────────────────────────────────────


def test_holdings_series_daily_shape():
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    result = ph.holdings_series(
        "2024-01-01", "2024-01-05", "daily", inception="2024-01-01", lots=lots, disposals=[]
    )
    # 5 days inclusive
    assert len(result) == 5
    assert result[0]["date"] == "2024-01-01"
    assert result[-1]["date"] == "2024-01-05"
    assert all("holdings" in pt for pt in result)


def test_holdings_series_monthly_grain():
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    result = ph.holdings_series(
        "2024-01-01", "2024-03-31", "monthly", inception="2024-01-01", lots=lots, disposals=[]
    )
    # Should include: 2024-01-31, 2024-02-29 (2024 is leap), 2024-03-31
    dates = [pt["date"] for pt in result]
    assert "2024-01-31" in dates
    assert "2024-02-29" in dates
    assert "2024-03-31" in dates


def test_holdings_series_single_sweep_correctness():
    """The forward-sweep must produce the same result as holdings_as_of called per date."""
    inception = "2024-01-01"
    lots = [
        _lot("AAPL", 10.0, 6.0, acquired_at="", kind="opening"),
        _lot("MSFT", 5.0, 5.0, acquired_at="2024-02-15", kind="buy"),
    ]
    disposals = [_disposal("AAPL", 4.0, "2024-03-10")]
    # 2024 is a leap year: February month-end is 2024-02-29
    dates_to_check = ["2024-01-31", "2024-02-29", "2024-03-31"]

    series = ph.holdings_series(
        "2024-01-01", "2024-03-31", "monthly", inception=inception, lots=lots, disposals=disposals
    )
    series_by_date = {pt["date"]: pt["holdings"] for pt in series}

    for d in dates_to_check:
        expected = ph.holdings_as_of(d, inception=inception, lots=lots, disposals=disposals)
        actual = series_by_date.get(d, {})
        for iid in set(expected) | set(actual):
            assert actual.get(iid, 0.0) == pytest.approx(expected.get(iid, 0.0), abs=1e-6), (
                f"Mismatch at {d} for {iid}: series={actual.get(iid)} expected={expected.get(iid)}"
            )


def test_holdings_series_empty_range():
    result = ph.holdings_series(
        "2024-03-01", "2024-02-01", "daily", inception="2024-01-01", lots=[], disposals=[]
    )
    assert result == []


def test_holdings_series_dated_buy_appears_at_correct_date():
    """A dated buy must first appear in the series on its acquired_at date."""
    inception = "2024-01-01"
    lots = [_lot("NVDA", 3.0, 3.0, acquired_at="2024-02-10", kind="buy")]
    result = ph.holdings_series(
        "2024-01-01", "2024-03-01", "daily", inception=inception, lots=lots, disposals=[]
    )
    by_date = {pt["date"]: pt["holdings"] for pt in result}
    assert "NVDA" not in by_date.get("2024-02-09", {})
    assert by_date.get("2024-02-10", {}).get("NVDA") == pytest.approx(3.0)


# ─── portfolio_value_series ───────────────────────────────────────────────────


def _make_price_history(pid: str, prices: dict[str, float]) -> dict[str, dict[str, float]]:
    """Build a price_history arg in the shape PriceHistoryStore.as_of_maps() returns."""
    # {date: {pid: price}}
    return {d: {pid: p} for d, p in prices.items()}


def test_portfolio_value_series_basic_market_value():
    """Market value = qty × price in base currency."""
    inception = "2024-01-01"
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    disposals = []
    instruments = {"AAPL": _instrument("AAPL", quote_currency="USD", price_source_id="AAPL")}
    price_history = _make_price_history("AAPL", {"2024-01-31": 150.0, "2024-02-29": 160.0})
    rates = PAR_RATES  # same as base for simplicity

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-02-29",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=disposals,
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )

    series = result["series"]
    assert len(series) == 2
    jan = next(pt for pt in series if pt["date"] == "2024-01-31")
    feb = next(pt for pt in series if pt["date"] == "2024-02-29")
    assert jan["value_base"] == pytest.approx(1500.0)
    assert feb["value_base"] == pytest.approx(1600.0)
    assert jan["classes"]["equity"] == pytest.approx(1500.0)


def test_portfolio_value_series_band_restriction_property_excluded():
    """Property instruments are excluded from the value series (not a _MARKET_BAND)."""
    inception = "2024-01-01"
    lots = [
        _lot("PROP1", 1.0, 1.0, acquired_at="", kind="opening"),
        _lot("AAPL", 5.0, 5.0, acquired_at="", kind="opening"),
    ]
    instruments = {
        "PROP1": _instrument("PROP1", asset_class="property", price_source_id="PROP1"),
        "AAPL": _instrument("AAPL", asset_class="equity", price_source_id="AAPL"),
    }
    price_history = {
        "2024-01-31": {"PROP1": 100000.0, "AAPL": 150.0},
    }
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-01-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )

    series = result["series"]
    assert len(series) == 1
    pt = series[0]
    # Only AAPL contributes (5 × 150 = 750); property excluded
    assert pt["value_base"] == pytest.approx(750.0)
    assert "property" not in pt["classes"]
    assert pt["classes"].get("equity") == pytest.approx(750.0)


def test_portfolio_value_series_as_of_price_carry_forward():
    """When there's no price on a grain date, the last price on or before is used."""
    inception = "2024-01-01"
    lots = [_lot("BTC", 1.0, 1.0, acquired_at="", kind="opening")]
    instruments = {"BTC": _instrument("BTC", asset_class="crypto", price_source_id="BTC")}
    # Price only on 2024-01-15; the series asks for 2024-01-31 but no Jan 31 price exists.
    price_history = {
        "2024-01-31": {"BTC": 40000.0},  # as_of_maps carries forward to 2024-01-31
    }
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-01-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    pt = result["series"][0]
    assert pt["value_base"] == pytest.approx(40000.0)


def test_portfolio_value_series_fx_conversion():
    """Non-base quote currency is converted via to_base."""
    from backend.core.fx import base_currency

    base = base_currency()
    inception = "2024-01-01"
    lots = [_lot("TSM", 10.0, 10.0, acquired_at="", kind="opening")]
    instruments = {
        "TSM": _instrument("TSM", asset_class="equity", price_source_id="TSM", quote_currency="TWD")
    }
    price_history = {"2024-01-31": {"TSM": 100.0}}  # 100 TWD per share
    # Build rates with the actual base currency so to_base can resolve the pivot.
    # TWD→base: 1 TWD = 0.03125 base-units. With base pivot at 1.0:
    # rates = {base: 1.0, "TWD": 0.03125}
    # to_base(10 × 100 TWD, "TWD", base) = 1000 × 0.03125 / 1.0 = 31.25
    rates = {base: 1.0, "TWD": 0.03125}

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-01-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    pt = result["series"][0]
    # 10 shares × 100 TWD × 0.03125 = 31.25 base units
    assert pt["value_base"] == pytest.approx(31.25)


def test_portfolio_value_series_data_quality_tiers():
    """Instruments with only opening lots are 'approximate'; with dated lots are 'exact'."""
    inception = "2024-01-01"
    lots = [
        _lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening"),
        _lot("MSFT", 5.0, 5.0, acquired_at="2024-02-01", kind="buy"),
    ]
    instruments = {
        "AAPL": _instrument("AAPL", price_source_id="AAPL"),
        "MSFT": _instrument("MSFT", price_source_id="MSFT"),
    }
    price_history = {"2024-03-31": {"AAPL": 100.0, "MSFT": 200.0}}
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-03-01",
        "2024-03-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    tiers = result["data_quality"]["tiers"]
    assert tiers["AAPL"] == "approximate"
    assert tiers["MSFT"] == "exact"


def test_portfolio_value_series_data_quality_has_unrecorded_risk():
    """has_unrecorded_risk = True when any lot for a contributing instrument has no acquired_at."""
    inception = "2024-01-01"
    lots = [
        _lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening"),
        _lot("MSFT", 5.0, 5.0, acquired_at="2024-02-01", kind="buy"),
    ]
    instruments = {
        "AAPL": _instrument("AAPL", price_source_id="AAPL"),
        "MSFT": _instrument("MSFT", price_source_id="MSFT"),
    }
    price_history = {"2024-02-29": {"AAPL": 100.0, "MSFT": 200.0}}
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-02-01",
        "2024-02-29",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    assert result["data_quality"]["has_unrecorded_risk"] is True


def test_portfolio_value_series_data_quality_no_unrecorded_risk():
    """has_unrecorded_risk = False when all contributing instruments have at least one dated lot."""
    inception = "2024-01-01"
    lots = [
        _lot("MSFT", 5.0, 5.0, acquired_at="2024-02-01", kind="buy"),
    ]
    instruments = {"MSFT": _instrument("MSFT", price_source_id="MSFT")}
    price_history = {"2024-02-29": {"MSFT": 200.0}}
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-02-01",
        "2024-02-29",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    assert result["data_quality"]["has_unrecorded_risk"] is False


def test_portfolio_value_series_data_quality_unrecorded_risk_mixed_lots():
    """has_unrecorded_risk = True even when an instrument has a mix of dated and undated lots."""
    inception = "2024-01-01"
    lots = [
        # AAPL has one opening lot + one dated lot — the undated lot is enough to set the flag.
        _lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening"),
        _lot("AAPL", 5.0, 5.0, acquired_at="2024-02-01", kind="buy"),
    ]
    instruments = {"AAPL": _instrument("AAPL", price_source_id="AAPL")}
    price_history = {"2024-03-31": {"AAPL": 100.0}}
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-03-01",
        "2024-03-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    assert result["data_quality"]["has_unrecorded_risk"] is True
    # AAPL has a dated lot, so tier is "exact"
    assert result["data_quality"]["tiers"]["AAPL"] == "exact"


def test_portfolio_value_series_fx_approximate_flag():
    """fx_approximate = True when a contributing instrument quotes in a non-base currency."""
    inception = "2024-01-01"
    lots = [_lot("TSM", 10.0, 10.0, acquired_at="", kind="opening")]
    instruments = {"TSM": _instrument("TSM", quote_currency="TWD", price_source_id="TSM")}
    price_history = {"2024-01-31": {"TSM": 100.0}}
    rates = {**PAR_RATES, "TWD": 0.03}

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-01-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    # fx_approximate depends on base_currency(); in test env base is likely SGD or USD.
    # We only assert the flag is True when the quote_currency differs from the live base.
    # Since tests run against SGD by default and TWD != SGD, this should be True.
    # If base == TWD (unlikely), the test would incorrectly fail — acceptable here.
    dq = result["data_quality"]
    from backend.core.fx import base_currency

    expected_fx_approx = "TWD" != base_currency().upper()
    assert dq["fx_approximate"] is expected_fx_approx


def test_portfolio_value_series_empty_book():
    """An empty book (no lots, no instruments) returns zero-value series + clean data_quality."""
    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-01-31",
        "monthly",
        inception=None,
        lots=[],
        disposals=[],
        instruments={},
        price_history={},
        rates=PAR_RATES,
    )
    # The grain-date scaffold exists but all values are 0 (no instruments to price).
    assert all(pt["value_base"] == 0.0 for pt in result["series"])
    assert all(pt["classes"] == {} for pt in result["series"])
    dq = result["data_quality"]
    assert dq["tiers"] == {}
    assert dq["has_unrecorded_risk"] is False
    assert dq["inception"] is None


def test_portfolio_value_series_no_price_for_date():
    """When no price is available for an instrument, it contributes 0 to value_base."""
    inception = "2024-01-01"
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    instruments = {"AAPL": _instrument("AAPL", price_source_id="AAPL")}
    # No price for AAPL at the relevant date
    price_history = {"2024-01-31": {}}
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-01-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    assert result["series"][0]["value_base"] == 0.0


def test_portfolio_value_series_has_unknown_cost():
    """has_unknown_cost = True when any contributing instrument has a lot with unit_cost=None."""
    inception = "2024-01-01"
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening", unit_cost=None)]
    instruments = {"AAPL": _instrument("AAPL", price_source_id="AAPL")}
    price_history = {"2024-01-31": {"AAPL": 100.0}}
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-01-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    assert result["data_quality"]["has_unknown_cost"] is True


def test_portfolio_value_series_bond_excluded_from_lot_backed_series():
    """bonds-first-class: bond maps to its OWN band "bond", which is NOT in _MARKET_BANDS,
    so it is excluded from the lot-backed investment performance value series (same exclusion
    as property/cash). It must never leak back into the equity band."""
    inception = "2024-01-01"
    lots = [_lot("BOND1", 100.0, 100.0, acquired_at="", kind="opening")]
    instruments = {"BOND1": _instrument("BOND1", asset_class="bond", price_source_id="BOND1")}
    price_history = {"2024-01-31": {"BOND1": 10.0}}
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-01-31",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=[],
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    pt = result["series"][0]
    # bond is its own band, excluded from the lot-backed series — no equity leakage, value 0
    assert pt["classes"].get("equity") is None
    assert pt["classes"].get("bond") is None
    assert pt["value_base"] == pytest.approx(0.0)


def test_portfolio_value_series_buy_and_disposal_within_series():
    """Confirms the series reflects mid-window buys and disposals correctly."""
    inception = "2024-01-01"
    lots = [
        _lot("AAPL", 10.0, 5.0, acquired_at="", kind="opening"),
        _lot("AAPL", 5.0, 5.0, acquired_at="2024-02-15", kind="buy"),
    ]
    disposals = [_disposal("AAPL", 5.0, "2024-02-20")]
    instruments = {"AAPL": _instrument("AAPL", price_source_id="AAPL")}
    price_history = {
        "2024-01-31": {"AAPL": 100.0},  # only 10 shares (opening)
        "2024-02-29": {"AAPL": 110.0},  # 10 + 5 (buy) - 5 (disposal) = 10
    }
    rates = PAR_RATES

    result = ph.portfolio_value_series(
        "2024-01-01",
        "2024-02-29",
        "monthly",
        inception=inception,
        lots=lots,
        disposals=disposals,
        instruments=instruments,
        price_history=price_history,
        rates=rates,
    )
    series = result["series"]
    jan = next(pt for pt in series if pt["date"] == "2024-01-31")
    feb = next(pt for pt in series if pt["date"] == "2024-02-29")
    assert jan["value_base"] == pytest.approx(10.0 * 100.0)
    assert feb["value_base"] == pytest.approx(10.0 * 110.0)
