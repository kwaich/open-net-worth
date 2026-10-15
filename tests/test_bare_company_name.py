"""Regression: a legal name stripped only once never matched a real headline (owner report
2026-09-24, live PLTR). "Palantir Technologies Inc." reduced to "Palantir Technologies" and
"Tesla, Inc." to "Tesla," — so the asset page dropped 31 of 44 genuine Yahoo stories, and the
shared-firehose ingest matcher never routed a "Tesla …" / "Palantir …" headline at all."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.core.news import bare_company_name, title_mentions_company
from backend.monitoring.rss_poller_hot import _monitor_news_entities
from backend.services.asset_detail import _live_news_for_instrument
from backend.services.evidence import record_evidence
from backend.services.feeds_hot import match_hits_to_subjects


@pytest.mark.parametrize(
    ("label", "bare"),
    [
        ("Palantir Technologies Inc.", "Palantir"),
        ("Micron Technology, Inc.", "Micron"),
        ("Tesla, Inc.", "Tesla"),
        ("Meta Platforms, Inc.", "Meta Platforms"),
        ("ServiceNow, Inc.", "ServiceNow"),
        ("JPMorgan Chase & Co.", "JPMorgan Chase"),
        ("Kestrel Dash SE", "Kestrel Dash"),
        ("NVIDIA Corporation", "NVIDIA"),
        # A descriptor is only dropped when ONE distinctive word survives — never down to a
        # generic phrase every unrelated story would substring-match.
        ("Space Exploration Technologies Corp.", "Space Exploration Technologies"),
        ("Interactive Brokers Group, Inc.", "Interactive Brokers Group"),
        ("Bitcoin", None),
        ("Vanguard S&P 500 ETF", None),
        ("", None),
    ],
)
def test_bare_company_name(label, bare):
    assert bare_company_name(label) == bare


def test_headline_naming_only_the_brand_matches_the_legal_name():
    assert title_mentions_company(
        "Palantir Hires Ex-Labour Deputy Tom Watson as It Aims to Expand U.K. Operations",
        ["Palantir Technologies Inc."],
        tickers=["PLTR"],
    )
    assert not title_mentions_company(
        "Surf Air Mobility Signs OperatorOS Contract with SkyDance Air",
        ["Palantir Technologies Inc."],
        tickers=["PLTR"],
    )


def test_asset_page_keeps_yahoo_stories_that_name_the_brand(db_isolated):
    published = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    titles = [
        "Palantir Hires Ex-Labour Deputy Tom Watson as It Aims to Expand U.K. Operations",
        "Why Palantir Stock Popped Today",
        "Surf Air Mobility Signs OperatorOS Contract with SkyDance Air",
    ]
    record_evidence(
        [
            {
                "title": t,
                "url": f"https://finance.yahoo.com/{i}",
                "source": "Yahoo",
                "published_date": published,
                "via": "rss:yahoo",
            }
            for i, t in enumerate(titles)
        ],
        subjects=["asset:pltr"],
        kind="news",
    )

    news = _live_news_for_instrument(
        "pltr",
        "Palantir Technologies Inc.",
        relevance_terms=["Palantir Technologies Inc."],
        relevance_tickers=["PLTR"],
    )

    assert sorted(n["title"] for n in news) == sorted(titles[:2])


def test_ingest_routes_a_firehose_headline_that_drops_the_legal_suffix():
    entities = _monitor_news_entities(
        [
            {"type": "asset", "instrument_id": "tsla", "label": "Tesla, Inc.", "value": "TSLA"},
            {
                "type": "asset",
                "instrument_id": "pltr",
                "label": "Palantir Technologies Inc.",
                "value": "PLTR",
            },
        ]
    )
    hits = [
        {"title": "Tesla recalls 40,000 vehicles over steering fault"},
        {"title": "Palantir wins Army contract extension"},
    ]

    matched = match_hits_to_subjects(hits, entities)

    assert [m["_subjects"] for m in matched] == [["asset:tsla"], ["asset:pltr"]]
