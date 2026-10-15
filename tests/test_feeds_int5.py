"""INT5 additions to services/feeds.py: source_kind/cadence stamping, topic when:1d rewrite,
linked_tickers, resolve_custom_feeds (news-ingestion-latency.md §1 + the market-registry/
custom-feeds owner amendment).
"""

from __future__ import annotations

from backend.services import feeds


def test_yahoo_feed_stamps_ticker_fast():
    fs = feeds.resolve_holding_feeds(
        {
            "instrument_id": "i1",
            "price_source_id": "AAPL",
            "label": "Apple",
            "asset_class": "equity",
        }
    )
    yahoo = [f for f in fs if f["via"] == "rss:yahoo"]
    assert len(yahoo) == 1
    assert yahoo[0]["source_kind"] == "ticker"
    assert yahoo[0]["cadence"] == "fast"


def test_google_feed_stamps_search_slow_defaults():
    fs = feeds.resolve_holding_feeds(
        {
            "instrument_id": "i1",
            "price_source_id": "AAPL",
            "label": "Apple",
            "asset_class": "equity",
        }
    )
    gnews = [f for f in fs if f["via"] == "rss:google_news"]
    assert gnews and all(f["source_kind"] == "search" and f["cadence"] == "slow" for f in gnews)


def test_resolve_topic_feeds_rewrites_query_with_when_1d():
    item = {"id": "t1", "search_queries": ["fed rate cut"], "value": "macro"}
    fs = feeds.resolve_topic_feeds(item)
    assert len(fs) == 1  # count UNCHANGED vs pre-INT5 (regression guard)
    assert "when%3A1d" in fs[0]["url"] or "when:1d" in feeds.urllib.parse.unquote(fs[0]["url"])


def test_resolve_topic_feeds_linked_tickers_add_yahoo_lane():
    item = {"id": "t1", "value": "ONWAI M&A", "search_queries": ["Uber ONWAI SE"]}
    fs_no_link = feeds.resolve_topic_feeds(item)

    item_linked = {**item, "linked_tickers": ["ONWAI.DE"]}
    fs_linked = feeds.resolve_topic_feeds(item_linked)

    assert len(fs_linked) == len(fs_no_link) + 1
    yahoo = [f for f in fs_linked if f["via"] == "rss:yahoo"]
    assert len(yahoo) == 1
    assert "ONWAI.DE" in yahoo[0]["url"]
    assert yahoo[0]["subjects"] == ["topic:t1", "scan:intel"]
    assert yahoo[0]["cadence"] == "fast"


def test_resolve_topic_feeds_linked_tickers_blank_entries_skipped():
    item = {"id": "t1", "value": "x", "search_queries": ["x"], "linked_tickers": ["", "  ", None]}
    fs = feeds.resolve_topic_feeds(item)
    assert not any(f["via"] == "rss:yahoo" for f in fs)


def test_resolve_topic_feeds_does_not_include_custom_feeds():
    # resolve_custom_feeds is a SEPARATE resolver (only the hot tier calls it) so a slow-tier/
    # intel-scan caller of resolve_topic_feeds never double-fetches a custom URL.
    item = {
        "id": "t1",
        "value": "x",
        "search_queries": ["x"],
        "custom_feed_urls": ["https://example.com/feed.xml"],
    }
    fs = feeds.resolve_topic_feeds(item)
    assert not any(f["via"] == "rss:custom" for f in fs)


# ── resolve_custom_feeds ────────────────────────────────────────────────────────


def test_resolve_custom_feeds_topic():
    item = {"id": "t1", "type": "topic", "custom_feed_urls": ["https://example.com/a.xml"]}
    fs = feeds.resolve_custom_feeds(item)
    assert len(fs) == 1
    f = fs[0]
    assert f["url"] == "https://example.com/a.xml"
    assert f["subjects"] == ["topic:t1", "scan:intel"]
    assert f["via"] == "rss:custom"
    assert f["source_kind"] == "custom"
    assert f["cadence"] == "hot"


def test_resolve_custom_feeds_ticker():
    item = {
        "type": "ticker",
        "instrument_id": "i1",
        "custom_feed_urls": ["https://example.com/a.xml", "https://example.com/b.xml"],
    }
    fs = feeds.resolve_custom_feeds(item)
    assert len(fs) == 2
    assert all(f["subjects"] == ["asset:i1", "scan:intel"] for f in fs)


def test_resolve_custom_feeds_empty_when_no_urls():
    assert feeds.resolve_custom_feeds({"id": "t1", "type": "topic"}) == []


def test_resolve_custom_feeds_ticker_without_instrument_id_falls_back_to_scan_intel():
    # mirrors resolve_holding_feeds's own no-instrument_id behavior — still scan:intel-tagged.
    fs = feeds.resolve_custom_feeds({"type": "ticker", "custom_feed_urls": ["https://e/a"]})
    assert fs and fs[0]["subjects"] == ["scan:intel"]


def test_resolve_custom_feeds_strips_blank_urls():
    item = {
        "id": "t1",
        "type": "topic",
        "custom_feed_urls": ["", "  ", "https://example.com/a.xml"],
    }
    fs = feeds.resolve_custom_feeds(item)
    assert len(fs) == 1
