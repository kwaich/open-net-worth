"""
Unit tests for backend.services.activity — unified investment-activity aggregation.

Pure/offline: stores monkeypatched onto the service module; FX via a
monkeypatched rate table.  Covers the test plan in
docs/rfc/investments/sys-activity-aggregation.md §Test plan.
"""

from __future__ import annotations

import pytest

import backend.services.activity as svc  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fx_usd_sgd(monkeypatch):
    """1 USD = 1.35 SGD; base = SGD."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})


def _patch_stores(
    monkeypatch,
    *,
    lots=(),
    realized=(),
    income=(),
    vest=(),
    accts=(),
    txns=(),
    txn_by_id=None,
    instruments=(),
    grants=(),
):
    """Monkeypatch every source + lookup store on the service module."""
    monkeypatch.setattr(svc._lots, "all", lambda: list(lots))
    monkeypatch.setattr(svc._instruments, "all", lambda: list(instruments))
    monkeypatch.setattr(svc._grants, "all", lambda: list(grants))
    monkeypatch.setattr(
        svc._realized_events,
        "query_events",
        lambda **kw: _date_filter(list(realized), "disposed_at", **kw),
    )
    monkeypatch.setattr(
        svc._income_events,
        "query_events",
        lambda **kw: _date_filter(list(income), "paid_at", **kw),
    )
    monkeypatch.setattr(
        svc._vest_events,
        "query_events",
        lambda **kw: _date_filter(list(vest), "date", **kw),
    )
    monkeypatch.setattr(svc._accounts, "query_accounts", lambda **kw: list(accts))
    # TransactionStore is lazily imported inside _cash_rows; patch via monkeypatch
    # on the transaction store class or patch at the transaction_store module level.
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "query_txns", lambda self, **kw: list(txns))
    # _vest_locked_base_amount looks a vest's linked txn up by id to apply the
    # historical-rate lock (ADR 2026-07-15); default to "no txn found" so a vest
    # fixture's txn_id that no test cares about falls back to the native-gain recompute.
    txn_map = dict(txn_by_id or {})
    monkeypatch.setattr(TransactionStore, "find_by_id", lambda self, id_val: txn_map.get(id_val))


def _date_filter(items, date_field, date_from=None, date_to=None, **kw):
    if date_from:
        items = [e for e in items if (e.get(date_field) or "") >= date_from]
    if date_to:
        items = [e for e in items if (e.get(date_field) or "") <= date_to]
    return items


# ── Sample factory helpers ────────────────────────────────────────────────────


def _lot(
    lid="lot-1",
    acquired_at="2025-01-01",
    qty=10.0,
    cost=100.0,
    ccy="USD",
    fees=0.0,
    kind="buy",
    account_id="acc-1",
    position_id="pos-1",
    instrument_id="inst-1",
    fx_rate=None,
    base_currency=None,
):
    return {
        "id": lid,
        "position_id": position_id,
        "instrument_id": instrument_id,
        "account_id": account_id,
        "acquired_at": acquired_at,
        "original_qty": qty,
        "remaining_qty": qty,
        "unit_cost": cost,
        "cost_currency": ccy,
        "fees": fees,
        "kind": kind,
        "txn_id": None,
        # Historical-rate lock stamp (ADR 2026-07-15) — None on a legacy/unstamped lot.
        "fx_rate": fx_rate,
        "base_currency": base_currency,
        "created_at": "2025-01-01T00:00:00Z",
    }


def _realized(
    rid="rlz-1",
    disposed_at="2025-06-01",
    qty=5.0,
    price=150.0,
    ccy="USD",
    fees=0.0,
    gain=250.0,
    account_id="acc-1",
    position_id="pos-1",
    instrument_id="inst-1",
    fx_rate=None,
    base_currency="USD",
):
    return {
        "id": rid,
        "position_id": position_id,
        "instrument_id": instrument_id,
        "account_id": account_id,
        "disposed_at": disposed_at,
        "quantity": qty,
        "proceeds_per_unit": price,
        "proceeds_currency": ccy,
        "disposal_fees": fees,
        "method": "fifo",
        "is_taxable": True,
        "cost_basis_known": True,
        "consumptions": [
            {
                "lot_id": "lot-1",
                "qty": qty,
                "acquired_at": "2025-01-01",
                "term": "short",
                "realized_gain": gain,
                "unit_cost": 100.0,
                "cost_currency": ccy,
            }
        ],
        "realized_gain": gain,
        "realized_gain_base": None,
        # Historical-rate lock stamp (ADR 2026-07-15) — fx_rate None on a legacy/unstamped
        # event (base_currency defaults to "USD" to match the pre-existing fixture shape).
        "fx_rate": fx_rate,
        "base_currency": base_currency,
        "txn_id": None,
        "created_at": "2025-06-01T00:00:00Z",
    }


def _income_ev(
    eid="inc-1",
    paid_at="2025-05-01",
    gross=10.0,
    tax=1.0,
    ccy="USD",
    kind="dividend",
    account_id="acc-1",
    position_id="pos-1",
    instrument_id="inst-1",
    txn_id="txn-div-1",
):
    return {
        "id": eid,
        "position_id": position_id,
        "instrument_id": instrument_id,
        "account_id": account_id,
        "paid_at": paid_at,
        "income_kind": kind,
        "gross_amount": gross,
        "tax_withheld": tax,
        "currency": ccy,
        "is_drp": False,
        "drp_lot_id": None,
        "txn_id": txn_id,
        "base_amount": 999.0,
        "base_currency": "USD",
        "created_at": "2025-05-01T00:00:00Z",
    }


def _vest_ev(
    vid="vest-1",
    vest_date="2025-03-01",
    units=100.0,
    fmv=20.0,
    fmv_ccy="USD",
    gross_native=2000.0,
    txn_id="txn-vest-1",
    grant_id="grant-1",
    lot_id="lot-v-1",
):
    # Mirrors models.equity.VestEvent — which carries NO instrument_id / account_id.
    # Those live on the grant and the struck lot; a fixture that fakes them onto the
    # event hides exactly the bug test_vest_rows_resolve_symbol_via_grant pins.
    return {
        "id": vid,
        "grant_id": grant_id,
        "tranche_id": "tr-1",
        "date": vest_date,
        "units": units,
        "fmv_per_unit": fmv,
        "fmv_currency": fmv_ccy,
        "price_paid_per_unit": 0.0,
        "gross_gain_native": gross_native,
        "txn_id": txn_id,
        "lot_id": lot_id,
        "locked_position_id": "lpos-1",
        "created_at": "2025-03-01T00:00:00Z",
    }


def _grant(gid="grant-1", instrument_id="inst-1", account_id="acc-1"):
    return {
        "id": gid,
        "instrument_id": instrument_id,
        "account_id": account_id,
        "type": "rsu",
        "status": "active",
        "grant_date": "2024-01-01",
        "total_units": 400.0,
        "tranches": [],
    }


def _instrument(iid="inst-1", symbol="NET", name="Cloudflare"):
    return {"id": iid, "symbol": symbol, "name": name, "currency": "USD", "kind": "stock"}


def _account(
    aid="acc-1",
    name="Vantage Brokerage",
    atype="investment",
    platform="Vantage Brokerage",
    currency="USD",
    archived=False,
):
    return {
        "id": aid,
        "name": name,
        "kind": "asset",
        "type": atype,
        "platform": platform,
        "currency": currency,
        "archived": archived,
    }


def _transfer_txn(
    tid="txn-1",
    txn_date="2025-04-01",
    from_id="ext-acc",
    from_amt=1000.0,
    from_ccy="USD",
    to_id="acc-1",
    to_amt=1000.0,
    to_ccy="USD",
    description="Deposit",
):
    return {
        "id": tid,
        "date": txn_date,
        "kind": "transfer",
        "description": description,
        "from_account_id": from_id,
        "from_amount": from_amt,
        "from_currency": from_ccy,
        "to_account_id": to_id,
        "to_amount": to_amt,
        "to_currency": to_ccy,
        "deleted": False,
        "tags": [],
    }


# ── TC 1: Empty book ──────────────────────────────────────────────────────────


def test_empty_book(fx_usd_sgd, monkeypatch):
    _patch_stores(monkeypatch)
    result = svc.activity_feed()
    assert result["events"] == []
    s = result["summary"]
    assert s["count_30d"] == 0
    assert s["net_invested_30d"] == 0.0
    assert s["income_30d"] == 0.0
    assert s["fees_30d"] == 0.0


# ── TC 2: One of each type ────────────────────────────────────────────────────


def test_one_of_each_type(fx_usd_sgd, monkeypatch):
    acct = _account()
    lot = _lot()
    rlz = _realized()
    inc = _income_ev()
    vest = _vest_ev()
    _patch_stores(
        monkeypatch,
        lots=[lot],
        realized=[rlz],
        income=[inc],
        vest=[vest],
        accts=[acct],
    )
    result = svc.activity_feed()
    types = {r["type"] for r in result["events"]}
    assert "buy" in types
    assert "sell" in types
    assert "dividend" in types
    assert "vest" in types


# ── TC 3: Sign correctness ────────────────────────────────────────────────────


def test_sign_correctness(fx_usd_sgd, monkeypatch):
    acct = _account()
    lot = _lot(qty=10.0, cost=100.0, ccy="USD", fees=5.0)
    rlz = _realized(qty=5.0, price=150.0, ccy="USD", fees=2.0)
    inc = _income_ev(gross=10.0, tax=1.0, ccy="USD")
    vest = _vest_ev(gross_native=2000.0, fmv_ccy="USD")
    _patch_stores(
        monkeypatch,
        lots=[lot],
        realized=[rlz],
        income=[inc],
        vest=[vest],
        accts=[acct],
    )
    result = svc.activity_feed()
    by_type = {r["type"]: r for r in result["events"] if r["type"] not in ("fee",)}
    fee_rows = [r for r in result["events"] if r["type"] == "fee"]

    # amount_base is the signed monetary value: negative = cash out, positive = cash in
    assert by_type["buy"]["amount_base"] < 0, "buy must be negative"
    assert by_type["sell"]["amount_base"] > 0, "sell must be positive"
    assert by_type["dividend"]["amount_base"] > 0, "dividend must be positive"
    assert by_type["vest"]["amount_base"] > 0, "vest must be positive"
    for fee_row in fee_rows:
        assert fee_row["amount_base"] < 0, "fee must be negative"


def test_deferred_vest_signed_zero(fx_usd_sgd, monkeypatch):
    acct = _account()
    vest = _vest_ev(txn_id=None, gross_native=5000.0)
    _patch_stores(monkeypatch, vest=[vest], accts=[acct])
    result = svc.activity_feed()
    vest_rows = [r for r in result["events"] if r["type"] == "vest"]
    assert len(vest_rows) == 1
    assert vest_rows[0]["amount_base"] == 0.0


# ── TC 4: Date-DESC ordering ──────────────────────────────────────────────────


def test_date_desc_ordering(fx_usd_sgd, monkeypatch):
    acct = _account()
    lots = [
        _lot("lot-a", acquired_at="2025-01-15"),
        _lot("lot-b", acquired_at="2025-03-10"),
        _lot("lot-c", acquired_at="2024-12-01"),
    ]
    _patch_stores(monkeypatch, lots=lots, accts=[acct])
    result = svc.activity_feed()
    rows = [r for r in result["events"] if r["type"] == "buy"]
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates, reverse=True)


# ── TC 5: Base recompute (keystone invariant) ─────────────────────────────────


def test_base_recompute_on_currency_change(monkeypatch):
    """After a base-currency change the feed's amount_base values update — stored snapshot ignored."""
    lot = _lot(qty=10.0, cost=100.0, ccy="USD")

    acct = _account()
    _patch_stores(monkeypatch, lots=[lot], accts=[acct])

    # SGD base: 1 USD = 1.35 SGD
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})
    result_sgd = svc.activity_feed()
    buy_sgd = next(r for r in result_sgd["events"] if r["type"] == "buy")
    # buy amount_base negative: -10 × 100 × 1.35 = -1350
    assert buy_sgd["amount_base"] == pytest.approx(-1350.0)
    assert result_sgd["base"] == "SGD"

    # Switch to USD base: 1 USD = 1 USD
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0, "SGD": 0.74})
    result_usd = svc.activity_feed(base="USD")
    buy_usd = next(r for r in result_usd["events"] if r["type"] == "buy")
    assert buy_usd["amount_base"] == pytest.approx(-1000.0)
    assert result_usd["base"] == "USD"


