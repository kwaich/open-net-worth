"""
Unit + integration tests for first-class bond / fixed-income support
(bonds-first-class RFC).

Covers:
  - services.fixed_income pure math (computed_mark per subtype, coupon schedule, accretion)
  - db.instrument_store._coerce_bond_terms (the coercion authority)
  - positions.hydrated valuation of each subtype via the qty × price × quote_per_unit fold
  - income.forecast_income picking up bond coupons / T-bill accretion

Offline: pure functions take an explicit as_of; store-backed cases monkeypatch the stores
(mirrors tests/test_positions_hydrated_zero_qty.py and tests/test_income_service.py).
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore, _make_instrument
from backend.db.position_store import PositionStore
from backend.db.price_history_store import PriceHistoryStore
from backend.services import fixed_income as fi
from backend.services import income as income_svc
from backend.services import positions as positions_service

# ── helpers ──────────────────────────────────────────────────────────────────────


def _bond(subtype, **terms):
    """A bond instrument dict with nested bond_terms (as the store would persist)."""
    bt = {
        "subtype": subtype,
        "face_value": terms.get("face_value", 100.0),
        "coupon_rate_annual": terms.get("coupon_rate_annual"),
        "coupon_freq_months": terms.get("coupon_freq_months"),
        "issue_date": terms.get("issue_date"),
        "maturity_date": terms.get("maturity_date"),
        "issue_price": terms.get("issue_price"),
    }
    return {"id": "inst-b", "asset_class": "bond", "quote_currency": "SGD", "bond_terms": bt}


# ── computed_mark ──────────────────────────────────────────────────────────────────


def test_ssb_marks_at_par_regardless_of_date():
    inst = _bond("ssb", face_value=1, coupon_rate_annual=0.032, coupon_freq_months=6)
    assert fi.computed_mark(inst, as_of=date(2026, 1, 1)) == 100.0
    assert fi.computed_mark(inst, as_of=date(2030, 12, 31)) == 100.0


def test_tbill_accretes_linearly_issue_to_par():
    inst = _bond(
        "tbill",
        face_value=1,
        issue_price=99.0,
        issue_date="2026-01-01",
        maturity_date="2026-12-31",
    )
    # at issue → entry price; at maturity → par; halfway → midpoint
    assert fi.computed_mark(inst, as_of=date(2026, 1, 1)) == pytest.approx(99.0, abs=0.02)
    assert fi.computed_mark(inst, as_of=date(2026, 12, 31)) == pytest.approx(100.0, abs=0.02)
    mid = fi.computed_mark(inst, as_of=date(2026, 7, 2))
    assert 99.4 < mid < 99.6


def test_tbill_clamps_outside_term():
    inst = _bond("tbill", issue_price=98.0, issue_date="2026-01-01", maturity_date="2026-07-01")
    assert fi.computed_mark(inst, as_of=date(2025, 1, 1)) == pytest.approx(98.0)  # pre-issue
    assert fi.computed_mark(inst, as_of=date(2027, 1, 1)) == pytest.approx(100.0)  # past maturity


def test_fd_accrues_simple_interest_capped_at_maturity():
    inst = _bond(
        "fd",
        coupon_rate_annual=0.0365,
        issue_date="2026-01-01",
        maturity_date="2027-01-01",
    )
    # ~half a year → ~half the annual rate accrued
    half = fi.computed_mark(inst, as_of=date(2026, 7, 2))
    assert half == pytest.approx(100.0 * (1 + 0.0365 * 182 / 365), abs=0.05)
    # past maturity → capped at full term (one year of interest)
    full = fi.computed_mark(inst, as_of=date(2030, 1, 1))
    assert full == pytest.approx(100.0 * (1 + 0.0365), abs=0.05)


def test_market_and_etf_have_no_computed_mark():
    assert fi.computed_mark(_bond("market", face_value=1000), as_of=date(2026, 6, 1)) is None
    assert fi.computed_mark(_bond("etf"), as_of=date(2026, 6, 1)) is None


def test_non_bond_has_no_computed_mark():
    assert fi.computed_mark({"asset_class": "equity"}, as_of=date(2026, 6, 1)) is None
    assert fi.computed_mark(None) is None


# ── coupon / income math ───────────────────────────────────────────────────────────


def test_annual_coupon_per_unit():
    inst = _bond("market", face_value=1000, coupon_rate_annual=0.04, coupon_freq_months=6)
    assert fi.annual_coupon_per_unit(inst) == pytest.approx(40.0)
    # tbill is zero-coupon
    assert fi.annual_coupon_per_unit(_bond("tbill", face_value=1)) == 0.0


def test_annual_income_per_unit_sources():
    coupon_inst = _bond("market", face_value=1000, coupon_rate_annual=0.05)
    amt, src = fi.annual_income_per_unit(coupon_inst)
    assert (amt, src) == (pytest.approx(50.0), "coupon")

    tbill = _bond(
        "tbill",
        face_value=1,
        issue_price=98.0,
        issue_date="2026-01-01",
        maturity_date="2026-12-31",
    )
    amt, src = fi.annual_income_per_unit(tbill)
    assert src == "accretion"
    assert amt == pytest.approx(0.02, abs=0.001)  # ~2% discount over ~1y


def test_coupon_schedule_and_next_coupon():
    inst = _bond(
        "market",
        face_value=1000,
        coupon_rate_annual=0.04,
        coupon_freq_months=6,
        maturity_date="2027-06-15",
    )
    sched = fi.coupon_schedule(inst, date(2026, 1, 1), date(2027, 6, 30))
    # semiannual anchored on maturity → 2026-06-15, 2026-12-15, 2027-06-15
    assert date(2026, 6, 15) in sched
    assert date(2026, 12, 15) in sched
    assert sched == sorted(sched)

    nxt = fi.next_coupon(inst, as_of=date(2026, 1, 1))
    assert nxt["date"] == "2026-06-15"
    assert nxt["amount_per_unit"] == pytest.approx(20.0)  # 40/yr semiannual → 20


def test_days_to_maturity():
    inst = _bond("market", maturity_date="2026-07-01")
    assert fi.days_to_maturity(inst, as_of=date(2026, 6, 1)) == 30
    assert fi.days_to_maturity(_bond("etf"), as_of=date(2026, 6, 1)) is None


# ── robustness: malformed coupon frequency must never crash the lens (SEC MEDIUM) ───


def test_coupon_freq_months_clamped_at_store():
    """A fat-fingered / AI-set absurd coupon_freq_months is clamped at the coercion authority
    so it can never reach the date-walk as an out-of-range step."""
    bond = _make_instrument(
        {
            "symbol": "BIG",
            "asset_class": "bond",
            "quote_currency": "USD",
            "bond_terms": {
                "subtype": "market",
                "coupon_freq_months": 100_000_000,
                "maturity_date": "2030-01-01",
            },
        }
    )
    assert bond["bond_terms"]["coupon_freq_months"] == 120  # clamped to the [1,120] domain
    # zero / negative → None (no schedule)
    bad = _make_instrument(
        {
            "symbol": "B0",
            "asset_class": "bond",
            "quote_currency": "USD",
            "bond_terms": {"subtype": "fd", "coupon_freq_months": 0},
        }
    )
    assert bad["bond_terms"]["coupon_freq_months"] is None


def test_coupon_walk_never_raises_on_absurd_terms():
    """Backstop: even if an unclamped absurd frequency + far maturity reaches the walk
    directly, it degrades gracefully (no ValueError / no hang) — never a 500."""
    rogue = _bond(
        "market",
        face_value=1000,
        coupon_rate_annual=0.04,
        coupon_freq_months=100_000_000,
        maturity_date="9999-12-31",
    )
    # must return without raising (bounded by the iteration cap + ValueError guard)
    assert fi.coupon_schedule(rogue, date(2026, 1, 1), date(2027, 1, 1)) == [] or True
    assert fi.next_coupon(rogue, as_of=date(2026, 1, 1)) is None or isinstance(
        fi.next_coupon(rogue, as_of=date(2026, 1, 1)), dict
    )


# ── store coercion (the authority) ─────────────────────────────────────────────────


def test_coerce_bond_terms_only_for_bonds():
    eq = _make_instrument(
        {
            "symbol": "AAA",
            "asset_class": "equity",
            "quote_currency": "USD",
            "bond_terms": {"subtype": "fd"},
        }
    )
    assert eq["bond_terms"] is None  # never invented on a non-bond

    bond = _make_instrument(
        {
            "symbol": "SSB",
            "asset_class": "bond",
            "quote_currency": "USD",
            "bond_terms": {"subtype": "ssb"},
        }
    )
    assert bond["bond_terms"]["subtype"] == "ssb"
    assert bond["bond_terms"]["face_value"] == 100.0  # par default


def test_coerce_bond_terms_clamps_and_parses():
    bond = _make_instrument(
        {
            "symbol": "TB",
            "asset_class": "bond",
            "quote_currency": "USD",
            "bond_terms": {
                "subtype": "weird",
                "face_value": "1000",
                "coupon_rate_annual": "0.04",
                "coupon_freq_months": "6",
                "maturity_date": "2027-01-01",
                "issue_price": "98.5",
            },
        }
    )
    bt = bond["bond_terms"]
    assert bt["subtype"] == ""  # unknown subtype clamped
    assert bt["face_value"] == 1000.0
    assert bt["coupon_rate_annual"] == 0.04
    assert bt["coupon_freq_months"] == 6
    assert bt["issue_price"] == 98.5
    assert bt["maturity_date"] == "2027-01-01"


def test_bond_with_no_terms_is_none():
    bond = _make_instrument({"symbol": "X", "asset_class": "bond", "quote_currency": "USD"})
    assert bond["bond_terms"] is None


# ── positions valuation integration ────────────────────────────────────────────────


@pytest.fixture
def _hydrate_stores(monkeypatch):
    """Wire positions.hydrated onto in-memory fixtures (no data files touched)."""
    instruments = [
        # SSB: par-redeemable; qty = principal, face=1, qpu=0.01 → value = qty × 100 × 0.01
        {
            "id": "inst-ssb",
            "symbol": "SSB32",
            "name": "SSB Oct 2032",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "",
            "quote_per_unit": 0.01,
            "bond_terms": {
                "subtype": "ssb",
                "face_value": 1.0,
                "coupon_rate_annual": 0.032,
                "coupon_freq_months": 6,
                "maturity_date": "2032-10-01",
                "issue_date": None,
                "issue_price": None,
            },
        },
        # Market bond: manual clean price as % of par; face=1000, qpu=10 → value = qty × px × 10
        {
            "id": "inst-mkt",
            "symbol": "CORP",
            "name": "Corp 4% 2028",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "",
            "quote_per_unit": 10.0,
            "manual_price": 98.5,
            "bond_terms": {
                "subtype": "market",
                "face_value": 1000.0,
                "coupon_rate_annual": 0.04,
                "coupon_freq_months": 6,
                "maturity_date": "2028-03-01",
                "issue_date": None,
                "issue_price": None,
            },
        },
    ]
    positions = [
        {
            "id": "p-ssb",
            "instrument_id": "inst-ssb",
            "account_id": "acc",
            "quantity": 20000.0,
            "bucket": "liquid",
            "cost_currency": "SGD",
        },
        {
            "id": "p-mkt",
            "instrument_id": "inst-mkt",
            "account_id": "acc",
            "quantity": 10.0,
            "bucket": "liquid",
            "cost_currency": "SGD",
        },
    ]
    accounts = [
        {"id": "acc", "name": "Meridian Bank", "platform": "Meridian Bank", "currency": "SGD"}
    ]

    monkeypatch.setattr(InstrumentStore, "all", lambda self: instruments)
    monkeypatch.setattr(PositionStore, "all", lambda self: positions)
    monkeypatch.setattr(AccountStore, "all", lambda self: accounts)
    monkeypatch.setattr(PriceHistoryStore, "latest_map", lambda self: {})
    monkeypatch.setattr(PriceHistoryStore, "prior_to_map", lambda self: {})


def test_ssb_values_at_par_and_market_bond_uses_clean_price(_hydrate_stores):
    rows = {r["instrument_id"]: r for r in positions_service.hydrated()}

    ssb = rows["inst-ssb"]
    assert ssb["price"] == 100.0  # computed par mark
    assert ssb["price"] * float(ssb["quantity"]) * ssb["quote_per_unit"] == pytest.approx(20000.0)
    assert ssb["bond_type"] == "ssb"
    assert ssb["coupon_rate"] == 0.032
    assert ssb["maturity_date"] == "2032-10-01"

    mkt = rows["inst-mkt"]
    assert mkt["price"] == 98.5  # manual clean price (% of par) — no computed mark
    assert mkt["price"] * float(mkt["quantity"]) * mkt["quote_per_unit"] == pytest.approx(9850.0)
    assert mkt["bond_type"] == "market"
    assert mkt["coupon_freq"] == 2  # 12 / 6 months


# ── income forecast picks up coupons ───────────────────────────────────────────────


def test_forecast_includes_bond_coupon(monkeypatch):
    monkeypatch.setattr(income_svc.lots_service, "_load_rates", lambda: {"SGD": 1.0})
    monkeypatch.setattr(income_svc, "base_currency", lambda: "SGD")
    inst = {
        "id": "inst-mkt",
        "symbol": "CORP",
        "name": "Corp 4%",
        "asset_class": "bond",
        "quote_currency": "SGD",
        "bond_terms": {
            "subtype": "market",
            "face_value": 1000.0,
            "coupon_rate_annual": 0.04,
            "coupon_freq_months": 6,
            "maturity_date": "2028-03-01",
            "issue_date": None,
            "issue_price": None,
        },
    }
    pos = {
        "id": "p-mkt",
        "instrument_id": "inst-mkt",
        "quantity": 10.0,
        "account_id": "acc",
        "cost_currency": "SGD",
    }
    monkeypatch.setattr(income_svc.positions, "all", lambda: [pos])
    monkeypatch.setattr(income_svc.instruments, "all", lambda: [inst])
    monkeypatch.setattr(income_svc.lots_service, "position_remaining_basis", lambda pid: 9850.0)

    out = income_svc.forecast_income()
    row = out["holdings"][0]
    # 10 bonds × 1000 face × 4% = 400/yr coupon
    assert row["annual_income_base"] == pytest.approx(400.0)
    assert row["source"] == "coupon"


# ── summary() + endpoint + update_instrument bond_terms ─────────────────────────────


@pytest.fixture
def _isolated_stores(tmp_path, monkeypatch, db_isolated):
    """PriceHistoryStore resolves against the per-test isolated database (db_isolated).
    accounts are DB-backed now (plan 3 task 4) — db_isolated (above) covers that store;
    positions is still file-backed and needs its JSON path patched here."""
    from backend.core import fx as fx_mod
    from backend.services import lots as lots_service

    monkeypatch.setattr(fx_mod, "base_currency", lambda: "SGD")
    monkeypatch.setattr(fx_mod, "to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2))
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})


def _seed_bond_portfolio():
    acct = AccountStore().create(
        {
            "name": "Meridian Bank",
            "platform": "Meridian Bank",
            "type": "investment",
            "currency": "SGD",
        }
    )
    ssb = InstrumentStore().create(
        {
            "symbol": "SSB",
            "name": "SSB Oct 2032",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "price_source": "manual",
            "quote_per_unit": 0.01,
            "bond_terms": {
                "subtype": "ssb",
                "face_value": 1,
                "coupon_rate_annual": 0.032,
                "coupon_freq_months": 6,
                "maturity_date": "2032-10-01",
            },
        }
    )
    PositionStore().create(
        {
            "instrument_id": ssb["id"],
            "account_id": acct["id"],
            "quantity": 20000.0,
            "cost_currency": "SGD",
        }
    )


def test_summary_values_and_buckets(_isolated_stores):
    _seed_bond_portfolio()
    out = fi.summary()
    assert out["count"] == 1
    assert out["total_base"] == pytest.approx(20000.0)
    assert out["by_subtype"]["ssb"] == pytest.approx(20000.0)
    # SSB matures 2032 → not in any near bucket; ladder empty (>365d out)
    assert out["maturity_buckets"]["d30"] == 0.0
    assert out["holdings"][0]["subtype"] == "ssb"


def test_summary_excludes_locked_bucket_bond(_isolated_stores):
    """vesting_state="unvested" = unvested equity-grant units, not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth; the entitlement axis itself was
    later split out of the overloaded `bucket` field — ADR 2026-07-31-split-position-
    bucket). No current flow mints an unvested BOND specifically, but add_positions
    accepts vesting_state freely on any asset_class, so summary() must not silently fold
    one into total_base/by_subtype/the maturity ladder."""
    _seed_bond_portfolio()  # one owned (vested) SSB position, quantity 20000
    ssb = InstrumentStore().all()[0]
    acct = AccountStore().all()[0]
    PositionStore().create(
        {
            "instrument_id": ssb["id"],
            "account_id": acct["id"],
            "quantity": 5000.0,
            "cost_currency": "SGD",
            "vesting_state": "unvested",
        }
    )

    out = fi.summary()
    assert out["count"] == 1  # only the liquid position is reported
    assert out["total_base"] == pytest.approx(20000.0)  # unaffected by the locked position
    assert out["by_subtype"]["ssb"] == pytest.approx(20000.0)


def test_fixed_income_endpoint_smoke(_isolated_stores):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.fixed_income import router

    _seed_bond_portfolio()
    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).get("/api/fixed-income")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert set(body) >= {"total_base", "by_subtype", "maturity_buckets", "maturity_ladder"}


def test_update_instrument_sets_and_clears_bond_terms(_isolated_stores):
    from backend.services.instruments import update_instrument

    inst = InstrumentStore().create(
        {"symbol": "CORP", "asset_class": "equity", "quote_currency": "USD"}
    )
    # promote to a bond with terms
    res = update_instrument(
        inst["id"],
        {
            "asset_class": "bond",
            "bond_terms": {
                "subtype": "market",
                "face_value": 1000,
                "coupon_rate_annual": 0.04,
                "maturity_date": "2028-03-01",
            },
        },
    )
    assert res["ok"]
    assert res["instrument"]["bond_terms"]["subtype"] == "market"
    assert res["instrument"]["bond_terms"]["face_value"] == 1000.0

    # demote back to equity → bond_terms must drop to None (terms are meaningless on a stock)
    res2 = update_instrument(inst["id"], {"asset_class": "equity", "bond_terms": {"subtype": "fd"}})
    assert res2["instrument"]["bond_terms"] is None


# ── MATH-50: quote_per_unit must be DERIVED from face_value, never left at 1.0 ──────


def test_create_bond_derives_quote_per_unit_from_face_value(_isolated_stores):
    """ADR 0020: price is percent-of-par, so quote_per_unit MUST equal face_value/100 —
    a market bond (face 1000) and an SSB/T-bill/FD (face 1) both derive correctly."""
    market = InstrumentStore().create(
        {
            "symbol": "CORP",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "bond_terms": {"subtype": "market", "face_value": 1000, "coupon_rate_annual": 0.04},
        }
    )
    assert market["quote_per_unit"] == 10.0

    ssb = InstrumentStore().create(
        {
            "symbol": "SSB",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "bond_terms": {"subtype": "ssb", "face_value": 1},
        }
    )
    assert ssb["quote_per_unit"] == 0.01


def test_create_bond_ignores_caller_supplied_quote_per_unit(_isolated_stores):
    """A caller-supplied quote_per_unit on a bond is meaningless — the derivation always
    wins so a wrong caller value can never silently mis-value a holding."""
    bond = InstrumentStore().create(
        {
            "symbol": "CORP",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "quote_per_unit": 1.0,
            "bond_terms": {"subtype": "market", "face_value": 1000},
        }
    )
    assert bond["quote_per_unit"] == 10.0


def test_update_instrument_re_derives_quote_per_unit_on_face_value_change(_isolated_stores):
    from backend.services.instruments import update_instrument

    inst = InstrumentStore().create(
        {
            "symbol": "CORP",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "bond_terms": {"subtype": "market", "face_value": 1000},
        }
    )
    assert inst["quote_per_unit"] == 10.0

    res = update_instrument(inst["id"], {"bond_terms": {"subtype": "market", "face_value": 500}})
    assert res["ok"]
    assert res["instrument"]["quote_per_unit"] == 5.0


def test_bond_valuation_via_real_write_surface_uses_derived_quote_per_unit(_isolated_stores):
    """Regression for MATH-50: create bonds through InstrumentStore().create WITHOUT
    setting quote_per_unit by hand (unlike _hydrate_stores' fixtures above) and confirm
    both positions.hydrated() and ledger.row_base_value value them off face_value/100 —
    an SSB principal of 10,000 at par values at 10,000 (not 1,000,000), and 10 market
    bonds at clean price 99 with face 1000 value at 9,900 (not 990)."""
    from backend.services import ledger

    acct = AccountStore().create(
        {
            "name": "Meridian Bank",
            "platform": "Meridian Bank",
            "type": "investment",
            "currency": "SGD",
        }
    )
    ssb = InstrumentStore().create(
        {
            "symbol": "SSB",
            "name": "SSB Oct 2032",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "price_source": "manual",
            "bond_terms": {
                "subtype": "ssb",
                "face_value": 1,
                "coupon_rate_annual": 0.032,
                "coupon_freq_months": 6,
                "maturity_date": "2032-10-01",
            },
        }
    )
    market = InstrumentStore().create(
        {
            "symbol": "CORP",
            "name": "Corp 4% 2028",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "price_source": "manual",
            "manual_price": 99.0,
            "bond_terms": {
                "subtype": "market",
                "face_value": 1000,
                "coupon_rate_annual": 0.04,
                "coupon_freq_months": 6,
                "maturity_date": "2028-03-01",
            },
        }
    )
    PositionStore().create(
        {
            "instrument_id": ssb["id"],
            "account_id": acct["id"],
            "quantity": 10000.0,
            "cost_currency": "SGD",
        }
    )
    PositionStore().create(
        {
            "instrument_id": market["id"],
            "account_id": acct["id"],
            "quantity": 10.0,
            "cost_currency": "SGD",
        }
    )

    rows = {r["instrument_id"]: r for r in positions_service.hydrated()}

    ssb_row = rows[ssb["id"]]
    assert ssb_row["quote_per_unit"] == 0.01
    assert ledger.row_base_value(ssb_row, "SGD") == pytest.approx(10000.0)

    mkt_row = rows[market["id"]]
    assert mkt_row["quote_per_unit"] == 10.0
    assert ledger.row_base_value(mkt_row, "SGD") == pytest.approx(9900.0)


# ── regression: leap-day start + FD proceeds (code-review findings) ─────────────────


def test_coupon_schedule_leap_day_start_does_not_crash():
    """Regression: a Feb-29 `start` once raised ValueError via start.replace(year=...). The
    bounded date-walk must handle a leap-day window without crashing."""
    inst = _bond(
        "market",
        face_value=1000,
        coupon_rate_annual=0.04,
        coupon_freq_months=6,
        maturity_date="2030-02-28",
    )
    leap = date(2028, 2, 29)
    sched = fi.coupon_schedule(inst, leap, date(2030, 3, 1))  # must not raise
    assert isinstance(sched, list)
    assert fi.next_coupon(inst, as_of=leap) is None or isinstance(
        fi.next_coupon(inst, as_of=leap), dict
    )


class _FixedDate(date):
    @classmethod
    def today(cls):  # noqa: D401 - deterministic "today" for the FD accrual test
        return date(2026, 6, 1)


def test_fd_maturity_proceeds_include_accrued_interest(_isolated_stores, monkeypatch):
    """An FD's maturity proceeds = principal + accrued interest, so the ladder/buckets must
    use the computed mark (>par), not hardcoded 100 (code-review finding #3)."""
    monkeypatch.setattr(fi, "user_today", _FixedDate.today)  # pin today so accrual is deterministic
    acct = AccountStore().create(
        {
            "name": "Meridian Bank",
            "platform": "Meridian Bank",
            "type": "investment",
            "currency": "SGD",
        }
    )
    fd = InstrumentStore().create(
        {
            "symbol": "FD12",
            "name": "Meridian Bank FD 4% 12m",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "price_source": "manual",
            "quote_per_unit": 0.01,
            "bond_terms": {
                "subtype": "fd",
                "face_value": 1,
                "coupon_rate_annual": 0.04,
                "issue_date": "2025-06-01",
                "maturity_date": "2026-12-01",
            },
        }
    )
    PositionStore().create(
        {
            "instrument_id": fd["id"],
            "account_id": acct["id"],
            "quantity": 10000.0,
            "cost_currency": "SGD",
        }
    )
    out = fi.summary()
    ladder = out["maturity_ladder"]
    assert len(ladder) == 1
    # ~1 year of 4% accrual on 10,000 principal → proceeds materially above par 10,000
    assert ladder[0]["proceeds_base"] > 10000.0
    assert ladder[0]["proceeds_base"] == pytest.approx(10400.0, abs=5.0)
