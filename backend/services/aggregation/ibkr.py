"""IBKR (Interactive Brokers) Flex Web Service provider — S1 read-only data source
(converted by RFC ``connectors-as-data-sources``).

Interactive Brokers provides batch position + trade data via the **Flex Web Service**
(https://www.interactivebrokers.com/en/software/reportguide/reportguide.htm#reportguide/
activity_flex_queries.htm).  The protocol is a two-step HTTP dance:

  1. ``SendRequest`` — POST with ``t=<flex_token>&q=<flex_query_id>&v=3`` → returns
     ``<Status> ReferenceCode=<code>``; the report is queued server-side.
  2. ``GetStatement`` — GET ``?t=<token>&q=<code>&v=3`` → poll until a real XML
     statement arrives (the first few calls often return "Statement generation in
     progress").

The Flex XML statement contains three sections of interest:

  * ``<OpenPositions>`` — one ``<OpenPosition>`` element per net open position. Normalized
    into the RFC broker-position shape; equities/ETFs get an ``instrument_hint`` of
    ``"equity"``/``"crypto"`` (yfinance-priceable), options/futures get ``"manual"`` (no
    live price source) — never written here.
  * ``<Trades>`` — per-execution fills. Normalized into common transaction DRAFT rows.
  * ``<CashTransactions>`` — dividends, interest, fees. Also normalized into draft rows.

This is a HYBRID provider: ``fetch_window`` returns positions via ``FetchWindow.extra
["positions"]`` (RFC §A broker-position shape — the caller applies them via
``add_positions``/``update_position``, never a write this module performs) AND trade/cash
events as normalized transaction drafts in ``FetchWindow.rows``, exactly like a bank
provider's rows.

``external_ref = "<ibkr_account_NUMBER>:<id>"`` — keyed on the STABLE IBKR account number
from the Flex statement, never a generated app account id. The Flex query's own reporting
period is set on IBKR's side (Account Management → Flex Queries); ``fetch_window`` applies
the caller's ``[since, before]`` window CLIENT-SIDE over whatever the statement returns
(RFC §B) — there is no per-call date param this protocol accepts.

This connector is READ-ONLY, and the Flex Web Service cannot place orders even in
principle — the only wire calls here are ``SendRequest``/``GetStatement``, a reporting
API. The dormant ``trading_enabled`` flag was removed 2026-07-20
(``docs/adr/2026-07-20-remove-ibkr-dead-trading-flag.md``). Do NOT add an order-placement
wire call to this module; broker trading goes through an external MCP server, which
already forces propose→confirm on money movement.

There is no adapter class and no scheduled sync any more: booking a trade/cash-event draft
happens through ``add_transactions`` (or a position write through the position tools) in
the SAME Lucius turn that called ``ibkr_fetch_activity``, never here.

Import rule (import-linter): a service may import ``db``/``core``/sibling services only —
never ``api``/``ai``. No monetary figure is fabricated here — positions are valued
downstream by the existing price path.
"""

from __future__ import annotations

import logging
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import httpx

from backend.core.errors import ExternalServiceError
from backend.db.ibkr_config_store import IbkrConfigStore
from backend.services.aggregation.providers import FetchWindow

logger = logging.getLogger(__name__)

SOURCE_ID = "ibkr"
EXTERNAL_SOURCE = "ibkr"

_HTTP_TIMEOUT = 30.0
# Maximum poll iterations before we give up waiting for the Flex statement.
_MAX_POLL_ATTEMPTS = 10
# Delay between poll attempts (IBKR suggests waiting a few seconds).
_POLL_DELAY_SECONDS = 3.0
# Hard cap on a Flex statement body we will read into memory (DoS guard — a hostile
# or runaway endpoint cannot make us buffer an unbounded response). 32 MiB is well
# above any realistic activity statement.
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
# XML constructs that enable entity-expansion ("billion laughs") / external-entity
# attacks. We refuse any body containing these BEFORE parsing — stdlib ElementTree
# does not expand them by default in modern CPython, but we reject defensively so a
# future / divergent parser can never be coaxed into expanding them.
_XML_BOMB_MARKERS = ("<!DOCTYPE", "<!ENTITY")
_ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")

