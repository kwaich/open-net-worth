"""Alpaca brokerage aggregation service — protocol + position/cash normalization +
error classification (all HTTP mocked — NO real network).

Alpaca is a positions+cash SNAPSHOT, not a date-windowed transaction feed (RFC
``connectors-as-data-sources`` §B): ``fetch_window`` always returns ``rows=[]`` and rides
the current positions/cash/buying_power on ``FetchWindow.extra``. There is no adapter class,
no scheduler, and no booking here any more — a Lucius turn calls ``alpaca_fetch_positions``,
which reaches this module's ``fetch_window``, and books via the ordinary position tools
through ``backend.services.connector_fetch`` (covered separately, not in this file).

Covers:
  * fetch_window maps Alpaca positions -> the RFC §A broker-position shape (instrument_hint,
    qty, avg_cost, market_value, currency, provider_account_id) and surfaces cash/
    buying_power read-only via `extra`;
  * a `account` filter that doesn't match the credential's own account number returns no
    accounts/positions (never fabricates a match);
  * `accounts_only=True` skips the positions call entirely;
  * a symbol-less position is dropped, not fabricated;
  * classify_error's exception -> status taxonomy.

The trading entrypoints (place_order / cancel_order / submit_alpaca_order /
cancel_alpaca_order) were REMOVED 2026-07-19 along with the rest of the trading half — see
``docs/adr/2026-07-19-remove-alpaca-trading-half.md``. Their three gate tests went with them;
that no wire call to /v2/orders survives is pinned by ``test_order_wire_calls_are_gone``.
"""

from __future__ import annotations

import httpx
import pytest

from backend.services.aggregation import alpaca as svc

# ── deterministic /v2/account + /v2/positions fixtures ───────────────────────────────

_ACCOUNT = {
    "account_number": "ALP-001",
    "cash": "1500.25",
    "buying_power": "3000.50",
    "portfolio_value": "12000.00",
    "currency": "USD",
}

_POSITIONS = [
    {
        "asset_id": "a1",
        "symbol": "AAPL",
        "asset_class": "us_equity",
        "qty": "10",
        "avg_entry_price": "150.00",
        "market_value": "1600.00",
    },
    {
        "asset_id": "a2",
        "symbol": "BTCUSD",
        "asset_class": "crypto",
        "qty": "0.5",
        "avg_entry_price": "40000",
        "market_value": "21000.00",
    },
]


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    for var in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_ENV", "ALPACA_ENABLED"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def store(db_isolated):
    """A configured (but not necessarily enabled) AlpacaConfigStore — fetch_window itself
    doesn't gate on `enabled`/`is_active` (the dormant gate lives in the fetch tool), it
    only needs a key pair + a base URL."""
    from backend.db.alpaca_config_store import AlpacaConfigStore

    s = AlpacaConfigStore()
    s.update({"api_key_id": "PK", "secret_key": "sec", "environment": "paper"})
    return s


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://x")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


# ── fetch_window ──────────────────────────────────────────────────────────────────────


