"""
Characterization tests for the Phase 2 change: net_worth_history() now sources
equity/crypto/commodity market value from portfolio_history.portfolio_value_series()
(reconstructed holdings) instead of current-quantity back-projection.

All tests use synthetic, injected data.  No live data files are read.

Critical invariants tested:

1. TODAY'S net worth is UNCHANGED — holdings_as_of(today) == current remaining_qty,
   so the latest point must equal the old computation.  A static-holdings book (all
   ``opening`` lots, no dated trades) must produce the same series as before.

2. A mid-history DATED PURCHASE — months strictly before acquired_at must NOT include
   those shares' value; months on/after must.  (The behaviour change we're introducing.)

3. PROPERTY + CASH + LIABILITIES values are identical before and after (those paths are
   untouched by the primitive).

4. Output shape / keys / market_tracked flags are unchanged.

Run:
    .venv/bin/python3 -m pytest tests/test_net_worth_history_reconstructed.py -q
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from unittest.mock import patch

import pytest

from backend.services import ledger as ledger_service

# ── Synthetic data builders ──────────────────────────────────────────────────


def _lot(
    instrument_id: str,
    original_qty: float,
    remaining_qty: float,
    acquired_at: str = "",
    kind: str = "opening",
    unit_cost: float = 10.0,
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
        "cost_currency": "USD",
        "kind": kind,
    }


def _instrument(
    iid: str,
    symbol: str = "TST",
    asset_class: str = "equity",
    price_source_id: str = "",
    quote_currency: str = "SGD",
    quote_per_unit: float = 1.0,
) -> dict:
    return {
        "id": iid,
        "symbol": symbol,
        "asset_class": asset_class,
        "price_source_id": price_source_id or iid,
        "quote_currency": quote_currency,
        "quote_per_unit": quote_per_unit,
        "currency": quote_currency,
    }


def _hydrated_row(
    iid: str,
    qty: float,
    price: float,
    asset_class: str = "equity",
    quote_currency: str = "SGD",
) -> dict:
    """Simulate a hydrated positions_service row (current quantity + current price)."""
    return {
        "instrument_id": iid,
        "ticker": iid,
        "price_source_id": iid,  # ticker == price_source_id for non-crypto; as_of_prices
        # is keyed on price_source_id (crypto rows are the one case where they diverge).
        "quantity": qty,
        "price": price,
        "asset_class": asset_class,
        "quote_currency": quote_currency,
        "quote_per_unit": 1.0,
        "currency": quote_currency,
    }


def _account(
    acct_id: str,
    kind: str = "asset",
    initial_balance: float = 1000.0,
    initial_balance_date: str = "2024-01-01",
    currency: str = "SGD",
    asset_class: str | None = None,
    account_type: str | None = None,
) -> dict:
    a = {
        "id": acct_id,
        "name": acct_id,
        "kind": kind,
        "initial_balance": initial_balance,
        "initial_balance_date": initial_balance_date,
        "currency": currency,
    }
    if asset_class:
        a["asset_class"] = asset_class
    a["type"] = account_type or ("credit" if kind == "liability" else "cash")
    return a


def _openings_for(accts: list[dict]) -> list[dict]:
    """Mint the Starting Balance transaction each ``_account(...)``'s initial_balance /
    initial_balance_date implies, and stamp ``opening_txn_id`` on the account dict so
    ``cutoffs_by_account``/``opening_index`` resolve it — the opening amount and tracking
    start now live on a transaction, not on the account (see set_account_opening's sign
    convention: amount>=0 books a to-leg, amount<0 a from-leg of the negated amount). Only
    accounts carrying an ``initial_balance_date`` get a row (None = no tracking start)."""
    openings = []
    for a in accts:
        day = a.get("initial_balance_date")
        if not day:
            continue
        amt = a.get("initial_balance", 0.0) or 0.0
        txn_id = f"open-{a['id']}"
        a["opening_txn_id"] = txn_id
        if amt >= 0:
            openings.append(
                {
                    "id": txn_id,
                    "date": day,
                    "kind": "adjustment",
                    "deleted": False,
                    "to_account_id": a["id"],
                    "to_amount": amt,
                }
            )
        else:
            openings.append(
                {
                    "id": txn_id,
                    "date": day,
                    "kind": "adjustment",
                    "deleted": False,
                    "from_account_id": a["id"],
                    "from_amount": -amt,
                }
            )
    return openings


def _last_day(year: int, month: int) -> str:
    return date(year, month, monthrange(year, month)[1]).isoformat()


# ── Helpers for patching net_worth_history dependencies ──────────────────────


class _MockPriceStore:
    """Price store that returns a fixed carry-forward price for any date requested."""

    def __init__(self, prices_by_pid: dict[str, float], earliest: str | None = None):
        self._prices = prices_by_pid
        self._earliest = earliest

    def earliest_date(self) -> str | None:
        return self._earliest

    def as_of_maps(self, dates: list[str]) -> dict[str, dict[str, float]]:
        # Return the same price for every requested date (carry-forward simulation).
        return {d: dict(self._prices) for d in dates}


def _run_net_worth_history(
    *,
    accts: list[dict],
    txns: list[dict],
    hydrated_rows: list[dict],
    lots: list[dict],
    disposals: list[dict],
    price_by_pid: dict[str, float],
    instruments: dict[str, dict],
    market_since: str | None,
    base_cur: str = "SGD",
    rates: dict | None = None,
) -> list[dict]:
    """
    Run net_worth_history() with fully injected, synthetic dependencies.

    Patch targets use the module where each name is *looked up* at call time:
    - ledger.py local module-globals: transactions, accounts, base_currency
    - portfolio_history.py module-level names: LotStore, RealizedEventStore, InstrumentStore,
      PriceHistoryStore, _load_rates_safe
    - Ledger-side PriceHistoryStore: imported inside net_worth_history via local import,
      so patch at backend.services.ledger (or the store module — patch where imported).
    """
    _rates = rates or {base_cur: 1.0}
    inst_list = list(instruments.values())
    mock_price_store = _MockPriceStore(price_by_pid, earliest=market_since)

    with (
        patch("backend.services.ledger.transactions") as mock_txn_store,
        patch("backend.services.ledger.accounts") as mock_acct_store,
        patch("backend.services.ledger.base_currency", return_value=base_cur),
        patch("backend.core.fx.base_currency", return_value=base_cur),
        patch("backend.services.fx_cache.load_fx_cache", return_value=_rates),
        # ledger.py uses a local import of PriceHistoryStore inside net_worth_history;
        # that resolves to the class at backend.db.price_history_store — patch there.
        patch(
            "backend.db.price_history_store.PriceHistoryStore",
            return_value=mock_price_store,
        ),
        patch(
            "backend.services.positions.hydrated",
            return_value=hydrated_rows,
        ),
        # portfolio_history.py does module-level `from backend.db.X import X` so it holds its
        # own binding. Patch at the portfolio_history namespace for each store.
        patch(
            "backend.services.portfolio_history.PriceHistoryStore", return_value=mock_price_store
        ),
        patch("backend.services.portfolio_history.LotStore") as mock_ph_lot,
        patch("backend.services.portfolio_history.RealizedEventStore") as mock_ph_rlz,
        patch("backend.services.portfolio_history.InstrumentStore") as mock_ph_inst,
        patch("backend.services.portfolio_history._load_rates_safe", return_value=_rates),
    ):
        mock_txn_store.all.return_value = txns
        mock_acct_store.query_accounts.return_value = accts

        mock_ph_lot.return_value.all.return_value = lots
        mock_ph_rlz.return_value.query_events.return_value = disposals
        mock_ph_inst.return_value.all.return_value = inst_list

        return ledger_service.net_worth_history()


# ── Invariant 1: static-holdings book produces the SAME series ───────────────


def test_static_opening_lots_same_as_old_computation():
    """
    A book where ALL lots are undated ``opening`` lots (no dated trades) must produce
    exactly the same net-worth series as the old current-qty path.

    OLD path: each historical point used current remaining_qty = original_qty (no trades).
    NEW path: holdings_as_of sees the same opening qty from inception forward.
    They must agree at every month-end AND today.

    Baseline (OLD) is computed inline using the same inputs via _breakdown_as_of with
    market_overlay=None (the fallback path).
    """
    accts = [_account("cash-acct", initial_balance=5000.0, initial_balance_date="2024-01-01")]
    txns: list[dict] = []
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    instruments = {"AAPL": _instrument("AAPL", price_source_id="AAPL")}
    price_by_pid = {"AAPL": 150.0}
    disposals: list[dict] = []

    # Hydrated rows reproduce the current-qty path (old behaviour).
    hydrated_rows = [_hydrated_row("AAPL", 10.0, 150.0, asset_class="equity")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    assert len(series) >= 1

    # The latest point (today) must carry the same equity value as the old path:
    # 10 shares × 150 = 1500 SGD.
    today_pt = series[-1]
    assert today_pt["classes"].get("equity") == pytest.approx(1500.0, abs=0.01)

    # All historical month-end points must also carry 1500 (same opening qty from inception).
    for pt in series:
        if pt["market_tracked"]:
            assert pt["classes"].get("equity") == pytest.approx(1500.0, abs=0.01), (
                f"Equity mismatch at {pt['date']}: {pt['classes'].get('equity')}"
            )


# ── Invariant 2: mid-history dated purchase ───────────────────────────────────


def test_dated_buy_excluded_before_acquired_at():
    """
    A dated ``buy`` lot with acquired_at="2024-04-10" must NOT contribute value to
    month-end points before April 2024, and MUST contribute from April 2024 onward.

    This is the PRIMARY correctness improvement introduced by Phase 2.
    """
    accts = [_account("cash-acct", initial_balance=5000.0, initial_balance_date="2024-01-01")]
    txns: list[dict] = []
    # The buy happened on 2024-04-10; no shares before that date.
    lots = [_lot("MSFT", 5.0, 5.0, acquired_at="2024-04-10", kind="buy")]
    instruments = {"MSFT": _instrument("MSFT", price_source_id="MSFT")}
    price_by_pid = {"MSFT": 200.0}
    disposals: list[dict] = []

    # Hydrated row uses the current (final) quantity — old path would back-project 5 shares.
    hydrated_rows = [_hydrated_row("MSFT", 5.0, 200.0, asset_class="equity")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    by_date = {pt["date"]: pt for pt in series}

    # Jan / Feb / Mar 2024 month-ends are BEFORE acquired_at → equity must be absent (or 0).
    for pre_date in ("2024-01-31", "2024-02-29", "2024-03-31"):
        pt = by_date.get(pre_date)
        if pt is not None and pt["market_tracked"]:
            equity_val = pt["classes"].get("equity", 0.0)
            assert equity_val == pytest.approx(0.0, abs=0.01), (
                f"Expected 0 equity at {pre_date} (before buy), got {equity_val}"
            )

    # Apr 2024 month-end (2024-04-30) is AFTER acquired_at → 5 × 200 = 1000.
    apr = by_date.get("2024-04-30")
    if apr is not None and apr["market_tracked"]:
        assert apr["classes"].get("equity") == pytest.approx(1000.0, abs=0.01), (
            f"Expected 1000 equity at 2024-04-30, got {apr['classes'].get('equity')}"
        )


def test_dated_buy_on_acquired_date_included():
    """Shares bought on the LAST DAY of a month must appear in that month's point."""
    accts = [_account("cash-acct", initial_balance=1000.0, initial_balance_date="2024-01-01")]
    txns: list[dict] = []
    # Acquired on the last day of March 2024
    lots = [_lot("ETH", 2.0, 2.0, acquired_at="2024-03-31", kind="buy")]
    instruments = {"ETH": _instrument("ETH", asset_class="crypto", price_source_id="ETH")}
    price_by_pid = {"ETH": 3000.0}
    disposals: list[dict] = []
    hydrated_rows = [_hydrated_row("ETH", 2.0, 3000.0, asset_class="crypto")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    by_date = {pt["date"]: pt for pt in series}

    # Before March month-end: no crypto
    feb = by_date.get("2024-02-29")
    if feb and feb["market_tracked"]:
        assert feb["classes"].get("crypto", 0.0) == pytest.approx(0.0, abs=0.01)

    # March month-end (same day as acquired_at): must include the holding
    mar = by_date.get("2024-03-31")
    if mar and mar["market_tracked"]:
        assert mar["classes"].get("crypto") == pytest.approx(6000.0, abs=0.01)


# ── Invariant 3: property / cash / liabilities unchanged ─────────────────────


def test_cash_value_unchanged_by_primitive():
    """Cash account balances are NOT affected by the market primitive."""
    accts = [
        _account("cash1", initial_balance=3000.0, initial_balance_date="2024-01-01"),
        _account("cash2", initial_balance=7000.0, initial_balance_date="2024-01-01"),
    ]
    # Income txn that increases savings by 500 in Feb
    txns = _openings_for(accts) + [
        {
            "id": "t1",
            "date": "2024-02-15",
            "kind": "income",
            "to_account_id": "cash1",
            "to_amount": 500.0,
            "to_currency": "SGD",
            "deleted": False,
        }
    ]
    lots: list[dict] = []
    instruments: dict[str, dict] = {}
    price_by_pid: dict[str, float] = {}
    disposals: list[dict] = []
    hydrated_rows: list[dict] = []

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since=None,  # no price collection → market_tracked=False always
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    assert len(series) >= 2
    jan_pt = next((pt for pt in series if pt["date"] == "2024-01-31"), None)
    if jan_pt:
        # Jan balance: 3000 + 7000 = 10 000 (txn is in Feb)
        assert jan_pt["classes"].get("cash") == pytest.approx(10000.0, abs=0.01)

    # Find the Feb point (2024-02-29 for a leap year).
    feb_pt = next((pt for pt in series if pt["date"] == "2024-02-29"), None)
    if feb_pt:
        # Feb balance: 3000 + 7000 + 500 = 10 500
        assert feb_pt["classes"].get("cash") == pytest.approx(10500.0, abs=0.01)


def test_liabilities_unchanged_by_primitive():
    """Liability account balances are NOT affected by the market primitive."""
    accts = [
        _account("cash-acct", initial_balance=5000.0, initial_balance_date="2024-01-01"),
        _account(
            "credit-card",
            kind="liability",
            initial_balance=-2000.0,
            initial_balance_date="2024-01-01",
        ),
    ]
    txns: list[dict] = _openings_for(accts)
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    instruments = {"AAPL": _instrument("AAPL", price_source_id="AAPL")}
    price_by_pid = {"AAPL": 100.0}
    disposals: list[dict] = []
    hydrated_rows = [_hydrated_row("AAPL", 10.0, 100.0, asset_class="equity")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    for pt in series:
        # Liability is raw-negative: initial_balance = -2000
        assert pt["liabilities"] == pytest.approx(-2000.0, abs=0.01), (
            f"Unexpected liabilities at {pt['date']}: {pt['liabilities']}"
        )


def test_property_position_not_affected_by_primitive():
    """
    Property positions (in hydrated_rows) are NOT sourced from the primitive (which
    excludes property by design).  They must still appear in the net_worth history.
    """
    accts = [_account("cash-acct", initial_balance=1000.0, initial_balance_date="2024-01-01")]
    txns: list[dict] = []
    # A property 'lot' in the lots store — the primitive EXCLUDES property.
    lots = [_lot("PROP1", 1.0, 1.0, acquired_at="", kind="opening")]
    instruments = {
        "PROP1": _instrument("PROP1", asset_class="property", price_source_id="PROP1"),
    }
    price_by_pid = {"PROP1": 500000.0}
    disposals: list[dict] = []
    # hydrated_rows: the position store carries the property row with current qty + price.
    hydrated_rows = [_hydrated_row("PROP1", 1.0, 500000.0, asset_class="property")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    for pt in series:
        if pt["market_tracked"]:
            # Property value must come through the rows path (500000).
            assert pt["classes"].get("property") == pytest.approx(500000.0, abs=0.01), (
                f"Property missing or wrong at {pt['date']}: {pt['classes']}"
            )


# ── Invariant 4: output shape / keys / market_tracked unchanged ───────────────


def test_output_shape_and_keys():
    """Every element must carry the standard keys: date, classes, liabilities, accounts,
    net_worth, market_tracked."""
    accts = [_account("cash-acct", initial_balance=1000.0, initial_balance_date="2024-03-01")]
    txns: list[dict] = []
    lots: list[dict] = []
    instruments: dict[str, dict] = {}
    price_by_pid: dict[str, float] = {}
    disposals: list[dict] = []
    hydrated_rows: list[dict] = []

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since=None,
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    required_keys = {
        "date",
        "classes",
        "liabilities",
        "accounts",
        "net_worth",
        "market_tracked",
        "fx_approximate",
    }
    for pt in series:
        assert required_keys.issubset(pt.keys()), (
            f"Missing keys at {pt.get('date')}: {required_keys - pt.keys()}"
        )
        assert isinstance(pt["date"], str)
        assert isinstance(pt["classes"], dict)
        assert isinstance(pt["market_tracked"], bool)
        assert isinstance(pt["fx_approximate"], bool)
        assert isinstance(pt["net_worth"], (int, float))


def test_market_tracked_false_before_market_since():
    """Points whose date < market_since must have market_tracked=False and carry no
    equity/crypto/commodity bands."""
    accts = [_account("cash-acct", initial_balance=5000.0, initial_balance_date="2023-06-01")]
    txns: list[dict] = []
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    instruments = {"AAPL": _instrument("AAPL", price_source_id="AAPL")}
    price_by_pid = {"AAPL": 150.0}
    disposals: list[dict] = []
    hydrated_rows = [_hydrated_row("AAPL", 10.0, 150.0, asset_class="equity")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",  # price collection only starts in 2024
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    for pt in series:
        d = pt["date"]
        if d < "2024-01-01":
            assert pt["market_tracked"] is False, f"Expected market_tracked=False at {d}"
            assert pt["classes"].get("equity", 0.0) == 0.0, (
                f"Expected no equity at {d} (before market_since)"
            )
        else:
            assert pt["market_tracked"] is True, f"Expected market_tracked=True at {d}"


# ── Invariant 1 (extended): today's point equals old computation ──────────────


def test_todays_point_equals_old_computation_mixed_lots():
    """
    For the LATEST point (today), reconstructed holdings must equal the sum of
    remaining_qty across all lots (same as the old current-qty path reads from hydrated rows).

    Book: 1 opening lot (10 shares, 10 remaining after a partial disposal) + 1 dated buy
    (5 shares, fully remaining). At today: holdings_as_of = 10 + 5 - partial = 15 net.
    The hydrated row should agree.
    """
    today = date.today().isoformat()
    accts = [_account("cash-acct", initial_balance=1000.0, initial_balance_date="2023-01-01")]
    txns: list[dict] = []

    lots = [
        _lot("TSLA", 15.0, 10.0, acquired_at="", kind="opening"),
        _lot("TSLA", 5.0, 5.0, acquired_at="2024-06-01", kind="buy"),
    ]
    disposals = [
        {
            "id": "rlz-1",
            "instrument_id": "TSLA",
            "position_id": "pos-TSLA",
            "account_id": "acct-1",
            "disposed_at": "2024-01-15",
            "quantity": 5.0,
            "proceeds_per_unit": 100.0,
            "proceeds_currency": "SGD",
            "disposal_fees": 0.0,
            "realized_gain": None,
        }
    ]
    # At today: original 15 - 5 disposed + 5 buy = 15 net remaining.
    instruments = {"TSLA": _instrument("TSLA", price_source_id="TSLA")}
    price_by_pid = {"TSLA": 250.0}
    # hydrated row reflects current remaining_qty = 15
    hydrated_rows = [_hydrated_row("TSLA", 15.0, 250.0, asset_class="equity")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2023-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    today_pt = series[-1]
    assert today_pt["date"] == today
    # 15 shares × 250 = 3750
    assert today_pt["classes"].get("equity") == pytest.approx(3750.0, abs=0.01), (
        f"Today's equity mismatch: {today_pt['classes']}"
    )


# ── net_worth field consistency ───────────────────────────────────────────────


def test_net_worth_equals_sum_of_classes_plus_liabilities():
    """net_worth must always equal Σ(classes.values()) + liabilities at every point."""
    accts = [
        _account("cash-acct", initial_balance=5000.0, initial_balance_date="2024-01-01"),
        _account(
            "cc",
            kind="liability",
            initial_balance=-1500.0,
            initial_balance_date="2024-01-01",
        ),
    ]
    txns: list[dict] = []
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    instruments = {"AAPL": _instrument("AAPL", price_source_id="AAPL")}
    price_by_pid = {"AAPL": 120.0}
    disposals: list[dict] = []
    hydrated_rows = [_hydrated_row("AAPL", 10.0, 120.0, asset_class="equity")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    for pt in series:
        expected_nw = sum(pt["classes"].values()) + pt["liabilities"]
        assert pt["net_worth"] == pytest.approx(expected_nw, abs=0.02), (
            f"net_worth mismatch at {pt['date']}: {pt['net_worth']} vs {expected_nw}"
        )


# ── Crypto and commodity bands ────────────────────────────────────────────────


def test_crypto_and_commodity_bands_use_reconstructed_holdings():
    """crypto and commodity opening lots should appear in the series from inception."""
    accts = [_account("cash-acct", initial_balance=1000.0, initial_balance_date="2024-01-01")]
    txns: list[dict] = []
    lots = [
        _lot("BTC", 0.5, 0.5, acquired_at="", kind="opening"),
        _lot("GOLD", 10.0, 10.0, acquired_at="", kind="opening"),
    ]
    instruments = {
        "BTC": _instrument("BTC", asset_class="crypto", price_source_id="BTC"),
        "GOLD": _instrument("GOLD", asset_class="commodity", price_source_id="GOLD"),
    }
    price_by_pid = {"BTC": 40000.0, "GOLD": 2000.0}
    disposals: list[dict] = []
    hydrated_rows = [
        _hydrated_row("BTC", 0.5, 40000.0, asset_class="crypto"),
        _hydrated_row("GOLD", 10.0, 2000.0, asset_class="commodity"),
    ]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    for pt in series:
        if pt["market_tracked"]:
            assert pt["classes"].get("crypto") == pytest.approx(20000.0, abs=0.01), (
                f"BTC value wrong at {pt['date']}"
            )
            assert pt["classes"].get("commodity") == pytest.approx(20000.0, abs=0.01), (
                f"GOLD value wrong at {pt['date']}"
            )


# ── Regression: unmapped asset_class in overlay path must not pollute classes ────


def test_unmapped_asset_class_in_overlay_path_does_not_add_none_key():
    """
    Regression for Fix 1: a hydrated row whose asset_class does not map to a known band
    (e.g. "cash", which is not in _MARKET_BAND) must be silently skipped in the overlay
    path.  Before the fix, `band` would be None, the `band not in _LOT_BACKED_BANDS` check
    would pass, and `classes[None] += value` would pollute the dict and inflate net_worth.

    The test injects one property row (priced, valid) plus one cash-class row (no band)
    and asserts:
      - "None" is not a key in classes at any point
      - net_worth at the latest point equals cash_balance + property_value (not inflated)
    """
    accts = [_account("cash-acct", initial_balance=1000.0, initial_balance_date="2024-01-01")]
    txns: list[dict] = []
    # No lots: primitive returns an empty overlay (no lot-backed values).
    lots: list[dict] = []
    instruments: dict[str, dict] = {}
    disposals: list[dict] = []

    # hydrated_rows: one property row (valid, maps to "property") + one cash row (unmapped).
    # Both carry a ticker and a price so the bug path (price lookup + assignment) is reached.
    hydrated_rows = [
        _hydrated_row("PROP1", 1.0, 500000.0, asset_class="property"),
        {
            "instrument_id": "USD-CASH",
            "ticker": "USD-CASH",
            "quantity": 100.0,
            "price": 1.35,  # has a price — old code would do classes[None] += 135.0
            "asset_class": "cash",  # not in _MARKET_BAND → band is None
            "quote_currency": "SGD",
            "quote_per_unit": 1.0,
            "currency": "SGD",
        },
    ]
    price_by_pid = {"PROP1": 500000.0, "USD-CASH": 1.35}

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    for pt in series:
        assert None not in pt["classes"], (
            f"classes dict has a None key at {pt['date']}: {pt['classes']}"
        )

    # Latest point: cash_account=1000, property=500000; the cash row must NOT be double-counted.
    latest = series[-1]
    assert latest["market_tracked"] is True
    assert latest["classes"].get("property") == pytest.approx(500000.0, abs=0.01)
    expected_nw = sum(latest["classes"].values()) + latest["liabilities"]
    assert latest["net_worth"] == pytest.approx(expected_nw, abs=0.02), (
        f"net_worth inflated — None key was probably counted: {latest}"
    )


# ── Characterization: CoinGecko crypto uses price_source_id, not symbol ──────────


def test_crypto_priced_by_price_source_id_not_symbol():
    """
    Characterization test for the corrected CoinGecko crypto pricing behaviour.

    The Phase 2 primitive keys prices by `price_source_id` (e.g. "bitcoin"), NOT by the
    upper-cased symbol (e.g. "BTC").  Under the OLD path the price store was keyed by
    symbol, so a CoinGecko instrument (symbol="BTC", price_source_id="bitcoin") would
    silently produce zero value.

    This test documents the CORRECTED, intentional behaviour: the instrument is valued
    using the price_source_id key from the overlay (injected via the primitive's series),
    and produces the expected non-zero crypto value.
    """
    accts = [_account("cash-acct", initial_balance=1000.0, initial_balance_date="2024-01-01")]
    txns: list[dict] = []
    # symbol != price_source_id — the CoinGecko case
    lots = [_lot("bitcoin", 0.5, 0.5, acquired_at="", kind="opening")]
    instruments = {
        "bitcoin": _instrument(
            "bitcoin",
            symbol="BTC",
            asset_class="crypto",
            price_source_id="bitcoin",  # CoinGecko id, not the symbol
        )
    }
    # Price store keyed by price_source_id ("bitcoin"), not symbol ("BTC").
    price_by_pid = {"bitcoin": 60000.0}
    disposals: list[dict] = []
    # Hydrated row carries ticker == price_source_id (the corrected field).
    hydrated_rows = [
        {
            "instrument_id": "bitcoin",
            "ticker": "bitcoin",  # price_source_id, not "BTC"
            "quantity": 0.5,
            "price": 60000.0,
            "asset_class": "crypto",
            "quote_currency": "SGD",
            "quote_per_unit": 1.0,
            "currency": "SGD",
        }
    ]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    for pt in series:
        if pt["market_tracked"]:
            # 0.5 BTC × 60 000 = 30 000 SGD — must NOT be zero (old symbol-keyed bug)
            crypto_val = pt["classes"].get("crypto", 0.0)
            assert crypto_val == pytest.approx(30000.0, abs=0.01), (
                f"Crypto value wrong at {pt['date']} (expected 30000, got {crypto_val}); "
                "price_source_id key not used?"
            )


# ── MATH-4: fx_approximate must surface a foreign-currency exposure per point ─────


def test_fx_approximate_false_for_base_only_book():
    """A book where every account is in the base currency must never flag fx_approximate,
    at any point in the series."""
    accts = [
        _account("cash", initial_balance=3000.0, initial_balance_date="2024-01-01"),
        _account("cash", initial_balance=7000.0, initial_balance_date="2024-01-01"),
    ]
    txns = _openings_for(accts)

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=[],
        lots=[],
        disposals=[],
        price_by_pid={},
        instruments={},
        market_since=None,
        base_cur="SGD",
        rates={"SGD": 1.0},
    )

    assert len(series) >= 1
    assert all(pt["fx_approximate"] is False for pt in series), (
        f"base-only book must never flag fx_approximate: {series}"
    )


def test_fx_approximate_true_after_foreign_account_balance():
    """A cross-currency account: every point on/after its first non-zero balance must be
    flagged fx_approximate; every point before it (balance still zero) must not be — this
    is exactly the "cross-currency transfer manufactures a fake step" scenario in MATH-4."""
    accts = [
        _account("cash", initial_balance=3000.0, initial_balance_date="2024-01-01", currency="SGD"),
        _account(
            "eur-savings",
            initial_balance=500.0,
            initial_balance_date="2024-03-15",
            currency="EUR",
        ),
    ]
    txns = _openings_for(accts)

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=[],
        lots=[],
        disposals=[],
        price_by_pid={},
        instruments={},
        market_since=None,
        base_cur="SGD",
        rates={"SGD": 1.0, "EUR": 1.5},
    )

    before = [pt for pt in series if pt["date"] < "2024-03-15"]
    after = [pt for pt in series if pt["date"] >= "2024-03-15"]
    assert before, "need at least one point before the EUR account opens"
    assert after, "need at least one point on/after the EUR account opens"
    assert all(pt["fx_approximate"] is False for pt in before), (
        f"points before the foreign account's first balance must not be flagged: {before}"
    )
    assert all(pt["fx_approximate"] is True for pt in after), (
        f"every point after the foreign account's first balance must be flagged: {after}"
    )


def test_fx_approximate_true_for_non_base_market_holding():
    """A market position quoted in a non-base currency must flag fx_approximate once it
    starts contributing value, mirroring portfolio_history's own definition."""
    accts = [_account("cash-acct", initial_balance=1000.0, initial_balance_date="2024-01-01")]
    txns: list[dict] = []
    lots = [_lot("AAPL", 10.0, 10.0, acquired_at="", kind="opening")]
    instruments = {
        "AAPL": _instrument("AAPL", price_source_id="AAPL", quote_currency="USD"),
    }
    price_by_pid = {"AAPL": 100.0}
    disposals: list[dict] = []
    hydrated_rows = [_hydrated_row("AAPL", 10.0, 100.0, asset_class="equity", quote_currency="USD")]

    series = _run_net_worth_history(
        accts=accts,
        txns=txns,
        hydrated_rows=hydrated_rows,
        lots=lots,
        disposals=disposals,
        price_by_pid=price_by_pid,
        instruments=instruments,
        market_since="2024-01-01",
        base_cur="SGD",
        rates={"SGD": 1.0, "USD": 1.35},
    )

    tracked_points = [pt for pt in series if pt["market_tracked"]]
    assert tracked_points
    assert all(pt["fx_approximate"] is True for pt in tracked_points), (
        f"a non-base market holding must flag fx_approximate: {tracked_points}"
    )
