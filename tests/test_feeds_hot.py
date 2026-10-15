"""services/feeds_hot.py — GDELT resolver/parser, combined query builder, and the shared
name/ticker-token matcher the hot tier uses to fan a shared firehose/query out to many
watched entities (news-ingestion-latency.md §1-2).
"""

from __future__ import annotations

import json

from backend.services import feeds_hot as fh


def test_gdelt_doc_url_shape():
    url = fh.gdelt_doc_url('"ONWAI SE"')
    assert url.startswith("https://api.gdeltproject.org/api/v2/doc/doc?query=")
    assert "mode=artlist" in url
    assert "format=json" in url


def test_bing_news_url_shape():
    url = fh.bing_news_url("ONWAI SE")
    assert url.startswith("https://www.bing.com/news/search?q=")
    assert "format=RSS" in url


# ── parse_gdelt ─────────────────────────────────────────────────────────────────


def _gdelt_payload(articles):
    return json.dumps({"articles": articles}).encode()


def test_parse_gdelt_maps_fields_to_provider_shape():
    payload = _gdelt_payload(
        [
            {
                "url": "https://example.com/a",
                "title": "Uber nears ONWAI SE deal",
                "seendate": "20260714T195400Z",
                "domain": "example.com",
                "socialimage": "https://example.com/img.png",
            }
        ]
    )
    hits = fh.parse_gdelt(payload)
    assert len(hits) == 1
    h = hits[0]
    assert h["url"] == "https://example.com/a"
    assert h["title"] == "Uber nears ONWAI SE deal"
    assert h["source"] == "example.com"
    assert h["via"] == "api:gdelt"
    assert h["image"] == "https://example.com/img.png"
    assert h["published_date"] == "2026-07-14T19:54:00+00:00"


def test_parse_gdelt_drops_non_http_urls():
    payload = _gdelt_payload([{"url": "javascript:alert(1)", "title": "x", "seendate": ""}])
    assert fh.parse_gdelt(payload) == []


def test_parse_gdelt_empty_bytes():
    assert fh.parse_gdelt(b"") == []


def test_parse_gdelt_garbage_json():
    assert fh.parse_gdelt(b"not json") == []


def test_parse_gdelt_missing_articles_key():
    assert fh.parse_gdelt(json.dumps({"foo": "bar"}).encode()) == []


def test_parse_gdelt_bad_seendate_leaves_published_date_blank():
    payload = _gdelt_payload([{"url": "https://e/a", "title": "t", "seendate": "not-a-date"}])
    hits = fh.parse_gdelt(payload)
    assert hits[0]["published_date"] == ""


# ── combined_gdelt_query ─────────────────────────────────────────────────────────


def test_combined_gdelt_query_ors_primary_terms():
    entities = [
        {"subject": "topic:t1", "terms": ["ONWAI M&A", "ONWAI.DE"]},
        {"subject": "topic:t2", "terms": ["Semiconductors"]},
    ]
    q = fh.combined_gdelt_query(entities)
    assert q == '"ONWAI M&A" OR "Semiconductors"'


def test_combined_gdelt_query_empty_when_no_entities():
    assert fh.combined_gdelt_query([]) == ""


def test_combined_gdelt_query_bounded_by_max_terms():
    entities = [{"subject": f"topic:t{i}", "terms": [f"term{i}"]} for i in range(20)]
    q = fh.combined_gdelt_query(entities, max_terms=3)
    assert q.count(" OR ") == 2  # exactly 3 terms


def test_combined_gdelt_query_skips_entity_with_no_terms():
    entities = [{"subject": "topic:t1", "terms": []}, {"subject": "topic:t2", "terms": ["x"]}]
    assert fh.combined_gdelt_query(entities) == '"x"'


# ── match_hits_to_subjects ────────────────────────────────────────────────────────


def test_match_hits_to_subjects_case_insensitive_substring():
    hits = [{"title": "ONWAI SE confirms takeover talks", "url": "https://e/a"}]
    entities = [{"subject": "topic:t1", "terms": ["onwai se"]}]
    matched = fh.match_hits_to_subjects(hits, entities)
    assert len(matched) == 1
    assert matched[0]["_subjects"] == ["topic:t1"]


def test_match_hits_to_subjects_drops_unmatched():
    hits = [{"title": "Totally unrelated headline", "url": "https://e/a"}]
    entities = [{"subject": "topic:t1", "terms": ["onwai se"]}]
    assert fh.match_hits_to_subjects(hits, entities) == []


def test_match_hits_to_subjects_multiple_entities_ride_all_matches():
    hits = [{"title": "Uber and ONWAI SE deal talks advance", "url": "https://e/a"}]
    entities = [
        {"subject": "topic:t1", "terms": ["onwai se"]},
        {"subject": "asset:uber-iid", "terms": ["uber"]},
    ]
    matched = fh.match_hits_to_subjects(hits, entities)
    assert set(matched[0]["_subjects"]) == {"topic:t1", "asset:uber-iid"}


