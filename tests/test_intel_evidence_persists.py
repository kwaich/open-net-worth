"""Regression (the bug, at the wiring level): run_intel_scan must feed still-valid evidence from an
earlier scan into the analysis prompt even when TODAY's live search returns nothing — instead of
losing it. Heavy deps are stubbed; we capture the prompt handed to _submit_and_poll and assert.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.db.evidence_store import EvidenceStore
from backend.monitoring import collect
from backend.monitoring import intel_agent as ia
from backend.services import evidence as ev


class _Stop(Exception):
    """Short-circuits run_intel_scan right after the prompt is built."""


class _NoNews:
    async def search(self, q, max_results=5):
        return []  # today's live search finds nothing


def test_intel_scan_feeds_cached_evidence_when_live_search_empty(
    tmp_path, monkeypatch, db_isolated
):
    # An article found in an earlier scan, still within TTL, tagged scan:intel.
    ev.record_evidence(
        [
            {
                "url": "https://news/yesterday",
                "title": "Acme wins major contract",
                "content": "still relevant context",
                "published_date": "yesterday",
                "_query": "Acme stock news",
            }
        ],
        subjects=["scan:intel"],
        kind="news",
        store=EvidenceStore(),
    )

    wl = {
        "config": {"enabled": True, "auto_scan_holdings": False, "search_budget": 4},
        "items": [
            {
                "type": "topic",
                "value": "semiconductors",
                "label": "Semiconductors",
                "monitor_news": True,
                "search_queries": ["semiconductors outlook"],
            }
        ],
    }
    monkeypatch.setattr(ia, "read_watchlist", lambda: wl)
    monkeypatch.setattr(ia, "write_watchlist", lambda _wl: None)
    monkeypatch.setattr(ia, "read_rows", lambda: [])
    monkeypatch.setattr(ia, "load_fx_cache", lambda: {})
    monkeypatch.setattr(ia, "read_profile", lambda: {"base_currency": "USD"})
    monkeypatch.setattr(ia, "_build_macro_queries", lambda *a, **k: [])
    monkeypatch.setattr(ia, "_build_opportunity_queries", lambda *a, **k: [])
    monkeypatch.setattr(ia, "_active_goals_summary", lambda: "none set")
    monkeypatch.setattr("backend.services.risk_signals.emit_risk_signals", lambda store: None)
    monkeypatch.setattr("backend.services.macro_exposure.currency_exposure", lambda **k: {})
    monkeypatch.setattr(collect, "get_search_provider", lambda: _NoNews())
    monkeypatch.setattr(
        collect, "_fetch_feed", lambda url, max_age=None: []
    )  # RSS lane returns nothing this run
    monkeypatch.setattr("backend.services.prices.fetch_movers", lambda *a, **kw: {}, raising=False)

    captured: dict = {}

    async def _fake_submit(prompt, **kw):
        captured["prompt"] = prompt
        raise _Stop()

    monkeypatch.setattr(ia, "_submit_and_poll", _fake_submit)

    with pytest.raises(_Stop):
        asyncio.run(ia.run_intel_scan())

    # The still-valid cached article reached the analysis prompt despite the empty live search.
    assert "Acme wins major contract" in captured["prompt"]


def test_intel_scan_pulls_rss_for_holdings_with_web_search_off(tmp_path, monkeypatch, db_isolated):
    """E2E: RSS feeds a holding's news into the prompt with paid web search OFF (RSS-only mode)."""
    wl = {
        "config": {"enabled": True, "auto_scan_holdings": False, "web_search_enabled": False},
        "items": [],
    }
    holding = {
        "instrument_id": "i1",
        "price_source_id": "ONWAI.DE",
        "ticker": "ONWAI.DE",
        "label": "ONWAI SE",
        "asset_class": "equity",
        "price": 37.0,
        "quantity": 1,
        "currency": "EUR",
    }
    monkeypatch.setattr(ia, "read_watchlist", lambda: wl)
    monkeypatch.setattr(ia, "write_watchlist", lambda _wl: None)
    monkeypatch.setattr(ia, "read_rows", lambda: [holding])
    monkeypatch.setattr(ia, "load_fx_cache", lambda: {"EUR": 1.0, "USD": 1.0})
    monkeypatch.setattr(ia, "read_profile", lambda: {"base_currency": "USD"})
    monkeypatch.setattr(ia, "_build_macro_queries", lambda *a, **k: [])
    monkeypatch.setattr(ia, "_build_opportunity_queries", lambda *a, **k: [])
    monkeypatch.setattr(ia, "_active_goals_summary", lambda: "none set")
    monkeypatch.setattr("backend.services.risk_signals.emit_risk_signals", lambda store: None)
    monkeypatch.setattr("backend.services.macro_exposure.currency_exposure", lambda **k: {})
    monkeypatch.setattr("backend.services.prices.fetch_movers", lambda *a, **kw: {}, raising=False)
    # RSS returns a holding story; web search must NOT be consulted (web_search_enabled=False).
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None: [
            {
                "url": "https://news/dh",
                "title": "ONWAI news via RSS",
                "content": "ctx",
                "published_date": "1 hour ago",
                "source": "Reuters",
            }
        ],
    )

    def _boom_provider():
        raise AssertionError("web search must not run when web_search_enabled=False")

    monkeypatch.setattr(collect, "get_search_provider", _boom_provider)

    captured: dict = {}

    async def _fake_submit(prompt, **kw):
        captured["prompt"] = prompt
        raise _Stop()

    monkeypatch.setattr(ia, "_submit_and_poll", _fake_submit)

    with pytest.raises(_Stop):
        asyncio.run(ia.run_intel_scan())

    assert "ONWAI news via RSS" in captured["prompt"]  # RSS reached the analysis


def test_opportunity_scan_feeds_cached_evidence_when_live_search_empty(
    tmp_path, monkeypatch, db_isolated
):
    """Same accumulation guarantee for the standalone opportunity scan (shared scan:opportunity)."""
    ev.record_evidence(
        [
            {
                "url": "https://news/opp",
                "title": "Sector breakout still live",
                "content": "thesis",
                "published_date": "yesterday",
                "_query": "sector breakout",
            }
        ],
        subjects=["scan:opportunity"],
        kind="opportunity",
        store=EvidenceStore(),
    )
    monkeypatch.setattr(ia, "read_rows", lambda: [])
    monkeypatch.setattr(ia, "load_fx_cache", lambda: {})
    monkeypatch.setattr(ia, "read_profile", lambda: {"base_currency": "USD"})
    monkeypatch.setattr(ia, "read_watchlist", lambda: {"items": [], "config": {}})
    monkeypatch.setattr(ia, "_build_opportunity_queries", lambda *a, **k: ["sector breakout"])
    monkeypatch.setattr(ia, "_active_goals_summary", lambda: "none set")
    monkeypatch.setattr(collect, "get_search_provider", lambda: _NoNews())

    captured: dict = {}

    async def _fake_submit(prompt, **kw):
        captured["prompt"] = prompt
        raise _Stop()

    monkeypatch.setattr(ia, "_submit_and_poll", _fake_submit)

    with pytest.raises(_Stop):
        asyncio.run(ia.run_opportunity_scan())

    assert "Sector breakout still live" in captured["prompt"]
