"""Unit tests for `backend.services.asset_detail._live_news_for_instrument` — the LIVE query
over `evidence` that replaced `asset_brief.news[]` (RFC intel-signals-data-model §5).

Covers:
- subject filtering: only THIS instrument's evidence rows are returned
- recency window: `fresh_only` TTL excludes an evidence row published outside the 90-day
  publish-based retention news/asset evidence rows carry
- quality filters applied in order: undated / generic-reference-titled / filename-shaped-titled
  rows are dropped, same-publisher and cross-publisher near-duplicates collapse
- Google-News-wrapped items resolve to the real publisher's domain, not the redirect wrapper
  (the regression `test_foldback_shows_publisher_not_google_news_wrapper` used to cover against
  the now-deleted `asset_brief.py` fold-back — this is the same guarantee at its new home)
- depth: more than 20 fresh cached articles for one subject all surface (the now-deleted
  `test_refresh_retains_more_than_20_cached_articles`'s replacement)
- unsafe URL schemes never reach the wire
- the wire shape carries no `headline`/`summary` key — raw `title` only, never an LLM rewrite
- regression: no LLM call happens anywhere in this path (the seam is mocked and asserted
  never called), on top of the existing static "module imports no ai/monitoring" guard
  (tests/test_asset_detail_service.py::test_service_module_does_not_import_ai_or_monitoring)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from backend.services.asset_detail import _live_news_for_instrument
from backend.services.evidence import record_evidence

_SUBJECT = "asset:inst-1"

# Content-DISTINCT titles (not "Distinct story number {i}") — a templated title differing only
# by a digit false-positives as a cross-publisher near-duplicate once
# `cluster_by_content_overlap`'s token-overlap clustering runs (mirrors
# tests/test_asset_brief_refresh.py's own `_DISTINCT_STORY_TITLES`, kept local to this file
# rather than imported so this test module has no cross-file test dependency).
_DISTINCT_STORY_TITLES = [
    "quarterly profit exceeds forecast",
    "factory recall widens scope",
    "chief executive steps down",
    "shipping delays disrupt deliveries",
    "antitrust regulators open inquiry",
    "board raises cash dividend",
    "buyback program gets expanded",
    "new plant breaks ground",
    "union workers reject contract",
    "rival files patent claim",
    "Moody's lifts credit outlook",
    "Fitch cuts debt rating",
    "merger rumors resurface again",
    "unit spinoff gains approval",
    "hackers breach customer database",
    "ransomware halts production line",
    "finance chief quits abruptly",
    "headcount reduction plan unveiled",
    "thousand jobs cut nationwide",
    "recruiting pause takes effect",
    "flagship device debuts globally",
    "distribution deal inked overseas",
    "venture combines two rivals",
    "watchdog scrutinizes pricing practices",
    "board weighs stock split",
    "trader charged with fraud",
    "regulator levies pollution penalty",
    "brakes prompt vehicle recall",
    "trial data shows promise",
    "regulators clear new therapy",
    "dozens of outlets shutter",
    "footprint grows across Asia",
    "weak yen pressures margins",
    "outlook trimmed for quarter",
    "brokerage lifts price target",
    "hedge fund exits position",
    "investors settle class action",
    "activist wins board seat",
    "fund discloses large stake",
    "notes priced above par",
]


def _seed(rows: list[dict], *, subjects: list[str] | None = None) -> None:
    record_evidence(rows, subjects=subjects or [_SUBJECT], kind="news")


def _row(title: str, url: str, *, hours_ago: float = 1.0, **extra) -> dict:
    published = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"title": title, "url": url, "source": "Src", "published_date": published, **extra}


# ── subject filtering ────────────────────────────────────────────────────────


def test_only_this_instruments_evidence_is_returned(db_isolated):
    _seed([_row("Instrument One story", "https://example.com/one")], subjects=["asset:inst-1"])
    _seed([_row("Instrument Two story", "https://example.com/two")], subjects=["asset:inst-2"])

    news = _live_news_for_instrument("inst-1", "Instrument One")

    assert [n["title"] for n in news] == ["Instrument One story"]


def test_no_evidence_returns_empty_list(db_isolated):
    assert _live_news_for_instrument("inst-1", "Nobody") == []


# ── recency window ───────────────────────────────────────────────────────────


def test_stale_evidence_past_the_publish_retention_window_is_excluded(db_isolated):
    """news/asset evidence rows expire 90 days after their own publish date
    (`services.evidence._compute_expires`) — `fresh_only=True` is the live query's recency
    window, no second filter needed on top of it."""
    old_published = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    _seed(
        [
            {
                "title": "Ancient story",
                "url": "https://example.com/old",
                "source": "Src",
                "published_date": old_published,
            }
        ]
    )

    assert _live_news_for_instrument("inst-1", "Test Co") == []


def test_recent_evidence_within_the_window_is_included(db_isolated):
    _seed([_row("Recent story", "https://example.com/recent", hours_ago=2)])

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert [n["title"] for n in news] == ["Recent story"]


# ── quality filters ───────────────────────────────────────────────────────────


def test_undated_row_is_dropped(db_isolated):
    _seed(
        [
            {"title": "No date at all", "url": "https://example.com/undated", "source": "Src"},
            _row("A real dated story", "https://example.com/dated", hours_ago=1),
        ]
    )

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert [n["title"] for n in news] == ["A real dated story"]


def test_story_tagged_to_another_companys_subject_too_is_dropped(db_isolated):
    """Owner report 2026-08-30: a sector-roundup story correctly matched to SEVERAL held/
    watched companies at once by the RSS entity-matching fan-out (e.g. "Meta Stock And 2
    Founder Led AI Picks") still leads a DIFFERENT company's own dedicated feed, diluting it
    with news that isn't really about that company. Its title doesn't NAME this company
    either, so it has no escape hatch (see the test below for the case where it does)."""
    _seed(
        [_row("AI stocks roundup mentioning several names", "https://example.com/roundup")],
        subjects=["asset:inst-1", "asset:inst-2"],
    )
    _seed(
        [_row("A story genuinely just about this company", "https://example.com/solo")],
        subjects=["asset:inst-1"],
    )

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert [n["title"] for n in news] == ["A story genuinely just about this company"]


def test_cross_tagged_story_survives_when_its_own_title_names_this_company(db_isolated):
    """Owner correction 2026-08-30: a bare exclusivity rule would ALSO drop a genuine
    two-company story (an acquisition, an M&A pair — the real Uber/Kestrel Dash story this
    whole investigation started from) from BOTH companies' feeds, which is exactly wrong. A
    cross-tagged row survives when its OWN title names this company, suffix stripped
    ("Test Co SE" -> "Test Co" — a real headline drops the formal corporate suffix)."""
    _seed(
        [
            _row(
                "Acquirer Corp to Buy Test Co in $10B Deal",
                "https://example.com/acquisition",
            )
        ],
        subjects=["asset:inst-1", "asset:inst-2"],
    )

    news = _live_news_for_instrument("inst-1", "Test Co SE", relevance_terms=["Test Co SE", "TCO"])

    assert [n["title"] for n in news] == ["Acquirer Corp to Buy Test Co in $10B Deal"]


def test_story_tagged_to_a_topic_alongside_this_asset_is_kept():
    """A topic:/scan: co-tag never counts toward the other-asset-subjects exclusivity check —
    only OTHER asset:-type subjects do (see is_too_broad_for_subject's own docstring)."""
    from backend.core.news import is_too_broad_for_subject

    row_subjects = ["asset:inst-1", "topic:ai-stocks", "scan:intel"]
    assert is_too_broad_for_subject(row_subjects, "asset:inst-1") is False


def test_generic_reference_title_is_dropped(db_isolated):
    _seed(
        [
            _row("Kestrel Dash SE Company Overview", "https://reuters.com/overview", hours_ago=1),
            _row(
                "Kestrel Dash posts record Q3 revenue", "https://reuters.com/earnings", hours_ago=2
            ),
        ]
    )

    news = _live_news_for_instrument("inst-1", "Kestrel Dash")

    assert [n["title"] for n in news] == ["Kestrel Dash posts record Q3 revenue"]


def test_robinhood_stock_quote_page_title_is_dropped(db_isolated):
    """Owner report 2026-08-30: a distinct publisher template ("Stock Price Quote & News",
    robinhood.com) from the earlier-fixed Reuters one ("Stock Price & Latest News") — same
    generic-reference-page class, different exact wording."""
    _seed(
        [
            _row(
                "Oracle: ORCL Stock Price Quote & News",
                "https://robinhood.com/stocks/ORCL",
                hours_ago=1,
            ),
            _row(
                "Oracle beats Q3 earnings estimates on cloud growth",
                "https://example.com/orcl-earnings",
                hours_ago=2,
            ),
        ]
    )

    news = _live_news_for_instrument("inst-1", "Oracle")

    assert [n["title"] for n in news] == ["Oracle beats Q3 earnings estimates on cloud growth"]


def test_filename_shaped_title_is_dropped(db_isolated):
    _seed(
        [
            _row("IMG_20260830_142233", "https://example.com/img", hours_ago=1),
            _row("A genuine headline about the company", "https://example.com/real", hours_ago=2),
        ]
    )

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert [n["title"] for n in news] == ["A genuine headline about the company"]


def test_offtopic_shared_firehose_item_is_dropped_even_when_solely_tagged(db_isolated):
    """Owner report 2026-08-31, live ServiceNow: a row tagged SOLELY to this subject (so
    `is_too_broad_for_subject` never flags it) can still be wrong when the tag itself came
    purely from the ticker "NOW" case-insensitively matching ordinary prose rather than the row
    being genuinely about the company. CNBC's/MarketWatch's general top-stories firehoses
    (`via="rss:cnbc"`/`"rss:marketwatch"`) are in `is_offtopic_ticker_feed_item`'s scope now,
    same as the earlier `rss:yahoo`/NVDA incident — a title that doesn't name the company by
    any of its `relevance_terms`/`relevance_tickers` is dropped. `relevance_tickers=["NOW"]` is
    matched exact-case (owner correction: this must be generic for any ticker/any user, never a
    curated list of "known collision" symbols) — proven generic (not special-cased to "NOW"
    specifically) by the sibling test using a made-up ticker below."""
    _seed(
        [
            _row(
                "5 affordable cities to retire in the U.S. right now",
                "https://marketwatch.com/retire",
                via="rss:marketwatch",
            ),
            _row(
                "Gen Zers quit their jobs to start a pickle company",
                "https://cnbc.com/pickle",
                via="rss:cnbc",
            ),
            _row(
                "ServiceNow Beats Q2 Estimates, Lifts Full-Year Outlook",
                "https://cnbc.com/now-earnings",
                via="rss:cnbc",
            ),
        ]
    )

    news = _live_news_for_instrument(
        "inst-1", "ServiceNow", relevance_terms=["ServiceNow"], relevance_tickers=["NOW"]
    )

    assert [n["title"] for n in news] == ["ServiceNow Beats Q2 Estimates, Lifts Full-Year Outlook"]


def test_ticker_exact_case_match_is_generic_not_special_cased_to_now(db_isolated):
    """Proves the fix is structural (any ticker in `relevance_tickers`), not a curated list of
    "known collision" symbols — a completely made-up ticker never seen by this codebase before
    gets the identical protection ServiceNow's "NOW" does, with zero code changes needed to
    cover it."""
    _seed(
        [
            _row(
                "Investors debate whether this rally can go the distance",
                "https://marketwatch.com/rally",
                via="rss:marketwatch",
            ),
            _row(
                "Voyage Corp Reports Record Quarterly Revenue",
                "https://marketwatch.com/go-earnings",
                via="rss:marketwatch",
            ),
        ]
    )

    news = _live_news_for_instrument(
        "inst-1", "Voyage Corp", relevance_terms=["Voyage Corp"], relevance_tickers=["GO"]
    )

    assert [n["title"] for n in news] == ["Voyage Corp Reports Record Quarterly Revenue"]


def test_offtopic_ticker_feed_check_leaves_a_google_news_result_alone(db_isolated):
    """A Google-News search result (`via="rss:google_news"`) was already queried using this
    company's own name — absence from its own title isn't itself suspicious, so it's outside
    `is_offtopic_ticker_feed_item`'s scope and survives even without naming the company."""
    _seed(
        [
            _row(
                "Analysts weigh in on the SaaS sector's latest earnings season",
                "https://example.com/saas-roundup",
                via="rss:google_news",
            ),
        ]
    )

    news = _live_news_for_instrument(
        "inst-1", "ServiceNow", relevance_terms=["ServiceNow"], relevance_tickers=["NOW"]
    )

    assert len(news) == 1


def test_same_publisher_near_duplicates_collapse(db_isolated):
    _seed(
        [
            _row(
                "Test Co In 'Advanced' Talks To Acquire Rival",
                "https://investors.com/a",
                source="Investor's Business Daily",
                hours_ago=1,
            ),
            _row(
                "Test Co in advanced talks to acquire rival",
                "https://investors.com/b",
                source="Investor's Business Daily",
                hours_ago=2,
            ),
        ]
    )

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert len(news) == 1


# ── Google-News-wrapper domain resolution (regression) ───────────────────────


def test_google_news_wrapped_item_resolves_to_real_publisher_domain(db_isolated):
    """A Google News RSS item links to news.google.com (redirect wrapper) but carries the real
    publisher in <source>/<source url>. The live-queried item must display that publisher
    ('Benzinga'), not the wrapper host 'news.google.com' — the same guarantee the now-deleted
    asset_brief.py fold-back used to provide, now provided by `_evidence_row_to_item`."""
    _seed(
        [
            {
                "title": "What's Going On With AMD Stock - Benzinga",
                "url": "https://news.google.com/rss/articles/CBMiABC123",
                "source": "Benzinga",
                "source_url": "https://www.benzinga.com",
                "published_date": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            }
        ]
    )

    news = _live_news_for_instrument("inst-1", "AMD")

    assert len(news) == 1
    assert news[0]["source"] == "Benzinga"
    assert news[0]["domain"] == "benzinga.com"  # NOT news.google.com


# ── depth (regression) ────────────────────────────────────────────────────────


def test_more_than_20_fresh_articles_all_surface(db_isolated):
    """Regression: a hot ticker's evidence isn't silently capped at some old hardcoded 20 —
    replaces the now-deleted test_refresh_retains_more_than_20_cached_articles, which proved
    the same depth guarantee against the deleted asset_brief.news[] cache."""
    rows = [
        _row(_DISTINCT_STORY_TITLES[i], f"https://example.com/{i:02d}", hours_ago=1 + i)
        for i in range(35)
    ]
    _seed(rows)

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert len(news) == 35


def test_limit_bounds_how_many_evidence_rows_are_considered(db_isolated):
    rows = [
        _row(_DISTINCT_STORY_TITLES[i], f"https://example.com/{i:02d}", hours_ago=1 + i)
        for i in range(10)
    ]
    _seed(rows)

    news = _live_news_for_instrument("inst-1", "Test Co", limit=3)

    assert len(news) == 3


# ── URL safety ────────────────────────────────────────────────────────────────


def test_unsafe_url_scheme_is_blanked_not_dropped(db_isolated):
    _seed([_row("A story with an unsafe link", "javascript:evil()", hours_ago=1)])

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert len(news) == 1  # the item survives — only its url is blanked
    assert news[0]["url"] == ""


# ── wire shape: raw title, no LLM-authored fields ────────────────────────────


def test_item_shape_has_no_headline_or_summary_key(db_isolated):
    """`snippet` (owner ask 2026-08-30) is the article's own raw excerpt, same "no LLM touches
    this" guarantee as every other field here — it stays in the wire shape. `headline`/`summary`
    are the LLM-AUTHORED fields the RFC explicitly retired and must never come back."""
    _seed([_row("Raw as-published title, verbatim", "https://example.com/x", hours_ago=1)])

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert len(news) == 1
    item = news[0]
    assert item["title"] == "Raw as-published title, verbatim"
    assert "headline" not in item
    assert "summary" not in item
    assert set(item.keys()) == {
        "title",
        "source",
        "url",
        "domain",
        "published_at",
        "image",
        "snippet",
    }


def test_item_snippet_passes_through_the_raw_source_excerpt(db_isolated):
    _seed(
        [
            _row(
                "A story with a real excerpt",
                "https://example.com/y",
                hours_ago=1,
                snippet="The company reported record revenue, beating consensus by 12%...",
            )
        ]
    )

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert len(news) == 1
    assert news[0]["snippet"] == "The company reported record revenue, beating consensus by 12%..."


def test_item_snippet_defaults_to_empty_string_when_absent(db_isolated):
    _seed([_row("A story with no excerpt", "https://example.com/z", hours_ago=1)])

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert len(news) == 1
    assert news[0]["snippet"] == ""


def test_item_snippet_blanked_when_redundant_with_title(db_isolated):
    """Owner report 2026-08-30: Google News' RSS <description> is frequently just the <title>
    again with the publisher name appended via a non-breaking-space separator, not a real
    excerpt — showing it visually duplicates the title line right above it."""
    _seed(
        [
            _row(
                "BTC price range on Aug 30, 2026 at 10am EDT Crypto Prediction Market - Robinhood",
                "https://example.com/w",
                hours_ago=1,
                snippet=(
                    "BTC price range on Aug 30, 2026 at 10am EDT Crypto Prediction "
                    "Market\xa0\xa0Robinhood"
                ),
            )
        ]
    )

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert len(news) == 1
    assert news[0]["snippet"] == ""


# ── no LLM call anywhere in this path (regression) ────────────────────────────


def test_no_llm_call_happens_in_the_live_news_path(db_isolated, monkeypatch):
    """The whole point of this RFC: raw titles, never an LLM rewrite. Mocks the ONE seam every
    synthesis call in this codebase goes through (`backend.ai.structured.complete_model`) and
    proves it is never invoked while building the live news feed — complementing the existing
    STATIC guard (test_asset_detail_service.py::test_service_module_does_not_import_ai_or_monitoring)
    with a BEHAVIORAL one."""
    from backend.ai import structured as _structured

    complete_model_spy = AsyncMock()
    monkeypatch.setattr(_structured, "complete_model", complete_model_spy)

    _seed(
        [
            _row("Messy SEO Title!! Click Here", "https://example.com/1", hours_ago=1),
            _row("Another raw headline about the company", "https://example.com/2", hours_ago=2),
        ]
    )

    news = _live_news_for_instrument("inst-1", "Test Co")

    assert len(news) == 2
    assert news[0]["title"] == "Messy SEO Title!! Click Here"  # verbatim — never rewritten
    complete_model_spy.assert_not_called()


def test_no_llm_call_via_read_asset_detail_end_to_end(db_isolated, monkeypatch):
    """Same proof, driven through the real REST-facing composer (`read_asset_detail`) rather
    than the helper directly, so the guarantee holds for the actual response path too."""
    from backend.ai import structured as _structured
    from backend.db.instrument_store import InstrumentStore
    from backend.services import fx_cache as _fx
    from backend.services.asset_detail import read_asset_detail
    from backend.services.positions import hydrated as hydrated_positions

    complete_model_spy = AsyncMock()
    monkeypatch.setattr(_structured, "complete_model", complete_model_spy)
    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"USD": 1.0})
    monkeypatch.setattr(
        "backend.services.asset_detail.read_profile", lambda: {"base_currency": "USD"}
    )
    monkeypatch.setattr("backend.services.asset_detail.load_fx_cache", lambda: {"USD": 1.0})
    assert hydrated_positions is not None  # sanity: real service, not monkeypatched away

    InstrumentStore().insert(
        {
            "id": "inst-1",
            "symbol": "TST",
            "name": "Test Co",
            "asset_class": "equity",
            "asset_subclass": "stock",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "TST.US",
            "identifiers": {},
            "quote_per_unit": 1.0,
        }
    )
    _seed([_row("A real headline, unmodified", "https://example.com/1", hours_ago=1)])

    detail = read_asset_detail("inst-1", record_price_history=False, persist_exchange=False)

    assert detail is not None
    assert len(detail["news"]) == 1
    assert detail["news"][0]["title"] == "A real headline, unmodified"
    complete_model_spy.assert_not_called()