def test_match_hits_to_subjects_ignores_short_noise_terms():
    hits = [{"title": "AI news roundup", "url": "https://e/a"}]
    entities = [{"subject": "topic:t1", "terms": ["ai"]}]  # len<3 term never matches
    assert fh.match_hits_to_subjects(hits, entities) == []


def test_match_hits_to_subjects_blank_title_dropped():
    hits = [{"title": "", "url": "https://e/a"}]
    entities = [{"subject": "topic:t1", "terms": ["anything"]}]
    assert fh.match_hits_to_subjects(hits, entities) == []


# ── SF-2 (code review): short terms require a whole-token match, not raw substring ──────


def test_match_hits_to_subjects_short_term_does_not_match_mid_word():
    """Regression: the ticker term "ALL" used to substring-match inside "Wall" — a real false
    positive the reviewer reproduced. A 3-5 char term must land on its OWN token now."""
    hits = [{"title": "Wall Street closes higher on tech rally", "url": "https://e/a"}]
    entities = [{"subject": "asset:all-iid", "terms": ["ALL"]}]
    assert fh.match_hits_to_subjects(hits, entities) == []


def test_match_hits_to_subjects_short_term_matches_its_own_standalone_word():
    hits = [{"title": "All systems go for the launch", "url": "https://e/a"}]
    entities = [{"subject": "asset:all-iid", "terms": ["ALL"]}]
    matched = fh.match_hits_to_subjects(hits, entities)
    assert matched[0]["_subjects"] == ["asset:all-iid"]


def test_match_hits_to_subjects_ticker_entry_requires_exact_case():
    """Owner report 2026-08-31, live ServiceNow: a short ticker that IS a common English word
    (NOW = ServiceNow) used to still whole-token-match that word used generically — an accepted
    tradeoff until a live incident showed CNBC's/MarketWatch's general firehoses tagging
    unrelated stories ("...right now", "...now it's valued at...") to ServiceNow purely off this
    collision. An entity's `tickers` entries (as opposed to `terms`) close it instead of merely
    shrinking it: EXACT-CASE whole-token match against the title's original casing — ordinary
    prose ("now", "Now") no longer matches, only a genuine all-caps ticker reference does. This
    is a STRUCTURAL property of `tickers` itself (owner correction: "must be generic for all
    users", not a curated word list) — proven by the sibling test below using a made-up ticker
    this codebase has never seen before, which gets the identical protection with zero code
    changes."""
    ordinary_prose = [{"title": "New rules apply now for overtime pay", "url": "https://e/a"}]
    sentence_initial = [{"title": "Now hiring: warehouse associates", "url": "https://e/b"}]
    entities = [{"subject": "asset:now-iid", "terms": ["ServiceNow"], "tickers": ["NOW"]}]
    assert fh.match_hits_to_subjects(ordinary_prose, entities) == []
    assert fh.match_hits_to_subjects(sentence_initial, entities) == []

    genuine_ticker_reference = [
        {"title": "NOW Stock Surges After Beating Q2 Estimates", "url": "https://e/c"}
    ]
    matched = fh.match_hits_to_subjects(genuine_ticker_reference, entities)
    assert matched[0]["_subjects"] == ["asset:now-iid"]


def test_match_hits_to_subjects_ticker_exact_case_is_generic_not_special_cased():
    """Same guarantee as the test above, for a ticker no code in this repo has ever named —
    proves the mechanism is structural (driven by an entry being in `tickers` at all), not a
    lookup against a maintained list of "known collision" symbols."""
    ordinary_prose = [
        {"title": "Investors debate whether this rally can go the distance", "url": "https://e/a"}
    ]
    entities = [{"subject": "asset:go-iid", "terms": ["GO Corp"], "tickers": ["GO"]}]
    assert fh.match_hits_to_subjects(ordinary_prose, entities) == []

    genuine_ticker_reference = [{"title": "GO Corp Reports Record Revenue", "url": "https://e/b"}]
    matched = fh.match_hits_to_subjects(genuine_ticker_reference, entities)
    assert matched[0]["_subjects"] == ["asset:go-iid"]


def test_match_hits_to_subjects_long_term_still_substring_matches():
    """>=6 char terms (incl. multi-word phrases) are UNCHANGED — plain substring, so a phrase
    like "onwai se" still matches inside a longer surrounding headline."""
    hits = [{"title": "Sources: ONWAI SE in exclusive Uber talks", "url": "https://e/a"}]
    entities = [{"subject": "topic:t1", "terms": ["onwai se"]}]
    matched = fh.match_hits_to_subjects(hits, entities)
    assert matched[0]["_subjects"] == ["topic:t1"]


def test_match_hits_to_subjects_true_positives_stay_green():
    """The pre-existing ONWAI SE/ONWAI.DE true positives (both >=6 chars) are unaffected."""
    hits = [{"title": "ONWAI.DE shares jump on takeover report", "url": "https://e/a"}]
    entities = [{"subject": "asset:onwai-iid", "terms": ["ONWAI.DE"]}]
    matched = fh.match_hits_to_subjects(hits, entities)
    assert matched[0]["_subjects"] == ["asset:onwai-iid"]
