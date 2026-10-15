"""Unit tests for services.dividend_calendar (ADR 0020) — network-free.

Freezes "today" to 2026-06-21 (matches the spike), monkeypatches FX + holdings + instruments
so the projection/scheduled logic is exercised purely. Covers: scheduled vs estimated dating,
non-payer exclusion, horizon filter, FX-to-base, sort + next_payment, clamp, empty portfolio.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest

from backend.services import dividend_calendar as svc


class _FixedToday:
    """Stand-in for the module's ``date`` so ``date.today()`` is deterministic; ``fromisoformat``
    delegates to the real class (subclass-free, so arithmetic returns plain dates)."""

    @staticmethod
    def today() -> _dt.date:
        return _dt.date(2026, 6, 21)

    @staticmethod
    def fromisoformat(s: str) -> _dt.date:
        return _dt.date.fromisoformat(s)


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc.lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})
    monkeypatch.setattr(svc, "user_today", _FixedToday.today)

    def _set(holdings: list[dict], insts: list[dict]):
        monkeypatch.setattr(svc.positions_service, "hydrated", lambda *a, **k: holdings)
        monkeypatch.setattr(svc, "instruments", SimpleNamespace(all=lambda: insts))

    return _set


def _hold(
    pos_id,
    inst_id,
    qty=100,
    ccy="USD",
    price=100.0,
    qpu=1.0,
    symbol="ABC",
    vesting_state="vested",
):
    return {
        "position_id": pos_id,
        "instrument_id": inst_id,
        "quantity": qty,
        "quote_currency": ccy,
        "price": price,
        "quote_per_unit": qpu,
        "symbol": symbol,
        "vesting_state": vesting_state,
    }


def _inst(inst_id, symbol="ABC", **meta):
    base = {"id": inst_id, "symbol": symbol, "name": symbol, "quote_currency": "USD"}
    base.update(meta)
    return base


def test_scheduled_uses_declared_dates(wired):
    """A future Yahoo-declared next_ex_date → status 'scheduled', exact dates, FX'd amount."""
    wired(
        [_hold("p1", "i1", qty=100, ccy="USD", symbol="MSFT")],
        [
            _inst(
                "i1",
                "MSFT",
                next_ex_date="2026-08-20",
                next_pay_date="2026-09-10",
                last_dividend_amount=0.91,
                dividend_frequency="quarterly",
            )
        ],
    )
    out = svc.upcoming_payments(days=90)
    assert out["count"] == 1
    row = out["payments"][0]
    assert row["status"] == "scheduled"
    assert row["ex_date"] == "2026-08-20"
    assert row["pay_date"] == "2026-09-10"
    assert row["per_share"] == 0.91
    assert row["amount_base"] == pytest.approx(122.85)  # 0.91*100*1.35
    assert out["next_payment"]["instrument_id"] == "i1"


def test_unvested_equity_grant_units_excluded(wired):
    """An unvested RSU tranche is not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — it must not project an upcoming
    dividend payment the holder will not receive."""
    wired(
        [_hold("p1", "i1", qty=1000, ccy="USD", symbol="RSU", vesting_state="unvested")],
        [
            _inst(
                "i1",
                "RSU",
                next_ex_date="2026-08-20",
                next_pay_date="2026-09-10",
                last_dividend_amount=0.91,
                dividend_frequency="quarterly",
            )
        ],
    )
    out = svc.upcoming_payments(days=90)
    assert out["count"] == 0
    assert out["payments"] == []
    assert out["next_payment"] is None


def test_estimated_projects_from_history(wired):
    """No declared date → project last_ex + quarterly cadence past today; status 'estimated'."""
    wired(
        [_hold("p1", "i1", qty=50, ccy="USD", symbol="QQQ")],
        [
            _inst(
                "i1",
                "QQQ",
                last_ex_date="2026-03-23",
                dividend_frequency="quarterly",
                last_dividend_amount=0.70,
            )
        ],
    )
    out = svc.upcoming_payments(days=90)
    row = out["payments"][0]
    assert row["status"] == "estimated"
    assert row["ex_date"] == "2026-06-22"  # 2026-03-23 + 91d, first after 2026-06-21
    assert row["pay_date"] == "2026-07-06"  # ex + 14d
    assert row["amount_base"] == pytest.approx(47.25)  # 0.70*50*1.35


