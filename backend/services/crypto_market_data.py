"""Provider-neutral, validated crypto tape selection and bounded stale fallback."""

from __future__ import annotations

import concurrent.futures
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, TypedDict

from backend.db.market_data_config_store import MarketDataConfigStore
from backend.db.market_series_cache_store import MarketSeriesCacheStore
from backend.services import market_data_observability as observability
from backend.services.market_data_settings import (
    acquire_yahoo_history,
    record_yahoo_failure,
    record_yahoo_success,
)

CRYPTO_YAHOO_ALIASES: dict[str, str] = {
    "bitcoin": "BTC-USD",
    "ethereum": "ETH-USD",
    "solana": "SOL-USD",
    "ripple": "XRP-USD",
    "dogecoin": "DOGE-USD",
    "pepe": "PEPE24478-USD",
}

_STALE_AGES = {
    "1D": timedelta(hours=6),
    "5D": timedelta(hours=24),
}
_DEFAULT_STALE_AGE = timedelta(hours=72)
_PROVIDER_SERIES_AGES = {
    **_STALE_AGES,
    "5Y": timedelta(days=8),
    "MAX": timedelta(days=35),
}
_MAX_FUTURE_SKEW = timedelta(minutes=5)

_history_inflight_lock = threading.Lock()
_history_inflight: dict[tuple[str, str, str, bool], concurrent.futures.Future] = {}


class MarketSeriesResult(TypedDict):
    provider: str | None
    state: str
    as_of: str | None
    degraded_reason: str | None
    points: list[dict]


class CryptoQuoteResult(TypedDict):
    provider: str | None
    state: str
    as_of: str | None
    degraded_reason: str | None
    price: float | None
    previous_close: float | None


def _parse_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
        except ValueError:
            return None
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def validate_series(points: object, timeframe: str) -> list[dict]:
    """Normalize only complete, ordered real observations; no price-move opinion is applied."""
    if not isinstance(points, list):
        return []
    normalized: dict[datetime, dict] = {}
    now = datetime.now(timezone.utc)
    for raw in points:
        if not isinstance(raw, dict):
            return []
        at = _parse_date(raw.get("date"))
        price = _finite_positive(raw.get("price"))
        if at is None or price is None or at > now + _MAX_FUTURE_SKEW:
            return []
        point = dict(raw)
        point["price"] = price
        ohlc_keys = ("open", "high", "low", "close")
        if any(key in raw for key in ohlc_keys):
            values = {key: _finite_positive(raw.get(key)) for key in ohlc_keys}
            if any(value is None for value in values.values()):
                return []
            if not (
                values["low"] <= values["open"] <= values["high"]
                and values["low"] <= values["close"] <= values["high"]
            ):
                return []
            point.update(values)
        normalized[at] = point
    if len(normalized) < 2:
        return []
    newest = max(normalized)
    if now - newest > _PROVIDER_SERIES_AGES.get(timeframe.upper(), _DEFAULT_STALE_AGE):
        return []
    return [normalized[key] for key in sorted(normalized)]


