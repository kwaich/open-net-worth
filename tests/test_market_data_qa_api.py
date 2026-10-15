"""QA market-data controls are deterministic and absent outside QA mode."""

from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_settings_singleton():
    import backend.core.config as config

    config._settings = None
    yield
    config._settings = None


def _app(monkeypatch, *, qa: bool | str):
    monkeypatch.setenv("ONW_QA_MODE", qa if isinstance(qa, str) else ("1" if qa else "0"))
    import backend.core.config as config
    import backend.main as main

    config._settings = None
    return importlib.reload(main).app


def test_qa_market_data_routes_are_absent_outside_qa(monkeypatch):
    client = TestClient(_app(monkeypatch, qa=False))
    for method, path in (
        ("GET", "/api/qa/market-data/status"),
        ("POST", "/api/qa/market-data/status"),
        ("GET", "/api/qa/market-data/scenario"),
        ("POST", "/api/qa/market-data/scenario"),
        ("GET", "/api/qa/attest"),
    ):
        assert client.request(method, path, json={"name": "yahoo_live"}).status_code == 404


def test_qa_attestation_requires_the_per_run_nonce(monkeypatch, db_isolated):
    monkeypatch.setenv("ONW_QA_NONCE", "one-run-only")
    client = TestClient(_app(monkeypatch, qa=True))

    assert client.get("/api/qa/attest").status_code == 404
    assert client.get("/api/qa/attest", headers={"X-ONW-QA-Nonce": "wrong"}).status_code == 404
    response = client.get("/api/qa/attest", headers={"X-ONW-QA-Nonce": "one-run-only"})
    assert response.status_code == 200
    assert response.json() == {"qa_mode": True}


def test_qa_market_data_scenarios_are_deterministic(monkeypatch, db_isolated):
    client = TestClient(_app(monkeypatch, qa=True))

    changed = client.post("/api/qa/market-data/scenario", json={"name": "yahoo_live"})
    status = client.get("/api/qa/market-data/status")

    assert changed.status_code == 200
    assert changed.json()["scenario"] == "yahoo_live"
    assert status.status_code == 200
    assert status.json()["scenario"] == "yahoo_live"
    assert status.json()["calls"] == {"yahoo": 0, "coingecko": 0, "metadata": 0}
    assert status.json()["requests"] == []


def test_qa_market_data_scenario_seeds_deterministic_fx(monkeypatch, db_isolated):
    client = TestClient(_app(monkeypatch, qa=True))
    assert client.put("/api/profile", json={"base_currency": "VND"}).status_code == 200

    changed = client.post(
        "/api/qa/market-data/scenario",
        json={"name": "yahoo_live", "fx_rates": {"VND": 1, "SGD": 19200}},
    )

    assert changed.status_code == 200
    assert changed.json() == {
        "scenario": "yahoo_live",
        "calls": {"yahoo": 0, "coingecko": 0, "metadata": 0},
        "requests": [],
    }
    from backend.services.fx_cache import load_fx_cache

    assert load_fx_cache()["SGD"] == 19200

    invalid = client.post(
        "/api/qa/market-data/scenario",
        json={"name": "yahoo_live", "fx_rates": {"SGD": 0}},
    )
    assert invalid.status_code == 422


def test_qa_history_records_pepe_yahoo_alias_without_rekeying(monkeypatch, db_isolated):
    client = TestClient(_app(monkeypatch, qa=True))
    assert (
        client.post("/api/qa/market-data/scenario", json={"name": "yahoo_live"}).status_code == 200
    )

    from backend.services.prices import fetch_history_result

    result = fetch_history_result("pepe", "1D", "coingecko", "usd")

    assert result["state"] == "live"
    assert result["provider"] == "yahoo"
    assert client.get("/api/qa/market-data/status").json()["requests"] == [
        {
            "provider": "yahoo",
            "canonical_id": "pepe",
            "provider_id": "PEPE24478-USD",
            "timeframe": "1D",
            "currency": "USD",
        }
    ]


