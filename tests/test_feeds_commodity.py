"""Commodity query fix + resolve_opportunity_feeds (pure; no network).

Phase 3b:
  * resolve_holding_feeds for asset_class=="commodity" must use a market/price query,
    NOT "stock" (a commodity like GLD has no equity story; "stock" returns wrong results).
  * crypto and equity paths are unchanged.
  * resolve_opportunity_feeds maps discovery queries → Google News feeds tagged scan:opportunity.
"""

from __future__ import annotations

import urllib.parse

from backend.services import feeds

# ── commodity query fix ───────────────────────────────────────────────────────────


def test_commodity_uses_price_market_query_not_stock():
    row = {
        "instrument_id": "c1",
        "price_source_id": "GLD",
        "label": "Gold",
        "asset_class": "commodity",
    }
    fs = feeds.resolve_holding_feeds(row)
    assert fs, "should return feeds for a commodity holding"
    # No feed URL must contain "stock" in the encoded query
    for f in fs:
        decoded = urllib.parse.unquote(f["url"])
        assert "stock" not in decoded.lower(), (
            f"commodity query must not include 'stock'; got: {decoded}"
        )
    # All feeds must contain a price/market-flavored keyword
    queries = [urllib.parse.unquote(f["url"]) for f in fs if "google" in f["url"]]
    assert queries, "expected at least one Google News feed"
    assert any(any(kw in q.lower() for kw in ("price", "commodity", "market")) for q in queries), (
        f"expected price/commodity/market keyword in commodity query; got: {queries}"
    )


def test_commodity_subjects_tagged_correctly():
    row = {
        "instrument_id": "c2",
        "price_source_id": "XAUUSD",
        "label": "Gold",
        "asset_class": "commodity",
    }
    fs = feeds.resolve_holding_feeds(row)
    for f in fs:
        assert "asset:c2" in f["subjects"]
        assert "scan:intel" in f["subjects"]


def test_commodity_includes_yahoo_feed_when_ticker_present():
    row = {
        "instrument_id": "c3",
        "price_source_id": "GLD",
        "label": "SPDR Gold",
        "asset_class": "commodity",
    }
    fs = feeds.resolve_holding_feeds(row)
    yahoo = [f for f in fs if f["via"] == "rss:yahoo"]
    assert yahoo, "commodity with a ticker should also have a Yahoo feed"


def test_commodity_no_ticker_no_yahoo():
    row = {"instrument_id": "c4", "label": "Gold Futures", "asset_class": "commodity"}
    fs = feeds.resolve_holding_feeds(row)
    # No ticker → no Yahoo feed
    assert all(f["via"] != "rss:yahoo" for f in fs)


# ── equity and crypto paths unchanged ────────────────────────────────────────────


def test_equity_still_uses_stock_query():
    row = {
        "instrument_id": "e1",
        "price_source_id": "AAPL",
        "label": "Apple",
        "asset_class": "equity",
    }
    fs = feeds.resolve_holding_feeds(row)
    gnews = [urllib.parse.unquote(f["url"]) for f in fs if "google" in f["url"]]
    assert any("stock" in q.lower() for q in gnews), "equity must still use 'stock' in query"


def test_crypto_unchanged():
    row = {"instrument_id": "b1", "ticker": "BTC", "label": "Bitcoin", "asset_class": "crypto"}
    fs = feeds.resolve_holding_feeds(row)
    assert len(fs) == 1 and fs[0]["via"] == "rss:google_news"
    decoded = urllib.parse.unquote(fs[0]["url"])
    assert "crypto" in decoded.lower() and "stock" not in decoded.lower()


# ── resolve_opportunity_feeds ─────────────────────────────────────────────────────


def test_resolve_opportunity_feeds_returns_google_news_feeds():
    queries = ["top stock movers this week", "sector rotation opportunities"]
    fs = feeds.resolve_opportunity_feeds(queries)
    assert len(fs) == 2
    for f in fs:
        assert f["via"] == "rss:google_news"
        assert f["url"].startswith("https://news.google.com/rss/search")
        assert f["subjects"] == ["scan:opportunity"]
        assert f["kind"] == "opportunity"


def test_resolve_opportunity_feeds_encodes_query():
    fs = feeds.resolve_opportunity_feeds(["top movers & gainers"])
    assert len(fs) == 1
    assert "top%20movers" in fs[0]["url"] or "top+movers" in fs[0]["url"]


def test_resolve_opportunity_feeds_skips_blank_queries():
    fs = feeds.resolve_opportunity_feeds(["", "  ", "real query"])
    assert len(fs) == 1
    assert "real query" in urllib.parse.unquote(fs[0]["url"])


def test_resolve_opportunity_feeds_empty_list():
    assert feeds.resolve_opportunity_feeds([]) == []


def test_resolve_opportunity_feeds_subjects_are_scan_opportunity_only():
    """Opportunity feeds must NOT carry scan:intel — they belong to a separate evidence lane."""
    fs = feeds.resolve_opportunity_feeds(["some query"])
    for f in fs:
        assert "scan:intel" not in f["subjects"]
        assert "scan:opportunity" in f["subjects"]
