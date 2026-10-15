"""REGRESSION (2026-07-15 Uber/ONWAI SE report): the Stories & Analysis grid showed
the SAME story twice — once from the brief's live search (direct publisher URL) and once
folded back from the RSS evidence cache (Google News redirect-wrapper URL). Canonical-URL
dedup can't see through the wrapper, and publishers also retitle developing stories
("… on report of Uber takeover talks" → "… on Uber takeover talks"), so URL identity is
not story identity. `core.news.dedupe_stories` collapses same-publisher near-duplicate
headlines within a publish window, keeping the first (priority-sorted) occurrence.
"""

from backend.core.news import dedupe_stories


def _item(title, source="", domain="", url="", published_at=""):
    return {
        "title": title,
        "source": source,
        "domain": domain,
        "url": url,
        "published_at": published_at,
    }


# ── The three live pairs from the owner's screenshot ───────────────────────────


def test_same_publisher_case_and_punctuation_variant_collapses():
    items = [
        _item(
            "Uber In 'Advanced' Talks To Acquire ONWAI SE",
            source="Investor's Business Daily",
            domain="investors.com",
            url="https://www.investors.com/news/uber-onwai-takeover/",
            published_at="2026-07-14T18:00:00+00:00",
        ),
        _item(
            "Uber in advanced talks to acquire ONWAI SE",
            source="Investor's Business Daily",
            domain="investors.com",
            url="https://news.google.com/rss/articles/CBMiabc123",
            published_at="2026-07-14T17:00:00+00:00",
        ),
    ]
    out = dedupe_stories(items)
    assert len(out) == 1
    assert out[0]["url"].startswith("https://www.investors.com/")


def test_same_publisher_retitled_story_collapses():
    items = [
        _item(
            "ONWAI SE stock jumps on Uber takeover talks",
            source="Investing.com",
            domain="investing.com",
            url="https://www.investing.com/news/1",
            published_at="2026-07-14T17:00:00+00:00",
        ),
        _item(
            "ONWAI SE stock jumps on report of Uber takeover talks",
            source="Investing.com",
            domain="investing.com",
            url="https://news.google.com/rss/articles/CBMixyz789",
            published_at="2026-07-14T17:30:00+00:00",
        ),
    ]
    assert len(dedupe_stories(items)) == 1


def test_same_publisher_reworded_headline_collapses():
    items = [
        _item(
            "ONWAI SE confirms advanced Uber takeover negotiations",
            source="Reuters",
            domain="reuters.com",
            url="https://www.reuters.com/markets/1",
            published_at="2026-07-14T17:00:00+00:00",
        ),
        _item(
            "ONWAI SE confirms advanced negotiations with Uber over potential takeover offer",
            source="Reuters",
            domain="reuters.com",
            url="https://news.google.com/rss/articles/CBMidef456",
            published_at="2026-07-14T17:10:00+00:00",
        ),
    ]
    assert len(dedupe_stories(items)) == 1


# ── What must NOT collapse ──────────────────────────────────────────────────────


def test_distinct_stories_same_publisher_are_kept():
    items = [
        _item(
            "Uber in advanced talks to acquire ONWAI SE",
            source="Reuters",
            domain="reuters.com",
            url="https://www.reuters.com/markets/1",
            published_at="2026-07-14T17:00:00+00:00",
        ),
        _item(
            "Uber second-quarter earnings beat expectations on mobility growth",
            source="Reuters",
            domain="reuters.com",
            url="https://www.reuters.com/markets/2",
            published_at="2026-07-14T12:00:00+00:00",
        ),
    ]
    assert len(dedupe_stories(items)) == 2


def test_similar_headline_from_different_publishers_is_kept():
    items = [
        _item(
            "Uber in advanced talks to acquire ONWAI SE",
            source="Reuters",
            domain="reuters.com",
            url="https://www.reuters.com/markets/1",
            published_at="2026-07-14T17:00:00+00:00",
        ),
        _item(
            "Uber in advanced talks to acquire ONWAI SE",
            source="Bloomberg",
            domain="bloomberg.com",
            url="https://www.bloomberg.com/news/1",
            published_at="2026-07-14T17:05:00+00:00",
        ),
    ]
    assert len(dedupe_stories(items)) == 2


def test_similar_headline_far_apart_in_time_is_kept():
    """A publisher's recurring headline pattern (e.g. daily movers) must not collapse
    across days — the near-duplicate window is tight."""
    items = [
        _item(
            "Stocks making the biggest moves midday: Uber, ONWAI SE",
            source="CNBC",
            domain="cnbc.com",
            url="https://www.cnbc.com/1",
            published_at="2026-07-14T17:00:00+00:00",
        ),
        _item(
            "Stocks making the biggest moves midday: Uber, ONWAI SE",
            source="CNBC",
            domain="cnbc.com",
            url="https://www.cnbc.com/2",
            published_at="2026-07-10T17:00:00+00:00",
        ),
    ]
    assert len(dedupe_stories(items)) == 2


