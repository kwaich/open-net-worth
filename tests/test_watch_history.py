"""watch_history — which watched tickers get their closes fetched, and when. No network: every
fetch is stubbed."""

from __future__ import annotations

import pytest

from backend.services import watch_history

# Captured at import (collection), before conftest's autouse guard swaps it for a no-op.
_REAL_REQUEST = watch_history.request


class _Insts:
    def find_by_id(self, iid):
        return {
            "i_amd": {"price_source": "yahoo", "price_source_id": "AMD"},
            "i_priv": {"price_source": "manual", "price_source_id": "PRIV"},
        }.get(iid)


@pytest.fixture
def insts(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _Insts)


def _item(value, **kw):
    return {"type": "ticker", "value": value, "asset_class": "equity", **kw}


def test_targets_use_the_instrument_else_the_typed_symbol(insts):
    items = [
        _item("AMD", instrument_id="i_amd"),
        _item("PRIV", instrument_id="i_priv"),  # manual price: no upstream
        _item("SHOP"),  # typed in by symbol
        _item("BTC", asset_class="crypto"),  # a bare crypto symbol isn't a Yahoo listing
        {"type": "topic", "value": "AI"},
    ]
    got = [(k, kind) for k, kind, _ in watch_history.targets(items)]
    assert got == [("AMD", "instrument"), ("SHOP", "symbol")]


def test_request_fetches_in_the_background_once_per_ticker_per_window(insts, monkeypatch):
    # conftest stubs `request` for every test (no real network threads); undo it here and stub
    # the fetch underneath instead.
    monkeypatch.setattr(watch_history, "request", _REAL_REQUEST)
    monkeypatch.setattr(watch_history, "_requested", {})

    class _S:
        onw_qa_mode = False

    monkeypatch.setattr("backend.core.config.get_settings", lambda: _S())
    fetched: list[tuple[str, str]] = []
    monkeypatch.setattr(watch_history, "_top_up", lambda k, kind: fetched.append((k, kind)) or 1)
    monkeypatch.setattr(watch_history, "ensure_sectors", lambda items: 0)

    class _Now:  # run the "background" work inline so the test can see it
        def __init__(self, target, daemon, name):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(watch_history.threading, "Thread", _Now)
    first = watch_history.request([_item("SHOP"), _item("AMD", instrument_id="i_amd")])
    assert first == ["SHOP", "AMD"]
    assert fetched == [("SHOP", "symbol"), ("AMD", "instrument")]
    assert watch_history.request([_item("SHOP")]) == []  # asked again within the window: no-op


def test_request_never_runs_in_qa_mode(insts, monkeypatch):
    monkeypatch.setattr(watch_history, "request", _REAL_REQUEST)

    class _S:
        onw_qa_mode = True

    monkeypatch.setattr("backend.core.config.get_settings", lambda: _S())
    assert watch_history.request([_item("SHOP")]) == []
