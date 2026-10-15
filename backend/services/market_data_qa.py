"""Deterministic market-data provider fixtures, available only in QA processes."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from backend.core.config import get_settings
from backend.db.market_series_cache_store import MarketSeriesCacheStore
from backend.services import market_data_observability as observability

_SCENARIOS = frozenset(
    {
        "yahoo_live",
        "yahoo_fail_cg_live",
        "yahoo_fail_cg_429",
        "delayed_yahoo_fail_cg_live",
        "malformed_both",
        "both_fail",
        "metadata_fail",
        "recovered",
    }
)
_scenario = "yahoo_live"
_calls = {"yahoo": 0, "coingecko": 0, "metadata": 0}
_requests: list[dict[str, str]] = []


def _enabled() -> bool:
    return get_settings().onw_qa_mode


def _assert_enabled() -> None:
    if not _enabled():
        raise RuntimeError("QA market-data fixture is disabled")


def _points(canonical_id: str) -> list[dict]:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base_price = 68000.0 if canonical_id == "bitcoin" else 0.000012
    return [
        {
            "date": (now - timedelta(minutes=2 - index)).isoformat(),
            "price": base_price * (1 + index / 100),
            "open": base_price * (1 + index / 200),
            "high": base_price * (1 + index / 80),
            "low": base_price * (1 - index / 200),
            "close": base_price * (1 + index / 100),
            "volume": 100.0 + index,
        }
        for index in range(3)
    ]


def history(
    provider: str,
    canonical_id: str,
    timeframe: str,
    currency: str,
    *,
    provider_id: str | None = None,
) -> list[dict]:
    """Return QA-only history and a scrubbed record of the requested provider pair."""
    _assert_enabled()
    name = "yahoo" if provider == "yahoo" else "coingecko"
    coingecko_started = time.monotonic() if name == "coingecko" else None
    if coingecko_started is not None:
        observability.emit(
            "provider_request",
            provider="coingecko",
            canonical_id=canonical_id,
            timeframe=timeframe,
        )

    def finish_coingecko(event: str, reason: str | None = None) -> None:
        if coingecko_started is not None:
            observability.emit(
                event,
                provider="coingecko",
                canonical_id=canonical_id,
                timeframe=timeframe,
                reason=reason,
                latency_ms=(time.monotonic() - coingecko_started) * 1000,
            )

    _calls[name] += 1
    _requests.append(
        {
            "provider": name,
            "canonical_id": canonical_id,
            "provider_id": provider_id or canonical_id,
            "timeframe": timeframe,
            "currency": currency.upper(),
        }
    )
    if canonical_id not in {"bitcoin", "pepe"} or currency.upper() != "USD":
        finish_coingecko("provider_success")
        return []
    if _scenario == "delayed_yahoo_fail_cg_live" and provider == "yahoo":
        time.sleep(0.2)
    if _scenario == "malformed_both":
        finish_coingecko("provider_success")
        return [{"date": "not-a-date", "price": "not-a-price"}]
    if _scenario == "both_fail":
        finish_coingecko("provider_failure", "fixture_unavailable")
        return []
    if (
        _scenario
        in {
            "yahoo_fail_cg_live",
            "yahoo_fail_cg_429",
            "delayed_yahoo_fail_cg_live",
        }
        and provider == "yahoo"
    ):
        return []
    if _scenario == "yahoo_fail_cg_429" and provider == "coingecko":
        observability.emit(
            "http_429",
            provider="coingecko",
            canonical_id=canonical_id,
            timeframe=timeframe,
            status_code=429,
        )
        observability.emit(
            "circuit_open",
            provider="coingecko",
            canonical_id=canonical_id,
            timeframe=timeframe,
            reason="http_429",
            status_code=429,
        )
        finish_coingecko("provider_failure", "http_429")
        raise TimeoutError("deterministic market-data fixture: CoinGecko 429")
    finish_coingecko("provider_success")
    return _points(canonical_id)


def quote(provider: str, canonical_id: str, currency: str = "USD") -> dict:
    points = history(provider, canonical_id, "1D", currency)
    if not points:
        return {}
    return {
        "price": points[-1]["price"],
        "previous_close": points[-2]["price"],
        "as_of": points[-1]["date"],
    }


def metadata(provider: str, canonical_id: str) -> dict:
    _assert_enabled()
    _calls["metadata"] += 1
    if _scenario == "metadata_fail":
        return {}
    return {"id": canonical_id, "name": "Bitcoin"} if canonical_id == "bitcoin" else {}


def set_scenario(
    name: str, clear_persistent: bool = False, fx_rates: dict[str, float] | None = None
) -> dict:
    _assert_enabled()
    if name not in _SCENARIOS:
        raise ValueError("Unknown QA market-data scenario")
    global _scenario
    _scenario = name
    for key in _calls:
        _calls[key] = 0
    _requests.clear()
    from backend.services.market_data_settings import reset_yahoo_history_circuit_for_qa

    reset_yahoo_history_circuit_for_qa()
    try:
        from backend.services import prices

        prices._price_cache.clear()
        prices._history_cache.clear()
        prices._coin_payload_cache.clear()
        prices._cg_ohlc_cache.clear()
        prices._market_cache.clear()
        prices._chart_cache.clear()
    except Exception:
        pass
    if clear_persistent:
        MarketSeriesCacheStore().clear()
    if fx_rates is not None:
        from backend.services.fx_cache import save_fx_cache

        save_fx_cache({code.upper(): rate for code, rate in fx_rates.items()})
    return snapshot()


def snapshot() -> dict:
    _assert_enabled()
    return {"scenario": _scenario, "calls": dict(_calls), "requests": list(_requests)}
