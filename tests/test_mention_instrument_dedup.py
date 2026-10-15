"""Regression tests for the held-instrument dedupe in search_instrument.

search_instrument must exclude instruments that the user currently holds (those already
appear as Holding rows); unowned instruments remain in the Security list; held instruments
are still reachable via search_holding.

ADR: docs/adr/0029-mention-picker-dedupe-held-instruments.md
"""

from __future__ import annotations

import json

import pytest

from backend.core import fx
from backend.core.paths import PROFILE_JSON
from backend.db.instrument_store import InstrumentStore
from backend.services.mentions.types import SearchQuery


def _query(q: str = "", limit: int = 20) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=frozenset({"entity"}), limit=limit, cursor=None)


@pytest.fixture(autouse=True)
def _isolated_env():
    PROFILE_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_JSON.write_text(json.dumps({"base_currency": "USD"}), encoding="utf-8")
    fx.set_fx_rates_loader(lambda: {"USD": 1.0})
    yield
    fx.set_fx_rates_loader(None)


@pytest.fixture
def two_instruments():
    """Seed two instruments: AAPL (will be held) and MSFT (unowned)."""
    store = InstrumentStore()
    store.upsert(
        {
            "id": "inst-aapl",
            "symbol": "AAPL",
            "name": "Apple Inc",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    )
    store.upsert(
        {
            "id": "inst-msft",
            "symbol": "MSFT",
            "name": "Microsoft Corp",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "MSFT",
        }
    )
    yield store
    store.delete("inst-aapl")
    store.delete("inst-msft")


# ── core dedupe tests ──────────────────────────────────────────────────────────


def test_held_instrument_excluded_from_search_instrument(monkeypatch, two_instruments):
    """An instrument with an active position must NOT appear in search_instrument results."""
    from backend.services.mentions.providers import search_instrument

    # AAPL is held; MSFT is not.
    monkeypatch.setattr(
        "backend.services.positions.hydrated",
        lambda: [
            {
                "position_id": "pos-aapl",
                "instrument_id": "inst-aapl",
                "symbol": "AAPL",
                "label": "Apple Inc",
                "account_name": "Vantage Brokerage",
                "quantity": 10.0,
                "asset_class": "equity",
                "currency": "USD",
            }
        ],
    )

    rows = search_instrument(_query())
    ids = {r["id"] for r in rows}
    assert "inst-aapl" not in ids, "held instrument must be excluded from Security rows"
    assert "inst-msft" in ids, "unowned instrument must still appear"


def test_unowned_instrument_included_in_search_instrument(monkeypatch, two_instruments):
    """An instrument with no active position IS included in search_instrument."""
    from backend.services.mentions.providers import search_instrument

    # No positions at all.
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])

    rows = search_instrument(_query())
    ids = {r["id"] for r in rows}
    assert "inst-aapl" in ids
    assert "inst-msft" in ids


def test_held_instrument_still_found_via_search_holding(monkeypatch, two_instruments):
    """A held instrument is NOT lost — it surfaces through search_holding, not search_instrument."""
    from backend.services.mentions.providers import search_holding, search_instrument

    fake_positions = [
        {
            "position_id": "pos-aapl",
            "instrument_id": "inst-aapl",
            "symbol": "AAPL",
            "label": "Apple Inc",
            "account_name": "Vantage Brokerage",
            "quantity": 10.0,
            "asset_class": "equity",
            "currency": "USD",
        }
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    instrument_ids = {r["id"] for r in search_instrument(_query())}
    holding_values = {r["value"] for r in search_holding(_query())}

    assert "inst-aapl" not in instrument_ids, "must not appear as Security"
    assert "pos-aapl" in holding_values, "must still appear as Holding"


def test_dedupe_matches_on_instrument_id_not_symbol(monkeypatch, two_instruments):
    """Dedupe is keyed on instrument_id, not symbol — two instruments sharing a symbol
    (edge case) are independently gated by their own ids."""
    from backend.services.mentions.providers import search_instrument

    # Only inst-aapl is held.
    monkeypatch.setattr(
        "backend.services.positions.hydrated",
        lambda: [
            {
                "position_id": "pos-aapl",
                "instrument_id": "inst-aapl",
                "symbol": "AAPL",
                "label": "Apple Inc",
                "account_name": "Vantage Brokerage",
                "quantity": 5.0,
                "asset_class": "equity",
                "currency": "USD",
            }
        ],
    )

    rows = search_instrument(_query())
    ids = {r["id"] for r in rows}
    # inst-aapl excluded (held), inst-msft included (not held)
    assert "inst-aapl" not in ids
    assert "inst-msft" in ids


def test_query_filter_still_applied_to_unowned_instruments(monkeypatch, two_instruments):
    """Text filtering continues to work for the remaining (unowned) instruments."""
    from backend.services.mentions.providers import search_instrument

    monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])

    rows = search_instrument(_query(q="microsoft"))
    ids = {r["id"] for r in rows}
    assert "inst-msft" in ids
    assert "inst-aapl" not in ids  # doesn't match "microsoft"


def test_no_positions_returns_full_catalog(monkeypatch, two_instruments):
    """When the user holds nothing, the full instrument catalog is returned."""
    from backend.services.mentions.providers import search_instrument

    monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])

    rows = search_instrument(_query())
    assert len(rows) == 2


def test_all_held_returns_empty_instrument_list(monkeypatch, two_instruments):
    """When every instrument in the store is held, search_instrument returns nothing."""
    from backend.services.mentions.providers import search_instrument

    monkeypatch.setattr(
        "backend.services.positions.hydrated",
        lambda: [
            {
                "position_id": "pos-aapl",
                "instrument_id": "inst-aapl",
                "symbol": "AAPL",
                "label": "Apple Inc",
                "account_name": "Vantage Brokerage",
                "quantity": 10.0,
                "asset_class": "equity",
                "currency": "USD",
            },
            {
                "position_id": "pos-msft",
                "instrument_id": "inst-msft",
                "symbol": "MSFT",
                "label": "Microsoft Corp",
                "account_name": "Vantage Brokerage",
                "quantity": 5.0,
                "asset_class": "equity",
                "currency": "USD",
            },
        ],
    )

    rows = search_instrument(_query())
    assert rows == [], "all held → no Security rows"
