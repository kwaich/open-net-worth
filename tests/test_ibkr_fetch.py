"""IBKR Flex Web Service aggregation — protocol (SendRequest/GetStatement/XML parse,
unchanged) + fetch_window normalize + classify_error (RFC connectors-as-data-sources).
Replaces tests/test_ibkr_sync.py, deleted — it tested `run_ibkr_sync` and `_cash_category`,
both retired: booking (and with it, category assignment) is no longer this module's job —
a Lucius turn calls `ibkr_fetch_activity`, gets drafts back, and books via `add_transactions`
through the ordinary ledger choke, which is what makes a re-fetch idempotent. That choke's
own dedup/idempotency is out of scope here (covered by tests/test_deleted_identity_is_remembered.py
and the ledger suite); `services.connector_fetch.fetch`'s dormant-gate/status handling is
covered by tests/test_connector_fetch_tools.py, not per-provider here.

Covers (all HTTP mocked — NO real network):
  * fetch_window maps OpenPositions/Trades/CashTransactions to the RFC §A common shape;
  * external_ref keys on the IBKR account NUMBER, never a generated id (P2-3);
  * a BUY nets to a negative (money out) amount, a SELL to a positive (money in) one;
  * a multi-currency (EUR) account's rows/accounts carry the NATIVE currency, unconverted;
  * a derivatives (OPT) position is created with a manual price source, never dropped;
  * a Trade/CashTransaction with no accountId is skipped and logged, never attributed to a
    synthetic "unknown" account;
  * the Flex token never leaks into a sanitized error or `classify_error`'s auth_error;
  * a DOCTYPE/entity-bomb body is REJECTED, not expanded (MEDIUM);
  * SendRequest/GetStatement response parsing (unchanged from the old sync module).
"""

from __future__ import annotations

import httpx
import pytest

from backend.core.errors import ExternalServiceError
from backend.services.aggregation import ibkr as svc

# ── deterministic Flex XML fixtures ──────────────────────────────────────────────────

_ACCT = "U1234567"

