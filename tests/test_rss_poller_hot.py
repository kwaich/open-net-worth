"""monitoring/rss_poller_hot.py — the hot tier (news-ingestion-latency.md §2): the regulatory
registry, the ONE combined GDELT topic query, and per-item custom feeds. All network I/O
stubbed at gather_matched/gather_rss.
"""

from __future__ import annotations

import asyncio


def _stub_watchlist(monkeypatch, items):
    import backend.monitoring.rss_poller_hot as hot

    monkeypatch.setattr(hot, "read_watchlist", lambda: {"items": items, "config": {}})


def _seed_source_row(via: str, kind: str) -> None:
    """INT9 Phase A: the hot tier now gates every fetch on the source store (`active_sources`),
    so a synthetic FAKE feed (a via not among the seeded builtins) needs its own enabled row —
    exactly like a real registry entry always has one — for a test to observe it actually run.

    `provenance="builtin"` deliberately, NOT "user": these rows simulate a REGISTRY entry (the
    tests only care that the hot tier fetches/backs-off a store-gated row), and `"builtin"`
    skips `create_source`'s Phase B `check_reachable` SSRF probe — a real `socket.getaddrinfo`
    call against the non-resolvable `fake-source.example` host would otherwise raise, breaking
    every test that calls this helper."""
    from backend.db.news_source_store import NewsSourceStore

    NewsSourceStore().create_source(
        {
            "id": f"src_test_{via.replace(':', '_')}",
            "label": via,
            "kind": kind,
            "transport": "poll",
            "url": "https://fake-source.example/feed",
            "resolver": None,
            "scope": "firehose",
            "match": "server_tokens",
            "cadence": "hot",
            "via": via,
            "enabled": True,
            "min_interval_sec": 60,
            "markets": [],
            "provenance": "builtin",
            "notes": "",
            "added_at": "2026-07-16T00:00:00+00:00",
        }
    )


# ── _monitor_news_entities ────────────────────────────────────────────────────────


def test_monitor_news_entities_builds_asset_and_topic_subjects():
    from backend.monitoring.rss_poller_hot import _monitor_news_entities

    items = [
        {"type": "ticker", "instrument_id": "i1", "value": "ONWAI.DE", "label": "ONWAI Corp"},
        {
            "type": "topic",
            "id": "t1",
            "value": "ONWAI M&A",
            "label": "ONWAI M&A",
            "linked_tickers": ["ONWAI.DE"],
        },
    ]
    entities = _monitor_news_entities(items)
    by_subject = {e["subject"]: e for e in entities}
    assert set(by_subject) == {"asset:i1", "topic:t1"}
    assert "ONWAI.DE" in by_subject["asset:i1"]["tickers"]
    assert (
        "ONWAI.DE" in by_subject["topic:t1"]["tickers"]
    )  # linked ticker rides the topic's tickers


def test_a_followed_private_company_is_a_topic_subject_not_a_dropped_ticker():
    """A `company` item (private/unlisted, no instrument) is followed like a topic (ADR
    2026-09-27-topics-are-followed-not-listed) — reading it as a ticker dropped it for want of
    an instrument_id."""
    from backend.monitoring.rss_poller_hot import _monitor_news_entities

    items = [{"type": "company", "id": "c1", "value": "SpaceX", "label": "SpaceX"}]
    assert [e["subject"] for e in _monitor_news_entities(items)] == ["topic:c1"]


def test_monitor_news_entities_adds_bare_name_and_bare_ticker_terms():
    """Regression (owner incident 2026-08-29): a journalistic headline almost always drops the
    corporate suffix ("Acme Metals" not "Acme Metals SE") and the exchange suffix ("ACME" not
    "ACME.DE") — only the label/value terms alone missed 4 of 5 real headlines for a live,
    material development. Synthetic fixture, not the real incident's company. Name-shaped forms
    land in `terms`, ticker-shaped forms in `tickers` (owner correction 2026-08-31: matched
    exact-case, see `feeds_hot._term_matches_title`'s `is_ticker` branch)."""
    from backend.monitoring.rss_poller_hot import _monitor_news_entities

    items = [
        {"type": "ticker", "instrument_id": "i1", "value": "ACME.DE", "label": "Acme Metals SE"},
    ]
    entity = _monitor_news_entities(items)[0]
    assert set(entity["terms"]) == {"Acme Metals SE", "Acme Metals"}
    assert set(entity["tickers"]) == {"ACME.DE", "ACME"}


