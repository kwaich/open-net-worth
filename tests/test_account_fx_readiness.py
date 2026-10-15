"""Fresh multi-currency onboarding must resolve FX before writing any account rows."""

import threading
from types import SimpleNamespace

import pytest

from backend.core.errors import MissingFxRateError
from backend.services import accounts, fx_cache, ledger, prices


@pytest.fixture
def fresh_fx(db_isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(fx_cache, "_FX_CACHE_FILE", tmp_path / "fx.json")
    monkeypatch.setattr(prices, "_price_cache", {})
    monkeypatch.setattr(prices, "get_settings", lambda: SimpleNamespace(onw_qa_mode=False))
    monkeypatch.setattr(prices, "_yf_batch_quote", lambda symbols: {})
    monkeypatch.setattr(prices, "_fetch_one", lambda symbol: (symbol, None, None))


def create(currency="VND", amount=2000000):
    return accounts.create_account(
        {
            "name": "Onboarding bank",
            "type": "cash",
            "currency": currency,
            "initial_balance": amount,
        }
    )


def test_fresh_vnd_opening_uses_inverse_quote_when_direct_missing(fresh_fx, monkeypatch):
    calls = []

    def quote(symbol):
        calls.append(symbol)
        return symbol, 20000.0 if symbol == "SGDVND=X" else None, None

    monkeypatch.setattr(prices, "_fetch_one", quote)
    account = create()
    opening = ledger.transactions.find_by_id(account["opening_txn_id"])
    assert set(calls) == {"VNDSGD=X", "SGDVND=X"}
    assert opening["to_amount"] == 2000000
    assert opening["base_amount"] == 100
    assert opening["base_currency"] == "SGD"
    assert opening["fx_rate"] == pytest.approx(1 / 20000)


def test_unavailable_fx_leaves_no_account_or_opening(fresh_fx):
    with pytest.raises(MissingFxRateError):
        create()
    assert ledger.accounts.all() == []
    assert ledger.transactions.all() == []


@pytest.mark.parametrize("currency,amount", [("SGD", 100), ("VND", 0)])
def test_local_or_empty_account_does_not_fetch_fx(fresh_fx, monkeypatch, currency, amount):
    def unexpected(*args, **kwargs):
        pytest.fail("No FX fetch should be started")

    monkeypatch.setattr(prices, "fetch_live", unexpected)
    account = create(currency, amount)
    assert ledger.transactions.find_by_id(account["opening_txn_id"])["base_amount"] == amount


def test_cached_foreign_rate_avoids_fetch(fresh_fx, monkeypatch):
    fx_cache.save_fx_cache({"VND": 0.00005})
    calls = []
    monkeypatch.setattr(prices, "fetch_live", lambda *a, **kw: calls.append(a))
    assert create()["opening_txn_id"]
    assert calls == []


def test_slow_fx_fetch_is_bounded_and_retries_share_it(fresh_fx, monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def slow_fetch(*args, **kwargs):
        calls.append(args)
        release.wait(2)
        finished.set()

    monkeypatch.setattr(prices, "fetch_live", slow_fetch)
    monkeypatch.setattr(fx_cache, "_READINESS_TIMEOUT", 0.01)
    try:
        for _ in range(2):
            with pytest.raises(MissingFxRateError):
                create()
        assert len(calls) == 1
        assert ledger.accounts.all() == []
    finally:
        pending = list(fx_cache._readiness_fetches.values())
        release.set()
        assert finished.wait(2)
        assert all(ready.wait(2) for ready in pending)


def test_live_refresh_recovers_cash_and_basis_currencies_without_positions(fresh_fx, monkeypatch):
    from backend.services import portfolio_rows

    create("VND", 0)
    fx_cache.save_fx_cache({"VND": 0.00005})
    fx_cache._FX_CACHE_FILE.unlink()  # Same empty cache as a base-currency change.
    requested = []

    def quote(symbol):
        requested.append(symbol)
        return symbol, 20000.0 if symbol == "SGDVND=X" else None, None

    monkeypatch.setattr(prices, "_fetch_one", quote)
    _, _, rates = portfolio_rows._refresh_prices([])
    assert set(requested) == {"VNDSGD=X", "SGDVND=X"}
    assert rates["VND"] == pytest.approx(1 / 20000)
    assert fx_cache.load_fx_cache()["VND"] == rates["VND"]


def test_live_refresh_requests_separate_cost_and_owned_asset_currency(fresh_fx, monkeypatch):
    from backend.services import portfolio_rows

    requested = []
    monkeypatch.setattr(
        prices,
        "fetch_live",
        lambda tickers, currencies, **kwargs: requested.extend(currencies) or {},
    )
    monkeypatch.setattr(
        portfolio_rows.OwnedAssetsStore,
        "query_assets",
        lambda self, **kwargs: [{"currency": "GBP"}],
    )
    portfolio_rows._refresh_prices(
        [
            {"ticker": "BTC", "quote_currency": "USD", "cost_currency": "EUR"},
        ]
    )
    assert set(requested) == {"USD", "EUR", "GBP"}


@pytest.mark.asyncio
async def test_ai_account_creation_keeps_event_loop_responsive_during_fx_fetch(
    fresh_fx, monkeypatch
):
    import asyncio
    import json

    from backend.ai.tools.accounts import create_account as create_account_tool

    release = threading.Event()
    started = threading.Event()

    def fetch(*args, **kwargs):
        started.set()
        if release.wait(0.5):
            fx_cache.save_fx_cache({"VND": 0.00005})

    async def heartbeat():
        while not started.is_set():
            await asyncio.sleep(0.001)
        release.set()

    monkeypatch.setattr(prices, "fetch_live", fetch)
    result, _ = await asyncio.gather(
        create_account_tool(
            {
                "name": "Async onboarding bank",
                "type": "cash",
                "currency": "VND",
                "initial_balance": 2000000,
            }
        ),
        heartbeat(),
    )
    assert json.loads(result[0])["ok"] is True


@pytest.mark.parametrize(
    "currency", ["USD=X", "../../bad", "US1", "US", "USDD", "ＵＳＤ", "ßD", 123]
)
def test_invalid_account_currency_never_starts_fx_fetch(fresh_fx, monkeypatch, currency):
    from backend.core.errors import ValidationError

    calls = []
    monkeypatch.setattr(prices, "fetch_live", lambda *a, **kw: calls.append(a))
    with pytest.raises(ValidationError, match="three-letter"):
        create(currency)
    assert calls == []
    assert ledger.accounts.all() == []


def test_fx_saturation_refuses_without_starting_threads(fresh_fx, monkeypatch):
    slots = threading.BoundedSemaphore(4)
    for _ in range(4):
        assert slots.acquire(blocking=False)
    monkeypatch.setattr(fx_cache, "_readiness_slots", slots)
    created_threads = []
    monkeypatch.setattr(fx_cache.threading, "Thread", lambda **kw: created_threads.append(kw))
    for currency in ["USD", "EUR", "VND", "JPY", "GBP", "CAD", "AUD", "CHF"]:
        with pytest.raises(MissingFxRateError):
            fx_cache.ensure_fx_rate(currency, 100)
    assert created_threads == []
    assert fx_cache._readiness_fetches == {}
