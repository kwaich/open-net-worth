"""Crypto quote stats (CHART3) — the CoinGecko counterpart of the Yahoo stats strip.

Covers:
- `_parse_crypto_stats`: pure /coins/{id} → stats shaping in the requested vs-currency
  (money in vs, supplies currency-free, derived vol/market-cap ratio, absent-not-null).
- `fetch_crypto_quote_stats`: USD fallback when CoinGecko doesn't serve the requested
  fiat; empty stats (never a raise) when the payload fetch failed.
- `asset_detail.quote_stats`: kind discrimination — the crypto branch extracts in the
  DISPLAY currency (the chart/headline rule) and reports the currency stats were actually
  extracted in; the equity branch is byte-identical to the pre-CHART3 shape plus
  kind="equity".

No network — the payload fetch is monkeypatched, matching test_coin_facts.py.
"""

from __future__ import annotations

from backend.services import asset_detail as ad
from backend.services import prices as prices_mod
from backend.services.prices import _parse_crypto_stats, fetch_crypto_quote_stats

BTC_PAYLOAD = {
    "genesis_date": "2009-01-03",
    "market_data": {
        "market_cap": {"usd": 1_284_000_000_000, "sgd": 1_720_000_000_000},
        "fully_diluted_valuation": {"usd": 1_350_000_000_000, "sgd": 1_810_000_000_000},
        "total_volume": {"usd": 28_940_000_000, "sgd": 38_800_000_000},
        "low_24h": {"usd": 63_899.15, "sgd": 85_600.0},
        "high_24h": {"usd": 64_893.33, "sgd": 86_900.0},
        "ath": {"usd": 126_198.07, "sgd": 169_000.0},
        "atl": {"usd": 171.51, "sgd": 230.0},
        "circulating_supply": 20_060_000,
        "total_supply": 20_060_000,
        "max_supply": 21_000_000,
    },
}


class TestParseCryptoStats:
    def test_extracts_money_fields_in_requested_vs(self):
        s = _parse_crypto_stats(BTC_PAYLOAD, "sgd")
        assert s["market_cap"] == 1_720_000_000_000
        assert s["fdv"] == 1_810_000_000_000
        assert s["volume_24h"] == 38_800_000_000
        assert s["day_low"] == 85_600.0
        assert s["day_high"] == 86_900.0
        assert s["ath"] == 169_000.0
        assert s["atl"] == 230.0

    def test_supplies_and_genesis_are_currency_free(self):
        s = _parse_crypto_stats(BTC_PAYLOAD, "usd")
        assert s["circulating_supply"] == 20_060_000
        assert s["total_supply"] == 20_060_000
        assert s["max_supply"] == 21_000_000
        assert s["genesis_date"] == "2009-01-03"

    def test_vol_to_mcap_is_derived(self):
        s = _parse_crypto_stats(BTC_PAYLOAD, "usd")
        assert abs(s["vol_to_mcap"] - 28_940_000_000 / 1_284_000_000_000) < 1e-9

    def test_missing_fields_are_absent_not_null(self):
        s = _parse_crypto_stats({"market_data": {"circulating_supply": 5}}, "usd")
        assert s == {"circulating_supply": 5}

    def test_unserved_vs_yields_no_money_fields(self):
        s = _parse_crypto_stats(BTC_PAYLOAD, "vnd")
        assert "market_cap" not in s
        assert s["circulating_supply"] == 20_060_000  # supplies survive


class TestFetchCryptoQuoteStats:
    def test_serves_requested_fiat(self, monkeypatch):
        monkeypatch.setattr(prices_mod, "_fetch_coin_payload", lambda cid: BTC_PAYLOAD)
        out = fetch_crypto_quote_stats("bitcoin", "SGD")
        assert out["currency"] == "SGD"
        assert out["stats"]["market_cap"] == 1_720_000_000_000

    def test_falls_back_to_usd_when_fiat_unserved(self, monkeypatch):
        monkeypatch.setattr(prices_mod, "_fetch_coin_payload", lambda cid: BTC_PAYLOAD)
        out = fetch_crypto_quote_stats("bitcoin", "VND")
        assert out["currency"] == "USD"
        assert out["stats"]["market_cap"] == 1_284_000_000_000

    def test_failed_payload_returns_empty_stats(self, monkeypatch):
        monkeypatch.setattr(prices_mod, "_fetch_coin_payload", lambda cid: {})
        out = fetch_crypto_quote_stats("bitcoin", "SGD")
        assert out == {"currency": "SGD", "stats": {}}


class _FakeInstStore:
    def __init__(self, inst):
        self._inst = inst

    def find_by_id(self, iid):
        return self._inst if self._inst and self._inst.get("id") == iid else None


class TestQuoteStatsKindRouting:
    def test_crypto_branch_uses_display_currency_and_reports_kind(self, monkeypatch):
        inst = {
            "id": "i-btc",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "asset_class": "crypto",
            "quote_currency": "USD",
        }
        monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInstStore(inst))
        monkeypatch.setattr(ad, "_display_currency", lambda i: "SGD")
        monkeypatch.setattr(prices_mod, "_fetch_coin_payload", lambda cid: BTC_PAYLOAD)

        out = ad.quote_stats("i-btc")

        assert out["kind"] == "crypto"
        assert out["currency"] == "SGD"
        assert out["stats"]["market_cap"] == 1_720_000_000_000

    def test_equity_branch_keeps_yahoo_shape(self, monkeypatch):
        inst = {
            "id": "i-aapl",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
            "quote_currency": "USD",
        }
        monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInstStore(inst))
        import backend.services.prices as p

        monkeypatch.setattr(p, "fetch_quote_stats", lambda sym: {"pe_ratio": 38.17})

        out = ad.quote_stats("i-aapl")

        assert out["kind"] == "equity"
        assert out["currency"] == "USD"
        assert out["stats"] == {"pe_ratio": 38.17}

    def test_unknown_instrument_is_empty_equity(self, monkeypatch):
        monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInstStore(None))
        assert ad.quote_stats("nope") == {"currency": "USD", "kind": "equity", "stats": {}}