def test_monitor_news_entities_no_bare_terms_when_nothing_to_strip():
    """A label with no known corporate suffix and a value with no exchange suffix contribute no
    redundant duplicate terms."""
    from backend.monitoring.rss_poller_hot import _monitor_news_entities

    items = [{"type": "ticker", "instrument_id": "i1", "value": "ACME", "label": "Acme Metals"}]
    entity = _monitor_news_entities(items)[0]
    assert set(entity["terms"]) == {"Acme Metals"}
    assert set(entity["tickers"]) == {"ACME"}


def test_bare_terms_actually_match_real_shaped_headlines():
    """End-to-end proof against services.feeds_hot's real matcher — the failure mode this fix
    closes: a headline that drops the corporate/exchange suffix must still match. The bare
    ticker form ("ACME.DE" -> "ACME") lives in `tickers` (exact-case matched, see
    `_term_matches_title`'s `is_ticker` branch), not `terms` — a real headline citing a ticker
    renders it in full caps, so the exchange-suffix-dropped case is exercised with that casing."""
    from backend.monitoring.rss_poller_hot import _monitor_news_entities
    from backend.services.feeds_hot import _term_matches_title

    items = [
        {"type": "ticker", "instrument_id": "i1", "value": "ACME.DE", "label": "Acme Metals SE"}
    ]
    entity = _monitor_news_entities(items)[0]
    terms, tickers = entity["terms"], entity["tickers"]

    dropped_suffix_headline = "acme metals raises 2026 view"
    dropped_exchange_headline = "ACME: Q2 2026 Saw Accelerated Growth"
    unrelated_headline = "wall street closes higher"

    assert any(
        _term_matches_title(t, dropped_suffix_headline, dropped_suffix_headline) for t in terms
    )
    assert any(
        _term_matches_title(t, dropped_exchange_headline, dropped_exchange_headline, is_ticker=True)
        for t in tickers
    )
    assert not any(_term_matches_title(t, unrelated_headline, unrelated_headline) for t in terms)
    assert not any(
        _term_matches_title(t, unrelated_headline, unrelated_headline, is_ticker=True)
        for t in tickers
    )


def test_monitor_news_entities_skips_muted_items():
    from backend.monitoring.rss_poller_hot import _monitor_news_entities

    items = [{"type": "ticker", "instrument_id": "i1", "value": "X", "monitor_news": False}]
    assert _monitor_news_entities(items) == []


def test_monitor_news_entities_skips_items_missing_key_id():
    from backend.monitoring.rss_poller_hot import _monitor_news_entities

    items = [{"type": "ticker", "value": "X"}, {"type": "topic", "value": "Y"}]
    assert _monitor_news_entities(items) == []


# ── run_hot_tick dispatch ─────────────────────────────────────────────────────────


def test_run_hot_tick_noop_with_no_watched_entities(monkeypatch):
    from backend.monitoring.rss_poller_hot import run_hot_tick

    _stub_watchlist(monkeypatch, [])
    assert asyncio.run(run_hot_tick()) == 0


def test_run_hot_tick_calls_regulatory_outlets_gdelt_and_custom(monkeypatch):
    import backend.monitoring.rss_poller_hot as hot

    items = [
        {
            "type": "topic",
            "id": "t1",
            "value": "ONWAI M&A",
            "label": "ONWAI M&A",
            "monitor_news": True,
            "custom_feed_urls": ["https://example.com/custom.xml"],
        },
    ]
    _stub_watchlist(monkeypatch, items)

    matched_calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        matched_calls.append({"url": url, "entities": entities, **kw})
        return []

    rss_calls: list = []

    async def _fake_gather_rss(feeds, **kw):
        rss_calls.append(feeds)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", _fake_gather_rss)

    count = asyncio.run(hot.run_hot_tick())

    # 2 regulatory feeds (FN + EDGAR) + 6 curated outlet feeds + 1 GDELT combined query +
    # 1 custom-feed batch = 10
    assert count == 10
    vias_hit = {c["url"] for c in matched_calls}
    assert any("finanznachrichten" in u for u in vias_hit)
    assert any("sec.gov" in u for u in vias_hit)
    assert any("ft.com" in u for u in vias_hit)
    assert any("gdeltproject" in u for u in vias_hit)
    assert len(rss_calls) == 1
    assert rss_calls[0][0]["via"] == "rss:custom"

    # SF-1 (code review): every gather_matched call must thread the SOURCE's own `via` —
    # never leave it for parse_rss's generic default to silently win.
    via_by_url = {c["url"]: c.get("via") for c in matched_calls}
    for url, via in via_by_url.items():
        if "finanznachrichten" in url:
            assert via == "rss:fn_adhoc"
        elif "sec.gov" in url:
            assert via == "atom:edgar"
        elif "gdeltproject" in url:
            assert via == "api:gdelt"
        elif "ft.com/companies" in url:
            assert via == "rss:ft_companies"
        elif "ft.com/technology" in url:
            assert via == "rss:ft_technology"
        else:
            assert via, f"gather_matched called for {url!r} with no via stamped"


