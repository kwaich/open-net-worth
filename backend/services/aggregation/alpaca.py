"""Alpaca brokerage provider — the first cloud-API BROKERAGE data source (alpaca-connector
RFC; converted to a read-only data source by RFC ``connectors-as-data-sources``).

Alpaca (https://alpaca.markets/) is a clean cloud REST broker: an ``APCA-API-KEY-ID`` +
``APCA-API-SECRET-KEY`` header pair, two base URLs (paper / live, derived from
``environment`` by ``AlpacaConfigStore.base_url``), and a positions/account API.

This module is the PURE protocol + normalize (``fetch_window``) — READ-ONLY, called from a
Lucius turn via ``alpaca_fetch_positions``. There is no adapter class and no scheduled sync
any more: Alpaca is a positions+cash SNAPSHOT (``lookback_days=0`` — there is no date window
to speak of), so ``fetch_window`` always returns the CURRENT state; the caller decides
whether/how to apply it (``add_positions``/``update_position``, never a write this module
performs). Positions ride ``FetchWindow.extra["positions"]`` (the RFC §A shape:
``{symbol, instrument_hint, qty, avg_cost, market_value, currency, provider_account_id}``) —
never ``rows``, which is reserved for transaction drafts (Alpaca has none); cash/
buying_power ride ``FetchWindow.extra`` too, applied to the config store by
``connector_fetch.fetch()`` via a plain ``store.update()`` (see ``providers.FetchWindow``'s
docstring for why that's generic and no-op-safe for every other provider).

The S2 TRADING slice (``place_order``/``cancel_order``/``submit_alpaca_order``/
``cancel_alpaca_order`` and the propose/apply order tools) was REMOVED 2026-07-19 — it booked
nothing locally (a fill was only ever noticed by the next read sync), so it bought no
integration over Alpaca's own MCP server, which the external-MCP path already routes with
forced confirmation on money movement. See
``docs/adr/2026-07-19-remove-alpaca-trading-half.md``. Do NOT re-add a wire call to
``/v2/orders`` here.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. No monetary figure is fabricated here — positions are valued
downstream by the existing price path, never priced in this module.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from backend.core.errors import ExternalServiceError
from backend.db.alpaca_config_store import AlpacaConfigStore
from backend.services.aggregation.providers import FetchWindow

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0
SOURCE_ID = "alpaca"
EXTERNAL_SOURCE = "alpaca"

# Alpaca asset_class → our economic-exposure ASSET_CLASSES (RFC §2.2). Unknown → equity.
_ASSET_CLASS_MAP = {
    "us_equity": "equity",
    "equity": "equity",
    "crypto": "crypto",
}


def _headers(key_id: str, secret: str) -> dict:
    return {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}


# ── protocol (pure I/O) ───────────────────────────────────────────────────────────────


def _safe_get(base_url: str, key_id: str, secret: str, path: str) -> httpx.Response:
    """GET wrapper for the Alpaca REST API that NEVER lets a raw exception escape (mirrors
    ``lunch_money._safe_get``/``ynab._safe_get``). Neither ``/v2/account`` nor
    ``/v2/positions`` carries a secret in its URL — this exists for CONSISTENCY with every
    sibling provider's own wrap (security review S-F1) rather than a leak this one
    specifically had, so a future endpoint added here doesn't have to remember to wrap
    itself. Raises ``ExternalServiceError`` (status code / exception class only) on a
    transport/HTTP failure OR any other unexpected exception raised while building/sending
    the request."""
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}{path}",
            headers=_headers(key_id, secret),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ExternalServiceError(f"Alpaca HTTP {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"Alpaca request failed ({type(exc).__name__})") from None
    except Exception as exc:
        raise ExternalServiceError(f"Alpaca request failed ({type(exc).__name__})") from None
    return resp


def fetch_account(base_url: str, key_id: str, secret: str) -> dict:
    """GET ``/v2/account`` — ``{account_number, cash, buying_power, portfolio_value, ...}``.
    Raises ``ExternalServiceError`` (never the raw httpx exception — see ``_safe_get``) on
    a transport/HTTP failure."""
    data = _safe_get(base_url, key_id, secret, "/v2/account").json()
    return data if isinstance(data, dict) else {}


def fetch_positions(base_url: str, key_id: str, secret: str) -> list[dict]:
    """GET ``/v2/positions`` — a list of net open positions. Raises
    ``ExternalServiceError`` (never the raw httpx exception — see ``_safe_get``)."""
    data = _safe_get(base_url, key_id, secret, "/v2/positions").json()
    return data if isinstance(data, list) else []


# ── normalize + fetch_window ─────────────────────────────────────────────────────────


def _instrument_hint(ap_pos: dict) -> str:
    """The economic-exposure asset class this position maps to (RFC §2.2) — a hint for the
    caller's ``resolve_instruments``/``add_positions`` call, never written here."""
    ap_class = str(ap_pos.get("asset_class") or "").lower()
    return _ASSET_CLASS_MAP.get(ap_class, "equity")


