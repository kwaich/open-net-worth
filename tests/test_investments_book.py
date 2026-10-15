"""The Investments book — grouped, weighted and totalled server-side.

The cross-account merge tests here were PORTED from `mergeByInstrument.test.ts`. That fold
summed value, P&L, quantity and cost basis in the browser; the sums moved to
`backend/services/investments_book.py` (ADR 2026-07-31-overview-aggregates-are-server-computed),
so its regression cover moves with them.

The scars it guarded, all preserved:
  * an instrument held at two brokers reads as ONE row, with SUMMED value/P&L/quantity;
  * a vested holding never merges with an unvested grant of the same instrument;
  * P&L % is recomputed from the summed BASE cost — never an average of percentages;
  * a native per-unit cost collapses to unknown when the contributors' cost currencies differ,
    rather than fabricating a figure by mixing currencies.
"""

from __future__ import annotations

import pytest

from backend.services.investments_book import _merge_by_instrument, investments_book


def _row(**overrides) -> dict:
    row = {
        "row_key": "p1",
        "position_id": "p1",
        "instrument_id": "inst-aapl",
        "symbol": "AAPL",
        "name": "Apple",
        "value": 1000.0,
        "weight": 0.0,
        "pl": 200.0,
        "pl_pct": 25.0,
        "day_pct": 1.5,
        "day_pl": 15.0,
        "cls": "equity",
        "sub": "stock",
        "sector": "Tech",
        "account": "Broker A",
        "account_id": "acc-a",
        "currency": "USD",
        "cost_currency": "USD",
        "cost_base": 800.0,
        "qty": 10.0,
        "price": 100.0,
        "cost": 80.0,
        "vesting_state": "vested",
        "coupon": None,
        "maturity": None,
        "ytm": None,
        "merged_count": 1,
    }
    row.update(overrides)
    return row


def test_two_accounts_holding_one_instrument_fold_into_a_single_row():
    rows = [
        _row(),
        _row(
            row_key="p2",
            position_id="p2",
            account="Broker B",
            account_id="acc-b",
            value=500.0,
            pl=100.0,
            cost_base=400.0,
            qty=5.0,
        ),
    ]
    merged = _merge_by_instrument(rows, grand=1500.0)
    assert len(merged) == 1
    r = merged[0]
    assert r["value"] == pytest.approx(1500)  # summed
    assert r["pl"] == pytest.approx(300)  # summed
    assert r["weight"] == pytest.approx(100)  # recomputed against grand
    assert r["qty"] == pytest.approx(15)
    assert r["merged_count"] == 2
    assert r["account"] == "2 accounts"  # a count, never a list of venues
    assert r["day_pct"] == pytest.approx(1.5)  # per-instrument, never averaged
    # A merged row has no single position, so per-position actions must be disabled.
    assert r["position_id"] is None


def test_different_instruments_never_merge():
    merged = _merge_by_instrument(
        [_row(), _row(row_key="p2", position_id="p2", instrument_id="inst-msft", symbol="MSFT")],
        grand=2000.0,
    )
    assert len(merged) == 2
    assert all(r["merged_count"] == 1 for r in merged)


def test_a_vested_holding_never_merges_with_an_unvested_grant():
    """Entitlement-distinguishing: unvested units are not yours, and must not be folded into
    the sellable position they share an instrument with."""
    merged = _merge_by_instrument(
        [_row(), _row(row_key="p2", position_id="p2", vesting_state="unvested")], grand=2000.0
    )
    assert len(merged) == 2
    assert all(r["merged_count"] == 1 for r in merged)


def test_a_single_account_holding_passes_through_unchanged():
    rows = [_row()]
    assert _merge_by_instrument(rows, grand=1000.0) == rows