def test_run_hot_tick_gdelt_skipped_when_no_topics(monkeypatch):
    """GDELT scopes to TOPICS only (news-ingestion-latency.md: "ONE combined query per hot tick
    for all monitor_news topics") — a ticker-only watchlist never triggers it."""
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    matched_calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        matched_calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())
    assert not any("gdeltproject" in u for u in matched_calls)


async def _noop():
    return []


def test_run_hot_tick_one_gdelt_call_regardless_of_topic_count(monkeypatch):
    """Rate-limit safety: N topics must still produce exactly ONE GDELT fetch."""
    import backend.monitoring.rss_poller_hot as hot

    items = [
        {"type": "topic", "id": f"t{i}", "value": f"Topic {i}", "monitor_news": True}
        for i in range(5)
    ]
    _stub_watchlist(monkeypatch, items)

    gdelt_calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        if "gdeltproject" in url:
            gdelt_calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())
    assert len(gdelt_calls) == 1


def test_run_hot_tick_regulatory_source_failure_isolated(monkeypatch):
    """One bad regulatory source must never sink the tick — the OTHER sources still run."""
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    calls: list = []

    async def _flaky_gather_matched(url, entities, **kw):
        calls.append(url)
        if "finanznachrichten" in url:
            raise RuntimeError("network down")
        return []

    monkeypatch.setattr(hot, "gather_matched", _flaky_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    count = asyncio.run(hot.run_hot_tick())
    # both regulatory feeds attempted despite the first failing, plus all 6 outlet feeds
    assert len(calls) == 8
    assert count == 7  # surviving EDGAR fetch + all 6 outlet fetches


# ── SF-3 (code review): per-source failure-streak backoff ────────────────────────


def test_source_backoff_stops_attempts_and_logs_once(monkeypatch, caplog):
    """After _FAILURE_STREAK_THRESHOLD consecutive failures, fetch attempts for that source
    STOP for its backoff window (no growth in call count) and exactly ONE warning is logged —
    never one per failing/skipped tick."""
    import logging

    import backend.monitoring.rss_poller_hot as hot
    from backend.services.feeds_regulatory import MarketFeed

    hot._source_health.clear()
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    fake_feed = MarketFeed(lambda: "https://fake-source.example/feed", lambda raw: [], "rss:fake")
    _seed_source_row("rss:fake", "regulatory")
    monkeypatch.setattr(hot, "all_regulatory_feeds", lambda: [fake_feed])
    monkeypatch.setattr(
        hot, "all_outlet_feeds", lambda: []
    )  # isolate: this test is regulatory-only

    call_count = {"n": 0}

    async def _fake_gather_matched(url, entities, **kw):
        call_count["n"] += 1
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot.collect_mod, "fetch_failed", lambda url: True)  # every fetch "fails"
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    caplog.set_level(logging.WARNING)

    for _ in range(hot._FAILURE_STREAK_THRESHOLD):
        asyncio.run(hot.run_hot_tick())
    calls_at_threshold = call_count["n"]
    assert calls_at_threshold == hot._FAILURE_STREAK_THRESHOLD  # one attempt per tick so far

    outage_logs = [r for r in caplog.records if "backing off" in r.message]
    assert len(outage_logs) == 1

    # One more tick: the source must now be SKIPPED — no new fetch attempt.
    asyncio.run(hot.run_hot_tick())
    assert call_count["n"] == calls_at_threshold

    # Still exactly one outage log line — the skip itself does not log again.
    outage_logs = [r for r in caplog.records if "backing off" in r.message]
    assert len(outage_logs) == 1


