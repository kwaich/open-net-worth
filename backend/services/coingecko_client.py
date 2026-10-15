"""The single fixed-host, quota-controlled CoinGecko HTTP boundary."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from backend.core.pinned_http import https_context
from backend.db.coingecko_quota_store import CoinGeckoQuotaStore
from backend.db.market_data_config_store import MarketDataConfigStore
from backend.services import market_data_observability as observability

_LOG = logging.getLogger(__name__)
_DEMO_HOST = "api.coingecko.com"
_PRO_HOST = "pro-api.coingecko.com"
_BASE_URL_BY_TIER = {
    "demo": f"https://{_DEMO_HOST}/api/v3",
    "pro": f"https://{_PRO_HOST}/api/v3",
}
_HEADER_BY_TIER = {
    "demo": "x-cg-demo-api-key",
    "pro": "x-cg-pro-api-key",
}
_MAX_RESPONSE_BYTES = 4_000_000
_DEFAULT_COOLDOWN_SECONDS = 60
# The self-imposed per-minute reservation this process enforces on ITSELF before ever making
# a request — protection against a runaway local loop, not a claim about what CoinGecko's
# own account entitlement actually is (a Pro plan's real limit varies by plan and CoinGecko
# does not publish one universal number). 4/25 are the long-standing keyless/Demo figures;
# 500 is a conservative floor comfortably below every published Pro plan's minimum, so a Pro
# key is never throttled down to Demo-tier levels by this client's own bookkeeping.
_QUOTA_NO_KEY = 4
_QUOTA_DEMO = 25
_QUOTA_PRO = 500

_inflight_lock = threading.Lock()
_inflight: dict[str, concurrent.futures.Future] = {}


class CoinGeckoClientError(RuntimeError):
    """Safe error sent to callers; upstream bodies, URLs, and keys stay private."""


class _CoinGeckoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Permit HTTPS redirects only when they stay on the SAME host the request was made to.

    Two fixed hosts are now valid CoinGecko origins (Demo vs. Pro, chosen per-request by the
    stored tier — see ``_request`` below), so this validates against the ORIGINATING
    request's own host (``req.full_url``) rather than one hardcoded hostname — same-origin
    redirects only, never a cross-tier hop either."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        origin_host = urllib.parse.urlsplit(req.full_url).hostname
        parsed = urllib.parse.urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname != origin_host
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise urllib.error.HTTPError(req.full_url, code, "unsafe redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener() -> urllib.request.OpenerDirector:
    """Same-origin-redirect opener whose TLS context comes from ``core.pinned_http``.

    A bare ``build_opener()`` uses Python's default trust store, which is EMPTY on a
    python.org macOS build whose ``Install Certificates.command`` was never run — every
    call then fails CERTIFICATE_VERIFY_FAILED while curl_cffi/httpx (certifi) keep
    working, so crypto search silently died on one user's machine (2026-09-21).
    """
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=https_context()),
        _CoinGeckoRedirectHandler(),
    )


_OPENER = _build_opener()


def _open_request(request, timeout):  # noqa: ANN001
    return _OPENER.open(request, timeout=timeout)


def _validated_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("CoinGecko path must be a relative API path")
    if path.startswith("//") or "://" in path or "?" in path or "#" in path:
        raise ValueError("CoinGecko path must be a relative API path")
    if any(segment in {"", ".", ".."} for segment in path.split("/")[1:]):
        raise ValueError("CoinGecko path must be a relative API path")
    return path


def _normalized_request(path: str, params: dict | None) -> tuple[str, str, str]:
    validated = _validated_path(path)
    pairs = []
    for key, value in (params or {}).items():
        if isinstance(value, (list, tuple)):
            pairs.extend((str(key), str(item)) for item in value)
        else:
            pairs.append((str(key), str(value)))
    pairs.sort()
    query = urllib.parse.urlencode(pairs, doseq=True)
    return validated, query, f"{validated}?{query}" if query else validated


def _retry_after(headers: object) -> int:
    value = None
    if headers is not None:
        try:
            value = headers.get("Retry-After")
        except AttributeError:
            value = None
    try:
        return max(1, int(str(value)))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, IndexError):
            return _DEFAULT_COOLDOWN_SECONDS
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(1, int((retry_at - datetime.now(timezone.utc)).total_seconds()))


def quota_limit_for_config(config: dict) -> int:
    """The local self-throttle limit for a given market-data config — keyed the same way a
    live reservation is (``_quota_limit`` below), exposed so a caller synthesizing a
    connector view (``services/connectors.py``) doesn't duplicate the tier logic."""
    key = str(config.get("coingecko_api_key") or "")
    if not key:
        return _QUOTA_NO_KEY
    tier = str(config.get("coingecko_api_key_tier") or "demo")
    return _QUOTA_PRO if tier == "pro" else _QUOTA_DEMO


def _quota_limit() -> tuple[int, str, str]:
    config = MarketDataConfigStore().runtime()
    key = config["coingecko_api_key"]
    tier = config["coingecko_api_key_tier"]
    return quota_limit_for_config(config), key, tier


