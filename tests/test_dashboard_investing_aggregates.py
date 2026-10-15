"""Dashboard investing / net-worth aggregates — regressions for the audit of the Overview.

These fold hand-built projection rows through the private aggregate functions, so each case is
the exact shape the bug needed (an unvested grant beside vested holdings, one ETF in three
accounts, an overdrawn cash account) without a full book.
"""

from __future__ import annotations

import pytest

from backend.services import investments_book, rebalance
from backend.services import portfolio_analytics as pa


def _holding(pid, inst, symbol, value, *, daily=None, pl=None, acct="Brokerage", **extra):
    return {
        "position_id": pid,
        "instrument_id": inst,
        "symbol": symbol,
        "label": symbol,
        "asset_class": extra.pop("asset_class", "equity"),
        "base_value": value,
        "daily_pl": daily,
        "pl": pl,
        "account_name": acct,
        "vesting_state": extra.pop("vesting_state", "vested"),
        **extra,
    }


def _account(aid, name, value, asset_class):
    return {
        "position_id": None,
        "account_id": aid,
        "label": name,
        "asset_class": asset_class,
        "base_value": value,
        "account_name": name,
        "vesting_state": "vested",
    }


@pytest.fixture
def rows():
    return [
        _holding("p1", "i_voo", "VOO", 25_000, daily=-800.0, pl=1_000.0, acct="Taxable"),
        _holding("p2", "i_voo", "VOO", 14_000, daily=-450.0, pl=-200.0, acct="Roth"),
        _holding("p3", "i_voo", "VOO", 42_000, daily=-1_400.0, pl=3_000.0, acct="401k"),
        _holding("p4", "i_aapl", "AAPL", 6_000, daily=90.0, pl=500.0, acct="Taxable"),
        _holding("p5", "i_net", "NET", 100_000, daily=7_000.0, pl=None, vesting_state="unvested"),
        _holding("p6", "i_net", "NET", 100_000, daily=7_000.0, pl=20_000.0),
    ]


def test_today_reads_the_investments_book_and_skips_the_unvested_grant(rows):
    kpis = pa._kpis(rows, {"classes": {}})
    vested = [r for r in rows if r["vesting_state"] != "unvested"]
    book = investments_book.today_move(vested)
    assert kpis["today"] == book["pl"] == pytest.approx(7_000 + 90 - 800 - 450 - 1_400)
    assert kpis["today_pct"] == book["pct"]
    assert kpis["has_market"] is True


def test_today_has_no_market_when_only_an_unvested_grant_moved():
    only_grant = [_holding("p5", "i_net", "NET", 100_000, daily=7_000.0, vesting_state="unvested")]
    kpis = pa._kpis(only_grant, {"classes": {}})
    assert kpis["today"] == 0.0
    assert kpis["has_market"] is False


def test_movers_ship_per_instrument_totals_and_skip_the_unvested_grant(rows):
    movers = pa._movers(rows)
    by_key = {m["key"]: m for m in movers["rows"]}
    # VOO is ONE entry carrying the group's total, its % on the prior close and its value —
    # keyed by the largest single contributor.
    voo = by_key["p3"]
    assert voo["day_pl"] == pytest.approx(-2_650.0)
    assert voo["value"] == pytest.approx(81_000.0)
    prior = 81_000 + 2_650
    assert voo["day_pct"] == pytest.approx(-2_650 / prior * 100, abs=1e-3)
    assert "p1" not in by_key and "p2" not in by_key
    assert "p5" not in by_key  # unvested NET
    assert movers["max_abs"] == pytest.approx(7_000.0)
    assert (movers["gainers"], movers["losers"]) == (2, 1)
    assert [m["day_pl"] > 0 for m in movers["rows"]] == [True, True, False]