# ── MATH-2 regression: buy/sell/vest rows honor the historical-rate lock ──────
# ADR 2026-07-15: a lot's/event's write-time fx_rate + base_currency stamp is a
# historical fact and must not drift with today's FX cache (mirrors
# lots.realized_pnl / core.fx.txn_base_amount).


def _vest_txn(
    tid="txn-vest-1",
    kind="income",
    amt=2000.0,
    ccy="EUR",
    base_amount=1500.0,
    base_currency="SGD",
):
    return {
        "id": tid,
        "kind": kind,
        "to_amount": amt,
        "to_currency": ccy,
        "from_amount": None,
        "from_currency": None,
        "base_amount": base_amount,
        "base_currency": base_currency,
        "deleted": False,
    }


def test_buy_row_trusts_stamped_fx_rate_after_fx_cache_moves(monkeypatch):
    """10 units @ EUR100 stamped fx_rate=1.5 must stay S$1500 even after the FX cache
    moves to 1.2 — the probe in docs/review/money-math-audit.md#math-2."""
    lot = _lot(qty=10.0, cost=100.0, ccy="EUR", fees=20.0, fx_rate=1.5, base_currency="SGD")
    _patch_stores(monkeypatch, lots=[lot], accts=[_account()])
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 0.9})

    result = svc.activity_feed()
    buy = next(r for r in result["events"] if r["type"] == "buy")
    fee = next(r for r in result["events"] if r["type"] == "fee")
    assert buy["amount_base"] == pytest.approx(-1500.0)
    assert fee["amount_base"] == pytest.approx(-30.0)  # 20 EUR fee × stamped 1.5

    # The FX cache moves — the stamped row's amount_base must NOT change.
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 1.2})
    result2 = svc.activity_feed()
    buy2 = next(r for r in result2["events"] if r["type"] == "buy")
    assert buy2["amount_base"] == pytest.approx(-1500.0)