def split_major_crypto_pairs(
    pairs: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return canonical-request/Yahoo-alias pairs without ever inventing an alias."""
    mapped: list[tuple[str, str]] = []
    coin_gecko_only: list[tuple[str, str]] = []
    for request_key, canonical_id in pairs:
        alias = CRYPTO_YAHOO_ALIASES.get(canonical_id)
        if alias:
            mapped.append((request_key, alias))
        else:
            coin_gecko_only.append((request_key, canonical_id))
    return mapped, coin_gecko_only


def _result(
    provider: str | None, state: str, points: list[dict], reason: str | None
) -> MarketSeriesResult:
    as_of = points[-1].get("date") if points else None
    return {
        "provider": provider,
        "state": state,
        "as_of": as_of if isinstance(as_of, str) else None,
        "degraded_reason": reason,
        "points": points,
    }


def _serve_stale(canonical_id: str, currency: str, timeframe: str) -> MarketSeriesResult:
    cached = MarketSeriesCacheStore().get(canonical_id, currency, timeframe)
    if not cached:
        observability.emit(
            "unavailable",
            canonical_id=canonical_id,
            timeframe=timeframe,
            reason="upstream_unavailable",
        )
        return _result(None, "unavailable", [], "upstream_unavailable")
    as_of = _parse_date(cached.get("as_of"))
    stored_at = _parse_date(cached.get("stored_at")) or as_of
    age = (
        datetime.now(timezone.utc) - stored_at
        if stored_at
        else _DEFAULT_STALE_AGE + timedelta(seconds=1)
    )
    if age > _STALE_AGES.get(timeframe.upper(), _DEFAULT_STALE_AGE):
        observability.emit(
            "unavailable",
            canonical_id=canonical_id,
            timeframe=timeframe,
            reason="stale_expired",
        )
        return _result(None, "unavailable", [], "stale_expired")
    provider = cached.get("provider")
    observability.emit(
        "stale_serve",
        provider=provider if provider in {"coingecko", "yahoo"} else None,
        canonical_id=canonical_id,
        timeframe=timeframe,
        reason="upstream_unavailable",
    )
    return {
        "provider": cached["provider"],
        "state": "stale",
        "as_of": cached["as_of"],
        "degraded_reason": "upstream_unavailable",
        "points": list(cached["points"]),
    }


def record_crypto_quote(
    canonical_id: str,
    currency: str,
    provider: str,
    price: object,
    previous_close: object,
    as_of: object,
) -> bool:
    """Persist one validated, provider-timestamped crypto quote as bounded last-good data."""
    current = _finite_positive(price)
    previous = _finite_positive(previous_close) if previous_close is not None else None
    observed_at = _parse_date(as_of)
    now = datetime.now(timezone.utc)
    if (
        current is None
        or (previous_close is not None and previous is None)
        or observed_at is None
        or observed_at > now + _MAX_FUTURE_SKEW
        or now - observed_at > _STALE_AGES["1D"]
    ):
        return False
    observed_iso = observed_at.isoformat()
    MarketSeriesCacheStore().put(
        str(canonical_id),
        str(currency or "USD").upper(),
        "QUOTE",
        {
            "provider": provider,
            "as_of": observed_iso,
            "points": [
                {
                    "date": observed_iso,
                    "price": current,
                    "previous_close": previous,
                }
            ],
        },
    )
    return True


def stale_crypto_quote(canonical_id: str, currency: str) -> CryptoQuoteResult | None:
    """Return a bounded, validated last-good crypto quote or no quote at all."""
    cached = MarketSeriesCacheStore().get(
        str(canonical_id), str(currency or "USD").upper(), "QUOTE"
    )
    if not cached:
        return None
    as_of = _parse_date(cached.get("as_of"))
    now = datetime.now(timezone.utc)
    if as_of is None or as_of > now + _MAX_FUTURE_SKEW or now - as_of > _STALE_AGES["1D"]:
        return None
    points = cached.get("points")
    point = points[-1] if isinstance(points, list) and points else None
    if not isinstance(point, dict):
        return None
    price = _finite_positive(point.get("price"))
    previous_raw = point.get("previous_close")
    previous = _finite_positive(previous_raw) if previous_raw is not None else None
    if price is None or (previous_raw is not None and previous is None):
        return None
    return {
        "provider": cached.get("provider"),
        "state": "stale",
        "as_of": as_of.isoformat(),
        "degraded_reason": "upstream_unavailable",
        "price": price,
        "previous_close": previous,
    }


def _attempt(
    provider: str,
    fetch: Callable[..., list[dict]],
    arguments: tuple,
    canonical_id: str,
    currency: str,
    timeframe: str,
    *,
    yahoo_generation: int | None = None,
) -> MarketSeriesResult | None:
    started = time.monotonic()
    if provider == "yahoo":
        observability.emit(
            "provider_request",
            provider=provider,
            canonical_id=canonical_id,
            timeframe=timeframe,
        )
    try:
        points = validate_series(fetch(*arguments), timeframe)
    except Exception as error:
        if provider == "yahoo":
            record_yahoo_failure(generation=yahoo_generation)
            observability.emit(
                "provider_failure",
                provider=provider,
                canonical_id=canonical_id,
                timeframe=timeframe,
                reason=type(error).__name__,
                latency_ms=(time.monotonic() - started) * 1000,
            )
        return None
    if not points:
        observability.emit(
            "validation_rejected",
            provider=provider,
            canonical_id=canonical_id,
            timeframe=timeframe,
            reason="empty_or_invalid_series",
        )
        if provider == "yahoo":
            record_yahoo_failure(generation=yahoo_generation)
            observability.emit(
                "provider_failure",
                provider=provider,
                canonical_id=canonical_id,
                timeframe=timeframe,
                reason="empty_or_invalid_series",
                latency_ms=(time.monotonic() - started) * 1000,
            )
        return None
    if provider == "yahoo":
        record_yahoo_success(generation=yahoo_generation)
        observability.emit(
            "provider_success",
            provider=provider,
            canonical_id=canonical_id,
            timeframe=timeframe,
            latency_ms=(time.monotonic() - started) * 1000,
        )
    observability.emit(
        "provider_selected",
        provider=provider,
        canonical_id=canonical_id,
        timeframe=timeframe,
    )
    result = _result(provider, "live", points, None)
    MarketSeriesCacheStore().put(canonical_id, currency, timeframe, result)
    return result


def _attempt_yahoo(
    fetch: Callable[..., list[dict]],
    arguments: tuple,
    canonical_id: str,
    currency: str,
    timeframe: str,
) -> tuple[MarketSeriesResult | None, bool]:
    reservation = acquire_yahoo_history()
    if not reservation["allowed"]:
        return None, False
    return (
        _attempt(
            "yahoo",
            fetch,
            arguments,
            canonical_id,
            currency,
            timeframe,
            yahoo_generation=reservation["generation"],
        ),
        True,
    )


def _fetch_crypto_history_uncached(
    canonical_id: str,
    timeframe: str,
    currency: str,
    *,
    yahoo_enabled: bool,
    yahoo_fetch: Callable[[str, str], list[dict]],
    coingecko_fetch: Callable[[str, str, str], list[dict]],
) -> MarketSeriesResult:
    yahoo_alias = CRYPTO_YAHOO_ALIASES.get(canonical_id)
    if yahoo_alias and currency == "USD":
        yahoo_arguments = (yahoo_alias, timeframe)
        coingecko_arguments = (canonical_id, timeframe, currency.lower())
        if yahoo_enabled:
            yahoo_result, _ = _attempt_yahoo(
                yahoo_fetch,
                yahoo_arguments,
                canonical_id,
                currency,
                timeframe,
            )
            if yahoo_result:
                return yahoo_result
            observability.emit(
                "fallback",
                provider="coingecko",
                canonical_id=canonical_id,
                timeframe=timeframe,
            )
            fallback = _attempt(
                "coingecko",
                coingecko_fetch,
                coingecko_arguments,
                canonical_id,
                currency,
                timeframe,
            )
            return fallback or _serve_stale(canonical_id, currency, timeframe)

        primary = _attempt(
            "coingecko",
            coingecko_fetch,
            coingecko_arguments,
            canonical_id,
            currency,
            timeframe,
        )
        if primary:
            return primary
        observability.emit(
            "fallback",
            provider="yahoo",
            canonical_id=canonical_id,
            timeframe=timeframe,
        )
        yahoo_result, _ = _attempt_yahoo(
            yahoo_fetch,
            yahoo_arguments,
            canonical_id,
            currency,
            timeframe,
        )
        if yahoo_result:
            return yahoo_result
        return _serve_stale(canonical_id, currency, timeframe)

    result = _attempt(
        "coingecko",
        coingecko_fetch,
        (canonical_id, timeframe, currency.lower()),
        canonical_id,
        currency,
        timeframe,
    )
    return result or _serve_stale(canonical_id, currency, timeframe)


def fetch_crypto_history(
    canonical_id: str,
    timeframe: str,
    currency: str,
    *,
    yahoo_fetch: Callable[[str, str], list[dict]],
    coingecko_fetch: Callable[[str, str, str], list[dict]],
) -> MarketSeriesResult:
    """Fetch a canonical crypto tape with explicit ordering and outage coalescing."""
    canonical_id = str(canonical_id)
    currency = str(currency or "USD").upper()
    timeframe = str(timeframe).upper()
    yahoo_enabled = MarketDataConfigStore().runtime()["yahoo_primary_for_major_crypto"]
    request_key = (canonical_id, timeframe, currency, yahoo_enabled)
    with _history_inflight_lock:
        future = _history_inflight.get(request_key)
        if future is None:
            future = concurrent.futures.Future()
            _history_inflight[request_key] = future
            leader = True
        else:
            leader = False
    if not leader:
        observability.emit(
            "coalesced",
            canonical_id=canonical_id,
            timeframe=timeframe,
            reason="identical_history_request",
        )
        return future.result()
    try:
        result = _fetch_crypto_history_uncached(
            canonical_id,
            timeframe,
            currency,
            yahoo_enabled=yahoo_enabled,
            yahoo_fetch=yahoo_fetch,
            coingecko_fetch=coingecko_fetch,
        )
        future.set_result(result)
        return result
    except Exception as error:
        future.set_exception(error)
        raise
    finally:
        with _history_inflight_lock:
            if _history_inflight.get(request_key) is future:
                _history_inflight.pop(request_key, None)