class TestFetchWindow:
    def test_maps_positions_and_surfaces_cash(self, store, monkeypatch):
        monkeypatch.setattr(svc, "fetch_account", lambda base, k, s: dict(_ACCOUNT))
        monkeypatch.setattr(
            svc, "fetch_positions", lambda base, k, s: [dict(p) for p in _POSITIONS]
        )

        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)

        assert window.rows == []  # Alpaca has no transactions, ever
        assert window.complete is True
        assert window.accounts == [
            {
                "provider_account_id": "ALP-001",
                "name": "Alpaca",
                "mask": None,
                "currency": "USD",
                "balance": "12000.00",
            }
        ]
        by_symbol = {p["symbol"]: p for p in window.extra["positions"]}
        assert by_symbol["AAPL"]["instrument_hint"] == "equity"  # us_equity -> equity
        assert by_symbol["AAPL"]["qty"] == 10.0
        assert by_symbol["AAPL"]["avg_cost"] == 150.0
        assert by_symbol["AAPL"]["market_value"] == 1600.0
        assert by_symbol["AAPL"]["currency"] == "USD"
        assert by_symbol["AAPL"]["provider_account_id"] == "ALP-001"
        assert by_symbol["BTCUSD"]["instrument_hint"] == "crypto"
        assert window.extra["cash"] == "1500.25"
        assert window.extra["buying_power"] == "3000.50"

    def test_unknown_asset_class_defaults_to_equity(self, store, monkeypatch):
        monkeypatch.setattr(svc, "fetch_account", lambda base, k, s: dict(_ACCOUNT))
        monkeypatch.setattr(
            svc,
            "fetch_positions",
            lambda base, k, s: [{"symbol": "XYZ", "asset_class": "some_future_thing"}],
        )
        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)
        assert window.extra["positions"][0]["instrument_hint"] == "equity"

    def test_symbol_less_position_is_dropped(self, store, monkeypatch):
        monkeypatch.setattr(svc, "fetch_account", lambda base, k, s: dict(_ACCOUNT))
        monkeypatch.setattr(svc, "fetch_positions", lambda base, k, s: [{"symbol": "", "qty": "1"}])
        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)
        assert window.extra["positions"] == []

    def test_accounts_only_skips_the_positions_call(self, store, monkeypatch):
        monkeypatch.setattr(svc, "fetch_account", lambda base, k, s: dict(_ACCOUNT))

        def _boom(base, k, s):
            raise AssertionError("fetch_positions must not be called when accounts_only=True")

        monkeypatch.setattr(svc, "fetch_positions", _boom)

        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None, accounts_only=True)
        assert window.extra["positions"] == []
        assert len(window.accounts) == 1

    def test_account_filter_mismatch_returns_nothing(self, store, monkeypatch):
        monkeypatch.setattr(svc, "fetch_account", lambda base, k, s: dict(_ACCOUNT))

        def _boom(base, k, s):
            raise AssertionError("fetch_positions must not be called for a mismatched account")

        monkeypatch.setattr(svc, "fetch_positions", _boom)

        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", "SOME-OTHER-ACCOUNT")
        assert window.accounts == []
        assert window.extra["positions"] == []

    def test_account_filter_match_returns_positions(self, store, monkeypatch):
        monkeypatch.setattr(svc, "fetch_account", lambda base, k, s: dict(_ACCOUNT))
        monkeypatch.setattr(
            svc, "fetch_positions", lambda base, k, s: [dict(p) for p in _POSITIONS]
        )
        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", "ALP-001")
        assert len(window.accounts) == 1
        assert len(window.extra["positions"]) == 2


# ── classify_error ────────────────────────────────────────────────────────────────────


class TestClassifyError:
    def test_401_is_auth_error(self):
        assert svc.classify_error(_http_status_error(401)) == "auth_error"

    def test_403_is_auth_error(self):
        assert svc.classify_error(_http_status_error(403)) == "auth_error"

    def test_429_is_rate_limited(self):
        assert svc.classify_error(_http_status_error(429)) == "rate_limited"

    def test_other_http_status_is_connect_error(self):
        assert svc.classify_error(_http_status_error(500)) == "connect_error"

    def test_transport_error_is_connect_error(self):
        assert svc.classify_error(httpx.ConnectError("refused")) == "connect_error"

    def test_unknown_exception_is_generic_error(self):
        assert svc.classify_error(RuntimeError("unexpected")) == "error"

    def test_external_service_error_401_is_auth_error(self):
        from backend.core.errors import ExternalServiceError

        assert svc.classify_error(ExternalServiceError("Alpaca HTTP 401")) == "auth_error"

    def test_external_service_error_429_is_rate_limited(self):
        from backend.core.errors import ExternalServiceError

        assert svc.classify_error(ExternalServiceError("Alpaca HTTP 429")) == "rate_limited"

    def test_external_service_error_other_is_connect_error(self):
        from backend.core.errors import ExternalServiceError

        assert svc.classify_error(ExternalServiceError("Alpaca HTTP 500")) == "connect_error"


class TestSafeGet:
    """S-F1 consistency wrap: fetch_account/fetch_positions never let a raw httpx
    exception escape, even though neither URL carries a secret."""

    def test_fetch_account_wraps_an_http_status_error(self, monkeypatch):
        from backend.core.errors import ExternalServiceError

        def boom(url, headers=None, timeout=None):
            request = httpx.Request("GET", url)
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401", request=request, response=response)

        monkeypatch.setattr(svc.httpx, "get", boom)
        with pytest.raises(ExternalServiceError, match="401"):
            svc.fetch_account("https://paper-api.alpaca.markets", "k", "s")

    def test_fetch_positions_wraps_a_transport_error(self, monkeypatch):
        from backend.core.errors import ExternalServiceError

        monkeypatch.setattr(
            svc.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused"))
        )
        with pytest.raises(ExternalServiceError):
            svc.fetch_positions("https://paper-api.alpaca.markets", "k", "s")


def test_order_wire_calls_are_gone():
    """The removal is the contract: this module must expose no order-placing entrypoint, so
    the capability cannot be reached by an import even if a caller tries."""
    for gone in ("place_order", "cancel_order", "submit_alpaca_order", "cancel_alpaca_order"):
        assert not hasattr(svc, gone)