# Single-account statement: 2 open positions, a BUY + a SELL trade, a dividend + a fee.
_FLEX_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="{_ACCT}" currency="USD" fromDate="20260101" toDate="20260131">
      <AccountInformation accountId="{_ACCT}" currency="USD" />
      <OpenPositions>
        <OpenPosition accountId="{_ACCT}" symbol="AAPL" assetCategory="STK"
          currency="USD" position="10" costBasisPrice="150.00"
          description="Apple Inc" />
        <OpenPosition accountId="{_ACCT}" symbol="VWRA" assetCategory="ETF"
          currency="USD" position="5" costBasisPrice="100.00"
          description="Vanguard FTSE All-World" />
      </OpenPositions>
      <CashReport>
        <CashReportCurrency accountId="U1234567" currency="USD" endingSettledCash="4243.20" />
      </CashReport>
      <Trades>
        <Trade accountId="{_ACCT}" symbol="AAPL" assetCategory="STK" currency="USD"
          buySell="BUY" ibOrderID="ORD-BUY-1" tradeDate="20260102"
          proceeds="-1500.00" ibCommission="-1.00" />
        <Trade accountId="{_ACCT}" symbol="TSLA" assetCategory="STK" currency="USD"
          buySell="SELL" ibOrderID="ORD-SELL-1" tradeDate="20260103"
          proceeds="2000.00" ibCommission="-1.00" />
      </Trades>
      <CashTransactions>
        <CashTransaction accountId="{_ACCT}" transactionID="CT-DIV-1" type="Dividends"
          currency="USD" amount="42.50" dateTime="20260105"
          description="AAPL CASH DIVIDEND" />
        <CashTransaction accountId="{_ACCT}" transactionID="CT-FEE-1"
          type="Other Fees" currency="USD" amount="-3.00" dateTime="20260106"
          description="Monthly activity fee" />
      </CashTransactions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    for var in ("IBKR_FLEX_TOKEN", "IBKR_FLEX_QUERY_ID", "IBKR_ENABLED"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def store(db_isolated):
    from backend.db.ibkr_config_store import IbkrConfigStore

    s = IbkrConfigStore()
    s.update({"flex_token": "SECRET-FLEX-TOKEN", "flex_query_id": "999", "enabled": True})
    return s


def _stub_flex(monkeypatch, xml=_FLEX_XML):
    monkeypatch.setattr(svc, "send_flex_request", lambda base, t, q: "REF-CODE")
    monkeypatch.setattr(svc, "poll_for_flex_statement", lambda base, t, r: xml)


# ── fetch_window: normalization ─────────────────────────────────────────────────────────


class TestFetchWindow:
    def test_maps_accounts_positions_and_rows(self, store, monkeypatch):
        _stub_flex(monkeypatch)
        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)

        assert window.accounts == [
            {
                "provider_account_id": _ACCT,
                "name": f"IBKR {_ACCT}",
                "mask": None,
                "currency": "USD",
                "balance": None,
            },
            {
                "provider_account_id": f"{_ACCT}:USD",
                "name": f"IBKR {_ACCT} USD",
                "mask": None,
                "currency": "USD",
                "balance": None,
            },
        ]
        by_symbol = {p["symbol"]: p for p in window.extra["positions"]}
        assert by_symbol["AAPL"]["instrument_hint"] == "equity"
        assert by_symbol["AAPL"]["qty"] == 10.0
        assert by_symbol["AAPL"]["avg_cost"] == 150.0
        assert by_symbol["VWRA"]["instrument_hint"] == "equity"  # ETF -> equity exposure

        assert window.complete is True
        ext_refs = {r["external_refs"][0]["ref"] for r in window.rows}
        assert ext_refs == {
            f"{_ACCT}:ORD-BUY-1",
            f"{_ACCT}:ORD-SELL-1",
            f"{_ACCT}:CT-DIV-1",
            f"{_ACCT}:CT-FEE-1",
        }
        provider_ids = {r["provider_account_id"] for r in window.rows}
        assert provider_ids == {f"{_ACCT}:USD"}
        # Every row carries the original element attrs as raw evidence for the drawer.
        assert all(r["raw"] for r in window.rows)

    def test_accounts_only_skips_positions_and_rows(self, store, monkeypatch):
        _stub_flex(monkeypatch)
        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None, accounts_only=True)
        assert window.extra["positions"] == []
        assert window.rows == []
        assert len(window.accounts) == 2

    def test_account_filter_scopes_positions_and_rows(self, store, monkeypatch):
        _stub_flex(monkeypatch)
        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", "SOME-OTHER-ACCOUNT")
        assert window.accounts == []
        assert window.extra["positions"] == []
        assert window.rows == []

    def test_rows_outside_the_window_are_excluded(self, store, monkeypatch):
        _stub_flex(monkeypatch)
        # Both trades (01-02, 01-03) fall before this window; both cash txns (01-05, 01-06)
        # fall inside it.
        window = svc.fetch_window(store, "2026-01-04", "2026-02-01", None)
        refs = {r["external_refs"][0]["ref"] for r in window.rows}
        assert refs == {f"{_ACCT}:CT-DIV-1", f"{_ACCT}:CT-FEE-1"}


# ── trade money direction: BUY = money out (negative), SELL = money in (positive) ───────


def test_buy_nets_negative_sell_nets_positive():
    buy = svc._trade_to_row(
        {
            "accountId": _ACCT,
            "symbol": "AAPL",
            "ibOrderID": "ORD-BUY-1",
            "tradeDate": "20260102",
            "currency": "USD",
            "buySell": "BUY",
            "proceeds": "-1500.00",
            "ibCommission": "-1.00",
        },
        _ACCT,
    )
    assert buy["amount"] == -1501.00  # money OUT of the brokerage

    sell = svc._trade_to_row(
        {
            "accountId": _ACCT,
            "symbol": "TSLA",
            "ibOrderID": "ORD-SELL-1",
            "tradeDate": "20260103",
            "currency": "USD",
            "buySell": "SELL",
            "proceeds": "2000.00",
            "ibCommission": "-1.00",
        },
        _ACCT,
    )
    assert sell["amount"] == 1999.00  # money IN to the brokerage