def test_source_backoff_recovers_and_logs_once(monkeypatch, caplog):
    """Once the backoff window elapses, the source is retried; a SUCCESS there resets the
    streak and logs a recovery message exactly once."""
    import logging

    import backend.monitoring.rss_poller_hot as hot
    from backend.services.feeds_regulatory import MarketFeed

    hot._source_health.clear()
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    fake_feed = MarketFeed(lambda: "https://fake-source.example/feed", lambda raw: [], "rss:fake")
    _seed_source_row("rss:fake", "regulatory")
    monkeypatch.setattr(hot, "all_regulatory_feeds", lambda: [fake_feed])
    monkeypatch.setattr(
        hot, "all_outlet_feeds", lambda: []
    )  # isolate: this test is regulatory-only

    async def _fake_gather_matched(url, entities, **kw):
        return []

    should_fail = {"v": True}
    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot.collect_mod, "fetch_failed", lambda url: should_fail["v"])
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    caplog.set_level(logging.WARNING)

    for _ in range(hot._FAILURE_STREAK_THRESHOLD):
        asyncio.run(hot.run_hot_tick())

    # Flip to healthy, then tick through the backoff window until the source is eligible
    # again (base backoff is _FAILURE_BACKOFF_BASE_TICKS ticks after crossing threshold).
    should_fail["v"] = False
    for _ in range(hot._FAILURE_BACKOFF_BASE_TICKS + 1):
        asyncio.run(hot.run_hot_tick())

    recovered_logs = [r for r in caplog.records if "recovered" in r.message]
    assert len(recovered_logs) == 1
    assert hot._source_state("rss:fake")["failures"] == 0
    assert hot._source_state("rss:fake")["skip_remaining"] == 0


def test_gdelt_backoff_uses_its_own_source_key(monkeypatch):
    """GDELT's backoff state is tracked independently under 'api:gdelt' — a failing GDELT
    query must not affect (or be affected by) the regulatory sources' own streaks.

    Overrides the store's GDELT row to min_interval_sec=60 (down from the Phase B default of
    300) so the per-row politeness gate (INT9 Phase B) can never mask this test's actual
    subject — the FAILURE-backoff streak — behind "not yet due for its slower cadence"."""
    import backend.monitoring.rss_poller_hot as hot
    from backend.db.news_source_store import NewsSourceStore

    hot._source_health.clear()
    NewsSourceStore().update_policy("src_gdelt_topics", {"min_interval_sec": 60})
    items = [{"type": "topic", "id": "t1", "value": "Topic", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    gdelt_calls = {"n": 0}

    async def _fake_gather_matched(url, entities, **kw):
        if "gdeltproject" in url:
            gdelt_calls["n"] += 1
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot.collect_mod, "fetch_failed", lambda url: "gdeltproject" in url)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    for _ in range(hot._FAILURE_STREAK_THRESHOLD):
        asyncio.run(hot.run_hot_tick())
    calls_at_threshold = gdelt_calls["n"]
    assert calls_at_threshold == hot._FAILURE_STREAK_THRESHOLD

    asyncio.run(hot.run_hot_tick())
    assert gdelt_calls["n"] == calls_at_threshold  # GDELT itself now backed off
    assert "api:gdelt" in hot._source_health


# ── Outlet-headline registry (INT8) ────────────────────────────────────────────────


def test_run_hot_tick_respects_outlet_feeds_enabled_kill_switch(monkeypatch):
    """outlet_feeds_enabled=False must skip every outlet feed for the tick — the regulatory
    registry, GDELT, and custom feeds are unaffected."""
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]

    def _stub_disabled():
        return {"items": items, "config": {"outlet_feeds_enabled": False}}

    monkeypatch.setattr(hot, "read_watchlist", _stub_disabled)

    matched_calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        matched_calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    count = asyncio.run(hot.run_hot_tick())
    assert not any("ft.com" in u or "cnbc.com" in u for u in matched_calls)
    assert count == 2  # only the 2 regulatory feeds (FN + EDGAR); no topics -> gdelt skipped


def test_run_hot_tick_outlet_feeds_enabled_by_default(monkeypatch):
    """Absent the config key entirely, outlet feeds still fetch — default is ON."""
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)  # config: {} — no outlet_feeds_enabled key

    matched_calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        matched_calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())
    assert any("ft.com" in u for u in matched_calls)


