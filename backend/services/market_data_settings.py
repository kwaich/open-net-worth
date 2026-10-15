"""Public-safe market-data configuration and provider-health service."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from backend.db.market_data_config_store import MarketDataConfigStore
from backend.db.market_provider_circuit_store import MarketProviderCircuitStore
from backend.services import coingecko_client
from backend.services import market_data_observability as observability

_YAHOO_HISTORY_CIRCUIT = "yahoo_history"
_YAHOO_HEALTH = "yahoo_health"


def acquire_yahoo_history() -> dict:
    """Admit normal traffic or the one provider-wide half-open history probe."""
    reservation = MarketProviderCircuitStore(_YAHOO_HISTORY_CIRCUIT).try_acquire(time.time())
    if not reservation["allowed"]:
        observability.emit(
            "circuit_blocked",
            provider="yahoo",
            reason="cooldown",
            cooldown_seconds=reservation.get("retry_after"),
        )
    return reservation


def acquire_yahoo_quote() -> dict:
    """Quotes share Yahoo's provider-wide outage circuit with chart history."""
    return acquire_yahoo_history()


def record_yahoo_success(*, generation: int | None = None) -> None:
    """Record success; a history reservation may close the history circuit."""
    now = time.time()
    if generation is not None:
        circuit = MarketProviderCircuitStore(_YAHOO_HISTORY_CIRCUIT)
        before = circuit.public_state(now)
        circuit.record_success(now, generation=generation)
        if before["failure_streak"] > 0:
            observability.emit("circuit_close", provider="yahoo", reason="provider_success")
    MarketProviderCircuitStore(_YAHOO_HEALTH).record_success(now)


def record_yahoo_failure(*, generation: int | None = None) -> None:
    """Record failure; a history reservation may open the history circuit."""
    now = time.time()
    if generation is not None:
        cooldown_until = MarketProviderCircuitStore(_YAHOO_HISTORY_CIRCUIT).record_failure(
            now, generation=generation
        )
        observability.emit(
            "circuit_open",
            provider="yahoo",
            reason="provider_failure",
            cooldown_seconds=max(0, int(cooldown_until - now)),
        )
    MarketProviderCircuitStore(_YAHOO_HEALTH).record_failure(now, open_circuit=False)


def _yahoo_status() -> dict:
    now = time.time()
    health = MarketProviderCircuitStore(_YAHOO_HEALTH).public_state(now)
    circuit = MarketProviderCircuitStore(_YAHOO_HISTORY_CIRCUIT).public_state(now)
    return {
        "last_ok_at": (
            datetime.fromtimestamp(health["last_ok_at"], timezone.utc).isoformat()
            if health["last_ok_at"]
            else None
        ),
        "failure_streak": health["failure_streak"],
        "cooldown_until": (
            datetime.fromtimestamp(circuit["cooldown_until"], timezone.utc).isoformat()
            if circuit["cooldown_until"] > now
            else None
        ),
    }


def get_public_config() -> dict:
    """Write-only credential state and the Yahoo rollback switch, without health detail."""
    return MarketDataConfigStore().public()


def coingecko_quota_limit() -> int:
    """The local self-throttle limit for the CURRENTLY saved CoinGecko config — tier-aware
    (see ``coingecko_client.quota_limit_for_config``), used by ``services/connectors.py`` to
    show it without duplicating tier logic itself."""
    return coingecko_client.quota_limit_for_config(MarketDataConfigStore().runtime())


def get_public_status() -> dict:
    """Bounded provider health only; configuration has its own public endpoint."""
    status = coingecko_client.status()
    status["yahoo"] = _yahoo_status()
    status["telemetry_24h"] = observability.summary()
    return status


def update_public_config(changes: dict) -> dict:
    """Persist a Settings-form patch and return its public representation."""
    return MarketDataConfigStore().update(changes)


def reset_yahoo_history_circuit_for_qa() -> None:
    """Clear only the history circuit when the existing QA scenario seam changes state."""
    from backend.core.config import get_settings

    if not get_settings().onw_qa_mode:
        raise RuntimeError("QA market-data fixture is disabled")
    MarketProviderCircuitStore(_YAHOO_HISTORY_CIRCUIT).clear()