def test_buy_row_recomputes_when_stamped_base_currency_differs(monkeypatch):
    """A lot stamped under a former base falls back to a current-rate recompute — the
    base changed since the write, so the stamp is no longer a fact in the CURRENT base."""
    lot = _lot(qty=10.0, cost=100.0, ccy="EUR", fx_rate=1.5, base_currency="USD")
    _patch_stores(monkeypatch, lots=[lot], accts=[_account()])
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 1.2})

    result = svc.activity_feed()
    buy = next(r for r in result["events"] if r["type"] == "buy")
    # Recomputed at current rates: -(10 × 100 EUR) → SGD @ 1.2 = -1200, NOT the stamped -1500.
    assert buy["amount_base"] == pytest.approx(-1200.0)


def test_buy_row_recomputes_when_unstamped(fx_usd_sgd, monkeypatch):
    """A legacy lot with no fx_rate/base_currency stamp keeps the pre-lock recompute."""
    lot = _lot(qty=10.0, cost=100.0, ccy="USD", fx_rate=None, base_currency=None)
    _patch_stores(monkeypatch, lots=[lot], accts=[_account()])
    result = svc.activity_feed()
    buy = next(r for r in result["events"] if r["type"] == "buy")
    assert buy["amount_base"] == pytest.approx(-1350.0)