def test_outlet_source_backoff_reuses_the_shared_failure_streak_machinery(monkeypatch, caplog):
    """An outlet source backs off identically to a regulatory one (SF-3 reused, not
    reimplemented) — same threshold, same skip window, same once-only logging."""
    import logging

    import backend.monitoring.rss_poller_hot as hot
    from backend.services.feeds_outlets import OutletFeed

    hot._source_health.clear()
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    fake_outlet = OutletFeed(
        lambda: "https://fake-outlet.example/feed", lambda raw: [], "rss:fake_outlet"
    )
    _seed_source_row("rss:fake_outlet", "outlet")
    monkeypatch.setattr(hot, "all_regulatory_feeds", lambda: [])
    monkeypatch.setattr(hot, "all_outlet_feeds", lambda: [fake_outlet])

    call_count = {"n": 0}

    async def _fake_gather_matched(url, entities, **kw):
        call_count["n"] += 1
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot.collect_mod, "fetch_failed", lambda url: True)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    caplog.set_level(logging.WARNING)

    for _ in range(hot._FAILURE_STREAK_THRESHOLD):
        asyncio.run(hot.run_hot_tick())
    calls_at_threshold = call_count["n"]
    assert calls_at_threshold == hot._FAILURE_STREAK_THRESHOLD

    asyncio.run(hot.run_hot_tick())
    assert call_count["n"] == calls_at_threshold  # now skipped — no new attempt

    outage_logs = [r for r in caplog.records if "backing off" in r.message]
    assert len(outage_logs) == 1
    assert "rss:fake_outlet" in hot._source_health


def test_run_hot_tick_isolates_one_bad_outlet_feed(monkeypatch):
    """A single failing outlet feed must not sink the other outlet fetches that tick."""
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    calls: list = []

    async def _flaky_gather_matched(url, entities, **kw):
        calls.append(url)
        if "ft.com" in url and "companies" in url:
            raise RuntimeError("network down")
        return []

    monkeypatch.setattr(hot, "gather_matched", _flaky_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    count = asyncio.run(hot.run_hot_tick())
    # 2 regulatory + 6 outlet feeds all attempted despite one outlet failing
    assert len(calls) == 8
    assert count == 7  # every source succeeded except the one flaky FT companies fetch


# ── INT9 Phase A: source-store gating ──────────────────────────────────────────────
# (.claude/rfcs/news-source-management.md) — the hot tier now consults
# services.news_sources.active_sources("hot") to decide whether a registry entry runs this
# tick. conftest's autouse fixture reseeds news_sources.json with every builtin ENABLED before
# each test, so the pre-existing tests above (which never touch the store) still see the
# byte-identical pre-change fetch set; these tests exercise the new enabled/disabled gating
# itself, plus pin the full URL set fetched when every builtin is enabled.


def _disable_source(source_id: str) -> None:
    from backend.db.news_source_store import NewsSourceStore

    NewsSourceStore().set_enabled(source_id, False)


def test_run_hot_tick_disabled_regulatory_row_skips_its_fetch(monkeypatch):
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)
    _disable_source("src_fn_adhoc")

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())
    assert not any("finanznachrichten" in u for u in calls)  # disabled row never fetched
    assert any("sec.gov" in u for u in calls)  # the OTHER regulatory row is unaffected


def test_run_hot_tick_disabled_outlet_row_skips_its_fetch(monkeypatch):
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)
    _disable_source("src_ft_companies")

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())
    assert not any("ft.com/companies" in u for u in calls)  # disabled row never fetched
    assert any("cnbc.com" in u for u in calls)  # the other outlet rows are unaffected


def test_run_hot_tick_disabled_gdelt_row_skips_its_query(monkeypatch):
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "topic", "id": "t1", "value": "Topic", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)
    _disable_source("src_gdelt_topics")

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())
    assert not any("gdeltproject" in u for u in calls)


def test_run_hot_tick_outlet_flag_off_beats_a_re_enabled_row(monkeypatch):
    """Documented precedence (RFC + module docstring): a fetch runs only when
    outlet_feeds_enabled AND the row itself are both enabled — the flag alone can still zero
    out every outlet row even though every row.enabled stays True."""
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]

    def _stub_disabled_flag():
        return {"items": items, "config": {"outlet_feeds_enabled": False}}

    monkeypatch.setattr(hot, "read_watchlist", _stub_disabled_flag)

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())
    assert not any("ft.com" in u or "cnbc.com" in u for u in calls)