def test_per_share_falls_back_to_rate_over_frequency(wired):
    """No last_dividend_amount → per-share = declared annual rate / payments-per-year."""
    wired(
        [_hold("p1", "i1", qty=10, ccy="USD")],
        [
            _inst(
                "i1",
                next_ex_date="2026-07-01",
                dividend_frequency="quarterly",
                dividend_rate_annual=4.0,  # → 1.0 per quarterly payment
            )
        ],
    )
    out = svc.upcoming_payments(days=90)
    assert out["payments"][0]["per_share"] == pytest.approx(1.0)
    assert out["payments"][0]["amount_base"] == pytest.approx(13.5)  # 1.0*10*1.35


def test_non_payer_excluded(wired):
    """No rate, no history → undatable / unpriceable → never shown."""
    wired(
        [_hold("p1", "i1", symbol="GLD")],
        [_inst("i1", "GLD")],  # no dividend meta at all
    )
    out = svc.upcoming_payments(days=90)
    assert out["count"] == 0
    assert out["next_payment"] is None
    assert out["total_base"] == 0.0


def test_horizon_filter_excludes_far_future(wired):
    """An annual payer whose next ex is >90d out is excluded at days=90 but included at 365."""
    wired(
        [_hold("p1", "i1", qty=10)],
        [
            _inst(
                "i1",
                last_ex_date="2025-12-01",
                dividend_frequency="annual",
                last_dividend_amount=1.0,
            )
        ],
    )
    # next annual ex projects to ~2026-12-01 — outside 90d, inside 365d.
    assert svc.upcoming_payments(days=90)["count"] == 0
    assert svc.upcoming_payments(days=365)["count"] == 1


def test_sorted_and_next_payment(wired):
    wired(
        [_hold("pA", "iA", symbol="AAA"), _hold("pB", "iB", symbol="BBB")],
        [
            _inst("iA", "AAA", next_ex_date="2026-08-15", last_dividend_amount=1.0),
            _inst("iB", "BBB", next_ex_date="2026-07-01", last_dividend_amount=1.0),
        ],
    )
    out = svc.upcoming_payments(days=90)
    assert [p["ex_date"] for p in out["payments"]] == ["2026-07-01", "2026-08-15"]
    assert out["next_payment"]["symbol"] == "BBB"


def test_days_clamped(wired):
    wired([], [])
    assert svc.upcoming_payments(days=999)["horizon_days"] == 365
    assert svc.upcoming_payments(days=-5)["horizon_days"] == 1
    assert svc.upcoming_payments(days=0)["horizon_days"] == 90  # 0 → default


def test_empty_portfolio(wired):
    wired([], [])
    out = svc.upcoming_payments()
    assert out["base_currency"] == "SGD"
    assert out["horizon_days"] == 90
    assert out["payments"] == []
    assert out["count"] == 0
    assert out["total_base"] == 0.0
    assert out["principal_base"] == 0
    assert out["next_payment"] is None


def test_stale_past_next_ex_falls_back_to_projection(wired):
    """A next_ex_date now in the PAST is ignored (self-healing) — projection takes over."""
    wired(
        [_hold("p1", "i1", qty=10)],
        [
            _inst(
                "i1",
                next_ex_date="2026-05-01",  # past relative to frozen today 2026-06-21
                last_ex_date="2026-05-01",
                dividend_frequency="quarterly",
                last_dividend_amount=1.0,
            )
        ],
    )
    row = svc.upcoming_payments(days=120)["payments"][0]
    assert row["status"] == "estimated"
    assert row["ex_date"] == "2026-07-31"  # 2026-05-01 + 91d


