"""Cross-screen parity: the Investments tab and the Intel tab read concentration and today's
move from ONE authority (`investments_book`), so they can never quote two different figures.
ADR 2026-09-25-concentration-today-and-benchmark-have-one-authority. Synthetic rows only."""

from __future__ import annotations

import pytest

from backend.services import investments_book, portfolio_brief, risk_signals
from backend.services.intel_digest import _compute_today_pl

BASE = "SGD"
RATES = {"SGD": 1.0}


def _row(iid, symbol, qty, price, *, day_change=None, asset_class="equity", account="a1"):
    return {
        "instrument_id": iid,
        "symbol": symbol,
        "ticker": symbol,
        "label": f"{symbol} Inc",
        "asset_class": asset_class,
        "quantity": qty,
        "price": price,
        "quote_per_unit": 1.0,
        "quote_currency": BASE,
        "currency": BASE,
        "cost_basis": price,
        "day_change": day_change,
        "vesting_state": "vested",
        "account_id": account,
    }


BOOK = [
    _row("i_nvda", "NVDA", 12, 100, day_change=24.0, account="a1"),
    _row("i_nvda", "NVDA", 12, 100, day_change=24.0, account="a2"),
    *[_row(f"i_{n}", n, 19, 100, day_change=-5.0) for n in ("AAA", "BBB", "CCC", "DDD")],
    _row("i_cash", "USD-CASH", 50_000, 1, asset_class="cash"),
]


def test_concentration_verdict_matches_across_brief_risk_card_and_risk_story():
    profile = {"base_currency": BASE, "risk_concentration_guideline": 20.0}
    projected, grand = investments_book.project_invested(BOOK, BASE, RATES)
    card = investments_book.concentration_by_name(projected, grand, 20.0)
    assert card[0]["name"] == "NVDA" and card[0]["hot"] is True
    assert card[0]["pct"] == pytest.approx(24.0)

    brief = portfolio_brief._concentration_signal(BOOK, BASE, 20.0)
    assert brief is not None and "NVDA is 24%" in brief["narrative"]

    story = [
        f
        for f in risk_signals.compute_risk(rows=BOOK, rates=RATES, profile=profile)
        if f["kind"] == "single_name"
    ]
    assert [f["affected_instrument_ids"] for f in story] == [["i_nvda"]]
    assert story[0]["metric_pct"] == pytest.approx(card[0]["pct"])


def test_today_move_matches_between_masthead_brief_and_intel_digest():
    projected, _ = investments_book.project_invested(BOOK, BASE, RATES)
    masthead = investments_book.today_move(projected)
    assert masthead["pl"] == pytest.approx(48.0 - 20.0)

    intel = _compute_today_pl(BOOK, BASE, RATES)
    assert intel["pl_base"] == pytest.approx(masthead["pl"])
    assert intel["pl_pct"] * 100 == pytest.approx(masthead["pct"], abs=1e-4)

    # Cash is not in the book: adding more of it must not move either figure.
    richer = [*BOOK, _row("i_cash2", "EUR-CASH", 90_000, 1, asset_class="cash")]
    assert _compute_today_pl(richer, BASE, RATES) == intel


def test_today_pct_is_measured_against_the_prior_close():
    projected, _ = investments_book.project_invested(
        [_row("i1", "UP", 10, 110, day_change=100.0)], BASE, RATES
    )
    assert investments_book.today_move(projected)["pct"] == pytest.approx(10.0)
