"""The portfolio row projection — every holding valued once, server-side.

These tests were PORTED from four frontend suites that covered `usePortfolio.compute()`
(`usePortfolio.foreignQuote|refGrantPl|unvested|propertyVsUnvested.test.ts`). That browser-side
fold was the app's second implementation of its own money math and has been deleted; the
behaviour it pinned now lives in `backend/services/portfolio_rows.py`, so its regression cover
moves here rather than being lost with it.

Each scar the originals guarded is preserved verbatim:

1. **quote_currency ≠ cost_currency.** The market price is denominated in `quote_currency`, the
   cost basis in `currency`. Converting both through one currency double-applies FX on the
   mismatched leg — a USD-quoted asset bought in SGD showed a phantom LOSS because
   `(price − cost) × qty` subtracted across currencies before converting.
2. **"Since grant" P&L** is unvested-only, and omitted entirely on a currency mismatch (the
   grant-date FX rate isn't known, so no number beats a wrong one).
3. **Only ENTITLEMENT gates net worth.** Unvested grant units are excluded and reported
   separately; an illiquid-but-owned property row is NOT.
4. **Entitlement and reachability must never answer for each other.** `bucket` used to mean
   both, and the obvious `bucket == "locked"` filter would have deleted a sizable illiquid
   property from net worth (ADR 2026-07-31-split-position-bucket).
"""

from __future__ import annotations

import pytest

from backend.services.portfolio_rows import _project, projected_rows

# 1 USD = 2 SGD — clean mental math, mirroring the original frontend fixtures.
FX = {"SGD": 1.0, "USD": 2.0}
BASE = "SGD"


def _row(**overrides) -> dict:
    row = {
        "category": "Gemini",
        "label": "Bitcoin",
        "asset_class": "crypto",
        "vesting_state": "vested",
        "liquidity": "liquid",
        "quantity": 1,
        "price": 100,  # market price, in quote_currency (USD)
        "cost_basis": 110,  # cost basis, in cost_currency (SGD)
        "currency": "SGD",  # cost currency
        "quote_currency": "USD",  # price currency
        "ticker": "BTC",
        "note": "",
    }
    row.update(overrides)
    return row


# ── 1. quote_currency ≠ cost_currency ─────────────────────────────────────────


def test_market_uses_quote_currency_and_cost_uses_cost_currency():
    r = _project(_row(), BASE, FX)
    # 1 × 100 USD → SGD = 200 (quote_currency, NOT cost_currency)
    assert r["base_value"] == pytest.approx(200, abs=0.01)
    # 1 × 110 SGD → SGD = 110 (cost currency, no FX)
    assert r["cost_base"] == pytest.approx(110, abs=0.01)


def test_pl_comes_from_the_two_base_legs_never_a_cross_currency_subtraction():
    r = _project(_row(), BASE, FX)
    # 200 − 110 = +90, a GAIN. The bug computed (100 − 110) = −10, a phantom loss.
    assert r["pl"] == pytest.approx(90, abs=0.01)
    assert r["pl_pct"] == pytest.approx(81.8182, abs=0.001)


def test_foreign_quoted_equity_displays_per_unit_figures_in_its_price_currency():
    r = _project(_row(asset_class="equity"), BASE, FX)
    assert r["display_currency"] == "USD"
    assert r["display_unit_price"] == pytest.approx(100, abs=0.01)
    assert r["display_unit_cost"] == pytest.approx(55, abs=0.01)  # 110 SGD → USD


def test_matching_currencies_are_unchanged():
    r = _project(
        _row(asset_class="equity", currency="USD", quote_currency="USD", cost_basis=80), BASE, FX
    )
    assert r["base_value"] == pytest.approx(200, abs=0.01)
    assert r["cost_base"] == pytest.approx(160, abs=0.01)
    assert r["pl"] == pytest.approx(40, abs=0.01)
    assert r["display_unit_cost"] == pytest.approx(80, abs=0.01)


def test_sgd_bought_crypto_reads_as_its_cost_currency():
    """Crypto shows in the currency you TRANSACTED in — its quote currency is just CoinGecko's
    reference, not how the holder thinks of it. Valuation is unaffected."""
    r = _project(_row(asset_class="crypto", currency="SGD"), BASE, FX)
    assert r["display_currency"] == "SGD"
    assert r["display_unit_price"] == pytest.approx(200, abs=0.01)
    assert r["display_unit_cost"] == pytest.approx(110, abs=0.01)
    assert r["base_value"] == pytest.approx(200, abs=0.01)
    assert r["pl"] == pytest.approx(90, abs=0.01)