def _position_to_common(ap_pos: dict, account_number: str) -> Optional[dict]:
    """One Alpaca position → the RFC §A broker-position shape. Returns None for a
    symbol-less row (nothing to resolve an instrument against)."""
    symbol = str(ap_pos.get("symbol") or "").upper().strip()
    if not symbol:
        return None
    try:
        qty = float(ap_pos.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    try:
        avg_cost = float(ap_pos.get("avg_entry_price"))
    except (TypeError, ValueError):
        avg_cost = None
    try:
        market_value = float(ap_pos.get("market_value"))
    except (TypeError, ValueError):
        market_value = None
    return {
        "symbol": symbol,
        "instrument_hint": _instrument_hint(ap_pos),
        "qty": qty,
        "avg_cost": avg_cost,
        "market_value": market_value,
        "currency": "USD",  # Alpaca is USD-only today
        "provider_account_id": account_number,
    }


def fetch_window(
    store: AlpacaConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """Alpaca is a positions+cash SNAPSHOT (RFC §B) — ``since``/``before`` are accepted for
    interface parity with every other provider but ignored (there is no date window; the
    dormant gate and ``lookback_days=0`` already tell the caller this). Always
    ``complete=True``: a snapshot has no pagination to truncate."""
    key_id, secret = store.keys()
    base = store.base_url()
    account_data = fetch_account(base, key_id, secret)
    account_number = str(account_data.get("account_number") or "alpaca")
    accounts = [
        {
            "provider_account_id": account_number,
            "name": "Alpaca",
            "mask": None,
            "currency": "USD",
            "balance": account_data.get("portfolio_value"),
        }
    ]
    if account and account != account_number:
        accounts = []

    positions: list[dict] = []
    if not accounts_only and (not account or account == account_number):
        for ap_pos in fetch_positions(base, key_id, secret):
            common = _position_to_common(ap_pos, account_number)
            if common is not None:
                positions.append(common)

    return FetchWindow(
        accounts=accounts,
        rows=[],
        complete=True,
        extra={
            "positions": positions,
            "cash": account_data.get("cash"),
            "buying_power": account_data.get("buying_power"),
        },
    )


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for Alpaca. ``_safe_get`` wraps every
    ``fetch_account``/``fetch_positions`` transport/HTTP failure into
    ``ExternalServiceError`` carrying only the HTTP status code; re-derive the class from
    that safe message the way ``simplefin.py``/``ynab.py`` do."""
    if isinstance(exc, ExternalServiceError):
        msg = str(exc).lower()
        if "http 401" in msg or "http 403" in msg:
            return "auth_error"  # bad/expired keys, or paper keys against live
        if "http 429" in msg:
            return "rate_limited"  # Alpaca ~200 req/min — a snapshot is 2-3 calls
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "auth_error"
        if code == 429:
            return "rate_limited"
        return "connect_error"
    if isinstance(exc, httpx.HTTPError):
        return "connect_error"
    return "error"