# IBKR asset categories → our economic-exposure ASSET_CLASSES.
# IBKR uses STK/ETF/OPT/FUT/FOP/CASH/BOND/FUND etc.
_ASSET_CLASS_MAP = {
    "STK": "equity",
    "ETF": "equity",
    "BOND": "bond",
    "CASH": "cash",
    "FUND": "equity",  # mutual fund — equity exposure
    "CRYPTO": "crypto",
}
# Option/futures → manual-priced (OPT/FUT/FOP/WAR/CFD stay as equity by default with
# manual_price set so the UI shows them without a yfinance lookup).
_MANUAL_PRICE_CLASSES = {"OPT", "FUT", "FOP", "WAR", "CFD"}


def _scrub(text: str, token: str) -> str:
    """Remove the Flex token (and the credential query-string fragment it lives in) from
    any text that could reach ``last_error``/logs.  Belt-and-braces alongside ``_safe_get``:
    even if a token substring sneaks into a message we never persist or log it verbatim."""
    if not text:
        return text
    if token:
        text = text.replace(token, "***")
        # IBKR carries the token as ``t=<token>`` in the query string.
        text = text.replace(f"t={token}", "t=***")
    return text


def _safe_get(url: str, params: dict, *, token: str) -> str:
    """GET wrapper for the Flex endpoints that NEVER lets a raw httpx exception escape.

    ``httpx`` puts the full request URL — including the ``t=<token>`` query param — into
    the string form of ``HTTPStatusError`` / request errors.  The ingestion orchestrator
    stores ``str(exc)[:300]`` into ``last_error`` (surfaced by the headless
    ``get_ibkr_status``) and logs it, so an unsanitized exception would leak the read-only
    Flex token.  We catch every httpx error here and re-raise an ``ExternalServiceError``
    carrying ONLY the HTTP status code / a generic transport message — no URL, no token.

    Also bounds the response read at ``_MAX_RESPONSE_BYTES`` (DoS guard against an
    unbounded body) before returning the decoded text.
    """
    try:
        resp = httpx.get(url, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Surface only the status code — NEVER exc (its str() contains the URL+token).
        raise ExternalServiceError(f"IBKR Flex HTTP {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        # Transport/timeout/connect — class name is safe, but scrub it to be certain no
        # token-bearing URL is embedded in the message.
        raise ExternalServiceError(
            f"IBKR Flex request failed ({_scrub(type(exc).__name__, token)})"
        ) from None

    body = resp.content
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ExternalServiceError(
            f"IBKR Flex statement too large (> {_MAX_RESPONSE_BYTES} bytes); refused."
        )
    return body.decode(resp.encoding or "utf-8", errors="replace")


# ── Flex Web Service wire protocol ───────────────────────────────────────────────────────


def send_flex_request(base_url: str, token: str, query_id: str) -> str:
    """Step 1 — submit the Flex query and return the reference code.

    POST ``/SendRequest?t=<token>&q=<query_id>&v=3`` → parse the ``ReferenceCode``
    from the XML response body.  HTTP/transport failures are re-raised as a sanitized
    ``ExternalServiceError`` (NO url/token — see ``_safe_get``); a malformed / error
    response from IBKR raises ``ValueError``.
    """
    url = f"{base_url.rstrip('/')}/SendRequest"
    params = {"t": token, "q": query_id, "v": "3"}
    body = _safe_get(url, params, token=token)
    return _parse_send_response(body)


def _reject_xml_bomb(body: str) -> None:
    """Refuse a Flex body that declares a DOCTYPE/entity BEFORE we parse it.

    Untrusted XML with internal entity definitions can blow up memory/CPU on expansion
    ("billion laughs"); external entities can exfiltrate local files (XXE).  stdlib
    ``ElementTree`` does not expand these by default, but we reject defensively so the
    parser can never be coaxed into it.  Case-insensitive (``<!doctype`` etc.)."""
    upper = body.upper()
    for marker in _XML_BOMB_MARKERS:
        if marker in upper:
            raise ValueError("IBKR Flex XML contains a disallowed DOCTYPE/ENTITY — refused.")


def _find_elem(root: ET.Element, *tags: str) -> Optional[ET.Element]:
    for tag in tags:
        el = root.find(tag)
        if el is not None:
            return el
    return None


def _parse_send_response(body: str) -> str:
    """Extract the ``ReferenceCode`` from the SendRequest XML body."""
    body = body.strip()
    if not body:
        raise ValueError("Empty response from IBKR SendRequest")
    _reject_xml_bomb(body)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"IBKR SendRequest returned invalid XML: {exc}") from exc
    # IBKR returns ReferenceCode either as an attribute or as a child element:
    # 1. <FlexStatementResponse><Status>Success</Status><ReferenceCode>...</ReferenceCode></FlexStatementResponse>
    # 2. <Status ReferenceCode="…"/> or <FlexStatementResponse ReferenceCode="…"/>
    code = root.get("ReferenceCode", "").strip()
    if not code:
        ref_el = _find_elem(root, ".//{*}ReferenceCode", "ReferenceCode")
        if ref_el is not None and ref_el.text:
            code = ref_el.text.strip()
    if not code:
        status_el = _find_elem(root, ".//{*}Status", "Status")
        if status_el is not None:
            code = status_el.get("ReferenceCode", "").strip()

    if not code:
        # Check for an error message in the XML
        msg_el = _find_elem(root, ".//{*}ErrorMessage", "ErrorMessage")
        code_el = _find_elem(root, ".//{*}ErrorCode", "ErrorCode")
        error_msg = (msg_el.text or "").strip() if msg_el is not None else ""
        err_code = (code_el.text or "").strip() if code_el is not None else ""
        if error_msg and err_code:
            error_msg = f"[{err_code}] {error_msg}"
        elif not error_msg:
            error_msg = body[:200]
        raise ValueError(f"IBKR SendRequest did not return a ReferenceCode: {error_msg}")
    return code


def get_flex_statement(base_url: str, token: str, reference_code: str) -> str:
    """Step 2 — fetch the statement XML once it is ready.

    GET ``/GetStatement?t=<token>&q=<code>&v=3``.  When IBKR returns a
    "Statement generation in progress" sentinel, this is a programming error (the
    caller should call ``poll_for_flex_statement`` which retries).  Returns the XML
    string on success; HTTP/transport failures are re-raised as a sanitized
    ``ExternalServiceError`` (NO url/token — see ``_safe_get``).
    """
    url = f"{base_url.rstrip('/')}/GetStatement"
    params = {"t": token, "q": reference_code, "v": "3"}
    return _safe_get(url, params, token=token)


def poll_for_flex_statement(
    base_url: str,
    token: str,
    reference_code: str,
    *,
    max_attempts: int = _MAX_POLL_ATTEMPTS,
    delay: float = _POLL_DELAY_SECONDS,
) -> str:
    """Poll ``GetStatement`` until the XML statement is available.

    Returns the XML string when the report is ready.  Raises ``TimeoutError`` if
    ``max_attempts`` is exceeded, ``ValueError`` on a permanent IBKR error, or a
    sanitized ``ExternalServiceError`` (NO url/token) on a transport failure.
    """
    for attempt in range(max_attempts):
        xml_text = get_flex_statement(base_url, token, reference_code)
        xml_text = xml_text.strip()
        if not xml_text:
            logger.debug("IBKR poll attempt %d: empty body, waiting …", attempt + 1)
            time.sleep(delay)
            continue
        # "Statement generation in progress" sentinel — keep polling.
        if _is_in_progress(xml_text):
            logger.debug("IBKR poll attempt %d: in progress …", attempt + 1)
            time.sleep(delay)
            continue
        # Any other non-XML text that looks like an error or unexpected format.
        if not xml_text.startswith("<"):
            if (
                "," in xml_text
                or xml_text.startswith('"')
                or "\n" in xml_text
                or "\r" in xml_text
                or "ClientAccountID" in xml_text
            ):
                raise ValueError(
                    "IBKR Flex Query format is set to CSV. Please edit your Flex Query in IBKR "
                    "Account Management (Reports → Flex Queries) and change the Output Format to XML."
                )
            raise ValueError(f"IBKR GetStatement returned unexpected response: {xml_text[:200]}")
        return xml_text
    raise TimeoutError(
        f"IBKR Flex statement not ready after {max_attempts} attempts "
        f"({max_attempts * delay:.0f}s). Try increasing poll_interval_seconds."
    )


def _is_in_progress(text: str) -> bool:
    """True when the Flex endpoint signals the report is still generating."""
    lower = text.lower()
    return "in progress" in lower or "please wait" in lower or "generating" in lower


# ── XML parsing ───────────────────────────────────────────────────────────────────────────


def _flex_statement_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if len(raw) == 8 and raw.isdigit():
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("IBKR Flex CashReport requires an ISO-like toDate") from exc


def _cash_report_snapshots(root: ET.Element) -> list[dict]:
    snapshots: list[dict] = []
    for statement in root.iter("FlexStatement"):
        account = str(statement.get("accountId") or "").strip()
        if not account:
            continue
        currency_elements = list(statement.iter("CashReportCurrency"))
        if not currency_elements:
            continue
        statement_date = _flex_statement_date(statement.get("toDate"))
        for currency_el in currency_elements:
            currency = str(currency_el.get("currency") or "").upper().strip()
            if currency == "BASE_SUMMARY":
                continue
            if not _ISO_CURRENCY.fullmatch(currency):
                raise ValueError("IBKR Flex CashReport has an invalid currency")
            raw_amount = currency_el.get("endingSettledCash")
            if raw_amount is None or not str(raw_amount).strip():
                continue
            try:
                amount = float(raw_amount)
            except (TypeError, ValueError) as exc:
                raise ValueError("IBKR Flex CashReport has an invalid settled-cash amount") from exc
            if not math.isfinite(amount):
                raise ValueError("IBKR Flex CashReport settled-cash amount is not finite")
            snapshots.append(
                {
                    "provider_account_id": account,
                    "currency": currency,
                    "ending_settled_cash": amount,
                    "statement_date": statement_date,
                }
            )
    return snapshots


def parse_flex_xml(xml_text: str) -> dict:
    """Parse the Flex XML into a structured dict with the sections we care about.

    Returns:
        {
            "open_positions": [...],   # list of element attrs dicts
            "trades": [...],           # list of element attrs dicts
            "cash_transactions": [...] # list of element attrs dicts
            "account_currency": {<accountId>: <reporting ccy>},  # from AccountInformation
        }

    Raises ``ValueError`` on unparseable XML or a body declaring a DOCTYPE/ENTITY
    (entity-expansion / XXE defense — see ``_reject_xml_bomb``).
    """
    _reject_xml_bomb(xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"IBKR Flex XML parse error: {exc}") from exc

    open_positions = [el.attrib for el in root.iter("OpenPosition")]
    trades = [el.attrib for el in root.iter("Trade")]
    cash_txns = [el.attrib for el in root.iter("CashTransaction")]
    cash_snapshots = _cash_report_snapshots(root)

    # Reporting currency per account: prefer the explicit <AccountInformation currency="…">,
    # else the per-statement currency on the <FlexStatement> element. Keyed by IBKR account
    # number so a multi-account statement maps each holding to the right base.
    account_currency: dict[str, str] = {}
    for el in root.iter("AccountInformation"):
        acct = str(el.get("accountId") or "").strip()
        ccy = str(el.get("currency") or "").upper().strip()
        if acct and ccy:
            account_currency[acct] = ccy
    for el in root.iter("FlexStatement"):
        acct = str(el.get("accountId") or "").strip()
        ccy = str(el.get("currency") or "").upper().strip()
        if acct and ccy:
            account_currency.setdefault(acct, ccy)

    return {
        "open_positions": open_positions,
        "trades": trades,
        "cash_transactions": cash_txns,
        "account_currency": account_currency,
        "cash_snapshots": cash_snapshots,
    }


# ── mapping helpers ───────────────────────────────────────────────────────────────────────


def _is_manual_price(asset_category: str) -> bool:
    return asset_category.upper() in _MANUAL_PRICE_CLASSES


def _position_to_common(pos: dict, account_number: str) -> Optional[dict]:
    """One IBKR OpenPosition → the RFC §A broker-position shape. ``instrument_hint`` folds
    in both the economic-exposure asset class (RFC §2.2) and the manual-vs-yahoo pricing
    call (options/futures have no live price source) so the caller's
    ``resolve_instruments``/``add_positions`` has everything it needs without re-deriving
    IBKR's own asset-category vocabulary. Returns None for a symbol-less row."""
    symbol = str(pos.get("symbol") or "").upper().strip()
    if not symbol:
        return None
    asset_cat = str(pos.get("assetCategory") or "STK").upper()
    try:
        qty = float(pos.get("position") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    cost_basis_raw = pos.get("costBasisPrice") or pos.get("averageCost")
    try:
        avg_cost = float(cost_basis_raw) if cost_basis_raw is not None else None
    except (TypeError, ValueError):
        avg_cost = None
    try:
        market_value = float(pos.get("positionValue"))
    except (TypeError, ValueError):
        market_value = None
    return {
        "symbol": symbol,
        "instrument_hint": (
            "manual" if _is_manual_price(asset_cat) else _ASSET_CLASS_MAP.get(asset_cat, "equity")
        ),
        "qty": qty,
        "avg_cost": avg_cost,
        "market_value": market_value,
        "currency": str(pos.get("currency") or "USD").upper(),
        "provider_account_id": account_number,
    }


def _trade_to_row(trade: dict, account_number: str) -> Optional[dict]:
    """One IBKR Trade element → the common normalized row shape (RFC §A step 3).

    Money direction: BUY → money OUT (negative); SELL → money IN (positive) — this app's
    own convention. Net amount = proceeds + commission (commission is negative in IBKR XML
    for buys). Returns None for an unparseable/zero-amount/symbol-or-order-id-less trade."""
    symbol = str(trade.get("symbol") or "").upper().strip()
    order_id = str(trade.get("ibOrderID") or trade.get("transactionID") or "").strip()
    if not symbol or not order_id:
        return None

    trade_date = str(trade.get("tradeDate") or trade.get("reportDate") or "").strip()
    if len(trade_date) == 8 and trade_date.isdigit():
        trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

    try:
        proceeds = float(trade.get("proceeds") or 0)
    except (TypeError, ValueError):
        proceeds = 0.0
    try:
        commission = float(trade.get("ibCommission") or trade.get("commission") or 0)
    except (TypeError, ValueError):
        commission = 0.0

    currency = str(trade.get("currency") or "USD").upper()
    buy_sell = str(trade.get("buySell") or "").upper()
    magnitude = abs(proceeds + commission) if (proceeds or commission) else None
    if not magnitude:
        return None
    amount = magnitude if buy_sell == "SELL" else -magnitude

    ref = f"{account_number}:{order_id}"
    description = f"IBKR {buy_sell.capitalize() if buy_sell else 'Trade'} {symbol}".strip()
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": trade,
        "provider_account_id": f"{account_number}:{currency}",
        "date": trade_date or datetime.now(timezone.utc).date().isoformat(),
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": None,
        "pending": False,
        "supersedes_ref": None,
    }


def _cash_txn_to_row(cash_txn: dict, account_number: str) -> Optional[dict]:
    """One IBKR CashTransaction (dividend, interest, fee, withholding tax, …) → the common
    normalized row shape (RFC §A step 3). Positive amount = money in; negative = money out
    — already this app's own convention. Returns None for an id-less/zero-amount row."""
    txn_id = str(cash_txn.get("transactionID") or cash_txn.get("tradeID") or "").strip()
    if not txn_id:
        return None
    try:
        amount = float(cash_txn.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount == 0:
        return None

    txn_type = str(cash_txn.get("type") or "").strip()
    currency = str(cash_txn.get("currency") or "USD").upper()
    txn_date = str(cash_txn.get("dateTime") or cash_txn.get("reportDate") or "").strip()
    if len(txn_date) >= 8 and txn_date[:8].isdigit():
        txn_date = f"{txn_date[:4]}-{txn_date[4:6]}-{txn_date[6:8]}"

    ref = f"{account_number}:{txn_id}"
    description = f"IBKR {txn_type}".strip() if txn_type else "IBKR cash transaction"
    return {
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": ref}],
        "raw": cash_txn,
        "provider_account_id": f"{account_number}:{currency}",
        "date": txn_date or datetime.now(timezone.utc).date().isoformat(),
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": None,
        "pending": False,
        "supersedes_ref": None,
    }


def fetch_window(
    store: IbkrConfigStore,
    since: str,
    before: str,
    account: Optional[str],
    *,
    accounts_only: bool = False,
) -> FetchWindow:
    """IBKR's Flex Web Service has no per-call date param (RFC §B) — the report's own
    period is set on IBKR's side (Account Management → Flex Queries). ``fetch_window`` runs
    the SendRequest/GetStatement/parse dance (unchanged) and then windows trades/cash
    transactions to ``[since, before]`` CLIENT-SIDE; positions are never windowed (a
    snapshot, returned via ``FetchWindow.extra["positions"]`` regardless of the caller's
    window — same shape as Alpaca's). ``account`` filters by IBKR account NUMBER,
    client-side. Always ``complete=True``: one XML statement is not paginated."""
    cfg = store.get()
    token = cfg.get("flex_token") or ""
    query_id = cfg.get("flex_query_id") or ""
    base_url = cfg.get("base_url") or (
        "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
    )

    reference_code = send_flex_request(base_url, token, query_id)
    xml_text = poll_for_flex_statement(base_url, token, reference_code)
    parsed = parse_flex_xml(xml_text)
    account_currency = parsed.get("account_currency") or {}

    account_numbers = {
        str(el.get("accountId") or "")
        for section in ("open_positions", "trades", "cash_transactions")
        for el in parsed.get(section) or []
        if el.get("accountId")
    }
    if account:
        account_numbers &= {account}
    accounts = [
        {
            "provider_account_id": num,
            "name": f"IBKR {num}",
            "mask": None,
            "currency": account_currency.get(num, "USD"),
            "balance": None,
        }
        for num in sorted(account_numbers)
    ]

    wallet_currencies = set()
    for snap in parsed.get("cash_snapshots") or []:
        if snap.get("provider_account_id") and snap.get("currency"):
            wallet_currencies.add((snap["provider_account_id"], snap["currency"].upper()))
    for trade in parsed.get("trades") or []:
        if trade.get("accountId") and trade.get("currency"):
            wallet_currencies.add((trade["accountId"], trade["currency"].upper()))
    for cash_txn in parsed.get("cash_transactions") or []:
        if cash_txn.get("accountId") and cash_txn.get("currency"):
            wallet_currencies.add((cash_txn["accountId"], cash_txn["currency"].upper()))

    for num, ccy in sorted(wallet_currencies):
        if account and num != account:
            continue
        accounts.append(
            {
                "provider_account_id": f"{num}:{ccy}",
                "name": f"IBKR {num} {ccy}",
                "mask": None,
                "currency": ccy,
                "balance": None,
            }
        )

    positions: list[dict] = []
    if not accounts_only:
        for pos in parsed.get("open_positions") or []:
            acct_num = str(pos.get("accountId") or "")
            if account and acct_num != account:
                continue
            common = _position_to_common(pos, acct_num)
            if common is not None:
                positions.append(common)

    rows: list[dict] = []
    if not accounts_only:
        for trade in parsed.get("trades") or []:
            acct_num = str(trade.get("accountId") or "").strip()
            if not acct_num or (account and acct_num != account):
                if not acct_num:
                    logger.warning(
                        "IBKR: trade element has no accountId (symbol=%s) — skipped; "
                        "check your Flex Query includes accountId in the output.",
                        str(trade.get("symbol") or ""),
                    )
                continue
            row = _trade_to_row(trade, acct_num)
            if row is not None and since <= row["date"] <= before:
                rows.append(row)
        for cash_txn in parsed.get("cash_transactions") or []:
            acct_num = str(cash_txn.get("accountId") or "").strip()
            if not acct_num or (account and acct_num != account):
                if not acct_num:
                    logger.warning(
                        "IBKR: cash transaction has no accountId (id=%s) — skipped; "
                        "check your Flex Query includes accountId in the output.",
                        str(cash_txn.get("transactionID") or cash_txn.get("tradeID") or ""),
                    )
                continue
            row = _cash_txn_to_row(cash_txn, acct_num)
            if row is not None and since <= row["date"] <= before:
                rows.append(row)
    onboarding_balances = []
    if not accounts_only:
        cash_snapshots = parsed.get("cash_snapshots") or []
        for snap in cash_snapshots:
            acct_num = snap["provider_account_id"]
            if account and acct_num != account:
                continue

            # The user wants to use CashReport purely for onboarding, starting from today's balance.
            # We no longer do backward math to calculate a 365-day-ago balance.
            onboarding_balances.append(
                {
                    "provider_account_id": f"{acct_num}:{snap['currency']}",
                    "currency": snap["currency"],
                    "implied_starting_balance": snap["ending_settled_cash"],
                    "as_of": snap["statement_date"],
                }
            )

    return FetchWindow(
        accounts=accounts,
        rows=rows,
        complete=True,
        extra={
            "positions": positions,
            "onboarding_starting_balances": onboarding_balances,
        },
    )


def classify_error(exc: Exception) -> str:
    """Exception → status taxonomy for IBKR. Transport failures surface as sanitized
    ``ExternalServiceError`` carrying the HTTP status code (NO url/token — see
    ``_safe_get``); re-derive the class from that safe message rather than the original
    httpx exception."""
    if isinstance(exc, ExternalServiceError):
        msg = str(exc).lower()
        if "http 401" in msg or "http 403" in msg:
            return "auth_error"
        if "http 429" in msg:
            return "rate_limited"
        if "too large" in msg:
            return "error"
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
    if isinstance(exc, ValueError):
        # Bad token / query id → likely auth error.
        msg = str(exc).lower()
        if "token" in msg or "access" in msg or "auth" in msg or "invalid" in msg:
            return "auth_error"
        return "error"
    if isinstance(exc, TimeoutError):
        return "connect_error"
    return "error"
