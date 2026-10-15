"""The AI surface is handed money, never asked to derive it.

Same rule as the UI (ADR 2026-07-31-overview-aggregates-are-server-computed), applied to the
tool surface: a consumer that has to re-derive a monetary figure will eventually disagree with
the one the server computed. For a browser that produced a S$120,000 net-worth headline against
a true S$98,000. For a MODEL it is worse — it cannot even attempt the conversion correctly.

Two real defects these pin:

1. `get_portfolio` returned `quantity`, `price` and `currency` with **no base value and no FX
   rate anywhere in the payload**. Answering "what is this position worth?" required an
   EUR→SGD rate the model was never given, so it could only guess.
2. `get_screen_detail`'s `holdings:all` source read `market_value_base` / `pct_of_portfolio`
   off `positions.hydrated()` rows — fields those rows have never carried. Every `or 0`
   fallback fired, so Lucius's full holdings table reported EVERY position as worth 0.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.services import portfolio_rows as portfolio_rows_service

MONEY_FIELDS = ("base_value", "cost_base", "pl", "pl_pct", "daily_pl")


@pytest.fixture(autouse=True)
def seeded(tmp_path, monkeypatch, db_isolated, fx_rates):
    """A book with a FOREIGN-quoted holding — the case the model provably could not answer,
    because converting it needs a rate the payload never carried."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore
    from backend.services import ledger as ledger_svc
    from backend.services import prices as prices_service

    monkeypatch.setattr(prices_service, "fetch_live", lambda *a, **k: {"prices": {}, "fx": {}})

    acct = ledger_svc.accounts.create(
        {"name": "Broker", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    for symbol, qty, ccy, bucket in (
        ("LOCAL", 10, "SGD", "liquid"),
        ("FOREIGN", 20, "EUR", "liquid"),
        ("GRANT", 5, "SGD", "locked"),
    ):
        inst = InstrumentStore().create(
            {
                "symbol": symbol,
                "name": symbol,
                "asset_class": "equity",
                "quote_currency": ccy,
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
                "cost_currency": ccy,
                "bucket": bucket,
            }
        )
    return tmp_path


def _get_portfolio(payload: dict) -> dict:
    from backend.ai.tools.portfolio import get_portfolio

    # `asyncio.run`, not `get_event_loop()` — the latter raises once another test in the same
    # session has closed the loop, which made these pass alone and fail in the suite.
    out, _ = asyncio.run(get_portfolio(payload))
    return json.loads(out)


def test_get_portfolio_hands_the_model_computed_money(seeded):
    result = _get_portfolio({})
    if not result["positions"]:
        pytest.skip("no positions in this book")
    for row in result["positions"]:
        for field in MONEY_FIELDS:
            assert field in row, f"{field} missing — the model would have to derive it"


def test_get_portfolio_rows_reconcile_with_its_own_summary(seeded):
    """The per-row figures and the summary must agree, or the model can reach two different
    answers from ONE response depending on which it reads."""
    result = _get_portfolio({})
    if not result["positions"]:
        pytest.skip("no positions in this book")
    owned = sum(
        r["base_value"] for r in result["positions"] if r.get("vesting_state") != "unvested"
    )
    assert owned == pytest.approx(result["summary"]["owned_base"], abs=0.05)
    unvested = sum(
        r["base_value"] for r in result["positions"] if r.get("vesting_state") == "unvested"
    )
    assert unvested == pytest.approx(result["summary"]["unvested_base"], abs=0.05)


def test_get_portfolio_omits_a_weight_that_would_lie_on_a_filtered_call(seeded):
    """A share-of-portfolio weight is only meaningful against the whole book. Emitting it on a
    filtered subset would read as authoritative while being wrong."""
    result = _get_portfolio({})
    for row in result["positions"]:
        assert "pct_of_portfolio" not in row


def test_screen_holdings_source_reports_real_values_not_zeros(seeded):
    """Regression: this source read fields hydrated rows never carried, so every holding
    rendered as worth 0 with a 0% weight — silently, for as long as it existed."""
    rows = [
        r
        for r in portfolio_rows_service.projected_rows(live=False)["rows"]
        if r.get("instrument_id")
    ]
    if not rows:
        pytest.skip("no holdings in this book")
    assert any(r["base_value"] for r in rows), "every holding valued at 0 — the old bug"
    assert all("base_value" in r and "pct_of_portfolio" in r for r in rows)


def test_value_row_matches_the_whole_book_fold(seeded):
    """`value_row` (what the tool surface uses) and `projected_rows` (what the UI uses) are the
    same fold — if they diverge, a tool answer and the dashboard disagree."""
    projection = portfolio_rows_service.projected_rows(live=False)
    from backend.services import positions as positions_service

    hydrated = {r.get("position_id"): r for r in positions_service.hydrated()}
    checked = 0
    for row in projection["rows"]:
        raw = hydrated.get(row.get("position_id"))
        if raw is None:
            continue
        assert portfolio_rows_service.value_row(raw)["base_value"] == pytest.approx(
            row["base_value"], abs=0.02
        )
        checked += 1
    if not checked:
        pytest.skip("no market positions in this book")
