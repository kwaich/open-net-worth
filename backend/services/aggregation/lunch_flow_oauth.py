"""Lunch Flow OAuth orchestration — begin / complete / refresh (the bank-linking half of the
Lunch Flow connector).

Lunch Flow (https://lunchflow.app/docs/api/platform-api-overview) is a Plaid/Enable-Banking-
shaped aggregation PLATFORM: the owner registers a developer app (client_id/client_secret,
Basic Auth, configured on ``LunchFlowConfigStore`` — mirrors how Plaid's own client_id/secret
are configured today) and the END USER (here: the one self-hosting operator) is redirected to
Lunch Flow's OWN hosted login+bank-link page. This is a genuinely different shape from the
MCP OAuth flow this app already built (``backend/mcp/oauth.py``/``oauth_flow.py``,
RFC ``2026-08-14-mcp-oauth``) — no Dynamic Client Registration, no PKCE, no per-server
resource-metadata discovery (``WWW-Authenticate``) — it is a plain OAuth 2.0
authorization-code redirect against ONE fixed, already-known authorization server. This
module deliberately does NOT reuse ``mcp/oauth.py``'s protocol functions (wrong shape — DCR/
PKCE machinery that has nothing to bind here); it reuses the SHAPE the MCP OAuth work
established for an OAuth-redirect flow in this app (a server-minted single-use TTL-bounded
``state`` staged in an in-memory map, an auth-exempt callback route, a popup+poll frontend) —
see the connector's own ADR for the full comparison.

Flow (Lunch Flow's own docs, verified live):

  1. ``begin()`` — builds the authorize URL: ``GET https://lunchflow.app/api/platform/oauth/
     authorize?client_id=&redirect_uri=&email=&state=``. The user (self-hosting operator)
     opens it, signs in / links a bank at Lunch Flow's own hosted UI, and is redirected back
     to our callback with ``?code=&state=`` (or ``?error=``).
  2. ``complete()`` — the callback route calls this with the returned ``code``/``state``.
     Pops the staged flow (single-use), then exchanges the code:
     ``POST /api/platform/oauth/token {grant_type: "authorization_code", code, redirect_uri,
     client_id, client_secret}`` → ``{access_token, refresh_token, token_type, expires_in,
     user_id}``. Persisted on ``LunchFlowConfigStore``.
  3. ``ensure_fresh_token()`` — Lunch Flow tokens expire after 1h (documented); called before
     every sync (proactive, from ``LunchFlowAdapter.fetch``) and once more on a live 401
     (reactive, ``force=True`` — ``LunchFlowAdapter._reactive_401_retry`` in
     ``lunch_flow.py``, one retry, never a loop, mirroring ``mcp/client.py``'s
     ``_rpc``/``_OAuthUnauthorized``). Refreshes via ``grant_type=refresh_token``,
     compare-and-swap written (``LunchFlowConfigStore.apply_token_refresh``) so two
     concurrent refreshes land exactly one winning network call + write — see the ADR for why
     this app's single-operator usage pattern still makes that race worth guarding against.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math (none here — this module never touches the ledger) stays
out; the sync/mapping half lives in ``services/aggregation/lunch_flow.py``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional
from urllib.parse import urlencode, urlsplit

import httpx

from backend.core.errors import ValidationError
from backend.db.lunch_flow_config_store import LunchFlowConfigStore
from backend.services.aggregation import lunch_flow_pending

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://lunchflow.app/api/platform/oauth/authorize"
TOKEN_URL = "https://lunchflow.app/api/platform/oauth/token"
_CALLBACK_PATH = "/api/lunch-flow/oauth/callback"
_HTTP_TIMEOUT = 30.0

# Refresh when a KNOWN expiry is within this many seconds (mirrors the MCP OAuth flow's own
# REFRESH_SKEW_SECONDS). Lunch Flow always documents `expires_in` on both grants, so — unlike
# the MCP flow — there is no "unknown expiry" branch to handle here.
REFRESH_SKEW_SECONDS = 60


def _is_loopback_host(host: str) -> bool:
    lowered = (host or "").lower().rstrip(".")
    if lowered in {"127.0.0.1", "::1", "localhost"} or lowered.endswith(".localhost"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _redirect_uri(request_base_url: str) -> Optional[str]:
    """The ONE redirect URI for this flow, derived from the browser-detected base URL
    (``api/shortcuts.detect_base_url``) — same reasoning as the MCP OAuth flow's own
    ``_redirect_uris``: works whether this instance is reached via localhost or a remote/
    tunneled address. ``None`` when the derived base is neither https nor loopback — an OAuth
    redirect over plaintext to a non-loopback host is a token-interception risk. Unlike the
    MCP flow, there is no DCR registration step to also list a loopback fallback against —
    Lunch Flow's ``redirect_uri`` is just a per-request parameter, so ONE URI is all a call
    ever needs."""
    base = (request_base_url or "").strip().rstrip("/")
    parts = urlsplit(base)
    host = parts.hostname or ""
    if not host:
        return None
    if parts.scheme.lower() != "https" and not _is_loopback_host(host):
        return None
    return f"{base}{_CALLBACK_PATH}"


def _fail(error: str) -> dict:
    return {"ok": False, "error": error}


def begin(*, request_base_url: str) -> dict:
    """Start a sign-in. Returns ``{ok: True, authorize_url, flow_id, expires_in}`` on success,
    or ``{ok: False, error}`` on any failure — never raises, matching the MCP OAuth flow's
    "the request succeeded, the vendor is what didn't answer" convention."""
    store = LunchFlowConfigStore()
    if not store.has_credentials():
        return _fail("Add your Lunch Flow client ID and client secret first.")

    redirect_uri = _redirect_uri(request_base_url)
    if redirect_uri is None:
        return _fail(
            "Sign-in needs a secure address. Reach this instance over HTTPS, or from the "
            "machine it runs on."
        )

    state = lunch_flow_pending.stage(redirect_uri=redirect_uri)
    params = {"client_id": store.client_id(), "redirect_uri": redirect_uri, "state": state}
    email = store.email()
    if email:
        params["email"] = email
    authorize_url = f"{AUTHORIZE_URL}?{urlencode(params)}"
    return {
        "ok": True,
        "authorize_url": authorize_url,
        "flow_id": state,
        "expires_in": lunch_flow_pending.TTL_SECONDS,
    }


def _post_token(payload: dict) -> dict:
    """POST the token endpoint. Raises ``ValidationError`` on a non-200 / transport failure.

    Form-urlencoded body (``data=``, not ``json=``) — RFC 6749 §4.1.3/§6 REQUIRES
    ``application/x-www-form-urlencoded`` for a token-endpoint request, and Lunch Flow's own
    docs never showed a worked token-exchange example to check against (re-verified during
    the 2026-08-17 security review: a page claiming to quote one turned out to be fabricated
    by the fetch tool's own summarizer — asking the SAME page for its content verbatim showed
    no such example at all). Genuine ambiguity resolves to the RFC-conformant default, not the
    JSON shape this previously guessed — see the connector's own ADR's security-review
    addendum for the full trail."""
    try:
        resp = httpx.post(TOKEN_URL, data=payload, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ValidationError(f"could not reach Lunch Flow: {exc}") from exc
    if resp.status_code != 200:
        detail = ""
        try:
            data = resp.json()
            detail = data.get("message") or data.get("error") or ""
        except Exception:
            detail = ""
        raise ValidationError(
            f"Lunch Flow token exchange failed: HTTP {resp.status_code}"
            + (f" — {detail}" if detail else "")
        )
    return resp.json() or {}


def complete(*, state: str, code: Optional[str], error: Optional[str] = None) -> dict:
    """The callback logic. Pops the staged flow by ``state`` (single-use). Returns
    ``{"ok": bool}`` — the callback route renders the SAME generic page regardless of which
    branch fired, so the reason is recorded to the status map for the poll, never echoed in
    this return value's shape (mirrors ``mcp/oauth_flow.py::complete``)."""
    flow = lunch_flow_pending.pop(state)
    if flow is None:
        return {"ok": False}

    if error:
        lunch_flow_pending.record_status(
            state, "error", "Lunch Flow declined or cancelled the sign-in."
        )
        return {"ok": False}
    if not code:
        lunch_flow_pending.record_status(
            state, "error", "The sign-in did not return an authorization code."
        )
        return {"ok": False}

    store = LunchFlowConfigStore()
    client_id = store.client_id()
    client_secret = store.client_secret()
    if not client_id or not client_secret:
        lunch_flow_pending.record_status(
            state, "error", "Lunch Flow credentials are no longer configured."
        )
        return {"ok": False}

    try:
        token = _post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": flow.redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )
    except ValidationError as exc:
        lunch_flow_pending.record_status(state, "error", str(exc))
        return {"ok": False}

    access_token = (token.get("access_token") or "").strip()
    if not access_token:
        lunch_flow_pending.record_status(
            state, "error", "Lunch Flow did not return an access token."
        )
        return {"ok": False}

    expires_in = token.get("expires_in")
    now = time.time()
    store.set_tokens(
        access_token=access_token,
        refresh_token=(token.get("refresh_token") or "").strip(),
        expires_at=(now + float(expires_in)) if isinstance(expires_in, (int, float)) else None,
        user_id=str(token.get("user_id") or "").strip(),
    )
    lunch_flow_pending.record_status(state, "ok", None)
    return {"ok": True}