def test_usd_bought_crypto_reads_as_usd_even_when_base_is_sgd():
    r = _project(_row(asset_class="crypto", currency="USD", cost_basis=90), BASE, FX)
    assert r["display_currency"] == "USD"  # the purchase currency, not the SGD base
    assert r["display_unit_price"] == pytest.approx(100, abs=0.01)
    assert r["display_unit_cost"] == pytest.approx(90, abs=0.01)
    # Valuation still lands in base.
    assert r["base_value"] == pytest.approx(200, abs=0.01)
    assert r["cost_base"] == pytest.approx(180, abs=0.01)
    assert r["pl"] == pytest.approx(20, abs=0.01)


def test_sub_cent_crypto_price_survives_when_no_conversion_is_needed():
    """SHIB-style meme coin: cost_currency == quote_currency == USD, so display_unit_price
    needs NO FX conversion at all. `to_base`'s blanket round-to-2-decimals silently zeroed
    a sub-cent price on this exact same-currency fast path — display_unit_price must keep
    full precision instead of reading as "no price"."""
    r = _project(
        _row(asset_class="crypto", currency="USD", quote_currency="USD", price=0.00000444),
        BASE,
        FX,
    )
    assert r["display_currency"] == "USD"
    assert r["display_unit_price"] == pytest.approx(0.00000444, rel=1e-6)
    assert r["display_unit_price"] != 0


def test_sub_cent_crypto_price_survives_a_real_fx_conversion():
    """Same bug, but on the branch that DOES convert — a sub-cent USD price must not get
    rounded to zero on its way into the cost currency either."""
    r = _project(
        _row(asset_class="crypto", currency="SGD", quote_currency="USD", price=0.00000444),
        BASE,
        FX,
    )
    assert r["display_currency"] == "SGD"
    # 0.00000444 USD × (2.0 / 1.0) = 0.00000888 SGD
    assert r["display_unit_price"] == pytest.approx(0.00000888, rel=1e-6)
    assert r["display_unit_price"] != 0


def test_display_unit_price_scales_by_quote_per_unit():
    """A per-gram holding must read per GRAM, not per troy ounce.

    `quantity` and `cost_basis` are per holding unit; the native quote is per `quote_per_unit`
    of them. Rendering the raw quote beside a per-gram quantity invites the reader to multiply
    two figures that are not in the same unit.
    """
    r = _project(
        _row(
            asset_class="commodity",
            currency="USD",
            quote_currency="USD",
            price=2000,
            quote_per_unit=0.001,
            cost_basis=1.8,
        ),
        "USD",
        {"USD": 1.0},
    )
    assert r["display_unit_price"] == pytest.approx(2, abs=0.001)  # 2000 x 0.001
    assert r["base_value"] == pytest.approx(2, abs=0.001)  # 1 unit x 2


# ── 2. "since grant" reference P&L ────────────────────────────────────────────

GRANT_FX = {"USD": 1.0, "SGD": 0.74}


def _grant_row(**overrides) -> dict:
    return _row(
        category="ACME",
        label="ACME Inc.",
        asset_class="equity",
        quantity=100,
        price=20,
        cost_basis=15,
        currency="USD",
        quote_currency="USD",
        ticker="ACME",
        **overrides,
    )


def test_ref_pl_is_computed_for_an_unvested_row_with_a_matching_grant_currency():
    r = _project(
        _grant_row(vesting_state="unvested", ref_grant_price=10, ref_grant_currency="USD"),
        "USD",
        GRANT_FX,
    )
    assert r["ref_pl"] == pytest.approx(1000, abs=0.01)  # (20 − 10) × 100
    assert r["ref_pl_pct"] == pytest.approx(100, abs=0.01)


def test_ref_pl_is_omitted_on_a_currency_mismatch():
    """No grant-date FX rate exists, so no figure beats a misleading one."""
    r = _project(
        _grant_row(vesting_state="unvested", ref_grant_price=10, ref_grant_currency="SGD"),
        "USD",
        GRANT_FX,
    )
    assert r["ref_pl"] is None
    assert r["ref_pl_pct"] is None


def test_ref_pl_is_omitted_on_a_vested_row_even_with_a_grant_price():
    r = _project(
        _grant_row(vesting_state="vested", ref_grant_price=10, ref_grant_currency="USD"),
        "USD",
        GRANT_FX,
    )
    assert r["ref_pl"] is None
    assert r["ref_pl_pct"] is None


def test_ref_pl_is_omitted_when_no_grant_price_is_recorded():
    r = _project(_grant_row(vesting_state="unvested", ref_grant_currency="USD"), "USD", GRANT_FX)
    assert r["ref_pl"] is None
    assert r["ref_pl_pct"] is None


# ── 3 + 4. entitlement gates net worth; reachability does not ─────────────────