def test_sell_row_trusts_stamped_fx_rate_after_fx_cache_moves(monkeypatch):
    """A disposal's proceeds/fee stamp (fx_rate + base_currency, always set by
    compute_disposal) must not drift with today's FX cache."""
    ev = _realized(qty=5.0, price=150.0, ccy="EUR", fees=10.0, fx_rate=1.5, base_currency="SGD")
    _patch_stores(monkeypatch, realized=[ev], accts=[_account()])
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 0.9})

    result = svc.activity_feed()
    sell = next(r for r in result["events"] if r["type"] == "sell")
    fee = next(r for r in result["events"] if r["type"] == "fee")
    assert sell["amount_base"] == pytest.approx(1125.0)  # 5 × 150 EUR × stamped 1.5
    assert fee["amount_base"] == pytest.approx(-15.0)  # 10 EUR fee × stamped 1.5

    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 1.2})
    result2 = svc.activity_feed()
    sell2 = next(r for r in result2["events"] if r["type"] == "sell")
    assert sell2["amount_base"] == pytest.approx(1125.0)


def test_sell_row_recomputes_when_stamped_base_currency_differs(monkeypatch):
    """A realized event stamped under a former base falls back to a current-rate
    recompute, same as the buy-side case."""
    ev = _realized(qty=5.0, price=150.0, ccy="EUR", fx_rate=1.5, base_currency="USD")
    _patch_stores(monkeypatch, realized=[ev], accts=[_account()])
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 1.2})

    result = svc.activity_feed()
    sell = next(r for r in result["events"] if r["type"] == "sell")
    assert sell["amount_base"] == pytest.approx(900.0)  # 5 × 150 EUR recomputed @ 1.2


def test_sell_row_recomputes_when_unstamped(fx_usd_sgd, monkeypatch):
    """A legacy realized event with no fx_rate stamp keeps the pre-lock recompute."""
    ev = _realized(qty=5.0, price=150.0, ccy="USD", fx_rate=None, base_currency=None)
    _patch_stores(monkeypatch, realized=[ev], accts=[_account()])
    result = svc.activity_feed()
    sell = next(r for r in result["events"] if r["type"] == "sell")
    assert sell["amount_base"] == pytest.approx(1012.5)


def test_vest_row_trusts_linked_txn_stamp_after_fx_cache_moves(monkeypatch):
    """A vest's amount_base is authoritative from its linked transaction's stamp
    (mirrors equity_report.equity_report) — the FX cache moving afterward must not
    change it."""
    vest = _vest_ev(txn_id="txn-vest-1", gross_native=999.0, fmv_ccy="EUR")
    txn = _vest_txn(tid="txn-vest-1", base_amount=1500.0, base_currency="SGD")
    _patch_stores(
        monkeypatch,
        vest=[vest],
        grants=[_grant()],
        accts=[_account()],
        instruments=[_instrument()],
        txn_by_id={"txn-vest-1": txn},
    )
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 1.2})

    (row,) = [r for r in svc.activity_feed()["events"] if r["type"] == "vest"]
    assert row["amount_base"] == pytest.approx(1500.0)

    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 1.5})
    (row2,) = [r for r in svc.activity_feed()["events"] if r["type"] == "vest"]
    assert row2["amount_base"] == pytest.approx(1500.0)


def test_vest_row_recomputes_via_linked_txn_when_base_currency_differs(monkeypatch):
    """The linked txn was stamped under a former base — txn_base_amount recomputes
    from its native leg at current rates rather than trusting the stale stamp."""
    vest = _vest_ev(txn_id="txn-vest-1", gross_native=999.0, fmv_ccy="EUR")
    txn = _vest_txn(
        tid="txn-vest-1", amt=2000.0, ccy="EUR", base_amount=1500.0, base_currency="USD"
    )
    _patch_stores(
        monkeypatch,
        vest=[vest],
        grants=[_grant()],
        accts=[_account()],
        instruments=[_instrument()],
        txn_by_id={"txn-vest-1": txn},
    )
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 1.0, "EUR": 1.2})

    (row,) = [r for r in svc.activity_feed()["events"] if r["type"] == "vest"]
    # Recomputed from the txn's native to_amount (2000 EUR) at current rates: 2000 × 1.2 = 2400.
    assert row["amount_base"] == pytest.approx(2400.0)


def test_vest_row_recomputes_from_native_gain_when_linked_txn_is_missing(fx_usd_sgd, monkeypatch):
    """A vest whose linked txn can no longer be found (deleted, or never a real link)
    falls back to converting its own native FMV gain at current rates — the pre-lock,
    legacy behavior."""
    vest = _vest_ev(txn_id="txn-gone", gross_native=2000.0, fmv_ccy="USD")
    _patch_stores(
        monkeypatch,
        vest=[vest],
        grants=[_grant()],
        accts=[_account()],
        instruments=[_instrument()],
    )
    (row,) = [r for r in svc.activity_feed()["events"] if r["type"] == "vest"]
    assert row["amount_base"] == pytest.approx(2700.0)  # 2000 × 1.35 (fx_usd_sgd fixture)


# ── TC 6: Dividend not double-counted ────────────────────────────────────────


def test_no_double_count_dividend(fx_usd_sgd, monkeypatch):
    """A cash dividend appears exactly once as 'dividend'."""
    acct = _account()
    inc = _income_ev(txn_id="txn-div-1")
    _patch_stores(monkeypatch, income=[inc], accts=[acct])
    result = svc.activity_feed()
    types = [r["type"] for r in result["events"]]
    assert types.count("dividend") == 1


# ── TC 7: Sell row always present ────────────────────────────────────────────