def test_harness_true_literal_uses_fixtures_before_any_provider_io(monkeypatch, db_isolated):
    """The browser harness exports ONW_QA_MODE='true', not the numeric spelling."""
    client = TestClient(_app(monkeypatch, qa="true"))
    assert (
        client.post("/api/qa/market-data/scenario", json={"name": "both_fail"}).status_code == 200
    )

    from backend.services import prices

    monkeypatch.setattr(prices, "_yf_batch_quote", lambda *_: pytest.fail("Yahoo network used"))
    monkeypatch.setattr(prices, "_fetch_one", lambda *_: pytest.fail("Yahoo fallback used"))
    monkeypatch.setattr(
        prices, "_fetch_coingecko_by_id", lambda *_: pytest.fail("CoinGecko network used")
    )
    monkeypatch.setattr(
        "backend.db.instrument_store.InstrumentStore.all",
        lambda _self: [
            {"symbol": "BTC", "price_source": "coingecko", "price_source_id": "bitcoin"}
        ],
    )

    result = prices._fetch_live_uncached(["BTC"], [], False)

    assert result["states"]["BTC"] == "unavailable"


def test_scenario_reset_clears_metadata_cache_between_failure_and_recovery(
    monkeypatch, db_isolated
):
    client = TestClient(_app(monkeypatch, qa="true"))
    from backend.services import prices

    client.post("/api/qa/market-data/scenario", json={"name": "yahoo_live"})
    assert prices._fetch_coin_payload("bitcoin") == {"id": "bitcoin", "name": "Bitcoin"}

    client.post("/api/qa/market-data/scenario", json={"name": "metadata_fail"})
    assert prices._fetch_coin_payload("bitcoin") == {}

    client.post("/api/qa/market-data/scenario", json={"name": "recovered"})
    assert prices._fetch_coin_payload("bitcoin") == {"id": "bitcoin", "name": "Bitcoin"}


def test_scenario_change_resets_only_the_qa_history_circuit(monkeypatch, db_isolated):
    client = TestClient(_app(monkeypatch, qa="true"))
    from backend.db.market_provider_circuit_store import MarketProviderCircuitStore
    from backend.services import market_data_settings

    circuit = MarketProviderCircuitStore("yahoo_history")
    reservation = market_data_settings.acquire_yahoo_history()
    market_data_settings.record_yahoo_failure(generation=reservation["generation"])
    assert circuit.try_acquire(market_data_settings.time.time())["allowed"] is False

    client.post("/api/qa/market-data/scenario", json={"name": "recovered"})

    acquired = circuit.try_acquire(market_data_settings.time.time())
    assert acquired["allowed"] is True
    assert acquired["probe"] is False


def test_qa_fixture_exposes_429_malformed_and_delayed_concurrency_controls(
    monkeypatch, db_isolated
):
    client = TestClient(_app(monkeypatch, qa="true"))
    from backend.services import market_data_qa

    client.post("/api/qa/market-data/scenario", json={"name": "yahoo_fail_cg_429"})
    assert market_data_qa.history("yahoo", "bitcoin", "1D", "USD") == []
    with pytest.raises(TimeoutError, match="429"):
        market_data_qa.history("coingecko", "bitcoin", "1D", "USD")
    from backend.db.market_data_observability_store import MarketDataObservabilityStore

    telemetry = MarketDataObservabilityStore().summary(hours=24)
    assert telemetry["provider_calls"]["coingecko"] == 1
    assert telemetry["provider_failures"]["coingecko"] == 1
    assert telemetry["http_429s"] == 1

    client.post("/api/qa/market-data/scenario", json={"name": "malformed_both"})
    assert market_data_qa.history("yahoo", "bitcoin", "1D", "USD") == [
        {"date": "not-a-date", "price": "not-a-price"}
    ]

    client.post(
        "/api/qa/market-data/scenario",
        json={"name": "delayed_yahoo_fail_cg_live"},
    )
    started = time.monotonic()
    assert market_data_qa.history("yahoo", "bitcoin", "1D", "USD") == []
    assert time.monotonic() - started >= 0.15