def test_run_hot_tick_url_set_pinned_when_all_builtins_enabled(monkeypatch):
    """Equivalence pin (INT9 Phase A): with every builtin source enabled (conftest's default
    seed), the hot tick must fetch EXACTLY the same URL set it fetched before the source store
    existed — the store must only be able to SHRINK the fetch set (via a disabled row), never
    change what a fully-enabled book fetches. No custom_feed_urls on either item, so gather_rss
    contributes nothing this tick — every fetch flows through gather_matched, letting this
    assert full equality (`==`) rather than a subset check."""
    import backend.monitoring.rss_poller_hot as hot
    from backend.services.feeds_hot import combined_gdelt_query, gdelt_doc_url

    items = [
        {"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True},
        {"type": "topic", "id": "t1", "value": "Topic", "monitor_news": True},
    ]
    _stub_watchlist(monkeypatch, items)

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append(url)
        return []

    rss_calls: list = []
    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: rss_calls.append(feeds) or _noop())

    asyncio.run(hot.run_hot_tick())

    expected_gdelt_url = gdelt_doc_url(
        combined_gdelt_query([{"subject": "topic:t1", "terms": ["Topic"]}])
    )
    expected = {
        "https://www.finanznachrichten.de/rss-aktien-adhoc/",
        (
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&"
            "dateb=&owner=include&count=100&output=atom"
        ),
        "https://www.ft.com/companies?format=rss",
        "https://www.ft.com/technology?format=rss",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
        "https://techcrunch.com/feed/",
        expected_gdelt_url,
    }
    assert set(calls) == expected  # every registry URL, and ONLY those, were fetched
    assert not rss_calls  # neither item carries custom_feed_urls — gather_rss never invoked


# ── SF-2 (code review): warn once if the source store looks unseeded ──────────────


def test_run_hot_tick_warns_once_when_source_store_looks_unseeded(monkeypatch, caplog):
    """An empty active_sources("hot") with watched entities present usually means
    ensure_builtin_sources() failed at boot — every regulatory/outlet/GDELT fetch would
    otherwise go dark with zero signal. Must log exactly ONE warning across repeated ticks
    (not per-tick), matching the failure-backoff outage-log discipline."""
    import logging

    import backend.monitoring.rss_poller_hot as hot

    hot._store_empty_warned = False
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)
    monkeypatch.setattr(hot, "active_sources", lambda cadence: [])
    monkeypatch.setattr(hot, "gather_matched", lambda url, entities, **kw: _noop())
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    caplog.set_level(logging.WARNING)

    asyncio.run(hot.run_hot_tick())
    asyncio.run(hot.run_hot_tick())

    unseeded_logs = [r for r in caplog.records if "may be unseeded" in r.message]
    assert len(unseeded_logs) == 1


def test_run_hot_tick_no_unseeded_warning_when_store_is_seeded(monkeypatch, caplog):
    """The normal case (conftest's default builtin seed) must never trip the SF-2 warning."""
    import logging

    import backend.monitoring.rss_poller_hot as hot

    hot._store_empty_warned = False
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)
    monkeypatch.setattr(hot, "gather_matched", lambda url, entities, **kw: _noop())
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    caplog.set_level(logging.WARNING)
    asyncio.run(hot.run_hot_tick())

    assert not [r for r in caplog.records if "may be unseeded" in r.message]


def test_run_hot_tick_no_unseeded_warning_when_news_freshness_off(monkeypatch, caplog):
    """news_freshness='off' is the USER's own switch, not a seed failure — must not trip the
    SF-2 warning even though active_sources("hot") also returns [] in that case."""
    import logging

    import backend.monitoring.rss_poller_hot as hot

    hot._store_empty_warned = False
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]

    def _stub_off():
        return {"items": items, "config": {"news_freshness": "off"}}

    monkeypatch.setattr(hot, "read_watchlist", _stub_off)
    monkeypatch.setattr(hot, "gather_matched", lambda url, entities, **kw: _noop())
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    caplog.set_level(logging.WARNING)
    asyncio.run(hot.run_hot_tick())

    assert not [r for r in caplog.records if "may be unseeded" in r.message]


# ── INT9 Phase B: user-added custom firehose sources + per-row min_interval politeness ──


_CUSTOM_FIREHOSE_URL = "https://example.com/custom-firehose.xml"