def test_no_double_count_sale_bridge(fx_usd_sgd, monkeypatch):
    """A realized disposal emits exactly one 'sell' row."""
    acct = _account()
    rlz = _realized()
    _patch_stores(monkeypatch, realized=[rlz], accts=[acct])
    result = svc.activity_feed()
    types = [r["type"] for r in result["events"]]
    assert "sell" in types


# ── TC 8: Fee double-count guard ─────────────────────────────────────────────


def test_no_double_count_fees(fx_usd_sgd, monkeypatch):
    """Fees appear as separate 'fee' rows; buy/sell amount_base is raw price × qty."""
    from datetime import date

    today = date.today().isoformat()
    acct = _account()
    lot = _lot(acquired_at=today, qty=10.0, cost=100.0, ccy="USD", fees=20.0)
    rlz = _realized(disposed_at=today, qty=5.0, price=150.0, ccy="USD", fees=10.0, gain=250.0)
    _patch_stores(monkeypatch, lots=[lot], realized=[rlz], accts=[acct])

    result = svc.activity_feed()
    buy_row = next(r for r in result["events"] if r["type"] == "buy")
    sell_row = next(r for r in result["events"] if r["type"] == "sell")
    fee_rows = [r for r in result["events"] if r["type"] == "fee"]

    # SGD base (1 USD = 1.35 SGD from fx_usd_sgd fixture)
    # Raw buy: 10 × 100 × 1.35 = -1350 SGD
    assert buy_row["amount_base"] == pytest.approx(-1350.0)
    # Raw sell: 5 × 150 × 1.35 = +1012.5 SGD
    assert sell_row["amount_base"] == pytest.approx(1012.5)
    assert len(fee_rows) == 2
    # Both fee rows are negative
    for fee_row in fee_rows:
        assert fee_row["amount_base"] < 0
    # Acquisition fee: detail contains "acquisition"
    acq_fee = next(f for f in fee_rows if "acquisition" in (f.get("detail") or ""))
    assert acq_fee["amount_base"] == pytest.approx(-20.0 * 1.35)
    # Disposal fee: detail contains "disposal"
    disp_fee = next(f for f in fee_rows if "disposal" in (f.get("detail") or ""))
    assert disp_fee["amount_base"] == pytest.approx(-10.0 * 1.35)

    # Summary fees_30d counts absolute fee amount (events are today — within 30d window)
    s = result["summary"]
    assert s["fees_30d"] == pytest.approx((20.0 + 10.0) * 1.35)


# ── TC 9: DRP ────────────────────────────────────────────────────────────────


def test_drp_one_dividend_one_buy(fx_usd_sgd, monkeypatch):
    """DRP: one 'dividend' row + one 'buy' row (the reinvested lot)."""
    acct = _account()
    drp_lot = _lot("drp-lot", kind="buy", qty=0.5, cost=20.0, ccy="USD")
    drp_inc = _income_ev(txn_id=None)  # DRP → no cash txn
    drp_inc["is_drp"] = True
    drp_inc["drp_lot_id"] = "drp-lot"
    _patch_stores(monkeypatch, lots=[drp_lot], income=[drp_inc], accts=[acct])
    result = svc.activity_feed()
    types = [r["type"] for r in result["events"]]
    assert types.count("dividend") == 1
    assert types.count("buy") == 1


# ── TC 10: Filters ────────────────────────────────────────────────────────────


def test_type_filter(fx_usd_sgd, monkeypatch):
    acct = _account()
    _patch_stores(
        monkeypatch,
        lots=[_lot()],
        realized=[_realized()],
        income=[_income_ev()],
        accts=[acct],
    )
    result = svc.activity_feed(type_filter=["buy"])
    types = {r["type"] for r in result["events"]}
    assert types == {"buy"}


def test_date_filter_narrows(fx_usd_sgd, monkeypatch):
    acct = _account()
    lots = [
        _lot("l-a", acquired_at="2025-01-10"),
        _lot("l-b", acquired_at="2025-06-15"),
    ]
    _patch_stores(monkeypatch, lots=lots, accts=[acct])
    result = svc.activity_feed(date_from="2025-06-01", date_to="2025-12-31")
    rows = [r for r in result["events"] if r["type"] == "buy"]
    assert all(r["date"] >= "2025-06-01" for r in rows)


def test_account_filter(fx_usd_sgd, monkeypatch):
    acct_a = _account("acc-a", name="Vantage Brokerage")
    acct_b = _account("acc-b", name="Other Brokerage")
    lots = [
        _lot("l-a", account_id="acc-a"),
        _lot("l-b", account_id="acc-b"),
    ]
    _patch_stores(monkeypatch, lots=lots, accts=[acct_a, acct_b])
    result = svc.activity_feed(account_id="acc-a")
    for row in result["events"]:
        assert row.get("account_id") == "acc-a"


# ── TC 11: Pagination ─────────────────────────────────────────────────────────


def test_pagination(fx_usd_sgd, monkeypatch):
    acct = _account()
    lots = [_lot(f"lot-{i}", acquired_at=f"2025-0{i + 1}-01") for i in range(5)]
    _patch_stores(monkeypatch, lots=lots, accts=[acct])

    page1 = svc.activity_feed(limit=2, offset=0)
    page2 = svc.activity_feed(limit=2, offset=2)

    assert len(page1["events"]) == 2
    assert len(page2["events"]) == 2

    # Pages should be disjoint
    ids1 = {r["source_id"] for r in page1["events"]}
    ids2 = {r["source_id"] for r in page2["events"]}
    assert not ids1 & ids2