def test_native_unit_cost_collapses_to_unknown_across_mixed_cost_currencies():
    """Never fabricate a per-unit cost by adding two currencies together."""
    merged = _merge_by_instrument(
        [
            _row(),
            _row(
                row_key="p2",
                position_id="p2",
                cost_currency="SGD",
                value=500.0,
                pl=100.0,
                cost_base=400.0,
                qty=5.0,
                cost=110.0,
            ),
        ],
        grand=1500.0,
    )
    assert len(merged) == 1
    assert merged[0]["cost"] == 0  # unknown, not a mixed-currency average
    assert merged[0]["pl"] == pytest.approx(300)
    # P&L% from the SUMMED base cost (1200), never an average of the two percentages.
    assert merged[0]["pl_pct"] == pytest.approx((300 / 1200) * 100, abs=0.01)


def test_native_unit_cost_is_weighted_when_the_cost_currency_is_shared():
    merged = _merge_by_instrument(
        [
            _row(),
            _row(
                row_key="p2",
                position_id="p2",
                value=500.0,
                pl=100.0,
                cost_base=400.0,
                qty=5.0,
                cost=80.0,
            ),
        ],
        grand=1500.0,
    )
    assert merged[0]["cost"] == pytest.approx(80)  # (80×10 + 80×5) / 15


def test_group_order_is_preserved_across_several_merged_instruments():
    merged = _merge_by_instrument(
        [
            _row(value=100.0, qty=1.0),
            _row(row_key="m1", position_id="m1", instrument_id="inst-msft", symbol="MSFT"),
            _row(row_key="p2", position_id="p2", value=50.0, qty=1.0, account_id="acc-b"),
        ],
        grand=1150.0,
    )
    assert [r["symbol"] for r in merged] == ["AAPL", "MSFT"]
    assert merged[0]["value"] == pytest.approx(150)


# ── whole-book reconciliation ─────────────────────────────────────────────────


