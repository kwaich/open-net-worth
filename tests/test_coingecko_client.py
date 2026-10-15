"""CoinGecko client safety and request-control behavior (no real network)."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from backend.db.market_data_config_store import MarketDataConfigStore
from backend.services import coingecko_client as client


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, _limit: int = -1) -> bytes:
        return self._body if _limit < 0 else self._body[:_limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.fixture(autouse=True)
def _reset_client_runtime():
    client.reset_runtime_for_tests()
    yield
    client.reset_runtime_for_tests()


def test_refuses_any_non_relative_or_traversing_path(db_isolated):
    for path in ("https://evil.example/x", "//evil.example/x", "/coins/../secret", "/coins://evil"):
        with pytest.raises(ValueError, match="relative"):
            client.get_json(path, {})


def test_sends_demo_key_only_as_exact_header_not_url(db_isolated, monkeypatch):
    MarketDataConfigStore().update(
        {"coingecko_api_key": "demo-secret", "coingecko_api_key_tier": "demo"}
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["timeout"] = timeout
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(client, "_open_request", fake_urlopen)
    assert client.get_json("/coins/bitcoin", {"vs_currency": "usd"}) == {"ok": True}
    assert captured["url"] == "https://api.coingecko.com/api/v3/coins/bitcoin?vs_currency=usd"
    assert captured["headers"]["x-cg-demo-api-key"] == "demo-secret"
    assert "demo-secret" not in captured["url"]
    assert captured["timeout"] == 15


def test_sends_pro_key_to_the_pro_host_with_the_pro_header(db_isolated, monkeypatch):
    MarketDataConfigStore().update(
        {"coingecko_api_key": "pro-secret", "coingecko_api_key_tier": "pro"}
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(client, "_open_request", fake_urlopen)
    assert client.get_json("/coins/bitcoin", {"vs_currency": "usd"}) == {"ok": True}
    assert captured["url"] == "https://pro-api.coingecko.com/api/v3/coins/bitcoin?vs_currency=usd"
    assert captured["headers"]["x-cg-pro-api-key"] == "pro-secret"
    assert "x-cg-demo-api-key" not in captured["headers"]


def test_no_key_uses_the_demo_host_with_no_header(db_isolated, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(client, "_open_request", fake_urlopen)
    assert client.get_json("/coins/bitcoin", {}) == {"ok": True}
    assert captured["url"].startswith("https://api.coingecko.com/api/v3/")
    assert "x-cg-demo-api-key" not in captured["headers"]
    assert "x-cg-pro-api-key" not in captured["headers"]


def test_a_pro_tier_selection_with_no_key_saved_still_uses_the_demo_host(db_isolated, monkeypatch):
    """The tier alone means nothing without a key — a Pro *selection* with a blank key must
    not send requests to the Pro host with no auth at all."""
    MarketDataConfigStore().update({"coingecko_api_key_tier": "pro"})
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(client, "_open_request", fake_urlopen)
    client.get_json("/coins/bitcoin", {})
    assert captured["url"].startswith("https://api.coingecko.com/api/v3/")


def test_never_puts_key_in_error_or_log_record(db_isolated, monkeypatch, caplog):
    MarketDataConfigStore().update({"coingecko_api_key": "demo-secret"})

    def fail(*_args, **_kwargs):
        raise OSError("upstream broken")

    monkeypatch.setattr(client, "_open_request", fail)
    caplog.set_level(logging.WARNING)
    with pytest.raises(client.CoinGeckoClientError) as error:
        client.get_json("/coins/bitcoin", {"secret": "demo-secret"})
    assert "demo-secret" not in str(error.value)
    assert "demo-secret" not in caplog.text


def test_rejects_response_larger_than_four_megabytes(db_isolated, monkeypatch):
    monkeypatch.setattr(
        client,
        "_open_request",
        lambda *_args, **_kwargs: _Response(b"x" * 4_000_001),
    )
    with pytest.raises(client.CoinGeckoClientError, match="too large"):
        client.get_json("/coins/bitcoin", {})


def test_identical_inflight_requests_coalesce_to_one_network_call(db_isolated, monkeypatch):
    from backend.services import market_data_observability

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        market_data_observability,
        "emit",
        lambda event, **fields: events.append((event, fields)),
    )
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return _Response(b'{"coin": "bitcoin"}')

    monkeypatch.setattr(client, "_open_request", fake_urlopen)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.get_json, "/coins/bitcoin", {"x": "1"})
        assert started.wait(1)
        second = executor.submit(client.get_json, "/coins/bitcoin", {"x": "1"})
        release.set()
        assert first.result() == second.result() == {"coin": "bitcoin"}
    assert calls == 1
    assert sum(event == "coalesced" for event, _fields in events) == 1
    assert sum(event == "provider_request" for event, _fields in events) == 1
    assert sum(event == "provider_success" for event, _fields in events) == 1
    assert sum(event == "quota_reserved" for event, _fields in events) == 1


def test_shared_keyless_quota_refuses_fifth_request_before_network(db_isolated, monkeypatch):
    calls = 0

    def fake_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response(b"{}")

    monkeypatch.setattr(client, "_open_request", fake_urlopen)
    for index in range(4):
        assert client.get_json(f"/coins/{index}", {}) == {}
    with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
        client.get_json("/coins/five", {})
    assert calls == 4


def test_429_honors_retry_after_and_does_not_immediately_retry(db_isolated, monkeypatch):
    from backend.services import market_data_observability

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        market_data_observability,
        "emit",
        lambda event, **fields: events.append((event, fields)),
    )
    calls = 0

    def rate_limited(request, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(
            request.full_url, 429, "Too Many Requests", {"Retry-After": "17"}, BytesIO()
        )

    monkeypatch.setattr(client, "_open_request", rate_limited)
    with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
        client.get_json("/coins/bitcoin", {})
    with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
        client.get_json("/coins/ethereum", {})
    assert calls == 1
    assert client.status()["cooldown_until"] is not None
    assert sum(event == "http_429" for event, _fields in events) == 1
    assert sum(event == "quota_denied" for event, _fields in events) == 1
    assert any(
        event == "provider_failure" and fields.get("status_code") == 429 for event, fields in events
    )


def test_client_side_404_consumes_quota_but_does_not_open_provider_circuit(
    db_isolated, monkeypatch
):
    calls = 0

    def not_found(request, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(request.full_url, 404, "Not Found", {}, BytesIO())

    monkeypatch.setattr(client, "_open_request", not_found)
    with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
        client.get_json("/coins/missing", {})
    with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
        client.get_json("/coins/also-missing", {})

    assert calls == 2
    assert client.status()["failure_streak"] == 0
    assert client.status()["cooldown_until"] is None


def test_persisted_429_cooldown_blocks_a_fresh_client_runtime(db_isolated, monkeypatch):
    calls = 0

    def rate_limited(request, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(
            request.full_url, 429, "Too Many Requests", {"Retry-After": "17"}, BytesIO()
        )

    monkeypatch.setattr(client, "_open_request", rate_limited)
    with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
        client.get_json("/coins/bitcoin", {})

    client.reset_runtime_for_tests()
    with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
        client.get_json("/coins/ethereum", {})
    assert calls == 1


def test_cooldown_expiry_admits_one_probe_across_different_requests(db_isolated, monkeypatch):
    clock = {"now": 10_000.0}
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def rate_limit_then_succeed(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(
                request.full_url, 429, "Too Many Requests", {"Retry-After": "17"}, BytesIO()
            )
        started.set()
        assert release.wait(2)
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(client.time, "time", lambda: clock["now"])
    monkeypatch.setattr(client, "_open_request", rate_limit_then_succeed)
    with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
        client.get_json("/coins/bitcoin", {})

    clock["now"] = 10_018.0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        probe = executor.submit(client.get_json, "/coins/ethereum", {})
        assert started.wait(1)
        blocked = executor.submit(client.get_json, "/coins/solana", {})
        with pytest.raises(client.CoinGeckoClientError, match="unavailable"):
            blocked.result(timeout=1)
        release.set()
        assert probe.result() == {"ok": True}

    assert calls == 2
    assert client.status()["failure_streak"] == 0
    assert client.status()["cooldown_until"] is None


def test_retry_after_http_date_is_honored():
    future = datetime.now(timezone.utc) + timedelta(seconds=37)
    seconds = client._retry_after({"Retry-After": format_datetime(future, usegmt=True)})
    assert 30 <= seconds <= 37


def test_invalid_header_value_becomes_safe_client_error(db_isolated, monkeypatch, caplog):
    secret = "demo\nsecret"
    monkeypatch.setattr(
        client.MarketDataConfigStore,
        "runtime",
        lambda _self: {
            "coingecko_api_key": secret,
            "coingecko_api_key_tier": "demo",
            "yahoo_primary_for_major_crypto": True,
        },
    )
    caplog.set_level(logging.WARNING)

    with pytest.raises(client.CoinGeckoClientError) as error:
        client.get_json("/coins/bitcoin", {})

    assert secret not in str(error.value)
    assert secret not in caplog.text


def test_cross_host_redirect_is_refused_before_a_target_request_is_created(monkeypatch):
    request = Request(
        "https://api.coingecko.com/api/v3/coins/bitcoin",
        headers={"x-cg-demo-api-key": "demo-secret"},
    )
    target_requests = []
    original_request = client.urllib.request.Request

    def record_target_request(*args, **kwargs):
        target_requests.append((args, kwargs))
        return original_request(*args, **kwargs)

    monkeypatch.setattr(client.urllib.request, "Request", record_target_request)
    handler = client._CoinGeckoRedirectHandler()

    with pytest.raises(HTTPError):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://evil.example/collect",
        )
    assert target_requests == []


def test_cross_tier_redirect_is_refused_too(monkeypatch):
    """A redirect must stay on the SAME host the request was made to — a Demo-tier request
    redirected to the Pro host (or vice versa) is refused exactly like any other cross-host
    hop, never silently followed just because both are "real" CoinGecko hosts."""
    request = Request(
        "https://api.coingecko.com/api/v3/coins/bitcoin",
        headers={"x-cg-demo-api-key": "demo-secret"},
    )
    handler = client._CoinGeckoRedirectHandler()
    with pytest.raises(HTTPError):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://pro-api.coingecko.com/api/v3/coins/bitcoin"
        )


def test_status_has_only_public_health_fields(db_isolated):
    MarketDataConfigStore().update({"coingecko_api_key": "demo-secret"})
    assert set(client.status()) == {
        "last_ok_at",
        "failure_streak",
        "cooldown_until",
        "calls_last_minute",
    }


def test_opener_uses_trust_store_aware_https_context(monkeypatch):
    """On a Python with NO platform CA file (python.org macOS without
    ``Install Certificates.command``), the opener's TLS context must still carry a
    populated, verifying trust store — certifi via ``core.pinned_http.https_context`` —
    instead of the bare default that fails every request with CERTIFICATE_VERIFY_FAILED."""
    import ssl
    from urllib.request import HTTPSHandler

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    missing = ssl.DefaultVerifyPaths(
        cafile=None,
        capath=None,
        openssl_cafile_env="SSL_CERT_FILE",
        openssl_cafile="/nonexistent/cert.pem",
        openssl_capath_env="SSL_CERT_DIR",
        openssl_capath="/nonexistent/certs",
    )
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: missing)

    opener = client._build_opener()
    https = [h for h in opener.handlers if isinstance(h, HTTPSHandler)]
    assert len(https) == 1
    ctx = https[0]._context
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.cert_store_stats()["x509"] > 0