def test_first_occurrence_wins_and_order_is_preserved():
    items = [
        _item("A story about one thing", source="S1", domain="s1.com", url="https://s1.com/a"),
        _item(
            "Completely different news item", source="S2", domain="s2.com", url="https://s2.com/b"
        ),
        _item(
            "A story about one thing today", source="S1", domain="s1.com", url="https://s1.com/a2"
        ),
    ]
    out = dedupe_stories(items)
    assert [i["url"] for i in out] == ["https://s1.com/a", "https://s2.com/b"]


def test_untitled_and_empty_items_pass_through():
    items = [_item(""), _item("")]
    assert len(dedupe_stories(items)) == 2


def test_undated_duplicate_still_collapses():
    """Cached evidence rows can lack a parseable date — a missing date must not defeat
    the collapse (the window guard only applies when BOTH sides carry dates)."""
    items = [
        _item(
            "Uber in advanced talks to acquire ONWAI SE",
            source="Reuters",
            domain="reuters.com",
            url="https://www.reuters.com/markets/1",
            published_at="2026-07-14T17:00:00+00:00",
        ),
        _item(
            "Uber In Advanced Talks To Acquire ONWAI SE",
            source="Reuters",
            domain="reuters.com",
            url="https://news.google.com/rss/articles/CBMi999",
            published_at="",
        ),
    ]
    assert len(dedupe_stories(items)) == 1


def test_publisher_match_via_domain_when_source_labels_differ():
    """Live-search rows and RSS fold-back rows can label the same outlet differently
    ('Investing.com' vs 'investing.com') — the domain is the fallback identity."""
    items = [
        _item(
            "ONWAI SE stock jumps on Uber takeover talks",
            source="Investing.com",
            domain="investing.com",
            url="https://www.investing.com/news/1",
            published_at="2026-07-14T17:00:00+00:00",
        ),
        _item(
            "ONWAI SE stock jumps on report of Uber takeover talks",
            source="investing.com",
            domain="investing.com",
            url="https://news.google.com/rss/articles/CBMi000",
            published_at="2026-07-14T17:30:00+00:00",
        ),
    ]
    assert len(dedupe_stories(items)) == 1


# ── Serving path: an already-stored duplicated brief renders clean immediately ──


def test_read_asset_detail_dedupes_live_evidence_news(monkeypatch):
    """RFC intel-signals-data-model §5: the news feed is a LIVE query over `evidence`, not a
    stored `AssetBriefStore.news[]` snapshot — the same GN-wrapper-duplicate collapse this
    module's other tests prove for `dedupe_stories` directly must still hold end-to-end
    through `read_asset_detail`, sourced from `read_evidence` instead."""
    import backend.services.asset_detail as ad

    evidence_rows = [
        {
            "title": "Uber In 'Advanced' Talks To Acquire ONWAI SE",
            "source": "Investor's Business Daily",
            "domain": "investors.com",
            "url": "https://www.investors.com/news/uber-onwai-takeover/",
            "published_at": "2026-07-14T18:00:00+00:00",
        },
        {
            "title": "Uber in advanced talks to acquire ONWAI SE",
            "source": "Investor's Business Daily",
            "domain": "investors.com",
            "url": "https://news.google.com/rss/articles/CBMiabc123",
            "published_at": "2026-07-14T17:00:00+00:00",
        },
    ]

    inst = {"id": "inst-1", "label": "Uber", "asset_class": "equity"}

    class _BriefStore:
        def get(self, iid):
            return {"key_issues": [], "facts": {}, "peers": []}

    class _InstStore:
        def find_by_id(self, iid):
            return dict(inst)

    monkeypatch.setattr(ad, "AssetBriefStore", _BriefStore)
    monkeypatch.setattr(ad, "InstrumentStore", _InstStore)
    monkeypatch.setattr(ad, "read_profile", lambda: {"base_currency": "SGD"})
    monkeypatch.setattr(ad, "load_fx_cache", lambda: {})
    monkeypatch.setattr(ad, "hydrated_positions", lambda *a, **k: [])
    monkeypatch.setattr(ad, "read_evidence", lambda **kw: list(evidence_rows))

    detail = ad.read_asset_detail("inst-1")
    assert detail is not None
    assert len(detail["news"]) == 1