@pytest.fixture
def invested(tmp_path, monkeypatch, db_isolated):
    """PriceHistoryStore resolves against the per-test isolated database (db_isolated)."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore
    from backend.services import accounts as accounts_service
    from backend.services import prices as prices_service

    monkeypatch.setattr(prices_service, "fetch_live", lambda *a, **k: {"prices": {}, "fx": {}})

    acct = accounts_service.create_account(
        {
            "name": "Broker",
            "kind": "asset",
            "type": "investment",
            "currency": "SGD",
            "initial_balance": 5_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    for symbol, qty in (("AAA", 10), ("BBB", 4)):
        inst = InstrumentStore().create(
            {
                "symbol": symbol,
                "name": symbol,
                "asset_class": "equity",
                "quote_currency": "SGD",
                "price_source": "manual",
                "price_source_id": symbol,
                "manual_price": 100.0,
            }
        )
        PositionStore().create(
            {
                "instrument_id": inst["id"],
                "account_id": acct["id"],
                "quantity": qty,
                "cost_basis": 80.0,
                "cost_currency": "SGD",
                "bucket": "liquid",
            }
        )
    return tmp_path


def test_the_grand_total_is_the_sum_of_its_rows(invested):
    book = investments_book()
    assert book["grand"] == pytest.approx(sum(r["value"] for r in book["rows"]), abs=0.02)
    assert book["grand"] == pytest.approx(sum(r["value"] for r in book["merged_rows"]), abs=0.02)


def test_weights_sum_to_one_hundred(invested):
    book = investments_book()
    assert sum(r["weight"] for r in book["rows"]) == pytest.approx(100, abs=0.05)


def test_sections_partition_the_book(invested):
    book = investments_book()
    assert sum(s["subtotal"] for s in book["sections"]) == pytest.approx(book["grand"], abs=0.02)


def test_the_book_excludes_cash_accounts(invested):
    """The book is what you are INVESTED in. The S$5,000 cash balance belongs to net worth."""
    book = investments_book()
    assert book["grand"] == pytest.approx(1400, abs=0.02)  # (10 + 4) × 100
    assert all(r["cls"] == "equity" for r in book["rows"])
    # …but it still knows its share of everything owned, from the same projection.
    assert book["portfolio_total"] == pytest.approx(6400, abs=0.02)
    assert book["pct_of_portfolio"] == pytest.approx((1400 / 6400) * 100, abs=0.01)


def test_unrealized_pl_is_a_return_on_what_was_paid(invested):
    book = investments_book()
    # 14 units: cost 80, now 100 → paid 1,120, worth 1,400, gain 280 = +25%.
    assert book["total_pl"] == pytest.approx(280, abs=0.02)
    assert book["total_pl_pct"] == pytest.approx(25, abs=0.05)


def test_crypto_bought_in_a_currency_other_than_the_quote_currency_labels_that_currency(
    tmp_path, monkeypatch, db_isolated, fx_rates
):
    """A BTC lot bought in SGD on Gemini reads its price/cost in SGD (the currency you
    transacted in — see portfolio_rows._display_ccy), even though the instrument's
    quote_currency is USD (CoinGecko's reference). CUR must name SGD, not USD, or the
    S$-labelled COST/PRICE numbers read as a wildly wrong USD figure."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore
    from backend.services import accounts as accounts_service
    from backend.services import prices as prices_service

    monkeypatch.setattr(prices_service, "fetch_live", lambda *a, **k: {"prices": {}, "fx": {}})

    acct = accounts_service.create_account(
        {
            "name": "Gemini",
            "kind": "asset",
            "type": "investment",
            "currency": "SGD",
            "initial_balance": 0.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    inst = InstrumentStore().create(
        {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "USD",
            "price_source": "manual",
            "price_source_id": "BTC",
            "manual_price": 60000.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 0.042,
            "cost_basis": 82523.46,
            "cost_currency": "SGD",
            "bucket": "liquid",
        }
    )

    book = investments_book()
    row = next(r for r in book["rows"] if r["symbol"] == "BTC")
    assert row["currency"] == "SGD"


def test_concentration_is_ranked_and_flagged(invested):
    book = investments_book(guideline_pct=50.0)
    assert [c["name"] for c in book["concentration"]] == ["AAA", "BBB"]
    assert book["top_name"]["name"] == "AAA"
    assert book["top_name"]["pct"] == pytest.approx((1000 / 1400) * 100, abs=0.01)
    assert book["top_name"]["hot"] is True  # 71% ≥ the 50% guideline
    assert book["concentration"][1]["hot"] is False


def test_the_book_excludes_unvested_equity_grant_units(invested, monkeypatch):
    """Unvested equity-grant units are not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — every other reader of position rows
    (portfolio_rows, portfolio_analytics, macro_exposure, risk_signals, asset_detail) excludes
    them. This book must too, or a locked RSU tranche reads as an ordinary, sellable holding:
    it inflates 'Total invested', shows up as a second row for an instrument already held
    vested, and can trip the concentration guideline on shares that aren't owned yet."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore
    from backend.services import prices as prices_service

    monkeypatch.setattr(prices_service, "fetch_live", lambda *a, **k: {"prices": {}, "fx": {}})

    inst = InstrumentStore().create(
        {
            "symbol": "AAA",
            "name": "AAA unvested tranche",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "AAA",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": None,
            "quantity": 1000,
            "cost_basis": None,
            "cost_currency": "SGD",
            "vesting_state": "unvested",
        }
    )

    book = investments_book()
    # Unchanged from test_the_book_excludes_cash_accounts's fixture-only baseline: the unvested
    # tranche (100,000 SGD at manual_price=100) contributes nothing.
    assert book["grand"] == pytest.approx(1400, abs=0.02)
    assert all(r["vesting_state"] != "unvested" for r in book["rows"])


def test_target_pct_is_a_percent_not_the_stored_fraction(monkeypatch, db_isolated):
    """`profile.target_allocation` is stored as a FRACTION 0-1 (profile_service.
    normalize_target_allocation), but `current_pct` is 0-100. A book sitting exactly on a
    60/40 target must show zero drift, not a spurious ~60pp gap from comparing 60.0 against
    the raw fraction 0.6 (MATH-35)."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore
    from backend.services import profile as profile_service

    monkeypatch.setattr(
        profile_service,
        "read_profile",
        lambda: {"target_allocation": {"equity": 0.6, "crypto": 0.4}},
    )

    equity = InstrumentStore().create(
        {
            "symbol": "EQ",
            "name": "Equity Co",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "EQ",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": equity["id"],
            "account_id": None,
            "quantity": 6,
            "cost_basis": 100.0,
            "cost_currency": "SGD",
        }
    )
    crypto = InstrumentStore().create(
        {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "BTC",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": crypto["id"],
            "account_id": None,
            "quantity": 4,
            "cost_basis": 100.0,
            "cost_currency": "SGD",
        }
    )

    book = investments_book()
    by_cls = {s["cls"]: s for s in book["sections"]}
    assert by_cls["equity"]["target_pct"] == pytest.approx(60.0)
    assert by_cls["crypto"]["target_pct"] == pytest.approx(40.0)
    alloc_by_cls = {a["cls"]: a for a in book["allocation"]}
    assert alloc_by_cls["equity"]["drift_pp"] == pytest.approx(0.0, abs=0.01)
    assert alloc_by_cls["crypto"]["drift_pp"] == pytest.approx(0.0, abs=0.01)
    assert all(c["name"] != "AAA unvested tranche" for c in book["concentration"])


def test_trade_base_is_computed_at_full_precision_not_from_rounded_percents(
    monkeypatch, db_isolated
):
    """MATH-38: `Rebalance.tsx` used to round `current_pct`/`target_pct` to whole points BEFORE
    sizing a trade, which compounds the rounding into real money. Book 1,000,000, current
    32.6%, target 29.4% → the true trade is -32,000; sizing off the rounded 33%/29% gives
    -40,000. `trade_base` must be computed here, at full precision, so no caller has to."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore
    from backend.services import prices as prices_service
    from backend.services import profile as profile_service

    monkeypatch.setattr(prices_service, "fetch_live", lambda *a, **k: {"prices": {}, "fx": {}})
    monkeypatch.setattr(
        profile_service,
        "read_profile",
        lambda: {"target_allocation": {"equity": 0.294}},
    )

    equity = InstrumentStore().create(
        {
            "symbol": "EQ",
            "name": "Equity Co",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "EQ",
            "manual_price": 1.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": equity["id"],
            "account_id": None,
            "quantity": 326_000,
            "cost_basis": 1.0,
            "cost_currency": "SGD",
        }
    )
    crypto = InstrumentStore().create(
        {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "BTC",
            "manual_price": 1.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": crypto["id"],
            "account_id": None,
            "quantity": 674_000,
            "cost_basis": 1.0,
            "cost_currency": "SGD",
        }
    )

    book = investments_book()
    assert book["grand"] == pytest.approx(1_000_000.0, abs=0.02)
    alloc_by_cls = {a["cls"]: a for a in book["allocation"]}
    eq = alloc_by_cls["equity"]
    assert eq["current_pct"] == pytest.approx(32.6, abs=0.01)
    assert eq["target_pct"] == pytest.approx(29.4, abs=0.01)
    assert eq["drift_pp"] == pytest.approx(3.2, abs=0.01)
    # The real number, not the one a rounded-percent caller would derive (-40,000).
    assert eq["trade_base"] == pytest.approx(-32_000.0, abs=0.01)
    for a in book["allocation"]:
        assert a["trade_base"] == pytest.approx(
            round((-a["drift_pp"] / 100) * book["grand"], 2), abs=0.01
        )


def test_threshold_pp_is_the_one_rebalance_constant(invested):
    """MATH-37: the "off target" drift threshold used to be 3, 4 or 5 points depending on
    which surface read it. `rebalance.DRIFT_FLAG_PP` is the ONE authority now."""
    from backend.services import rebalance

    book = investments_book()
    assert book["threshold_pp"] == rebalance.DRIFT_FLAG_PP
    assert book["threshold_pp"] == 5.0


# ── unknown cost basis is UNKNOWN, never a gain of zero ──────────────────────
#
# Scar: three SHIB positions and a PEPE position imported without a cost read as a green
# "+S$0.00 0.00%" on the Holdings list — `pl: None` from portfolio_rows was collapsed to 0.0
# here, so "we don't know" rendered as "break-even" (2026-09-21).


@pytest.fixture
def invested_with_an_unknown_cost(invested, monkeypatch):
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore

    acct = next(a for a in AccountStore().all() if a["name"] == "Broker")
    inst = InstrumentStore().create(
        {
            "symbol": "SHIB",
            "name": "Shiba Inu",
            "asset_class": "crypto",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "SHIB",
            "manual_price": 0.00001,
        }
    )
    PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 10_000_000,
            "cost_basis": None,
            "cost_currency": "SGD",
            "bucket": "liquid",
        }
    )
    return invested


def test_a_holding_with_unknown_cost_reports_pl_as_none_not_zero(
    invested_with_an_unknown_cost,
):
    book = investments_book()
    shib = next(r for r in book["rows"] if r["symbol"] == "SHIB")
    assert shib["value"] == pytest.approx(100, abs=0.02)  # still valued and weighted
    assert shib["pl"] is None
    assert shib["pl_pct"] is None
    assert shib["cost_base"] is None


def test_an_unknown_cost_holding_is_left_out_of_total_pl_not_counted_as_zero(
    invested_with_an_unknown_cost,
):
    book = investments_book()
    # The two known rows: paid 1,120, worth 1,400 → +280, +25%. SHIB adds value, not P&L.
    assert book["grand"] == pytest.approx(1500, abs=0.02)
    assert book["total_pl"] == pytest.approx(280, abs=0.02)


def test_merging_a_known_leg_with_an_unknown_one_reports_unknown():
    """A merged row's P&L is the P&L of the WHOLE holding. Summing the known leg alone would
    claim a figure for shares whose cost was never recorded."""
    rows = [
        _row(),
        _row(
            row_key="p2",
            position_id="p2",
            account="Broker B",
            account_id="acc-b",
            value=500.0,
            pl=None,
            pl_pct=None,
            cost_base=None,
            cost=0.0,
            qty=5.0,
        ),
    ]
    merged = _merge_by_instrument(rows, grand=1500.0)
    assert len(merged) == 1
    r = merged[0]
    assert r["value"] == pytest.approx(1500)  # value is still the sum
    assert r["pl"] is None
    assert r["pl_pct"] is None
    assert r["cost_base"] is None
    assert r["cost"] == 0.0  # the book's "unknown native cost" convention


def test_merging_only_unknown_legs_reports_unknown():
    rows = [
        _row(pl=None, pl_pct=None, cost_base=None, cost=0.0),
        _row(
            row_key="p2",
            position_id="p2",
            account="Broker B",
            account_id="acc-b",
            pl=None,
            pl_pct=None,
            cost_base=None,
            cost=0.0,
        ),
    ]
    r = _merge_by_instrument(rows, grand=2000.0)[0]
    assert r["pl"] is None
    assert r["pl_pct"] is None
    assert r["cost_base"] is None


# ── Single-name concentration never flags a diversified fund ─────────────────────────────


def _held(iid, symbol, value, *, sub="stock", asset_class="equity", day_change=None):
    return {
        "instrument_id": iid,
        "symbol": symbol,
        "ticker": symbol,
        "label": f"{symbol} Inc",
        "asset_class": asset_class,
        "asset_subclass": sub,
        "quantity": 1,
        "price": value,
        "quote_per_unit": 1.0,
        "quote_currency": "SGD",
        "currency": "SGD",
        "cost_basis": value,
        "day_change": day_change,
        "vesting_state": "vested",
        "account_id": "a1",
    }


# 39% in an all-world index ETF (the persona's VWRA), 22% in one company, the rest spread thin.
HELD = [
    _held("i_etf", "VWRA", 39_000, sub="etf"),
    _held("i_msft", "MSFT", 22_000),
    _held("i_aaa", "AAA", 14_000),
    _held("i_bbb", "BBB", 13_000),
    _held("i_ccc", "CCC", 12_000),
]


def test_an_all_world_index_fund_is_not_a_single_name():
    from backend.services import investments_book

    projected, grand = investments_book.project_invested(HELD, "SGD", {"SGD": 1.0})
    ranked = investments_book.concentration_by_name(projected, grand, 20.0)

    assert "VWRA" not in [c["name"] for c in ranked]
    assert [c["name"] for c in ranked if c["hot"]] == ["MSFT"]  # a real single name still breaches
    # The denominator is still the whole invested book, funds included.
    assert ranked[0]["pct"] == pytest.approx(22.0)


def test_every_fund_wrapper_over_equities_or_bonds_is_exempt_but_a_commodity_etp_is_not():
    from backend.services import investments_book

    for sub in ("etf", "index", "mutual_fund", "etp"):
        assert investments_book.is_diversified_fund(_held("i", "X", 1, sub=sub)) is True
    assert investments_book.is_diversified_fund(_held("i", "BND", 1, sub="etf", asset_class="bond"))
    # One commodity or coin is one exposure however it is wrapped.
    for asset_class, sub in (("commodity", "etc"), ("crypto", "etf")):
        row = _held("i", "G", 1, sub=sub, asset_class=asset_class)
        assert investments_book.is_diversified_fund(row) is False
    assert investments_book.is_diversified_fund(_held("i", "MSFT", 1)) is False
    # Book rows (the Risk card's input) carry `sub`/`cls` instead.
    assert investments_book.is_diversified_fund({"sub": "etf", "cls": "equity"}) is True


def test_the_risk_card_brief_and_risk_story_agree_a_fund_is_not_flagged():
    from backend.services import investments_book, portfolio_brief, risk_signals

    projected, grand = investments_book.project_invested(HELD, "SGD", {"SGD": 1.0})
    book = investments_book.concentration_by_name(projected, grand, 20.0)

    brief = portfolio_brief._concentration_signal(HELD, "SGD", 20.0)
    assert brief is not None
    assert "MSFT is 22% of your investments" in brief["narrative"]
    assert "VWRA" not in brief["narrative"]

    story = [
        f
        for f in risk_signals.compute_risk(
            rows=HELD, rates={"SGD": 1.0}, profile={"base_currency": "SGD"}
        )
        if f["kind"] == "single_name"
    ]
    assert [f["affected_instrument_ids"] for f in story] == [["i_msft"]]
    assert story[0]["metric_pct"] == pytest.approx(book[0]["pct"])


def test_a_book_of_only_a_broad_fund_has_no_concentration_flag():
    from backend.services import portfolio_brief

    only_etf = [_held("i_etf", "VWRA", 76_000, sub="etf")]
    assert portfolio_brief._concentration_signal(only_etf, "SGD", 20.0) is None


def test_the_concentration_copy_is_information_not_an_order():
    """Persona QA round 7: a day-one user was told to trim against a 20% rule they never set."""
    from backend.services import portfolio_brief

    narrative = portfolio_brief._concentration_signal(HELD, "SGD", 20.0)["narrative"]
    assert narrative == (
        "MSFT is 22% of your investments, 2 percentage points over the 20% single-name guideline."
    )
    assert "trim" not in narrative.lower()


def test_a_guideline_the_user_chose_is_theirs_not_a_default():
    from backend.services import portfolio_brief

    narrative = portfolio_brief._concentration_signal(HELD, "SGD", 15.0)["narrative"]
    assert narrative == (
        "MSFT is 22% of your investments, 7 percentage points over your 15% single-name guideline."
    )
    assert "default" not in narrative


def test_the_pulse_still_weights_a_fund_by_asking_for_every_holding():
    from backend.services import investments_book

    projected, grand = investments_book.project_invested(HELD, "SGD", {"SGD": 1.0})
    everything = investments_book.concentration_by_name(projected, grand, 100.0, names_only=False)
    assert everything[0]["name"] == "VWRA" and everything[0]["pct"] == pytest.approx(39.0)