# ── external_ref keys on the IBKR account NUMBER, never a generated id (P2-3) ───────────


def test_external_ref_uses_ibkr_account_number(store, monkeypatch):
    _stub_flex(monkeypatch)
    window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)
    for row in window.rows:
        assert row["external_refs"] == [{"source": "ibkr", "ref": row["external_refs"][0]["ref"]}]
        assert row["external_refs"][0]["ref"].startswith(f"{_ACCT}:")


# ── multi-currency: the row/account carry the NATIVE currency, unconverted ─────────────


def test_multi_currency_account_carries_native_currency(store, monkeypatch):
    """A EUR-denominated IBKR account: the account and its rows stay in EUR (the reporting
    currency read from <AccountInformation>) -- recomputing base currency is the ledger
    choke's job at booking time, not this module's."""
    eur_acct = "U7654321"
    eur_xml = f"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="{eur_acct}" currency="EUR">
    <AccountInformation accountId="{eur_acct}" currency="EUR" />
    <CashTransactions>
      <CashTransaction accountId="{eur_acct}" transactionID="CT-DIV-EUR" type="Dividends"
        currency="EUR" amount="100.00" dateTime="20260105" description="EUR DIVIDEND" />
    </CashTransactions>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>"""
    _stub_flex(monkeypatch, xml=eur_xml)
    window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)

    assert window.accounts[0]["currency"] == "EUR"
    [row] = window.rows
    assert row["currency"] == "EUR"
    assert row["amount"] == 100.0


# ── derivatives (OPT/FUT) positions must not be silently dropped ───────────────────────
# Prior to the manual-vs-strict id validation split, a downstream _validate_price_source_id
# rejected ids like "ibkr:AAPL  240119C00150000" (colon + internal spaces), which silently
# dropped every derivative position. _position_to_common itself has no such validation —
# this pins that it still classifies the option correctly for whatever resolves it next.

_OPT_SYMBOL = "AAPL  240119C00150000"  # IBKR OCC option symbol: spaces are canonical


def test_derivatives_position_is_manual_priced_not_dropped():
    common = svc._position_to_common(
        {
            "accountId": _ACCT,
            "symbol": _OPT_SYMBOL,
            "assetCategory": "OPT",
            "currency": "USD",
            "position": "2",
            "costBasisPrice": "3.50",
        },
        _ACCT,
    )
    assert common is not None
    assert common["symbol"] == _OPT_SYMBOL
    assert common["instrument_hint"] == "manual"
    assert common["qty"] == 2.0


def test_derivatives_position_surfaces_through_fetch_window(store, monkeypatch):
    xml_with_opt = f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="{_ACCT}" currency="USD" fromDate="20260101" toDate="20260131">
      <AccountInformation accountId="{_ACCT}" currency="USD" />
      <OpenPositions>
        <OpenPosition accountId="{_ACCT}" symbol="{_OPT_SYMBOL}" assetCategory="OPT"
          currency="USD" position="2" costBasisPrice="3.50"
          description="AAPL Jan 2024 150 CALL" />
      </OpenPositions>
      <Trades/>
      <CashTransactions/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    _stub_flex(monkeypatch, xml=xml_with_opt)
    window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)
    assert len(window.extra["positions"]) == 1
    assert window.extra["positions"][0]["instrument_hint"] == "manual"


# ── a Trade/CashTransaction with no accountId is skipped, never attributed elsewhere ────


def test_trade_with_no_account_id_is_skipped_and_logged(store, monkeypatch, caplog):
    import logging

    xml_no_acct = f"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="{_ACCT}" currency="USD" fromDate="20260101" toDate="20260131">
    <AccountInformation accountId="{_ACCT}" currency="USD" />
    <OpenPositions/>
    <Trades>
      <Trade symbol="AAPL" assetCategory="STK" currency="USD"
        buySell="BUY" ibOrderID="ORD-NOACCT" tradeDate="20260102"
        proceeds="-1000.00" ibCommission="-1.00" />
    </Trades>
    <CashTransactions/>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>"""
    _stub_flex(monkeypatch, xml=xml_no_acct)
    with caplog.at_level(logging.WARNING, logger="backend.services.aggregation.ibkr"):
        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)

    assert window.rows == []
    assert any(
        "accountId" in rec.message or "acct" in rec.message.lower() for rec in caplog.records
    )


