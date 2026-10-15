"""Public market-data settings/status routes stay secret-free and auth-gated."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.db.market_data_config_store import MarketDataConfigStore
from backend.services import coingecko_client, market_data_settings


@pytest.fixture(autouse=True)
def _clean_runtime(db_isolated):
    coingecko_client.reset_runtime_for_tests()
    config._settings = None
    yield
    coingecko_client.reset_runtime_for_tests()
    config._settings = None


def _client() -> TestClient:
    from backend.main import app

    return TestClient(app)


def test_get_config_exposes_only_public_configuration():
    body = _client().get("/api/market-data/config").json()
    assert body == {
        "has_coingecko_key": False,
        "coingecko_api_key_tier": "demo",
        "yahoo_primary_for_major_crypto": True,
    }


def test_status_has_exactly_safe_provider_health_fields():
    body = _client().get("/api/market-data/status").json()
    assert set(body) == {
        "last_ok_at",
        "failure_streak",
        "cooldown_until",
        "calls_last_minute",
        "yahoo",
        "telemetry_24h",
    }
    assert body["yahoo"] == {
        "last_ok_at": None,
        "failure_streak": 0,
        "cooldown_until": None,
    }
    assert "coingecko_api_key" not in body
    assert body["telemetry_24h"]["window_hours"] == 24
    assert body["telemetry_24h"]["http_429s"] == 0


def test_yahoo_status_retains_last_success_when_a_later_request_fails():
    reservation = market_data_settings.acquire_yahoo_history()
    market_data_settings.record_yahoo_success(generation=reservation["generation"])
    available = _client().get("/api/market-data/status").json()["yahoo"]

    assert available["last_ok_at"] is not None
    assert available["failure_streak"] == 0

    reservation = market_data_settings.acquire_yahoo_history()
    market_data_settings.record_yahoo_failure(generation=reservation["generation"])
    degraded = _client().get("/api/market-data/status").json()["yahoo"]

    assert degraded["last_ok_at"] == available["last_ok_at"]
    assert degraded["failure_streak"] == 1
    assert degraded["cooldown_until"] is not None


def test_yahoo_circuit_transitions_emit_structured_events(monkeypatch):
    from backend.services import market_data_observability

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        market_data_observability,
        "emit",
        lambda event, **fields: events.append((event, fields)),
    )
    reservation = market_data_settings.acquire_yahoo_history()

    market_data_settings.record_yahoo_failure(generation=reservation["generation"])
    blocked = market_data_settings.acquire_yahoo_history()

    assert blocked["allowed"] is False
    assert any(event == "circuit_open" for event, _fields in events)
    assert any(event == "circuit_blocked" for event, _fields in events)


def test_quote_success_does_not_close_an_open_history_circuit():
    reservation = market_data_settings.acquire_yahoo_history()
    market_data_settings.record_yahoo_failure(generation=reservation["generation"])
    before = _client().get("/api/market-data/status").json()["yahoo"]

    market_data_settings.record_yahoo_success()
    after = _client().get("/api/market-data/status").json()["yahoo"]

    assert before["cooldown_until"] is not None
    assert after["last_ok_at"] is not None
    assert after["failure_streak"] == 0
    assert after["cooldown_until"] == before["cooldown_until"]
    assert market_data_settings.acquire_yahoo_history()["allowed"] is False


def test_put_config_saves_write_only_key_and_rollback_toggle():
    response = _client().put(
        "/api/market-data/config",
        json={"coingecko_api_key": "demo-secret", "yahoo_primary_for_major_crypto": False},
    )
    assert response.status_code == 200
    assert response.json()["has_coingecko_key"] is True
    assert response.json()["yahoo_primary_for_major_crypto"] is False
    assert "demo-secret" not in json.dumps(response.json())
    assert MarketDataConfigStore().runtime()["coingecko_api_key"] == "demo-secret"


def test_put_config_saves_the_tier_independently_of_the_key():
    response = _client().put(
        "/api/market-data/config",
        json={"coingecko_api_key": "pro-secret", "coingecko_api_key_tier": "pro"},
    )
    assert response.status_code == 200
    assert response.json()["coingecko_api_key_tier"] == "pro"
    assert MarketDataConfigStore().runtime()["coingecko_api_key_tier"] == "pro"


def test_put_absent_key_preserves_saved_secret():
    client = _client()
    client.put("/api/market-data/config", json={"coingecko_api_key": "keep-me"})
    # coingecko_api_key omitted entirely — a save that only changes the tier/toggle must not
    # disturb the saved key.
    response = client.put("/api/market-data/config", json={"coingecko_api_key_tier": "pro"})
    assert response.status_code == 200
    assert response.json()["has_coingecko_key"] is True
    assert MarketDataConfigStore().runtime()["coingecko_api_key"] == "keep-me"


def test_put_null_key_clears_saved_secret_and_blank_keeps_it():
    """Security-review fix: CoinGecko's Remove action refuses outright, so an EXPLICIT null
    key in the PUT body is the only way to revoke a leaked/compromised saved key. A blank
    string keeps it, like the key simply being absent (see the preserves test above)."""
    client = _client()
    client.put("/api/market-data/config", json={"coingecko_api_key": "leaked-secret"})

    kept = client.put("/api/market-data/config", json={"coingecko_api_key": ""})
    assert kept.json()["has_coingecko_key"] is True
    assert MarketDataConfigStore().runtime()["coingecko_api_key"] == "leaked-secret"

    response = client.put("/api/market-data/config", json={"coingecko_api_key": None})

    assert response.status_code == 200
    assert response.json()["has_coingecko_key"] is False
    assert MarketDataConfigStore().runtime()["coingecko_api_key"] == ""


def test_put_config_rejects_control_character_key_without_echoing_it():
    secret = "demo\nsecret"
    response = _client().put("/api/market-data/config", json={"coingecko_api_key": secret})
    assert response.status_code == 422
    assert secret not in response.text


def test_malformed_config_json_never_logs_the_write_only_key(caplog):
    secret = "demo-secret-must-not-reach-logs"
    malformed = f'{{"coingecko_api_key":"{secret}"'.encode()

    with caplog.at_level(logging.WARNING):
        response = _client().put(
            "/api/market-data/config",
            content=malformed,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
    assert secret not in response.text
    assert secret not in caplog.text


def test_market_data_routes_are_protected_by_normal_app_auth_dependency():
    from backend.main import app

    response = TestClient(app, client=("203.0.113.7", 9999)).get("/api/market-data/status")
    assert response.status_code == 401
