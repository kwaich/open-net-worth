"""update_instrument — service whitelist + AI-tool dispatch (the VOO missing-ETF-badge
fix path: instruments created before the subclass classifier carry a stale generic
subclass, and resolve_instrument is find-or-create so it can never correct them).

Offline, isolated to tmp_path, no LLM / network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.instrument_store import InstrumentStore
from backend.services.instruments import update_instrument


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """InstrumentStore is DB-backed (plan 2, task 5) — db_isolated gives it a fresh
    per-test database."""
    return tmp_path


def _seed_voo():
    return InstrumentStore().create(
        {
            "symbol": "VOO",
            "name": "Vanguard S&P 500 ETF",
            "asset_class": "equity",
            "asset_subclass": "equity",  # stale pre-classifier value
            "quote_currency": "USD",
            "price_source_id": "VOO",
        }
    )


# ── service ───────────────────────────────────────────────────────────────────────


def test_update_subclass_normalizes_and_persists(isolated):
    inst = _seed_voo()
    out = update_instrument(inst["id"], {"asset_subclass": " ETF "})
    assert out["ok"] is True
    assert out["instrument"]["asset_subclass"] == "etf"
    assert InstrumentStore().find_by_id(inst["id"])["asset_subclass"] == "etf"


def test_update_unknown_instrument_errors(isolated):
    out = update_instrument("ghost", {"asset_subclass": "etf"})
    assert "error" in out


def test_valuation_fields_rebind_not_ignore(isolated):
    """quote_currency / price_source changes alter money math — REVERSED (KSTL.DE
    incident): they used to be silently ignored, which left a mis-denominated
    instrument with NO correction path (resolve was find-or-create, update refused).
    They are now controlled RE-BIND fields: a currency+source change on a row with no
    stored price history goes through, explicitly, instead of being dropped."""
    inst = _seed_voo()
    out = update_instrument(
        inst["id"], {"quote_currency": "EUR", "price_source": "manual", "manual_price": 410.0}
    )
    assert out["ok"] is True
    assert out["instrument"]["quote_currency"] == "EUR"
    assert out["instrument"]["price_source"] == "manual"
    stored = InstrumentStore().find_by_id(inst["id"])
    assert stored["quote_currency"] == "EUR"
    assert stored["price_source"] == "manual"


def test_rebind_currency_only_allowed_without_history(isolated):
    """A quote_currency-ONLY change (same feed key) is allowed while no price history is
    stored under the id — nothing is denominated yet, so relabelling can't corrupt
    anything. The history-exists refusal has its own test."""
    inst = _seed_voo()
    out = update_instrument(inst["id"], {"quote_currency": "EUR"})
    assert out["ok"] is True
    assert InstrumentStore().find_by_id(inst["id"])["quote_currency"] == "EUR"


def test_non_iso_quote_currency_refused(isolated):
    inst = _seed_voo()
    out = update_instrument(inst["id"], {"quote_currency": "euro"})
    assert "error" in out
    assert InstrumentStore().find_by_id(inst["id"])["quote_currency"] == "USD"


def test_invalid_asset_class_refused(isolated):
    inst = _seed_voo()
    out = update_instrument(inst["id"], {"asset_class": "wrapper"})
    assert "error" in out
    assert InstrumentStore().find_by_id(inst["id"])["asset_class"] == "equity"


def test_dividend_rate_update_stamps_manual_source(isolated):
    """This is the only user-facing write path for dividend_rate_annual — an explicit
    value is by definition user-confirmed, so it must ratchet dividend_rate_source to
    'manual' in the SAME write (mirrors set_classification stamping 'manual' alongside
    sector/region)."""
    inst = _seed_voo()
    out = update_instrument(inst["id"], {"dividend_rate_annual": 2.4})
    assert out["ok"] is True
    assert out["instrument"]["dividend_rate_annual"] == 2.4
    assert out["instrument"]["dividend_rate_source"] == "manual"
    stored = InstrumentStore().find_by_id(inst["id"])
    assert stored["dividend_rate_annual"] == 2.4
    assert stored["dividend_rate_source"] == "manual"


# ── tool dispatch ─────────────────────────────────────────────────────────────────


def test_update_instrument_tool_fixes_stale_subclass(isolated):
    inst = _seed_voo()
    res, proposed = call("update_instruments", instrument_id=inst["id"], asset_subclass="etf")
    assert proposed is None
    assert res["ok"] is True
    assert res["instrument"]["asset_subclass"] == "etf"
    assert res["instrument"]["symbol"] == "VOO"


def test_update_instrument_tool_unknown_id(isolated):
    res, proposed = call("update_instruments", instrument_id="ghost", note="x")
    assert proposed is None
    assert "error" in res


# ── factory invariant: coingecko rows are USD-quoted ───────────────────────────────


def test_coingecko_instrument_quote_currency_pinned_usd(isolated):
    """CoinGecko live prices are fetched vs USD and valuation converts the mark AS
    quote_currency — a non-USD quote on a coingecko row would double-apply FX. The
    factory pins it regardless of what the caller supplies."""
    inst = InstrumentStore().create(
        {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "quote_currency": "SGD",  # wrong on purpose
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
        }
    )
    assert inst["quote_currency"] == "USD"


def test_store_factory_defaults_dividend_rate_source_empty(isolated):
    inst = InstrumentStore().create({"symbol": "AAPL", "quote_currency": "USD"})
    assert inst["dividend_rate_source"] == ""


def test_store_factory_coerces_bad_dividend_rate_source(isolated):
    inst = InstrumentStore().create(
        {"symbol": "X", "quote_currency": "USD", "dividend_rate_source": "bogus"}
    )
    assert inst["dividend_rate_source"] == ""


def test_yahoo_instrument_quote_currency_respected(isolated):
    inst = InstrumentStore().create(
        {
            "symbol": "ONWAI",
            "name": "ONWAI Corp",
            "asset_class": "equity",
            "quote_currency": "eur",
            "price_source": "yahoo",
            "price_source_id": "ONWAI.DE",
        }
    )
    assert inst["quote_currency"] == "EUR"


# ── pinning a price (QA 2026-10-13) ──────────────────────────────────────────────
# "Pin VWRA at 139.20": with no manual_price field the price went into price_source_id,
# the holding was valued at 0 and 1,261 days of the feed's history were deleted.


def _seed_feed_with_history():
    inst = InstrumentStore().create(
        {
            "symbol": "VWRA",
            "name": "Vanguard FTSE All-World",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "VWRA.L",
        }
    )
    from backend.db.price_history_store import PriceHistoryStore

    PriceHistoryStore().record("VWRA.L", 150.0, day="2026-10-01")
    return inst


def test_a_price_passed_as_the_source_id_is_refused(isolated):
    inst = _seed_feed_with_history()
    out = update_instrument(inst["id"], {"price_source": "manual", "price_source_id": "139.20"})
    assert "manual_price" in out["error"]
    assert InstrumentStore().find_by_id(inst["id"])["price_source"] == "yahoo"


def test_switching_to_manual_without_a_price_is_refused(isolated):
    inst = _seed_feed_with_history()
    out = update_instrument(inst["id"], {"price_source": "manual"})
    assert "valued at 0" in out["error"]


def test_a_pin_values_at_the_manual_price_and_keeps_the_feed_history(isolated):
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services.positions import _resolve_price

    inst = _seed_feed_with_history()
    out = update_instrument(inst["id"], {"price_source": "manual", "manual_price": 139.2})
    assert out["ok"] is True
    stored = InstrumentStore().find_by_id(inst["id"])
    assert stored["price_source"] == "manual"
    assert stored["price_source_id"] == ""  # the feed can no longer outvote the pin
    assert stored["manual_price"] == 139.2
    assert _resolve_price(stored, {}, {}) == 139.2
    assert PriceHistoryStore().series("VWRA.L")  # real market data, not wiped
    assert "wiped_points" not in out.get("price_history", {})


def test_manual_price_on_a_feed_instrument_is_refused(isolated):
    inst = _seed_feed_with_history()
    out = update_instrument(inst["id"], {"manual_price": 139.2})
    assert "price_source='manual'" in out["error"]


def test_a_manual_instrument_s_price_can_be_updated_alone(isolated):
    inst = _seed_feed_with_history()
    update_instrument(inst["id"], {"price_source": "manual", "manual_price": 139.2})
    out = update_instrument(inst["id"], {"manual_price": 141.0})
    assert out["ok"] is True
    assert InstrumentStore().find_by_id(inst["id"])["manual_price"] == 141.0