def test_cash_txn_with_no_account_id_is_skipped_and_logged(store, monkeypatch, caplog):
    import logging

    xml_no_acct_cash = f"""<?xml version="1.0"?>
<FlexQueryResponse><FlexStatements>
  <FlexStatement accountId="{_ACCT}" currency="USD" fromDate="20260101" toDate="20260131">
    <AccountInformation accountId="{_ACCT}" currency="USD" />
    <OpenPositions/>
    <Trades/>
    <CashTransactions>
      <CashTransaction transactionID="CT-NOACCT" type="Dividends"
        currency="USD" amount="50.00" dateTime="20260105"
        description="ORPHAN DIVIDEND" />
    </CashTransactions>
  </FlexStatement>
</FlexStatements></FlexQueryResponse>"""
    _stub_flex(monkeypatch, xml=xml_no_acct_cash)
    with caplog.at_level(logging.WARNING, logger="backend.services.aggregation.ibkr"):
        window = svc.fetch_window(store, "2026-01-01", "2026-02-01", None)

    assert window.rows == []
    assert any(
        "accountId" in rec.message or "acct" in rec.message.lower() for rec in caplog.records
    )


# ── SECURITY: token never leaks into the sanitized exception / classify_error (HIGH) ───


def test_http_error_message_omits_token_and_query_string(monkeypatch):
    """A 401 from the Flex endpoint must surface a sanitized error containing NEITHER the
    token NOR the token-bearing query string."""
    token = "SUPER-SECRET-TOKEN-XYZ"
    url = f"https://flex.example/SendRequest?t={token}&q=1&v=3"

    def boom(url_, params=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(401, request=request, text="Invalid token")
        raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    monkeypatch.setattr(svc.httpx, "get", boom)

    with pytest.raises(ExternalServiceError) as ei:
        svc._safe_get(url, {"t": token, "q": "1", "v": "3"}, token=token)
    msg = str(ei.value)
    assert token not in msg
    assert url not in msg
    assert "t=" + token not in msg
    assert "401" in msg  # status code is safe to surface


def test_classify_error_never_needs_the_token_to_say_auth_error(monkeypatch):
    """End-to-end through fetch_window: a 401 raised inside send_flex_request reaches
    classify_error as the ALREADY-sanitized ExternalServiceError _safe_get raises -- no
    token anywhere in the value classify_error inspects."""
    token = "LEAKY-TOKEN-123"

    def boom(base, t, q):
        request = httpx.Request("GET", "https://flex.example/SendRequest?t=" + token)
        response = httpx.Response(401, request=request, text="bad")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ExternalServiceError("IBKR HTTP 401") from exc

    monkeypatch.setattr(svc, "send_flex_request", boom)
    try:
        svc.send_flex_request("https://flex.example", token, "999")
    except ExternalServiceError as exc:
        assert token not in str(exc)
        assert svc.classify_error(exc) == "auth_error"


class TestClassifyError:
    def test_external_service_error_401_is_auth_error(self):
        assert svc.classify_error(ExternalServiceError("IBKR HTTP 401")) == "auth_error"

    def test_external_service_error_429_is_rate_limited(self):
        assert svc.classify_error(ExternalServiceError("IBKR HTTP 429")) == "rate_limited"

    def test_external_service_error_too_large_is_error(self):
        assert svc.classify_error(ExternalServiceError("response too large")) == "error"

    def test_external_service_error_other_is_connect_error(self):
        assert svc.classify_error(ExternalServiceError("IBKR HTTP 500")) == "connect_error"

    def test_connect_error_is_connect_error(self):
        assert svc.classify_error(httpx.ConnectError("refused")) == "connect_error"

    def test_generic_exception_is_error(self):
        assert svc.classify_error(RuntimeError("unexpected")) == "error"


# ── SECURITY: XML entity bomb / DOCTYPE rejected, not expanded (MEDIUM) ─────────────────


def test_entity_bomb_body_is_rejected():
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;">]>'
        "<FlexQueryResponse>&lol2;</FlexQueryResponse>"
    )
    with pytest.raises(ValueError, match="DOCTYPE/ENTITY"):
        svc.parse_flex_xml(bomb)