def _seed_custom_firehose(
    via: str, *, cadence: str = "hot", min_interval_sec: int | None = None
) -> str:
    """A user-added kind=custom/scope=firehose row (POST /api/intel/sources' shape) — via
    services.news_sources.create_user_source so it goes through the real Phase B write choke
    (SSRF check included; example.com is the RFC 2606 reserved, always-resolvable test domain).
    `min_interval_sec=None` (default) leaves whatever create_user_source naturally assigned for
    `cadence` (60/300/1800) — only pass an explicit value to override it. Returns the minted id."""
    from backend.services.news_sources import create_user_source

    row = create_user_source(label=via, url=_CUSTOM_FIREHOSE_URL, cadence=cadence)
    if min_interval_sec is not None and min_interval_sec != row["min_interval_sec"]:
        from backend.db.news_source_store import NewsSourceStore

        NewsSourceStore().update_policy(row["id"], {"min_interval_sec": min_interval_sec})
    return row["id"]


def test_run_hot_tick_fetches_an_enabled_custom_firehose_source(monkeypatch):
    import backend.monitoring.rss_poller_hot as hot

    _seed_custom_firehose("rss:custom_test1")
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append({"url": url, **kw})
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())

    custom_calls = [c for c in calls if c["url"] == _CUSTOM_FIREHOSE_URL]
    assert len(custom_calls) == 1
    assert custom_calls[0]["via"].startswith("rss:custom_")  # SF-1-style: own via stamped


def test_run_hot_tick_skips_a_disabled_custom_firehose_source(monkeypatch):
    """A disabled custom row is never fetched, regardless of its cadence — 'enabled' is the
    on/off switch; 'cadence' only affects politeness (see the slow-cadence tests below)."""
    import backend.monitoring.rss_poller_hot as hot
    from backend.db.news_source_store import NewsSourceStore

    source_id = _seed_custom_firehose("rss:custom_test2", cadence="slow")
    NewsSourceStore().set_enabled(source_id, False)
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    calls: list = []
    monkeypatch.setattr(
        hot, "gather_matched", lambda url, entities, **kw: calls.append(url) or _noop()
    )
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    for _ in range(3):
        asyncio.run(hot.run_hot_tick())
    assert _CUSTOM_FIREHOSE_URL not in calls


def test_slow_cadence_custom_source_fetches_on_first_tick_then_waits_its_own_interval(
    monkeypatch,
):
    """Review fix (2026-07-16, security/QA/code-review gate): the hot tier's custom-firehose
    lane no longer cadence-filters its row universe (``active_custom_firehose_sources`` —
    every ENABLED custom row, any cadence) — a freshly POSTed source's default cadence "slow"
    (1800s min_interval) still fetches on tick 1 (never dormant), then waits ~20 ticks before
    its next attempt, exactly like any other min_interval-honoring row."""
    import backend.monitoring.rss_poller_hot as hot

    _seed_custom_firehose("rss:custom_test_slow", cadence="slow")  # default min_interval 1800s
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    for _ in range(20):
        asyncio.run(hot.run_hot_tick())
    custom_calls = [c for c in calls if c == _CUSTOM_FIREHOSE_URL]
    assert len(custom_calls) == 1  # tick 1 only — ticks 2-20 are inside the 1800s/20-tick window

    asyncio.run(hot.run_hot_tick())  # tick 21 — now due again
    custom_calls = [c for c in calls if c == _CUSTOM_FIREHOSE_URL]
    assert len(custom_calls) == 2


def test_disabled_slow_cadence_custom_source_never_fetched(monkeypatch):
    """Explicit pin: a disabled custom row stays unfetched across many ticks even at the
    default 'slow' cadence — 'enabled' always wins over 'due for interval'."""
    import backend.monitoring.rss_poller_hot as hot
    from backend.db.news_source_store import NewsSourceStore

    source_id = _seed_custom_firehose("rss:custom_test_slow_disabled", cadence="slow")
    NewsSourceStore().set_enabled(source_id, False)
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    calls: list = []
    monkeypatch.setattr(
        hot, "gather_matched", lambda url, entities, **kw: calls.append(url) or _noop()
    )
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    for _ in range(5):
        asyncio.run(hot.run_hot_tick())
    assert _CUSTOM_FIREHOSE_URL not in calls


