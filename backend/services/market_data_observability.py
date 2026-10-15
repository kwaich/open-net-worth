"""Failure-isolated structured telemetry for market-provider selection and soak evidence."""

from __future__ import annotations

import json
import logging

from backend.db.market_data_observability_store import MarketDataObservabilityStore

_LOG = logging.getLogger(__name__)


def emit(event: str, **fields: object) -> None:
    """Persist and log one secret-safe event; telemetry can never fail market data."""
    try:
        row = MarketDataObservabilityStore().record(event, **fields)
        _LOG.info("market_data_event %s", json.dumps(row, sort_keys=True, separators=(",", ":")))
    except Exception as error:  # pragma: no cover - defensive operational isolation
        _LOG.debug("market-data telemetry unavailable: %s", type(error).__name__)


def summary() -> dict:
    """Return the bounded public-safe 24-hour soak counters."""
    try:
        return MarketDataObservabilityStore().summary(hours=24)
    except Exception:  # pragma: no cover - status remains available during telemetry failure
        return {
            "window_hours": 24,
            "since": None,
            "event_count": 0,
            "provider_calls": {},
            "provider_successes": {},
            "provider_failures": {},
            "fallbacks": 0,
            "stale_serves": 0,
            "unavailable_responses": 0,
            "http_429s": 0,
            "coalesced_requests": 0,
            "validation_rejections": 0,
            "quota_denials": 0,
            "latency": {},
        }