def test_quarterly_payer_forecasts_full_year_schedule(wired):
    """MATH-36: a quarterly payer over a 366-day horizon must forecast every occurrence
    (4), not just the next one — each spaced by the quarterly cadence and priced at
    qty * per-share."""
    wired(
        [_hold("p1", "i1", qty=100, ccy="USD", symbol="ABC")],
        [
            _inst(
                "i1",
                "ABC",
                last_ex_date="2026-05-07",  # 45 days before frozen today 2026-06-21
                dividend_frequency="quarterly",
                last_dividend_amount=1.00,
            )
        ],
    )
    out = svc.upcoming_payments(days=366)
    assert out["count"] == 4
    ex_dates = [p["ex_date"] for p in out["payments"]]
    assert ex_dates == sorted(ex_dates)
    for prev, nxt in zip(ex_dates, ex_dates[1:]):
        gap = (_dt.date.fromisoformat(nxt) - _dt.date.fromisoformat(prev)).days
        assert gap == 91
    for p in out["payments"]:
        assert p["amount_base"] == pytest.approx(135.0)  # 100 sh * $1.00 * 1.35 USD/SGD
        assert p["ex_date"] <= "2027-06-22"  # within the 366-day horizon


def test_monthly_payer_forecasts_full_year_schedule(wired):
    """A monthly payer over a 366-day horizon forecasts all 12 occurrences."""
    wired(
        [_hold("p1", "i1", qty=10, ccy="USD", symbol="MON")],
        [
            _inst(
                "i1",
                "MON",
                last_ex_date="2026-06-10",
                dividend_frequency="monthly",
                last_dividend_amount=0.50,
            )
        ],
    )
    out = svc.upcoming_payments(days=366)
    assert out["count"] == 12


def test_annual_payer_forecasts_single_payment_in_year_horizon(wired):
    """An annual payer over a 366-day horizon still forecasts exactly one occurrence."""
    wired(
        [_hold("p1", "i1", qty=10, ccy="USD", symbol="ANN")],
        [
            _inst(
                "i1",
                "ANN",
                last_ex_date="2026-05-07",
                dividend_frequency="annual",
                last_dividend_amount=2.00,
            )
        ],
    )
    out = svc.upcoming_payments(days=366)
    assert out["count"] == 1


def test_holding_beyond_horizon_yields_zero_payments(wired):
    """A payer whose next ex-date falls beyond the requested horizon is excluded entirely."""
    wired(
        [_hold("p1", "i1", qty=10, ccy="USD", symbol="FAR")],
        [
            _inst(
                "i1",
                "FAR",
                next_ex_date="2027-06-21",  # 365 days out — beyond a 90-day horizon
                next_pay_date="2027-07-05",
                dividend_frequency="quarterly",
                last_dividend_amount=1.00,
            )
        ],
    )
    out = svc.upcoming_payments(days=90)
    assert out["count"] == 0
    assert out["payments"] == []


def test_estimated_projection_includes_ex_date_exactly_today(wired):
    """Regression: when last_ex + N×cadence lands EXACTLY on today, the projected ex-date is
    today (not skipped to next quarter) — symmetric with the scheduled path's >= today guard."""
    # 2026-03-22 + 91d == 2026-06-21 (frozen today).
    wired(
        [_hold("p1", "i1", qty=10)],
        [
            _inst(
                "i1",
                last_ex_date="2026-03-22",
                dividend_frequency="quarterly",
                last_dividend_amount=1.0,
            )
        ],
    )
    row = svc.upcoming_payments(days=90)["payments"][0]
    assert row["status"] == "estimated"
    assert row["ex_date"] == "2026-06-21"  # today — eligible, not rolled to 2026-09-20


def test_summary_forecast_buckets_quarterly_income_into_four_months(wired, monkeypatch):
    """MATH-36 downstream: dividend_summary.income_aggregates buckets the FULL rolled-forward
    schedule by month, so a quarterly payer shows income in 4 of the 12 forecast months
    (not 1)."""
    from backend.services import dividend_summary

    monkeypatch.setattr(dividend_summary, "user_today", _FixedToday.today)
    monkeypatch.setattr(
        dividend_summary.income_service, "income_summary", lambda *a, **k: {"events": []}
    )
    wired(
        [_hold("p1", "i1", qty=100, ccy="USD", symbol="ABC")],
        [
            _inst(
                "i1",
                "ABC",
                last_ex_date="2026-05-07",
                dividend_frequency="quarterly",
                last_dividend_amount=1.00,
            )
        ],
    )
    out = dividend_summary.income_aggregates()
    nonzero_months = [b for b in out["forecast"] if b["amount"] > 0]
    assert len(nonzero_months) == 4
    assert out["forecast_total"] == pytest.approx(4 * 135.0)  # 4 * 100sh * $1.00 * 1.35 USD/SGD
