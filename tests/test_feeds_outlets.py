"""services/feeds_outlets.py — the curated outlet-headline REGISTRY (INT8, live finding
2026-07-16: FT's own free RSS carried the Uber/ONWAI SE scoop headline AT publication,
69 minutes before our first aggregator-rewrite copy).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from backend.db.evidence_store import EvidenceStore
from backend.services import feeds_hot
from backend.services import feeds_outlets as fo


def _pub_date_now() -> str:
    """An RSS pubDate a moment ago. Evidence expires 90 days after an article's OWN publish
    date and an expired item is never stored, so a fixed date turns this suite red on a
    calendar day nobody chose."""
    return format_datetime(datetime.now(timezone.utc) - timedelta(hours=1), usegmt=True)


def test_all_outlet_feeds_returns_the_curated_set():
    feeds = fo.all_outlet_feeds()
    assert len(feeds) == 6


def test_every_outlet_feed_has_a_distinct_via():
    feeds = fo.all_outlet_feeds()
    vias = [f.via for f in feeds]
    assert len(vias) == len(set(vias))  # no two feeds share a via — the dashboard attributes each


def test_outlet_feed_urls_are_live_https_and_shape_matches_via():
    feeds = fo.all_outlet_feeds()
    by_via = {f.via: f for f in feeds}
    assert "ft.com" in by_via["rss:ft_companies"].url_fn()
    assert "companies" in by_via["rss:ft_companies"].url_fn()
    assert "ft.com" in by_via["rss:ft_technology"].url_fn()
    assert "technology" in by_via["rss:ft_technology"].url_fn()
    assert "cnbc.com" in by_via["rss:cnbc"].url_fn()
    assert "marketwatch.com" in by_via["rss:marketwatch"].url_fn()
    assert "dowjones.io" in by_via["rss:wsj"].url_fn()
    assert "techcrunch.com" in by_via["rss:techcrunch"].url_fn()


def test_no_outlet_feed_requires_a_custom_user_agent():
    # Unlike EDGAR's regulatory feed, no outlet here mandates its own declared UA — the
    # caller's default RSS User-Agent is sufficient for every entry.
    for feed in fo.all_outlet_feeds():
        assert feed.user_agent is None


def test_every_outlet_feed_uses_parse_rss():
    from backend.services.feeds import parse_rss

    for feed in fo.all_outlet_feeds():
        assert feed.parser is parse_rss


# ── Matching (news-ingestion-latency.md's server-side entity match, reused by outlet feeds) ──


def _ft_style_rss(headline: str, link: str = "https://www.ft.com/content/example") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Companies</title>
<item><title><![CDATA[{headline}]]></title>
<link>{link}</link>
<pubDate>{_pub_date_now()}</pubDate></item>
</channel></rss>""".encode()


def test_ft_style_scoop_headline_tags_the_watched_topic_subject():
    feed = fo.all_outlet_feeds()[0]  # rss:ft_companies
    raw = _ft_style_rss("Uber nears €12.5bn deal to acquire ONWAI SE")
    hits = feed.parser(raw)
    entities = [{"subject": "topic:onwai_ma", "terms": ["ONWAI SE", "ONWAI.DE"]}]
    matched = feeds_hot.match_hits_to_subjects(hits, entities)
    assert len(matched) == 1
    assert matched[0]["_subjects"] == ["topic:onwai_ma"]


def test_unrelated_headline_matches_nothing_and_is_dropped():
    feed = fo.all_outlet_feeds()[0]
    raw = _ft_style_rss("Athens opposes new EU sanctions on Russian shipping")
    hits = feed.parser(raw)
    entities = [{"subject": "topic:onwai_ma", "terms": ["ONWAI SE", "ONWAI.DE"]}]
    matched = feeds_hot.match_hits_to_subjects(hits, entities)
    assert matched == []


# ── SF-1 (code review): via must actually LAND in the stored evidence row, per feed ──────
#
# A registry-attribute check (`feed.parser is parse_rss`) gives false confidence — parse_rss's
# bare default_via="rss" means EVERY outlet's hits were landing in evidence as the literal
# "rss", never their real per-source via, until rss_poller_hot.py started threading
# `via=feed.via` through gather_matched. These tests run a feed's OWN parser over a realistic
# fixture and drive it through the real gather_matched -> record_evidence path (exactly how
# rss_poller_hot._run_feed_registry calls it) to prove what actually persists.


@pytest.fixture
def store(db_isolated):
    return EvidenceStore()


def test_each_outlet_feed_lands_its_own_via_in_the_evidence_row(store, monkeypatch):
    from backend.monitoring import collect

    for feed in fo.all_outlet_feeds():
        link = f"https://example.com/story-{feed.via}"  # unique per feed — avoid dedup collision
        raw = _ft_style_rss("Uber nears €12.5bn deal to acquire ONWAI SE", link=link)
        monkeypatch.setattr(
            collect,
            "_fetch_feed",
            lambda url, max_age=None, user_agent=None, parser=None, _raw=raw: parser(_raw),
        )
        entities = [{"subject": "topic:onwai_ma", "terms": ["ONWAI SE", "ONWAI.DE"]}]

        asyncio.run(
            collect.gather_matched(
                feed.url_fn(),
                entities,
                parser=feed.parser,
                user_agent=feed.user_agent,
                via=feed.via,
                store=store,
            )
        )

        rows = store.for_subjects(["topic:onwai_ma"], now="")
        matching = [r for r in rows if r["url"] == link]
        assert matching, f"no stored row for feed {feed.via}"
        assert matching[0]["via"] == feed.via  # NOT parse_rss's generic "rss"