def test_send_response_rejects_doctype():
    with pytest.raises(ValueError, match="DOCTYPE/ENTITY"):
        svc._parse_send_response('<!DOCTYPE x><Status ReferenceCode="ABC"/>')


def test_oversized_body_refused(monkeypatch):
    """A response larger than the cap is refused before we buffer/parse it."""
    monkeypatch.setattr(svc, "_MAX_RESPONSE_BYTES", 16)

    class _Resp:
        content = b"x" * 64
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(svc.httpx, "get", lambda *a, **k: _Resp())
    with pytest.raises(ExternalServiceError, match="too large"):
        svc._safe_get("https://flex.example/GetStatement", {}, token="tok")


# ── SendRequest/GetStatement response parsing (unchanged from the retired sync module) ──


def test_parse_send_response_formats():
    """Verify that _parse_send_response handles all known IBKR SendRequest response formats."""
    xml_child_el = """<FlexStatementResponse timestamp='29 August, 2026 11:19 PM EDT'>
<Status>Success</Status>
<ReferenceCode>2049081806</ReferenceCode>
<Url>https://gdcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url>
</FlexStatementResponse>"""
    assert svc._parse_send_response(xml_child_el) == "2049081806"

    xml_root_attr = '<FlexStatementResponse ReferenceCode="987654321"><Status>Success</Status></FlexStatementResponse>'
    assert svc._parse_send_response(xml_root_attr) == "987654321"

    xml_status_attr = '<FlexStatementResponse><Status ReferenceCode="1122334455">Success</Status></FlexStatementResponse>'
    assert svc._parse_send_response(xml_status_attr) == "1122334455"

    xml_simple = '<Status ReferenceCode="5566778899"/>'
    assert svc._parse_send_response(xml_simple) == "5566778899"

    xml_err = """<FlexStatementResponse timestamp='29 August, 2026 11:19 PM EDT'>
<Status>Warn</Status>
<ErrorCode>1018</ErrorCode>
<ErrorMessage>Token has expired</ErrorMessage>
</FlexStatementResponse>"""
    with pytest.raises(ValueError, match=r"\[1018\] Token has expired"):
        svc._parse_send_response(xml_err)


def test_poll_for_flex_statement_rejects_csv(monkeypatch):
    """Verify that poll_for_flex_statement raises a clear ValueError when IBKR returns CSV."""
    csv_sample = (
        '"NetCashBalanceSLB","NetCashBalanceSLBSecurities","NetCashBalanceSLBCommodities"\n'
        '"6047.620925982","6047.620925982","0"'
    )
    monkeypatch.setattr(svc, "get_flex_statement", lambda *args, **kwargs: csv_sample)
    with pytest.raises(
        ValueError, match=r"IBKR Flex Query format is set to CSV.*change the Output Format to XML"
    ):
        svc.poll_for_flex_statement("https://example.com", "token", "12345", max_attempts=1)

    single_line_csv = (
        '"NetCashBalanceSLB","NetCashBalanceSLBSecurities","NetCashBalanceSLBCommodities"'
    )
    monkeypatch.setattr(svc, "get_flex_statement", lambda *args, **kwargs: single_line_csv)
    with pytest.raises(
        ValueError, match=r"IBKR Flex Query format is set to CSV.*change the Output Format to XML"
    ):
        svc.poll_for_flex_statement("https://example.com", "token", "12345", max_attempts=1)