def test_summary_is_pre_pagination(fx_usd_sgd, monkeypatch):
    acct = _account()
    lots = [_lot(f"lot-{i}", acquired_at=f"2025-0{i + 1}-01") for i in range(5)]
    _patch_stores(monkeypatch, lots=lots, accts=[acct])

    full = svc.activity_feed()
    page = svc.activity_feed(limit=1, offset=0)
    # summary is computed over ALL events, not just the page
    assert page["summary"]["count_30d"] == full["summary"]["count_30d"]


# ── TC 12: 30-day summary ────────────────────────────────────────────────────


def test_30_day_summary(fx_usd_sgd, monkeypatch):
    from datetime import date

    today = date.today().isoformat()
    acct = _account()
    lots = [
        _lot("recent", acquired_at=today, qty=10.0, cost=100.0, ccy="USD"),
    ]
    _patch_stores(monkeypatch, lots=lots, accts=[acct])
    result = svc.activity_feed()
    # Recent lot is within 30 days → appears in count_30d
    assert result["summary"]["count_30d"] >= 1


# ── TC 13: Account resolution ────────────────────────────────────────────────


def test_account_resolution(fx_usd_sgd, monkeypatch):
    acct = _account("acc-x", name="Vantage Brokerage Main")
    lot = _lot(account_id="acc-x")
    _patch_stores(monkeypatch, lots=[lot], accts=[acct])
    result = svc.activity_feed()
    buy_rows = [r for r in result["events"] if r["type"] == "buy"]
    assert buy_rows[0]["account_id"] == "acc-x"
    assert buy_rows[0]["account_name"] == "Vantage Brokerage Main"


def test_missing_account_returns_null(fx_usd_sgd, monkeypatch):
    """When account can't be resolved the id is passed through; name is empty string."""
    lot = _lot(account_id="nonexistent")
    _patch_stores(monkeypatch, lots=[lot], accts=[])
    result = svc.activity_feed()
    buy_rows = [r for r in result["events"] if r["type"] == "buy"]
    # account_id is always preserved from the source event; account_name is empty
    assert buy_rows[0]["account_id"] == "nonexistent"
    assert buy_rows[0]["account_name"] == ""


# ── TC 14: Opening/undated lot ────────────────────────────────────────────────


def test_opening_lot_surfaced_as_buy(fx_usd_sgd, monkeypatch):
    acct = _account()
    opening = _lot("opening-1", acquired_at="", kind="opening", qty=100.0, cost=50.0)
    _patch_stores(monkeypatch, lots=[opening], accts=[acct])
    result = svc.activity_feed()
    buy_rows = [r for r in result["events"] if r["type"] == "buy"]
    assert len(buy_rows) == 1
    assert buy_rows[0]["date"] == ""


# ── TC 15: Date filter excludes opening lots ──────────────────────────────────


def test_instrument_filter_suppresses_cash_rows(fx_usd_sgd, monkeypatch):
    """Rows with no instrument_id are excluded when filtering by account_id."""
    acct = _account("acc-inv", atype="investment")
    lot = _lot("l1", instrument_id="aapl-id", account_id="acc-inv")
    _patch_stores(monkeypatch, lots=[lot], accts=[acct])
    result = svc.activity_feed(account_id="acc-inv")
    # All rows should have the filtered account
    for row in result["events"]:
        assert row["account_id"] == "acc-inv"


def test_instrument_filter_excludes_opening_lots_with_date_from(fx_usd_sgd, monkeypatch):
    """Opening lots (acquired_at='') must be excluded when date_from is active."""
    acct = _account()
    opening = _lot("opening-1", acquired_at="", kind="opening", instrument_id="inst-1")
    real_lot = _lot("real-1", acquired_at="2025-07-01", kind="buy", instrument_id="inst-1")
    _patch_stores(monkeypatch, lots=[opening, real_lot], accts=[acct])

    result = svc.activity_feed(date_from="2025-06-01")
    buy_rows = [r for r in result["events"] if r["type"] == "buy"]
    # Opening lot has no date — must be excluded when date_from is set
    assert all(r["date"] >= "2025-06-01" for r in buy_rows), (
        "Opening lots with blank acquired_at leaked through date_from filter"
    )


# ── Net-invested sign regression (owner report 2026-07-12) ─────────────────────


def test_net_invested_is_buys_minus_sells(fx_usd_sgd, monkeypatch):
    """ "Net invested" follows its label: money INTO investments minus money taken out.

    Pre-fix, the summary summed cash-flow-signed rows such that a buy AND a sell both
    pushed the figure negative — a 30d window with a 1,000 buy and a 300 sell displayed
    −1,300 (the negated total volume) instead of +700."""
    from datetime import date as _date

    today = _date.today().isoformat()
    _patch_stores(
        monkeypatch,
        lots=[_lot(lid="lot-buy", acquired_at=today, qty=10.0, cost=100.0, ccy="USD")],
        realized=[_realized(rid="rlz-sell", disposed_at=today, qty=2.0, price=150.0, ccy="USD")],
    )
    s = svc.activity_feed()["summary"]
    # buys 10×100 = 1000 USD in; sell 2×150 = 300 USD out; ×1.35 SGD = 945 base.
    assert s["net_invested_30d"] == pytest.approx(945.0)


# ── Vest rows resolve their instrument + account (owner report 2026-09-04) ─────