@pytest.fixture
def book(tmp_path, monkeypatch, db_isolated):
    """A book holding a vested position, an unvested grant, and an illiquid property account.
    PriceHistoryStore resolves against the per-test isolated database (db_isolated)."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore
    from backend.services import ledger as ledger_svc
    from backend.services import prices as prices_service

    monkeypatch.setattr(prices_service, "fetch_live", lambda *a, **k: {"prices": {}, "fx": {}})

    broker = ledger_svc.accounts.create(
        {"name": "Broker", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    # An ILLIQUID but fully-OWNED thing — the illiquid property from the original scar. A
    # property is an owned asset, not an account (ADR 2026-09-06-assets-tab-faithful-port).
    from backend.services.owned_assets import apply_owned_asset_change

    apply_owned_asset_change(
        {
            "op": "create",
            "name": "Apartment",
            "category": "property",
            "value": 350_000.0,
            "cost": 350_000.0,
            "cost_date": "2026-01-01",
        }
    )
    for symbol, qty, bucket in (("VEST", 10, "liquid"), ("GRANT", 5, "locked")):
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
                "account_id": broker["id"],
                "quantity": qty,
                "bucket": bucket,
            }
        )
    return tmp_path


def test_unvested_units_stay_out_of_the_total_and_are_reported_separately(book):
    d = projected_rows(live=False)
    assert d["unvested_base"] == pytest.approx(500, abs=0.01)  # 5 × 100
    # 10 × 100 vested + 350,000 property — the unvested 500 is absent.
    assert d["portfolio_total"] == pytest.approx(351_000, abs=0.01)


def test_an_illiquid_but_vested_property_row_is_never_dropped(book):
    """The regression the overloaded `bucket == "locked"` filter would have caused: property is
    illiquid AND owned. Reachability must not answer an entitlement question."""
    d = projected_rows(live=False)
    prop = next(r for r in d["rows"] if r["asset_class"] == "property")
    assert prop["liquidity"] == "illiquid"
    assert prop["vesting_state"] == "vested"
    assert prop["base_value"] == pytest.approx(350_000, abs=0.01)
    assert prop["base_value"] <= d["portfolio_total"]


def test_an_unvested_row_carries_no_allocation_percentage(book):
    d = projected_rows(live=False)
    for row in d["rows"]:
        if row.get("vesting_state") == "unvested":
            assert row["pct_of_portfolio"] == 0.0
        elif row["base_value"] > 0:
            assert row["pct_of_portfolio"] > 0


def test_per_account_holdings_exclude_unvested_grants(book):
    """`holdings_by_account` is what the Accounts tab shows beside a securities account.

    It counted unvested grant units, so an equity-grant account read S$60.0K when S$45.0K of
    that was not the holder's at all — and the tab's net worth came out ~S$70K above the
    authoritative figure. Same gate as net worth; unvested is reported separately.
    """
    d = projected_rows(live=False)
    assert d["holdings_by_account"]  # the broker account holds something
    assert sum(d["holdings_by_account"].values()) == pytest.approx(
        sum(
            r["base_value"]
            for r in d["rows"]
            if r.get("instrument_id") and r.get("vesting_state") != "unvested"
        ),
        abs=0.02,
    )
    assert sum(d["unvested_by_account"].values()) == pytest.approx(d["unvested_base"], abs=0.02)
    # The two must not overlap — a unit is held OR unvested, never counted twice.
    assert sum(d["holdings_by_account"].values()) + sum(
        d["unvested_by_account"].values()
    ) == pytest.approx(sum(r["base_value"] for r in d["rows"] if r.get("instrument_id")), abs=0.02)


def test_weights_are_computed_against_the_vested_total(book):
    d = projected_rows(live=False)
    vested = [r for r in d["rows"] if r.get("vesting_state") != "unvested" and r["base_value"] > 0]
    assert sum(r["pct_of_portfolio"] for r in vested) == pytest.approx(100, abs=0.05)


def test_portfolio_reads_account_and_opening_from_one_snapshot(db_isolated, monkeypatch):
    from backend.core.fx import base_currency
    from backend.services import accounts as account_service
    from backend.services import ledger as ledger_service

    store = ledger_service.transactions
    original_all = store.all
    created = []

    def read_then_create(*, conn=None):
        rows = original_all() if conn is None else original_all(conn=conn)
        if not created:
            created.append(
                account_service.create_account(
                    {
                        "name": "Racing account",
                        "type": "cash",
                        "currency": base_currency(),
                        "initial_balance": 42,
                    }
                )
            )
        return rows

    monkeypatch.setattr(store, "all", read_then_create)
    snapshot = projected_rows(live=False)

    assert created
    assert not any(r.get("account_name") == "Racing account" for r in snapshot["rows"])
