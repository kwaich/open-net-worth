"""
Owned-assets → net-worth seam (ADR 2026-09-06-assets-tab-faithful-port, decision 6).

Covers the three fold sites in `services.ledger` (`net_worth`, `net_worth_bands` via
`_breakdown_as_of`, `net_worth_history`), the `portfolio_rows.projected_rows` row
projection, the read-cache invalidation on write, and the two REST endpoints (`/api/net-worth`,
`/api/portfolio/analytics`) that surface them.

Real stores, db_isolated (fresh migrated DB per test) — no live data, no network. Base
currency in a fresh instance is "SGD", so every fixture below stays in SGD and every FX
conversion in the code under test is a same-currency identity round.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.db.account_store import AccountStore
from backend.main import app
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services import owned_assets as owned_assets_service
from backend.services import portfolio_rows

client = TestClient(app)


@pytest.fixture(autouse=True)
def _offline_prices(monkeypatch):
    """No network in unit tests — mirrors tests/test_portfolio_analytics.py."""
    from backend.services import prices as prices_service

    def _fail(*_args, **_kwargs):
        raise RuntimeError("offline (test)")

    monkeypatch.setattr(prices_service, "fetch_live", _fail)


@pytest.fixture
def isolated(db_isolated):
    return db_isolated


def _create_asset(**kw):
    payload = {"op": "create", "name": "Test Asset", "category": "property", "value": 100_000}
    payload.update(kw)
    return owned_assets_service.apply_owned_asset_change(payload)


# ── net_worth() ──────────────────────────────────────────────────────────────────────────


def test_asset_with_share_pct_adds_share_adjusted_value_under_property_band(isolated):
    before = ledger_service.net_worth()["net_worth"]
    _create_asset(category="property", value=100_000, share_pct=50)
    nw = ledger_service.net_worth()
    assert nw["net_worth"] == pytest.approx(before + 50_000)
    assert nw["breakdown"]["account_classes"]["property"] == pytest.approx(50_000)
    assert nw["breakdown"]["owned_assets"] == pytest.approx(50_000)


def test_metal_asset_lands_in_commodity_band(isolated):
    _create_asset(category="metal", value=8_000)
    nw = ledger_service.net_worth()
    assert nw["breakdown"]["account_classes"]["commodity"] == pytest.approx(8_000)
    assert "metal" not in nw["breakdown"]["account_classes"]


def test_disposed_asset_contributes_zero(isolated):
    cash = AccountStore().create({"name": "DBS", "type": "cash", "kind": "asset"})
    asset = _create_asset(category="vehicle", value=20_000)
    before = ledger_service.net_worth()
    assert before["breakdown"]["account_classes"].get("other_asset", 0.0) == pytest.approx(20_000)

    owned_assets_service.apply_owned_asset_change(
        {"op": "dispose", "id": asset["id"], "proceeds": 20_000, "account_id": cash["id"]}
    )
    after = ledger_service.net_worth()
    assert after["breakdown"]["account_classes"].get("other_asset", 0.0) == 0.0
    # Proceeds landed in cash — total net worth is unchanged by a like-for-like disposal.
    assert after["net_worth"] == pytest.approx(before["net_worth"])


def test_add_mark_invalidates_net_worth_cache(isolated):
    asset = _create_asset(category="property", value=50_000)
    nw1 = ledger_service.net_worth()["net_worth"]
    owned_assets_service.apply_owned_asset_change(
        {"op": "add_mark", "id": asset["id"], "value": 70_000}
    )
    nw2 = ledger_service.net_worth()["net_worth"]
    assert nw2 == pytest.approx(nw1 + 20_000)


def test_summary_and_analytics_do_not_double_count_asset_gain(isolated):
    """BLOCKER regression: `_asset_as_row` gives every costed owned asset a `pl`, so
    `portfolio_analytics`'s market-wide folds (`_kpis`, `_gains`, `_venues`) must exclude
    `account_type == "owned_asset"` rows — `owned_assets.summary()`'s own `asset_gain` loop
    is the single owner of that figure."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore
    from backend.services import portfolio_analytics as portfolio_analytics_service

    bank = AccountStore().create({"name": "Everyday", "kind": "asset", "type": "cash"})
    instrument = InstrumentStore().create(
        {
            "symbol": "ACME",
            "name": "Acme Corp",
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": "ACME",
            "manual_price": 100.0,
        }
    )
    PositionStore().create(
        {
            "instrument_id": instrument["id"],
            "account_id": bank["id"],
            "quantity": 10.0,
            "cost_basis": 80.0,
            "cost_currency": "SGD",
            "bucket": "liquid",
        }
    )
    # market upl = (100 - 80) * 10 = 200

    asset = _create_asset(category="watch", value=15_000, cost=10_000)  # asset gain = 5,000

    summary = owned_assets_service.summary()
    assert summary["unrealized"] == pytest.approx(200 + 5_000)

    analytics = portfolio_analytics_service.portfolio_analytics()
    assert analytics["kpis"]["upl"] == pytest.approx(200)  # excludes the asset's gain

    asset_row_key = f"owned_asset:{asset['id']}"
    assert asset_row_key not in {g["key"] for g in analytics["gains"]["rows"]}
    assert analytics["gains"]["total_pl"] == pytest.approx(200)

    venue_keys = {row["key"] for row in analytics["venues"]["rows"]}
    assert asset["name"] not in venue_keys

    # Allocation / reach still pick the asset up — that's the point of the row.
    allocation_rows = {row["key"]: row["value"] for row in analytics["allocation"]["rows"]}
    assert allocation_rows["other_asset"] == pytest.approx(15_000)
    reach_rows = {row["key"]: row["value"] for row in analytics["reach"]["rows"]}
    assert reach_rows["illiquid"] == pytest.approx(15_000)