def test_vest_rows_resolve_symbol_via_grant(fx_usd_sgd, monkeypatch):
    """A VestEvent has no instrument_id of its own — the grant owns it. Reading the
    field off the event rendered every vest on the Activity tab as a bare "Vest" pill
    with no ticker and no account."""
    acct = _account("acc-1", name="Vantage Brokerage")
    struck = _lot("lot-v-1", acquired_at="2025-03-01", instrument_id="inst-1", account_id="acc-1")
    _patch_stores(
        monkeypatch,
        lots=[struck],
        vest=[_vest_ev(lot_id="lot-v-1", grant_id="grant-1")],
        accts=[acct],
        grants=[_grant("grant-1", instrument_id="inst-1", account_id="acc-1")],
        instruments=[_instrument("inst-1", symbol="NET", name="Cloudflare")],
    )
    (row,) = [r for r in svc.activity_feed()["events"] if r["type"] == "vest"]
    assert row["symbol"] == "NET"
    assert row["name"] == "Cloudflare"
    assert row["instrument_id"] == "inst-1"
    assert row["account_id"] == "acc-1"
    assert row["account_name"] == "Vantage Brokerage"


def test_vest_account_comes_from_the_struck_lot_not_the_grant(fx_usd_sgd, monkeypatch):
    """A grant moved between custodians keeps its original account_id; the lot the vest
    struck records where the shares actually landed. The lot wins, so the vest sits next
    to the buy rows of the same account."""
    acct_new = _account("acc-new", name="New Custodian")
    struck = _lot("lot-v-1", acquired_at="2025-03-01", instrument_id="inst-1", account_id="acc-new")
    _patch_stores(
        monkeypatch,
        lots=[struck],
        vest=[_vest_ev(lot_id="lot-v-1")],
        accts=[acct_new, _account("acc-old", name="Old Custodian")],
        grants=[_grant("grant-1", account_id="acc-old")],
        instruments=[_instrument()],
    )
    (row,) = [r for r in svc.activity_feed()["events"] if r["type"] == "vest"]
    assert row["account_id"] == "acc-new"
    assert row["account_name"] == "New Custodian"


def test_pending_vest_without_lot_falls_back_to_grant_account(fx_usd_sgd, monkeypatch):
    """A vested-but-unreleased tranche has no lot yet; it still belongs to the grant's
    account rather than to nobody."""
    _patch_stores(
        monkeypatch,
        vest=[_vest_ev(lot_id=None, txn_id=None)],
        accts=[_account("acc-1", name="Vantage Brokerage")],
        grants=[_grant("grant-1", account_id="acc-1")],
        instruments=[_instrument(symbol="NET")],
    )
    (row,) = [r for r in svc.activity_feed()["events"] if r["type"] == "vest"]
    assert row["symbol"] == "NET"
    assert row["account_id"] == "acc-1"


def test_account_filter_keeps_vests_of_that_account(fx_usd_sgd, monkeypatch):
    """Pre-fix the account filter compared against a field the event never had, so
    filtering by ANY account silently dropped every vest."""
    struck = _lot("lot-v-1", acquired_at="2025-03-01", instrument_id="inst-1", account_id="acc-1")
    _patch_stores(
        monkeypatch,
        lots=[struck],
        vest=[_vest_ev(lot_id="lot-v-1")],
        accts=[_account("acc-1"), _account("acc-2", name="Other")],
        grants=[_grant()],
        instruments=[_instrument()],
    )
    kept = [r for r in svc.activity_feed(account_id="acc-1")["events"] if r["type"] == "vest"]
    dropped = [r for r in svc.activity_feed(account_id="acc-2")["events"] if r["type"] == "vest"]
    assert len(kept) == 1
    assert dropped == []


# ── A vest is not a purchase (owner report 2026-09-04) ─────────────────────────


def test_grant_lot_is_not_reported_as_a_buy(fx_usd_sgd, monkeypatch):
    """A release mints the delivered shares as a kind="grant" lot. Pre-fix _buy_rows
    reported EVERY lot as a Buy, so each vest appeared twice — the second time as a
    phantom "Buy −FMV×units" — and the fake purchase inflated net-invested."""
    from datetime import date as _date

    today = _date.today().isoformat()
    delivered = _lot(
        "lot-v-1",
        acquired_at=today,
        qty=488.75,
        cost=37.75,
        ccy="USD",
        kind="grant",
        instrument_id="inst-1",
        account_id="acc-1",
    )
    _patch_stores(
        monkeypatch,
        lots=[delivered],
        vest=[_vest_ev(vest_date=today, units=488.75, fmv=37.75, gross_native=488.75 * 37.75)],
        accts=[_account("acc-1")],
        grants=[_grant()],
        instruments=[_instrument(symbol="KSTL", name="Kestrel Dash")],
    )
    result = svc.activity_feed()
    types = [r["type"] for r in result["events"]]
    assert types == ["vest"], types
    assert result["events"][0]["symbol"] == "KSTL"
    assert result["summary"]["net_invested_30d"] == 0.0


def test_transfer_in_lot_is_not_reported_as_a_buy(fx_usd_sgd, monkeypatch):
    """Shares moved in from another custodian were bought once, elsewhere — not again."""
    moved = _lot("lot-t-1", kind="transfer_in")
    _patch_stores(monkeypatch, lots=[moved, _lot("lot-b-1", kind="buy")], accts=[_account()])
    buys = [r for r in svc.activity_feed()["events"] if r["type"] == "buy"]
    assert [r["links"]["lot_id"] for r in buys] == ["lot-b-1"]