def test_holdings_map_excludes_unvested_and_has_one_tile_per_instrument_in_a_group(rows):
    for r in rows:
        r["sector"] = "Index" if r["symbol"] == "VOO" else "Tech"
    hmap = pa._holdings_map(rows)
    assert hmap["total"] == pytest.approx(25_000 + 14_000 + 42_000 + 6_000 + 100_000)
    assert hmap["holdings"] == 3  # VOO, AAPL, NET — not five positions

    for facet in ("sector", "asset_class", "none"):
        keys = [leaf["key"] for g in hmap["groups"][facet] for leaf in g["leaves"]]
        assert len(keys) == len(set(keys)) == 3
        assert "p5" not in keys  # the unvested NET grant

    sector = {g["key"]: g for g in hmap["groups"]["sector"]}
    (voo,) = sector["Index"]["leaves"]
    assert voo["value"] == pytest.approx(81_000.0)
    assert voo["positions"] == 3
    assert voo["day_pl"] == pytest.approx(-2_650.0)
    assert voo["pl"] == pytest.approx(3_800.0)
    # percent recomputed on summed cost (value - pl), not averaged
    assert voo["pl_pct"] == pytest.approx(3_800 / (81_000 - 3_800) * 100, abs=1e-3)

    # Under the Account facet each account keeps its own VOO tile, labelled by its group.
    accounts = {g["key"]: g for g in hmap["groups"]["account"]}
    assert {"Taxable", "Roth", "401k"} <= set(accounts)
    assert all(
        len(g["leaves"]) == len({leaf["key"] for leaf in g["leaves"]}) for g in accounts.values()
    )


def test_holdings_map_pl_is_unknown_when_any_leg_has_no_cost():
    rows = [
        _holding("a", "i_x", "XYZ", 100.0, pl=10.0),
        _holding("b", "i_x", "XYZ", 100.0, pl=None, acct="Other"),
    ]
    (leaf,) = pa._holdings_map(rows)["groups"]["none"][0]["leaves"]
    assert leaf["pl"] is None and leaf["pl_pct"] is None


def test_overdrawn_cash_account_is_not_debt():
    rows = [
        _account("a1", "Everyday Checking", -725.89, "cash"),
        _account("a2", "Sapphire Card", -2_822.54, "credit"),
        _account("a3", "Mortgage", -380_000.0, "loan"),
        _account("a4", "Savings", 5_000.0, "cash"),
    ]
    kpis = pa._kpis(rows, {"classes": {"cash": 4_274.11}})
    assert kpis["debt"] == pytest.approx(382_822.54)
    assert kpis["debt_accounts"] == 2
    assert pa._attention(rows)["debt_total"] == pytest.approx(-382_822.54)
    debts = pa._debts(rows)
    assert debts["total"] == pytest.approx(-382_822.54)
    assert {d["label"] for d in debts["rows"]} == {"Sapphire Card", "Mortgage"}


def test_gains_collapse_one_instrument_across_accounts(rows):
    gains = pa._gains(rows)
    by_key = {g["key"]: g for g in gains["rows"]}
    # VOO's three positions are one line; the unvested NET grant (pl unknown) is absent.
    assert len(gains["rows"]) == len({g["key"] for g in gains["rows"]}) == 3
    voo = by_key["p3"]
    assert voo["value"] == pytest.approx(81_000.0)
    assert voo["pl"] == pytest.approx(3_800.0)
    assert voo["pl_pct"] == pytest.approx(3_800 / (81_000 - 3_800) * 100, abs=1e-3)
    assert [abs(g["pl"]) for g in gains["rows"]] == sorted(
        (abs(g["pl"]) for g in gains["rows"]), reverse=True
    )
    assert gains["total_pl"] == pytest.approx(3_800 + 500 + 20_000)
    assert gains["total_pl"] == pytest.approx(pa.market_upl(rows))


def test_drift_does_not_suggest_trimming_a_car_or_a_watch():
    rows = [
        _holding("e", "i_e", "VTI", 70_000.0),
        _holding("b", "i_b", "BND", 30_000.0, asset_class="bond"),
        {**_account("oa", "2022 RAV4", 32_000.0, "other_asset"), "account_type": "owned_asset"},
    ]
    targets = {"equity": 0.7, "bond": 0.3}
    drift = pa._drift(rows, targets)
    assert "other_asset" not in {r["key"] for r in drift["rows"]}
    assert drift["total"] == pytest.approx(100_000.0)
    assert drift["top_trade"] is None
    assert "other_asset" in rebalance.NON_INVESTMENT_CLASSES