# ── net_worth_history() ──────────────────────────────────────────────────────────────────


def test_history_points_before_first_mark_are_zero_after_carry_newest(isolated):
    # Seed ledger history so net_worth_history walks several monthly points.
    accounts_service.create_account(
        {
            "name": "Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1_000,
            "initial_balance_date": "2024-01-01",
        }
    )
    asset = _create_asset(category="property", value=100_000, cost=80_000, cost_date="2024-06-01")
    assert {m["date"] for m in asset["marks"]} >= {"2024-06-01"}

    history = ledger_service.net_worth_history()
    by_date = {p["date"]: p for p in history}

    before_first_mark = next(d for d in sorted(by_date) if d < "2024-06-01")
    assert by_date[before_first_mark]["classes"].get("property", 0.0) == 0.0

    after_first_before_today = next(
        d for d in sorted(by_date) if "2024-06-01" <= d < by_date[max(by_date)]["date"]
    )
    assert by_date[after_first_before_today]["classes"]["property"] == pytest.approx(80_000)

    today_point = max(by_date)
    assert by_date[today_point]["classes"]["property"] == pytest.approx(100_000)


def _seed_checking(since: str) -> None:
    """A ledger account old enough that net_worth_history charts monthly points from `since`."""
    accounts_service.create_account(
        {
            "name": "Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1_000,
            "initial_balance_date": since,
        }
    )


def _history_classes() -> dict[str, dict]:
    return {p["date"]: p["classes"] for p in ledger_service.net_worth_history()}


def test_history_follows_an_edited_purchase_date(isolated):
    """The purchase point is derived from cost_date, so editing the date moves the history's
    purchase point with it — a stored copy used to stay behind at the old date."""
    _seed_checking("2024-01-01")
    asset = _create_asset(category="property", value=100_000, cost=80_000, cost_date="2024-06-01")
    assert _history_classes()["2024-04-30"].get("property", 0.0) == 0.0

    owned_assets_service.apply_owned_asset_change(
        {"op": "update", "id": asset["id"], "changes": {"cost_date": "2024-03-01"}}
    )
    classes = _history_classes()
    assert classes["2024-02-29"].get("property", 0.0) == 0.0
    assert classes["2024-04-30"]["property"] == pytest.approx(80_000)


def test_policy_history_starts_at_first_valuation(isolated):
    """Premiums paid are not a historical value: a policy contributes nothing before its first
    valuation, whatever its cost and start date."""
    _seed_checking("2024-01-01")
    _create_asset(category="policy", value=42_000, cost=18_000, cost_date="2024-06-01")
    classes = _history_classes()
    today = max(classes)
    assert all(classes[d].get("other_asset", 0.0) == 0.0 for d in classes if d != today)
    assert classes[today]["other_asset"] == pytest.approx(42_000)