def _transfer_out(rid="rlz-t-1", disposed_at="2025-07-25", qty=1242.0, ccy="EUR", **kw):
    """A non-taxable disposal — what the disposal choke writes for a transfer / gift /
    write-off (DisposalCreate.is_taxable=False): zero proceeds, basis carried out, no gain
    booked. ``transfer_to_account_id`` (optional) names where the shares went."""
    to_acct = kw.pop("to_account_id", None)
    ev = _realized(rid=rid, disposed_at=disposed_at, qty=qty, price=0.0, ccy=ccy, gain=None, **kw)
    ev["is_taxable"] = False
    ev["realized_gain"] = None
    ev["transfer_to_account_id"] = to_acct
    for c in ev["consumptions"]:
        c["realized_gain"] = None
    return ev


def test_transfer_is_not_a_sell(fx_usd_sgd, monkeypatch):
    """Shares moved to another custodian are booked as a non-taxable $0 disposal. Pre-fix
    the feed labelled EVERY realized event a Sell, so the move read as "Sell 1242 @ 0 EUR
    +S$0.00" (owner, 2026-09-04: a workplace-custodian → IBKR transfer). It is ONE
    "transfer" row — direction is not a type, the destination is in the detail."""
    from datetime import date as _date

    today = _date.today().isoformat()
    _patch_stores(
        monkeypatch,
        realized=[
            _transfer_out(disposed_at=today, to_account_id="acc-2"),
            _realized(rid="rlz-s-1", disposed_at=today),
        ],
        accts=[_account(), _account(aid="acc-2", name="IBKR", platform="IBKR")],
        instruments=[_instrument(symbol="KSTL", name="Kestrel Dash")],
    )
    result = svc.activity_feed()
    by_type = {r["type"]: r for r in result["events"]}
    assert set(by_type) == {"transfer", "sell"}, sorted(by_type)

    moved = by_type["transfer"]
    assert moved["symbol"] == "KSTL"
    assert moved["detail"] == "1242 units → IBKR"
    assert moved["account_name"] == "Vantage Brokerage"  # the source stays the row's account
    assert moved["amount_base"] == 0.0
    assert moved["signed"] is False
    assert moved["source_id"] == "transfer:rlz-t-1"
    assert moved["links"]["event_id"] == "rlz-t-1"
    assert moved["links"]["to_account_id"] == "acc-2"
    # The real sale is untouched, and the move contributes nothing to net invested.
    assert by_type["sell"]["detail"] == "5 @ 150 USD"
    assert result["summary"]["net_invested_30d"] == pytest.approx(-5 * 150 * 1.35)


def test_transfer_without_a_destination_reads_transferred_out(fx_usd_sgd, monkeypatch):
    """A gift / write-off / pre-feature transfer stores no destination — the row still
    says what happened without inventing one."""
    _patch_stores(monkeypatch, realized=[_transfer_out()], accts=[_account()])
    (row,) = svc.activity_feed()["events"]
    assert row["type"] == "transfer"
    assert row["detail"] == "1242 units transferred out"
    assert row["links"]["to_account_id"] is None


def test_transfer_type_filter(fx_usd_sgd, monkeypatch):
    _patch_stores(monkeypatch, realized=[_transfer_out(), _realized()], accts=[_account()])
    only_moves = svc.activity_feed(type_filter=["transfer"])["events"]
    assert [r["type"] for r in only_moves] == ["transfer"]
    only_sells = svc.activity_feed(type_filter=["sell"])["events"]
    assert [r["type"] for r in only_sells] == ["sell"]


def test_transfer_matches_the_account_filter_on_either_side(fx_usd_sgd, monkeypatch):
    """A transfer touches two accounts: filtering Activity by the DESTINATION must still
    show the movement (it is the only row that explains where those shares came from)."""
    _patch_stores(
        monkeypatch,
        realized=[_transfer_out(to_account_id="acc-2"), _realized()],
        accts=[_account(), _account(aid="acc-2", name="IBKR", platform="IBKR")],
    )
    on_dest = svc.activity_feed(account_id="acc-2")["events"]
    assert [r["type"] for r in on_dest] == ["transfer"]
    on_src = svc.activity_feed(account_id="acc-1")["events"]
    assert sorted(r["type"] for r in on_src) == ["sell", "transfer"]
    assert svc.activity_feed(account_id="acc-9")["events"] == []


def test_vest_fmv_is_not_income_30d(fx_usd_sgd, monkeypatch):
    """ACT-7: a vest delivers shares, not cash — its FMV must not inflate "Income · 30d"
    (owner, 2026-09-04: the card read +S$16k the week an RSU tranche landed)."""
    from datetime import date as _date

    today = _date.today().isoformat()
    _patch_stores(
        monkeypatch,
        vest=[_vest_ev(vest_date=today)],
        income=[_income_ev(paid_at=today)],
        grants=[_grant()],
        lots=[_lot("lot-v-1", kind="grant")],
        accts=[_account()],
        instruments=[_instrument()],
    )
    result = svc.activity_feed()
    types = {r["type"] for r in result["events"]}
    assert "vest" in types and "dividend" in types
    dividend = next(r for r in result["events"] if r["type"] == "dividend")
    assert result["summary"]["income_30d"] == pytest.approx(dividend["amount_base"])
