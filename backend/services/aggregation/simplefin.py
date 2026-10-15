"""SimpleFIN aggregation provider — the FIRST real bank/card account-aggregation data
source.

SimpleFIN (https://www.simplefin.org/) is a cheap (~$15/yr), OAuth-free, US/Canada
bank-sync protocol: a one-time **setup token** is claimed for a long-lived **access URL**
(HTTP-basic creds embedded), and `GET {access_url}/accounts` returns accounts + their
transactions as JSON, windowed by `start-date`/`end-date` (unix seconds) and optionally
filtered by `account` — all server-side, no client-side windowing needed.

This module is the PURE protocol (``claim_setup_token`` / ``fetch_accounts``) + normalize
(``fetch_window``) — READ-ONLY, called from a Lucius turn via
``simplefin_fetch_transactions`` (RFC ``connectors-as-data-sources``). There is no adapter
class, no auto-created account, and no scheduled sync any more: the provider-account →
app-account link lives on ``Account.external_ids``, and booking happens through
``add_transactions`` in the SAME turn that fetched, never here.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. All money math goes through ``ledger_service`` (the base figure is
recomputed from the native leg there, never fabricated here).
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import httpx

from backend.core.errors import ExternalServiceError, ValidationError
from backend.core.net_guard import UnsafeUrlError, assert_public_url
from backend.db.simplefin_config_store import SimpleFinConfigStore
from backend.services import ledger as ledger_service
from backend.services.aggregation.providers import FetchWindow, derived_ref

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0
# SimpleFIN "type" → an Open Net Worth account type/kind. Most SimpleFIN orgs are banks /
# cards; we default to a bank-style asset and let the user re-classify in the UI. A
# negative balance on a card/loan account stays raw-negative (the net-worth invariant).
_DEFAULT_ACCOUNT_TYPE = "cash"
# Hard cap on a SimpleFIN response body we will read into memory (DoS guard — a hostile
# or runaway endpoint cannot make us buffer an unbounded response). SimpleFIN /accounts
# feeds are small JSON documents (accounts + a rolling window of transactions), so 8 MiB
# is generous — orders of magnitude above any realistic feed, well below ibkr.py's 32 MiB
# (a Flex activity statement can legitimately be much bigger).
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

SOURCE_ID = "simplefin"
EXTERNAL_SOURCE = "simplefin"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── protocol ───────────────────────────────────────────────────────────────────────


def _scrub(text: str, access_url: str) -> str:
    """Strip the SimpleFIN access URL — and the HTTP-basic credential riding in its netloc
    — out of any text reaching ``last_error``/logs. Belt-and-braces alongside ``_safe_get``/
    ``claim_setup_token``'s own sanitization: even if a credential-bearing string sneaks into
    a message some other way (a future bug, a third-party library), we never persist or log
    it verbatim. Mirrors ``ibkr._scrub``."""
    if not text or not access_url:
        return text
    text = text.replace(access_url, "***")
    parts = urlsplit(access_url)
    if parts.username:
        userinfo = parts.username + (f":{parts.password}" if parts.password else "")
        text = text.replace(userinfo, "***")
    return text


def _safe_get(url: str, params: dict) -> httpx.Response:
    """GET wrapper for the SimpleFIN endpoints that NEVER lets a raw httpx exception escape.

    ``httpx`` puts the full request URL into the string form of ``HTTPStatusError`` /
    request errors — and unlike IBKR's token-in-query-param, SimpleFIN's HTTP-basic
    credentials live IN the URL's netloc (``https://user:pass@host/...``), so
    ``repr()``/``str()`` of the URL never masks them (only ``httpx.URL.__repr__`` does — the
    exception string does not). The ingestion orchestrator stores ``str(exc)[:300]`` into
    ``last_error`` (surfaced by the ungated ``get_simplefin_status``/bulk ``get_connector_status``
    AI tools) and logs it, so an unsanitized exception hands the model the bank credential. We
    catch every httpx error here and re-raise ``ExternalServiceError`` carrying ONLY the HTTP
    status code / exception class name — no URL, no credential. Mirrors ``ibkr._safe_get``.

    Also bounds the response body at ``_MAX_RESPONSE_BYTES`` (DoS guard) before returning.
    """
    try:
        resp = httpx.get(url, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ExternalServiceError(f"SimpleFIN HTTP {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"SimpleFIN request failed ({type(exc).__name__})") from None
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise ExternalServiceError(
            f"SimpleFIN response too large (> {_MAX_RESPONSE_BYTES} bytes); refused."
        )
    return resp


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-time SimpleFIN **setup token** for a long-lived **access URL**.

    The setup token is base64(claim_url). POST (empty body) to the claim URL; the 200
    response body IS the access URL (HTTP-basic creds embedded). The decoded claim URL is
    validated against ``core.net_guard.assert_public_url`` BEFORE any request is made — a
    setup token is often pasted in from outside the app (support docs, a forum post), so its
    claim URL is attacker-influenceable the same way a malicious ``access_url`` is (see
    ``fetch_accounts``). Raises ``ValidationError`` on a malformed token, an unsafe claim URL,
    or a failed/duplicate claim (setup tokens are single-use); ``ExternalServiceError``
    (sanitized — no URL, no credential) on a transport failure.
    """
    token = (setup_token or "").strip()
    if not token:
        raise ValidationError("setup token is empty")
    try:
        claim_url = base64.b64decode(token, validate=True).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationError("setup token is not valid base64") from exc
    if not claim_url.lower().startswith(("http://", "https://")):
        raise ValidationError("setup token does not decode to a claim URL")
    try:
        claim_url = assert_public_url(claim_url)
    except UnsafeUrlError as exc:
        raise ValidationError(f"claim URL failed the network safety check: {exc}") from exc

    try:
        resp = httpx.post(claim_url, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ExternalServiceError(
            f"could not reach the SimpleFIN claim URL ({type(exc).__name__})"
        ) from None
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise ExternalServiceError(
            f"SimpleFIN claim response too large (> {_MAX_RESPONSE_BYTES} bytes); refused."
        )
    if resp.status_code != 200:
        # 403 is the classic "already claimed" (setup tokens are single-use).
        hint = " (already claimed? setup tokens are one-time)" if resp.status_code == 403 else ""
        raise ValidationError(f"claiming the setup token failed: HTTP {resp.status_code}{hint}")

    access_url = (resp.text or "").strip()
    if not access_url.lower().startswith(("http://", "https://")):
        raise ValidationError("claim did not return a valid access URL")
    return access_url


def fetch_accounts(
    access_url: str,
    start_date: int | None = None,
    end_date: int | None = None,
    account: str | None = None,
) -> dict:
    """GET the SimpleFIN ``/accounts`` feed. The access URL carries HTTP-basic creds; we
    append ``/accounts`` and the optional ``start-date``/``end-date`` (unix seconds) +
    ``account`` window/filter params — all server-side, per SimpleFIN's own protocol.
    ``access_url`` is whatever the claim response body said (see ``claim_setup_token``) —
    fully attacker-influenceable if the user was socially engineered into pasting a
    malicious setup token — so it is validated against ``core.net_guard.assert_public_url``
    before every fetch, not just once at claim time (this can run on every Lucius turn that
    calls the fetch tool). Returns the parsed JSON ``{errors:[...], accounts:[...]}``.
    Raises ``ValidationError`` on an unsafe access URL, ``ExternalServiceError``
    (sanitized — no URL, no credential) on a transport/HTTP failure — never silently
    fabricates data."""
    base = access_url.rstrip("/")
    url = f"{base}/accounts"
    try:
        url = assert_public_url(url)
    except UnsafeUrlError as exc:
        raise ValidationError(
            f"SimpleFIN access URL failed the network safety check: {exc}"
        ) from exc
    params: dict = {}
    if start_date is not None:
        params["start-date"] = int(start_date)
    if end_date is not None:
        params["end-date"] = int(end_date)
    if account:
        params["account"] = account
    resp = _safe_get(url, params)
    return resp.json()


# ── normalize + fetch_window ─────────────────────────────────────────────────────────


def _account_to_common(sf_account: dict) -> dict:
    """A SimpleFIN account → the common ``accounts[]`` shape (RFC §A)."""
    org = sf_account.get("org") or {}
    org_name = org.get("name") or org.get("domain") or ""
    name = sf_account.get("name") or "SimpleFIN account"
    display = f"{org_name} · {name}".strip(" ·") if org_name else name
    try:
        balance = float(sf_account.get("balance"))
    except (TypeError, ValueError):
        balance = None
    return {
        "provider_account_id": str(sf_account.get("id") or ""),
        "name": display,
        "mask": None,
        "currency": (sf_account.get("currency") or "").upper() or None,
        "balance": balance,
    }


def _txn_to_row(sf_txn: dict, sf_account_id: str, currency: str) -> dict | None:
    """One SimpleFIN transaction → the common normalized row shape (RFC §A step 3).

    SimpleFIN ``amount`` is a signed decimal STRING: positive = money in, negative = money
    out — already this app's own convention, no sign flip needed. Returns None only for an
    unparseable/zero amount (never for a missing id — SimpleFIN's own ``id`` is required by
    its schema, but a defensive derived ref still covers it)."""
    try:
        amount = float(sf_txn.get("amount"))
    except (TypeError, ValueError):
        return None
    if amount == 0:
        return None

    posted = sf_txn.get("posted")
    try:
        date_str = datetime.fromtimestamp(int(posted), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        date_str = datetime.now(timezone.utc).date().isoformat()

    description = (sf_txn.get("description") or sf_txn.get("payee") or "").strip() or "SimpleFIN"
    txn_id = str(sf_txn.get("id") or "").strip()
    ref = (
        f"{sf_account_id}:{txn_id}"
        if txn_id
        else derived_ref(sf_account_id, date_str, amount, currency, description)
    )
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": sf_txn,
        "provider_account_id": sf_account_id,
        "date": date_str,
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": None,
        "pending": bool(sf_txn.get("pending")),
        "supersedes_ref": None,
    }


def fetch_window(
    store: SimpleFinConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """SimpleFIN's provider-side windowed fetch (RFC §B) — ``start-date``/``end-date``/
    ``account`` are all server-side SimpleFIN params, so the window and account filter
    apply BEFORE the data ever reaches us. Always ``complete=True``: SimpleFIN's
    ``/accounts`` feed is not paginated."""
    access_url = store.access_url()
    start_ts = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.fromisoformat(before).replace(tzinfo=timezone.utc).timestamp())
    if accounts_only:
        end_ts = start_ts  # zero-width window — accounts still come back in full
    try:
        feed = fetch_accounts(access_url, start_date=start_ts, end_date=end_ts, account=account)
    except Exception as exc:  # noqa: BLE001 — belt-and-braces scrub, see _scrub's docstring
        raise type(exc)(_scrub(str(exc), access_url)) from exc
    sf_accounts = feed.get("accounts") or []
    accounts = [_account_to_common(a) for a in sf_accounts]
    rows: list[dict] = []
    if not accounts_only:
        for sf_account in sf_accounts:
            sf_account_id = str(sf_account.get("id") or "")
            currency = (sf_account.get("currency") or ledger_service.base_currency()).upper()
            for sf_txn in sf_account.get("transactions") or []:
                row = _txn_to_row(sf_txn, sf_account_id, currency)
                if row is not None:
                    rows.append(row)
    return FetchWindow(accounts=accounts, rows=rows, complete=True)


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for SimpleFIN. The real fetch path raises the sanitized
    ``ExternalServiceError`` from ``_safe_get`` (no url/credential — see its docstring);
    re-derive the class from that safe message. The raw ``httpx.*`` branches below stay for
    callers/tests that raise those directly."""
    if isinstance(exc, ExternalServiceError):
        msg = str(exc).lower()
        if "http 401" in msg or "http 403" in msg:
            return "auth_error"
        if "http 429" in msg:
            return "rate_limited"
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "auth_error" if exc.response.status_code in (401, 403) else "connect_error"
    if isinstance(exc, httpx.HTTPError):
        return "connect_error"
    return "error"
