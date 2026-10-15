"""Regression: peer resolution must reject non-tradeable Yahoo search hits.

Yahoo's search returns private "Company Level" placeholders (quoteType=MUTUALFUND, e.g.
"Axiom Space" -> AXISZZX) alongside indices/futures/currencies. A loose ``quotes[0]``
fallback used to accept that junk as a peer ticker; it then priced as NaN and 500'd the
asset-detail endpoint (and rendered a bogus peer). search_symbol must return only a
genuinely tradeable EQUITY/ETF, else {} so the caller drops the peer.

Offline: Yahoo's HTTP call is monkeypatched, no network.
"""

from backend.services import prices
from backend.services.prices import _symbol_search_cache, search_symbol


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def close(self):
        pass


def _mock_yahoo(monkeypatch, payload):
    """Yahoo search rides yfinance's managed session, not bare urllib."""
    import importlib

    _symbol_search_cache.clear()

    class _FakeYfData:
        def get(self, url, params=None, timeout=30):
            return _FakeResp(payload)

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)


def test_drops_private_company_placeholder(monkeypatch):
    # The real Yahoo response for "Axiom Space": one MUTUALFUND "Company Level" placeholder.
    _mock_yahoo(
        monkeypatch,
        {
            "quotes": [
                {
                    "symbol": "AXISZZX",
                    "quoteType": "MUTUALFUND",
                    "shortname": "Axiom Space - Company Level",
                    "exchDisp": "NASDAQ",
                }
            ]
        },
    )
    assert search_symbol("Axiom Space") == {}  # not tradeable -> dropped, not accepted as a peer


def test_picks_a_real_equity(monkeypatch):
    _mock_yahoo(
        monkeypatch,
        {
            "quotes": [
                {
                    "symbol": "DASH",
                    "quoteType": "EQUITY",
                    "shortname": "DoorDash, Inc.",
                    "exchDisp": "NYSE",
                }
            ]
        },
    )
    r = search_symbol("DoorDash")
    assert r["symbol"] == "DASH"
    assert r["exchange"] == "NYSE"


def test_skips_index_in_favor_of_equity(monkeypatch):
    # An index ranks first; the loose quotes[0] fallback would have taken it. Must pick the equity.
    _mock_yahoo(
        monkeypatch,
        {
            "quotes": [
                {"symbol": "^IXIC", "quoteType": "INDEX", "shortname": "NASDAQ Composite"},
                {
                    "symbol": "BA",
                    "quoteType": "EQUITY",
                    "shortname": "Boeing Company",
                    "exchDisp": "NYSE",
                },
            ]
        },
    )
    assert search_symbol("Boeing")["symbol"] == "BA"


def test_accepts_an_etf(monkeypatch):
    _mock_yahoo(
        monkeypatch,
        {
            "quotes": [
                {
                    "symbol": "ARKX",
                    "quoteType": "ETF",
                    "shortname": "ARK Space ETF",
                    "exchDisp": "BATS",
                }
            ]
        },
    )
    assert search_symbol("ARK Space ETF")["symbol"] == "ARKX"


def test_search_symbol_uses_yfinance_session_not_bare_urllib(monkeypatch):
    """Same scar as the palette search: an empty stdlib trust store must not drop peers."""
    import importlib

    _symbol_search_cache.clear()

    def _cert_verify_failed(*a, **k):
        raise OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    monkeypatch.setattr(prices.urllib.request, "urlopen", _cert_verify_failed)

    class _FakeResponse:
        def json(self):
            return {
                "quotes": [{"symbol": "DASH", "shortname": "DoorDash, Inc.", "quoteType": "EQUITY"}]
            }

        def close(self):
            pass

    class _FakeYfData:
        def get(self, url, params=None, timeout=30):
            return _FakeResponse()

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    assert search_symbol("DoorDash")["symbol"] == "DASH"