def _is_stale(store: LunchFlowConfigStore, *, now: float) -> bool:
    expires_at = store.token_expires_at()
    if expires_at is None:
        # Lunch Flow's docs consistently document `expires_in` on both grants; an absent
        # value here means we never successfully completed an exchange — nothing to refresh.
        return False
    return expires_at <= now + REFRESH_SKEW_SECONDS


# ONE process-wide lock (not per-server like the MCP flow's `_refresh_lock` registry) — this
# app holds exactly one Lunch Flow config, never several, so there is only ever one thing to
# serialize refreshes against.
_refresh_lock = threading.Lock()


def ensure_fresh_token(*, force: bool = False) -> str:
    """Return a valid ``access_token``, refreshing first if it is stale (or ``force=True``,
    the reactive 401 path). Raises ``ValidationError`` when there is nothing to refresh with
    (not linked, or the refresh itself failed) — the caller (the sync adapter) turns that into
    an ``auth_error`` status.

    Two phases, same reasoning as ``mcp/oauth_flow.py::ensure_fresh_token``: an UNLOCKED read
    decides whether a refresh is even still needed, then the lock serializes the network call
    itself so two truly concurrent callers make exactly one HTTP request (the loser blocks,
    then re-reads and finds the winner's token already fresh). The compare-and-swap write
    (``LunchFlowConfigStore.apply_token_refresh``) is the second, DB-level layer — it refuses
    to overwrite if some OTHER writer already changed the row while the network call was in
    flight, so a reconnect racing a refresh can never be clobbered by a stale write landing
    after it.
    """
    store = LunchFlowConfigStore()
    if not store.has_link():
        raise ValidationError("Lunch Flow is not linked — connect a bank first.")
    if not force and not _is_stale(store, now=time.time()):
        return store.access_token()

    with _refresh_lock:
        current = LunchFlowConfigStore()
        stale_access_token = current.access_token()
        if not force and not _is_stale(current, now=time.time()):
            return stale_access_token  # someone else already refreshed it
        refresh_token = current.refresh_token()
        if not refresh_token:
            raise ValidationError("Lunch Flow sign-in expired — reconnect the bank in Connectors.")

        token = _post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": current.client_id(),
                "client_secret": current.client_secret(),
            }
        )
        access_token = (token.get("access_token") or "").strip()
        if not access_token:
            raise ValidationError("Lunch Flow did not return a refreshed access token.")

        expires_in = token.get("expires_in")
        now = time.time()
        updated = current.apply_token_refresh(
            expected_access_token=stale_access_token,
            access_token=access_token,
            refresh_token=(token.get("refresh_token") or "").strip() or refresh_token,
            token_expires_at=(now + float(expires_in))
            if isinstance(expires_in, (int, float))
            else None,
        )
        return updated.get("access_token") or access_token
