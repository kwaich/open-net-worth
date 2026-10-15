"""services/feeds_regulatory.py — the per-market regulatory-disclosure REGISTRY (owner
directive 2026-07-15, open-source generalization amendment to news-ingestion-latency.md).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from backend.db.evidence_store import EvidenceStore
from backend.services import feeds_regulatory as fr


def _pub_date_now() -> str:
    """An RSS pubDate a moment ago. Evidence expires 90 days after an article's OWN publish
    date and an expired item is never stored, so a fixed date turns this suite red on a
    calendar day nobody chose."""
    return format_datetime(datetime.now(timezone.utc) - timedelta(hours=1), usegmt=True)


def test_de_suffix_resolves_to_eqs_firehose():
    market = fr.regulatory_feeds_for_market("DE")
    assert len(market) == 1
    assert market[0].via == "rss:fn_adhoc"
    assert "finanznachrichten.de" in market[0].url_fn()


def test_ch_at_suffixes_also_resolve_to_eqs_firehose():
    for suffix in ("SW", "VI", "F"):
        market = fr.regulatory_feeds_for_market(suffix)
        assert market[0].via == "rss:fn_adhoc"


def test_unknown_suffix_falls_back_to_edgar_default():
    for suffix in ("", "T", "HK", "US", None):
        market = fr.regulatory_feeds_for_market(suffix)
        assert len(market) == 1
        assert market[0].via == "atom:edgar"


def test_edgar_feed_declares_its_own_user_agent():
    market = fr.regulatory_feeds_for_market("US")
    assert market[0].user_agent == fr.EDGAR_USER_AGENT
    assert "OpenNetWorth" in fr.EDGAR_USER_AGENT


def test_eqs_feed_has_no_custom_user_agent():
    # None → caller falls back to its own default RSS UA (no SEC-style fair-use requirement).
    market = fr.regulatory_feeds_for_market("DE")
    assert market[0].user_agent is None


def test_all_regulatory_feeds_is_the_distinct_set():
    feeds_list = fr.all_regulatory_feeds()
    vias = {f.via for f in feeds_list}
    assert vias == {"rss:fn_adhoc", "atom:edgar"}  # exactly 2 distinct sources in v1
    # every registered suffix's feed is a member of this distinct set
    for suffix in ("DE", "F", "VI", "SW"):
        assert all(f in feeds_list for f in fr.regulatory_feeds_for_market(suffix))


def test_edgar_url_is_the_current_8k_atom_endpoint():
    url = fr._edgar_current_8k_url()
    assert "sec.gov" in url
    assert "type=8-K" in url
    assert "output=atom" in url


# ── SF-1 (code review): via must actually LAND in the stored evidence row ────────────
#
# A registry-attribute check (`feed.via == "rss:fn_adhoc"`) gives false confidence — parse_rss's
# bare default_via="rss" meant every regulatory hit was landing in evidence as the literal
# "rss", never its real per-market via, until rss_poller_hot.py started threading
# `via=feed.via` through gather_matched. This drives each registered feed through the real
# gather_matched -> record_evidence path (exactly how rss_poller_hot._run_feed_registry calls
# it) to prove what actually persists.


@pytest.fixture
def store(db_isolated):
    return EvidenceStore()


def _rss_fixture(headline: str, link: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Disclosures</title>
<item><title><![CDATA[{headline}]]></title>
<link>{link}</link>
<pubDate>{_pub_date_now()}</pubDate></item>
</channel></rss>""".encode()


def test_each_regulatory_feed_lands_its_own_via_in_the_evidence_row(store, monkeypatch):
    from backend.monitoring import collect

    for feed in fr.all_regulatory_feeds():
        link = f"https://example.com/story-{feed.via}"  # unique per feed — avoid dedup collision
        raw = _rss_fixture("ONWAI SE ad-hoc disclosure", link=link)
        monkeypatch.setattr(
            collect,
            "_fetch_feed",
            lambda url, max_age=None, user_agent=None, parser=None, _raw=raw: parser(_raw),
        )
        entities = [{"subject": "asset:onwai-iid", "terms": ["ONWAI SE", "ONWAI.DE"]}]

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

        rows = store.for_subjects(["asset:onwai-iid"], now="")
        matching = [r for r in rows if r["url"] == link]
        assert matching, f"no stored row for feed {feed.via}"
        assert matching[0]["via"] == feed.via  # NOT parse_rss's generic "rss"
