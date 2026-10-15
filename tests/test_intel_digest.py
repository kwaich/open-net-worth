"""
Unit tests for backend.services.intel_digest — deterministic, no LLM.

Covers:
- top movers ranked by abs day_change_pct in base currency
- crypto_benchmark omitted when no crypto held
- degrades to weight-ranked when no day_change available (degraded=True)
- data_quality.sufficient=false propagated from benchmark
- determinism: same inputs → same output
- narrate=false never calls LLM (TestClient smoke + unit)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app


def _make_rows(specs: list[dict]) -> list[dict]:
    rows = []
    for i, s in enumerate(specs):
        rows.append(
            {
                "instrument_id": s.get("instrument_id", f"inst-{i}"),
                "label": s.get("label", f"Asset{i}"),
                "ticker": s.get("ticker", f"TKR{i}"),
                "quantity": float(s.get("quantity", 10)),
                "price": float(s.get("price", 100)),
                "quote_per_unit": float(s.get("qpu", 1.0)),
                "quote_currency": s.get("ccy", "USD"),
                "currency": s.get("cost_ccy", s.get("ccy", "USD")),
                "asset_class": s.get("asset_class", "equity"),
                "sector": s.get("sector", ""),
                "bucket": s.get("bucket", "liquid"),
                "cost_basis": s.get("cost_basis", 0),
                "prev_price": s.get("prev_price"),
                "day_change": s.get("day_change"),
                "day_change_pct": s.get("day_change_pct"),
                "tags": s.get("tags", []),
                "vesting_state": s.get("vesting_state", "vested"),
            }
        )
    return rows


def _make_profile(base: str = "SGD") -> dict:
    return {"base_currency": base}


# ── Unit tests for build_digest ───────────────────────────────────────────────


def test_top_movers_ranked_by_abs_pct(monkeypatch):
    """Top movers are ordered by absolute day_change_pct descending."""
    from backend.services.intel_digest import build_digest

    monkeypatch.setattr("backend.services.intel_digest.read_profile", lambda: _make_profile())
    monkeypatch.setattr(
        "backend.services.intel_digest.load_fx_cache", lambda: {"SGD": 1.0, "USD": 0.74}
    )

    rows = _make_rows(
        [
            {"instrument_id": "i1", "day_change": 5, "day_change_pct": 5.0, "price": 100},
            {"instrument_id": "i2", "day_change": -15, "day_change_pct": -15.0, "price": 100},
            {"instrument_id": "i3", "day_change": 10, "day_change_pct": 10.0, "price": 100},
        ]
    )
    monkeypatch.setattr("backend.services.intel_digest.hydrated_positions", lambda: rows)

    # No benchmark available
    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)

    digest = build_digest(period="1M", rows=rows, rates={"SGD": 1.0, "USD": 0.74})
    movers = digest["top_movers"]
    assert movers[0]["instrument_id"] == "i2"  # abs 15% is largest
    assert movers[1]["instrument_id"] == "i3"  # abs 10%
    assert movers[2]["instrument_id"] == "i1"  # abs 5%


def test_unvested_equity_grant_units_excluded_from_movers_and_drivers(monkeypatch):
    """An unvested RSU tranche is not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — its paper day-move must not appear
    as a "driver"/"mover", and must not dilute the denominator (total_base) other holdings'
    contribution percentages are measured against."""
    from backend.services.intel_digest import build_digest

    monkeypatch.setattr("backend.services.intel_digest.read_profile", lambda: _make_profile())
    monkeypatch.setattr(
        "backend.services.intel_digest.load_fx_cache", lambda: {"SGD": 1.0, "USD": 0.74}
    )
    rows = _make_rows(
        [
            {"instrument_id": "i1", "day_change": 5, "day_change_pct": 5.0, "price": 100},
            {
                "instrument_id": "rsu",
                "day_change": 50000,
                "day_change_pct": 90.0,
                "price": 100000,
                "quantity": 1,
                "vesting_state": "unvested",
            },
        ]
    )
    monkeypatch.setattr("backend.services.intel_digest.hydrated_positions", lambda: rows)
    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)

    digest = build_digest(period="1M", rows=rows, rates={"SGD": 1.0, "USD": 0.74})
    driver_ids = {d["instrument_id"] for d in digest["return_drivers"]}
    mover_ids = {m["instrument_id"] for m in digest["top_movers"]}
    assert "rsu" not in driver_ids
    assert "rsu" not in mover_ids
    assert digest["data_quality"]["rows_count"] == 1


def test_crypto_benchmark_omitted_when_no_crypto(monkeypatch):
    """Crypto benchmark must be null when no crypto positions are held."""
    from backend.services.intel_digest import build_digest

    rows = _make_rows(
        [{"instrument_id": "i1", "asset_class": "equity", "day_change": 1, "day_change_pct": 1.0}]
    )

    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)

    digest = build_digest(period="1M", rows=rows, rates={"SGD": 1.0, "USD": 0.74})
    assert digest["market_backdrop"]["crypto_benchmark"] is None


def test_degrade_to_weight_ranked_when_no_day_change(monkeypatch):
    """When no positions have day_change, return_drivers should degrade with degraded=True."""
    from backend.services.intel_digest import build_digest

    rows = _make_rows(
        [
            {
                "instrument_id": "i1",
                "quantity": 100,
                "price": 200,
                "day_change": None,
                "day_change_pct": None,
            },
            {
                "instrument_id": "i2",
                "quantity": 10,
                "price": 50,
                "day_change": None,
                "day_change_pct": None,
            },
        ]
    )

    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)

    digest = build_digest(period="1M", rows=rows, rates={"SGD": 1.0, "USD": 0.74})
    drivers = digest["return_drivers"]
    # All should be degraded
    assert all(d["degraded"] is True for d in drivers)
    # i1 has higher value → ranked first
    assert drivers[0]["instrument_id"] == "i1"
    assert drivers[0]["contribution_base"] is None


def test_data_quality_benchmark_sufficient_false(monkeypatch):
    """When benchmark has no sufficient data, data_quality.benchmark_sufficient is False."""
    from backend.services.intel_digest import build_digest

    rows = _make_rows([{"instrument_id": "i1", "day_change": 1, "day_change_pct": 1.0}])

    def _mock_bench(ref, period="1M", today=None):
        return {
            "symbol": "SPY",
            "instrument_id": "spy-id",
            "twr": None,
            "data_quality": {"sufficient": False, "value_points": 0, "market_since": None},
        }

    def _find_bench(syms):
        return {"id": "spy-id", "symbol": "SPY"}

    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", _find_bench)
    with patch("backend.services.intel_digest.build_digest.__wrapped__", create=True):
        from backend.services import returns as _returns_svc

        monkeypatch.setattr(_returns_svc, "benchmark_return", _mock_bench)

    digest = build_digest(period="1M", rows=rows, rates={"SGD": 1.0, "USD": 0.74})
    assert digest["data_quality"]["benchmark_sufficient"] is False


def test_determinism(monkeypatch):
    """Same inputs always produce same output."""
    from backend.services.intel_digest import build_digest

    rows = _make_rows(
        [
            {"instrument_id": "i1", "day_change": 5, "day_change_pct": 5.0},
            {"instrument_id": "i2", "day_change": -3, "day_change_pct": -3.0},
        ]
    )
    rates = {"SGD": 1.0, "USD": 0.74}

    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)

    d1 = build_digest(period="1M", rows=rows, rates=rates)
    d2 = build_digest(period="1M", rows=rows, rates=rates)
    # Exclude generated_at (timestamp varies)
    d1.pop("generated_at")
    d2.pop("generated_at")
    assert d1 == d2


# ── TestClient smoke: narrate=false never calls LLM ───────────────────────────


@pytest.fixture
def client():
    return TestClient(app)


def test_get_intel_digest_200(client, monkeypatch):
    """GET /api/intel/digest returns 200 with expected keys."""
    monkeypatch.setattr("backend.services.intel_digest.hydrated_positions", lambda: [])
    monkeypatch.setattr("backend.services.intel_digest.load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        "backend.services.intel_digest.read_profile", lambda: {"base_currency": "SGD"}
    )
    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)

    resp = client.get("/api/intel/digest")
    assert resp.status_code == 200
    body = resp.json()
    required = {
        "generated_at",
        "base_currency",
        "period",
        "market_backdrop",
        "return_drivers",
        "top_movers",
        "today",
        "portfolio_return",
        "narration",
        "data_quality",
    }
    assert required <= set(body.keys())

    # today block has correct shape and has_data=False for empty rows
    today = body["today"]
    assert isinstance(today["pl_base"], float)
    assert isinstance(today["pl_pct"], float)
    assert today["has_data"] is False


def test_narrate_false_never_calls_llm(client, monkeypatch):
    """narrate=false must NEVER call the narration function."""
    monkeypatch.setattr("backend.services.intel_digest.hydrated_positions", lambda: [])
    monkeypatch.setattr("backend.services.intel_digest.load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        "backend.services.intel_digest.read_profile", lambda: {"base_currency": "SGD"}
    )
    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)

    llm_called = {"called": False}

    def _boom(digest):
        llm_called["called"] = True
        raise AssertionError("narrate must not be called with narrate=false")

    monkeypatch.setattr(
        "backend.api.intel_digest.get_intel_digest.__wrapped__", _boom, raising=False
    )

    resp = client.get("/api/intel/digest?narrate=false")
    assert resp.status_code == 200
    assert not llm_called["called"]


def test_digest_invalid_period_defaults_to_1m(client, monkeypatch):
    """An unrecognised period param should default to 1M rather than 422."""
    monkeypatch.setattr("backend.services.intel_digest.hydrated_positions", lambda: [])
    monkeypatch.setattr("backend.services.intel_digest.load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        "backend.services.intel_digest.read_profile", lambda: {"base_currency": "SGD"}
    )
    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)

    resp = client.get("/api/intel/digest?period=BADVALUE")
    assert resp.status_code == 200
    assert resp.json()["period"] == "1M"


# ── New-field tests: today P&L + portfolio_return ─────────────────────────────


def test_today_pl_math(monkeypatch):
    """Regression: today.pl_base and pl_pct computed correctly from synthetic rows.

    Setup:
      - 2 rows in base currency (SGD=1.0), no FX conversion needed.
      - row A: qty=10, price=100, day_change=+5   → day_change_base = +5
      - row B: qty=5,  price=200, day_change=-20  → day_change_base = -20
      total_book_base_value = (10*100) + (5*200) = 1000 + 1000 = 2000
      pl_base = 5 + (-20) = -15
      pl_pct  = -15 / 2015 (the book at the PRIOR close: 2000 − (−15)) ≈ -0.007444

    The denominator moved from today's value to the prior close on 2026-09-25 so this figure
    equals the Investments masthead's own (ADR 2026-09-25-concentration-today-and-benchmark-
    have-one-authority) — the two screens used to quote different "today" moves.
    """
    from backend.services.intel_digest import build_digest

    monkeypatch.setattr("backend.services.intel_digest.read_profile", lambda: _make_profile("SGD"))
    monkeypatch.setattr("backend.services.intel_digest.load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)
    # Stub portfolio_return so this test doesn't hit the DB
    monkeypatch.setattr(
        "backend.services.intel_digest._compute_portfolio_return", lambda period: None
    )

    rows = _make_rows(
        [
            {
                "instrument_id": "rA",
                "quantity": 10,
                "price": 100,
                "qpu": 1.0,
                "ccy": "SGD",
                "day_change": 5.0,
                "day_change_pct": 0.05,
            },
            {
                "instrument_id": "rB",
                "quantity": 5,
                "price": 200,
                "qpu": 1.0,
                "ccy": "SGD",
                "day_change": -20.0,
                "day_change_pct": -0.02,
            },
        ]
    )

    digest = build_digest(period="1M", rows=rows, rates={"SGD": 1.0})
    today = digest["today"]

    assert today["has_data"] is True
    assert today["pl_base"] == -15.0
    assert today["pl_pct"] == pytest.approx(-15 / 2015, abs=1e-6)


def test_today_has_data_false_when_no_day_change(monkeypatch):
    """today.has_data is False and pl_base/pl_pct are 0.0 when no row has a day_change."""
    from backend.services.intel_digest import build_digest

    monkeypatch.setattr("backend.services.intel_digest.read_profile", lambda: _make_profile("SGD"))
    monkeypatch.setattr("backend.services.intel_digest.load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr("backend.services.intel_digest._find_benchmark_instrument", lambda *a: None)
    monkeypatch.setattr(
        "backend.services.intel_digest._compute_portfolio_return", lambda period: None
    )

    rows = _make_rows(
        [{"instrument_id": "i1", "ccy": "SGD", "day_change": None, "day_change_pct": None}]
    )

    digest = build_digest(period="1M", rows=rows, rates={"SGD": 1.0})
    today = digest["today"]

    assert today["has_data"] is False
    assert today["pl_base"] == 0.0
    assert today["pl_pct"] == 0.0


def test_portfolio_return_none_on_failure(monkeypatch):
    """_compute_portfolio_return returns null when portfolio_returns raises (e.g. no price history)."""
    import backend.services.returns as returns_mod
    from backend.services.intel_digest import _compute_portfolio_return

    def _raise(period):
        raise RuntimeError("no price data")

    monkeypatch.setattr(returns_mod, "portfolio_returns", _raise)

    result = _compute_portfolio_return("1M")
    assert result is None


def test_compute_portfolio_return_null_when_twr_none(monkeypatch):
    """_compute_portfolio_return returns null when portfolio_returns().twr is None."""
    from backend.services.intel_digest import _compute_portfolio_return

    def _mock_returns(period):
        return {"twr": None, "mwr": None, "period_gain_base": 0.0}

    monkeypatch.setattr(
        "backend.services.intel_digest._compute_portfolio_return.__module__",
        "backend.services.intel_digest",
        raising=False,
    )
    # Patch the returns module that _compute_portfolio_return imports dynamically
    import backend.services.returns as returns_mod

    monkeypatch.setattr(returns_mod, "portfolio_returns", _mock_returns)

    result = _compute_portfolio_return("1M")
    assert result is None


def test_compute_portfolio_return_extracts_period_return(monkeypatch):
    """_compute_portfolio_return returns {period_return} when twr is present."""
    import backend.services.returns as returns_mod
    from backend.services.intel_digest import _compute_portfolio_return

    monkeypatch.setattr(
        returns_mod,
        "portfolio_returns",
        lambda period: {"twr": {"period_return": 0.0423, "annualized": 0.0521}},
    )

    result = _compute_portfolio_return("1M")
    assert result is not None
    assert result["period_return"] == 0.0423


def test_digest_is_built_off_the_event_loop(monkeypatch):
    """GET /api/intel/digest must not fold the ledger on the loop thread.

    build_digest is fully synchronous and deep — portfolio_returns →
    portfolio_history.inception_date → TransactionStore.all, then a PriceHistoryStore scan.
    The route is `async def`, so it ran on the loop and stalled everything else: measured
    4.8s live on 2026-08-11, with core.loop_watch naming both legs inside the one request
    and reporting a 4712ms stall across 17 waiting tasks.

    Asserts the THREAD, not the duration — a timing assertion would pass on an empty test
    ledger and rot exactly as the real book grew, which is the failure mode being fixed.
    """
    import threading

    import backend.services.intel_digest as digest_mod

    seen: dict = {}

    def fake_build_digest(period="1M"):
        seen["thread"] = threading.current_thread().name
        seen["period"] = period
        return {"period": period, "sections": []}

    monkeypatch.setattr(digest_mod, "build_digest", fake_build_digest)

    resp = TestClient(app).get("/api/intel/digest", params={"period": "1M", "narrate": "false"})

    assert resp.status_code == 200
    assert seen["period"] == "1M"
    # to_thread dispatches to the default executor ("asyncio_N"); inline would be MainThread.
    assert seen["thread"].startswith("asyncio_"), (
        f"digest built on {seen['thread']} — expected a worker thread"
    )


def test_row_base_value_delegates_to_ledger_service(monkeypatch):
    """MATH-47: _row_base_value must delegate to ledger.row_base_value() (the ONE
    canonical qty×price×quote_per_unit×FX fold) instead of restating it inline. Proven
    via a sentinel monkeypatch, not numeric equality — a duplicated-but-consistent
    formula would also pass a numeric check (that's exactly the MATH-40 bug class)."""
    from backend.services import ledger as ledger_service
    from backend.services.intel_digest import _row_base_value

    monkeypatch.setattr(ledger_service, "row_base_value", lambda row, base, **kw: 12345.0)

    row = {
        "quantity": 3,
        "price": 100,
        "quote_per_unit": 0.03215,
        "quote_currency": "HKD",
        "currency": "USD",
    }
    result = _row_base_value(row, "USD", {"USD": 1.0, "HKD": 0.128})
    assert result == 12345.0