def _request(path: str, query: str, *, timeout: int | float) -> dict:
    now = time.time()
    limit, api_key, tier = _quota_limit()
    quota = CoinGeckoQuotaStore()
    reservation = quota.reserve(
        limit,
        now,
        probe_lease_seconds=max(30.0, float(timeout) + 5.0),
    )
    if not reservation["allowed"]:
        observability.emit(
            "quota_denied",
            provider="coingecko",
            reason="cooldown_or_quota",
            remaining_budget=max(0, limit - int(reservation.get("calls_last_minute") or 0)),
            cooldown_seconds=reservation.get("retry_after"),
        )
        raise CoinGeckoClientError("CoinGecko unavailable")
    remaining_budget = max(0, limit - int(reservation.get("calls_last_minute") or 0))
    observability.emit(
        "quota_reserved",
        provider="coingecko",
        remaining_budget=remaining_budget,
    )

    base_url = _BASE_URL_BY_TIER["pro" if (api_key and tier == "pro") else "demo"]
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{query}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers[_HEADER_BY_TIER["pro" if tier == "pro" else "demo"]] = api_key
    started = time.monotonic()
    observability.emit("provider_request", provider="coingecko")
    try:
        if any(ord(char) < 32 or ord(char) == 127 for char in api_key):
            raise ValueError("invalid CoinGecko API key")
        request = urllib.request.Request(url, headers=headers)
        with _open_request(request, timeout=timeout) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            quota.record_failure(
                _DEFAULT_COOLDOWN_SECONDS,
                time.time(),
                generation=reservation["generation"],
            )
            observability.emit(
                "provider_failure",
                provider="coingecko",
                reason="response_too_large",
                latency_ms=(time.monotonic() - started) * 1000,
                remaining_budget=remaining_budget,
            )
            observability.emit("circuit_open", provider="coingecko", reason="response_too_large")
            raise CoinGeckoClientError("CoinGecko response too large")
        parsed = json.loads(body.decode("utf-8"))
        quota.record_success(time.time(), generation=reservation["generation"])
        observability.emit(
            "provider_success",
            provider="coingecko",
            latency_ms=(time.monotonic() - started) * 1000,
            remaining_budget=remaining_budget,
        )
        if reservation.get("probe"):
            observability.emit("circuit_close", provider="coingecko", reason="half_open_success")
        return parsed
    except urllib.error.HTTPError as error:
        if error.code == 429 or error.code >= 500:
            cooldown = _retry_after(error.headers) if error.code == 429 else None
            quota.record_failure(
                cooldown or _DEFAULT_COOLDOWN_SECONDS,
                time.time(),
                generation=reservation["generation"],
            )
            observability.emit(
                "circuit_open",
                provider="coingecko",
                reason="http_429" if error.code == 429 else "http_5xx",
                status_code=error.code,
                cooldown_seconds=cooldown or _DEFAULT_COOLDOWN_SECONDS,
            )
        else:
            quota.release_probe(time.time(), generation=reservation["generation"])
        if error.code == 429:
            observability.emit(
                "http_429",
                provider="coingecko",
                status_code=429,
                remaining_budget=remaining_budget,
                cooldown_seconds=cooldown or _DEFAULT_COOLDOWN_SECONDS,
            )
        observability.emit(
            "provider_failure",
            provider="coingecko",
            reason="http_error",
            status_code=error.code,
            latency_ms=(time.monotonic() - started) * 1000,
            remaining_budget=remaining_budget,
        )
        _LOG.warning(
            "coingecko request failed provider=coingecko path=%s status=%s type=%s",
            path,
            error.code,
            type(error).__name__,
        )
        raise CoinGeckoClientError("CoinGecko unavailable") from None
    except CoinGeckoClientError:
        raise
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        quota.record_failure(
            _DEFAULT_COOLDOWN_SECONDS,
            time.time(),
            generation=reservation["generation"],
        )
        observability.emit(
            "provider_failure",
            provider="coingecko",
            reason=type(error).__name__,
            latency_ms=(time.monotonic() - started) * 1000,
            remaining_budget=remaining_budget,
        )
        observability.emit(
            "circuit_open",
            provider="coingecko",
            reason=type(error).__name__,
            cooldown_seconds=_DEFAULT_COOLDOWN_SECONDS,
        )
        _LOG.warning(
            "coingecko request failed provider=coingecko path=%s status=%s type=%s",
            path,
            None,
            type(error).__name__,
        )
        raise CoinGeckoClientError("CoinGecko unavailable") from None


def get_json(path: str, params: dict | None, *, timeout: int | float = 15) -> dict:
    """Fetch one JSON payload from the fixed CoinGecko API host.

    Identical requests join one leader so page refreshes do not multiply quota use.
    """
    validated_path, query, request_key = _normalized_request(path, params)
    with _inflight_lock:
        existing = _inflight.get(request_key)
        if existing is None:
            future: concurrent.futures.Future = concurrent.futures.Future()
            _inflight[request_key] = future
            leader = True
        else:
            future = existing
            leader = False
    if not leader:
        observability.emit(
            "coalesced",
            provider="coingecko",
            reason="identical_http_request",
        )
        return future.result()
    try:
        result = _request(validated_path, query, timeout=timeout)
        future.set_result(result)
        return result
    except Exception as error:
        future.set_exception(error)
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(request_key, None)


def status() -> dict:
    """Public-safe client health; it deliberately excludes keys, URLs, and errors."""
    now = time.time()
    quota = CoinGeckoQuotaStore().public_state(now)
    cooldown_until = (
        datetime.fromtimestamp(quota["cooldown_until"], timezone.utc).isoformat()
        if quota["cooldown_until"] > now
        else None
    )
    return {
        "last_ok_at": quota["last_ok_at"],
        "failure_streak": quota["failure_streak"],
        "cooldown_until": cooldown_until,
        "calls_last_minute": quota["calls_last_minute"],
    }


def reset_runtime_for_tests() -> None:
    """Clear process-only state; database quota state remains test-fixture owned."""
    with _inflight_lock:
        _inflight.clear()