def test_builtin_non_hot_cadence_rows_are_still_never_fetched_by_the_hot_tier(monkeypatch):
    """The cadence-blind read (active_custom_firehose_sources) is SCOPED to kind=custom rows
    only — a builtin resolver-policy row (src_yahoo_ticker, cadence=fast; src_google_news_
    search, cadence=slow — both kind != custom) stays untouched by the hot tier, UNCHANGED by
    the review fix: the builtin registries (regulatory/outlet/topic_engine) still gate on
    active_sources("hot") exactly as before."""
    import backend.monitoring.rss_poller_hot as hot

    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    vias_hit: list = []
    monkeypatch.setattr(
        hot,
        "gather_matched",
        lambda url, entities, **kw: vias_hit.append(kw.get("via")) or _noop(),
    )
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    asyncio.run(hot.run_hot_tick())
    assert "rss:yahoo" not in vias_hit
    assert "rss:google_news" not in vias_hit


def test_min_interval_politeness_skips_ticks_until_due(monkeypatch):
    """A row whose min_interval_sec needs 3 hot ticks (270s / 90s-per-tick, rounded up) fetches
    on tick 1, is skipped on ticks 2-3, then fetches again on tick 4 — TICK-counted, not
    wall-clock, so this is deterministic across a test's synchronous back-to-back calls. Every
    builtin (regulatory/outlet/gdelt) is ALSO live in this test process (conftest's autouse
    seed) and fetches every tick regardless — the assertion filters to ONLY our custom row's
    own URL so those don't contaminate the count."""
    import backend.monitoring.rss_poller_hot as hot

    _seed_custom_firehose("rss:custom_test4", min_interval_sec=270)
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    for _ in range(4):
        asyncio.run(hot.run_hot_tick())

    custom_calls = [c for c in calls if c == _CUSTOM_FIREHOSE_URL]
    assert len(custom_calls) == 2  # tick 1 and tick 4 only


def test_min_interval_at_or_under_tick_interval_is_due_every_tick(monkeypatch):
    """A row whose min_interval_sec (60s) is <= the hot tick's own ~90s cadence is due EVERY
    tick — byte-identical to pre-Phase-B behavior (regulatory/outlet rows all default to 60s)."""
    import backend.monitoring.rss_poller_hot as hot

    _seed_custom_firehose("rss:custom_test5", min_interval_sec=60)
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    calls: list = []

    async def _fake_gather_matched(url, entities, **kw):
        calls.append(url)
        return []

    monkeypatch.setattr(hot, "gather_matched", _fake_gather_matched)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    for _ in range(3):
        asyncio.run(hot.run_hot_tick())

    custom_calls = [c for c in calls if c == _CUSTOM_FIREHOSE_URL]
    assert len(custom_calls) == 3


def test_source_health_snapshot_reflects_failure_streak(monkeypatch):
    import backend.monitoring.rss_poller_hot as hot
    from backend.services.feeds_regulatory import MarketFeed

    hot._source_health.clear()
    items = [{"type": "ticker", "instrument_id": "i1", "value": "AAPL", "monitor_news": True}]
    _stub_watchlist(monkeypatch, items)

    fake_feed = MarketFeed(lambda: "https://fake-source.example/feed", lambda raw: [], "rss:fake5")
    _seed_source_row("rss:fake5", "regulatory")
    monkeypatch.setattr(hot, "all_regulatory_feeds", lambda: [fake_feed])
    monkeypatch.setattr(hot, "all_outlet_feeds", lambda: [])
    monkeypatch.setattr(hot, "gather_matched", lambda url, entities, **kw: _noop())
    monkeypatch.setattr(hot.collect_mod, "fetch_failed", lambda url: True)
    monkeypatch.setattr(hot, "gather_rss", lambda feeds, **kw: _noop())

    assert hot.source_health_snapshot().get("rss:fake5") is None  # never attempted yet

    for _ in range(hot._FAILURE_STREAK_THRESHOLD):
        asyncio.run(hot.run_hot_tick())

    snap = hot.source_health_snapshot()
    assert snap["rss:fake5"]["consecutive_failures"] == hot._FAILURE_STREAK_THRESHOLD
    assert snap["rss:fake5"]["backing_off"] is True
    assert snap["rss:fake5"]["skip_remaining_ticks"] > 0

    # The snapshot is a COPY — mutating it must never touch the poller's live state.
    snap["rss:fake5"]["consecutive_failures"] = 999
    assert hot._source_health["rss:fake5"]["failures"] == hot._FAILURE_STREAK_THRESHOLD