def test_purchase_edit_never_moves_todays_net_worth(isolated):
    asset = _create_asset(
        category="property", value=100_000, cost=80_000, cost_date="2024-06-01", share_pct=50
    )
    before = ledger_service.net_worth()["net_worth"]
    for changes in (
        {"cost_date": "2020-01-01"},
        {"cost": 120_000},
        {"cost_date": date.today().isoformat()},
        {"cost": None},
        {"cost_date": None},
        {"category": "policy", "cost": 5_000, "cost_date": "2021-01-01"},
        {"category": "vehicle"},
    ):
        owned_assets_service.apply_owned_asset_change(
            {"op": "update", "id": asset["id"], "changes": changes}
        )
        assert ledger_service.net_worth()["net_worth"] == pytest.approx(before), changes


def test_correcting_a_valuation_moves_history(isolated):
    _seed_checking("2024-01-01")
    asset = _create_asset(category="property", value=100_000)
    added = owned_assets_service.apply_owned_asset_change(
        {"op": "add_mark", "id": asset["id"], "value": 90_000, "date": "2024-03-15"}
    )
    mark_id = next(m["id"] for m in added["marks"] if m["date"] == "2024-03-15")
    assert _history_classes()["2024-04-30"]["property"] == pytest.approx(90_000)

    owned_assets_service.apply_owned_asset_change(
        {"op": "correct_mark", "id": asset["id"], "mark_id": mark_id, "value": 95_000}
    )
    assert _history_classes()["2024-04-30"]["property"] == pytest.approx(95_000)


def test_removing_a_valuation_moves_net_worth(isolated):
    asset = _create_asset(category="property", value=100_000)
    owned_assets_service.apply_owned_asset_change(
        {"op": "add_mark", "id": asset["id"], "value": 80_000, "date": "2024-01-01"}
    )
    before = ledger_service.net_worth()["net_worth"]
    owned_assets_service.apply_owned_asset_change(
        {"op": "remove_mark", "id": asset["id"], "mark_id": asset["marks"][0]["id"]}
    )
    assert ledger_service.net_worth()["net_worth"] == pytest.approx(before - 20_000)


def test_net_worth_history_reads_owned_assets_store_once(isolated, monkeypatch):
    """SHOULD regression: `net_worth_history` used to call `values_by_band` (and so
    `OwnedAssetsStore().all()`) once PER CHARTED MONTH. It must load the rows once and refold
    them per point via `values_by_band_from_rows` instead."""
    accounts_service.create_account(
        {
            "name": "Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1_000,
            "initial_balance_date": "2024-01-01",
        }
    )
    _create_asset(category="property", value=100_000, cost=80_000, cost_date="2024-06-01")

    from backend.db.owned_assets_store import OwnedAssetsStore

    calls = {"count": 0}
    original_all = OwnedAssetsStore.all

    def _spy(self, *args, **kwargs):
        calls["count"] += 1
        return original_all(self, *args, **kwargs)

    monkeypatch.setattr(OwnedAssetsStore, "all", _spy)

    history = ledger_service.net_worth_history()
    assert len(history) > 2  # several charted months — the whole point of this test
    assert calls["count"] == 1


# ── portfolio_rows.projected_rows() ──────────────────────────────────────────────────────


def test_projected_rows_includes_asset_as_illiquid_row(isolated):
    asset = _create_asset(category="watch", value=15_000)
    rows = portfolio_rows.projected_rows(live=False)["rows"]
    asset_row = next(r for r in rows if r.get("position_id") == f"owned_asset:{asset['id']}")
    assert asset_row["liquidity"] == "illiquid"
    assert asset_row["asset_class"] == "other_asset"
    assert asset_row["is_account_row"] is False
    assert asset_row["base_value"] == pytest.approx(15_000)


# ── REST endpoints ───────────────────────────────────────────────────────────────────────


def test_net_worth_api_reflects_asset(isolated):
    _create_asset(category="property", value=40_000)
    r = client.get("/api/net-worth")
    assert r.status_code == 200
    assert r.json()["breakdown"]["account_classes"]["property"] == pytest.approx(40_000)


def test_portfolio_analytics_api_reflects_asset(isolated):
    _create_asset(category="property", value=40_000)
    r = client.get("/api/portfolio/analytics")
    assert r.status_code == 200
    body = r.json()
    allocation_rows = {row["key"]: row["value"] for row in body["allocation"]["rows"]}
    assert allocation_rows["property"] == pytest.approx(40_000)
    reach_rows = {row["key"]: row["value"] for row in body["reach"]["rows"]}
    assert reach_rows["illiquid"] == pytest.approx(40_000)
