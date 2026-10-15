"""Phase 3b — opportunity discovery free path.

Verifies that:
1. With web_search_enabled=False, the opportunity scan gathers RSS + screener movers
   (free) and does NOT call the paid search provider.
2. notify() is NEVER called for opportunity signals — they are in-app only (written to
   IntelStoriesStore) and must not reach alert_channels / Telegram.
3. run_opportunity_scan respects web_search_enabled via the watchlist config.
4. The full intel scan's opportunity leg also gathers opportunity RSS (free) regardless
   of web_search_enabled.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.db.evidence_store import EvidenceStore
from backend.monitoring import collect
from backend.monitoring import intel_agent as ia
from backend.services import evidence as ev


class _Stop(Exception):
    """Short-circuits the scan right after the prompt is built."""


# ── opportunity scan: free path (web_search_enabled=False) ───────────────────────


def _common_stubs(monkeypatch):
    """Patch common service calls. Callers must also depend on db_isolated — it covers
    both the evidence DB and IntelStoriesStore (DB-backed now, plan 4d task 2; no more
    per-store INTEL_SIGNALS_JSON to monkeypatch here)."""
    monkeypatch.setattr(ia, "read_rows", lambda: [])
    monkeypatch.setattr(ia, "load_fx_cache", lambda: {})
    monkeypatch.setattr(ia, "read_profile", lambda: {"base_currency": "USD"})
    monkeypatch.setattr(ia, "_active_goals_summary", lambda: "none set")


def test_opportunity_scan_rss_populates_evidence_when_search_disabled(monkeypatch, db_isolated):
    """With web_search_enabled=False the scan gathers RSS and the resulting evidence reaches the LLM."""
    _common_stubs(monkeypatch)

    wl = {"config": {"enabled": True, "web_search_enabled": False}, "items": []}
    monkeypatch.setattr(ia, "read_watchlist", lambda: wl)
    monkeypatch.setattr(ia, "write_watchlist", lambda _wl: None)
    monkeypatch.setattr(ia, "_build_opportunity_queries", lambda *a: ["top movers this week"])

    # RSS returns an article; paid search must NOT be consulted.
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, *a, **kw: [
            {
                "url": "https://news/free-opp",
                "title": "Big mover discovered via RSS",
                "content": "tech sector up 10%",
                "published_date": "1 hour ago",
                "source": "Reuters",
            }
        ],
    )

    search_called: list = []

    def _boom_provider():
        search_called.append(True)
        raise AssertionError("paid search must not run when web_search_enabled=False")

    monkeypatch.setattr(collect, "get_search_provider", _boom_provider)
    # Stub movers so we don't need yfinance (fetch_movers is imported locally in intel_agent)
    monkeypatch.setattr("backend.services.prices.fetch_movers", lambda *a, **kw: {}, raising=False)

    captured: dict = {}

    async def _fake_submit(prompt, **kw):
        captured["prompt"] = prompt
        raise _Stop()

    monkeypatch.setattr(ia, "_submit_and_poll", _fake_submit)

    with pytest.raises(_Stop):
        asyncio.run(ia.run_opportunity_scan())

    assert search_called == [], "paid search provider was invoked (should not be)"
    assert "Big mover discovered via RSS" in captured["prompt"]


def test_opportunity_scan_no_notify_called(monkeypatch, db_isolated):
    """Opportunity signals must NEVER reach notify() / alert_channels — in-app only."""
    _common_stubs(monkeypatch)

    # Pre-seed some evidence so the scan has content and doesn't bail early.
    ev.record_evidence(
        [
            {
                "url": "https://news/opp-signal",
                "title": "High upside sector opportunity",
                "content": "tech breakout",
                "published_date": "today",
                "_query": "sector breakout",
            }
        ],
        subjects=["scan:opportunity"],
        kind="opportunity",
        store=EvidenceStore(),
    )

    wl = {"config": {"enabled": True, "web_search_enabled": False}, "items": []}
    monkeypatch.setattr(ia, "read_watchlist", lambda: wl)
    monkeypatch.setattr(ia, "write_watchlist", lambda _wl: None)
    monkeypatch.setattr(ia, "_build_opportunity_queries", lambda *a: ["sector breakout"])
    monkeypatch.setattr(collect, "_fetch_feed", lambda url, *a, **kw: [])
    monkeypatch.setattr(collect, "get_search_provider", lambda: _FakeProvider({}))

    notify_calls: list = []

    import backend.services.notify as notify_mod

    async def _fake_notify(message, channels=None, title=None):
        notify_calls.append({"message": message, "channels": channels})

    monkeypatch.setattr(notify_mod, "notify", _fake_notify)

    # Stub _submit_and_poll to avoid real LLM calls but return a fake opportunity signal.
    async def _fake_submit(prompt, **kw):
        return (
            [
                {
                    "type": "opportunity",
                    "label": "Tech Breakout",
                    "status": "active",
                    "summary": "Tech sector is breaking out.",
                    "new_timeline_event": {
                        "date": "2026-06-19",
                        "title": "Sector move",
                        "summary": "Prices rising.",
                        "sources": [{"title": "S", "url": "https://s/a"}],
                    },
                }
            ],
            None,
            "cid-test",
            None,
        )

    monkeypatch.setattr(ia, "_submit_and_poll", _fake_submit)

    count = asyncio.run(ia.run_opportunity_scan())
    assert count >= 0  # may be 0 if evidence doesn't pass the gate — key is no notify
    assert notify_calls == [], (
        f"notify() was called for an opportunity signal — must never happen; calls={notify_calls}"
    )


# ── full intel scan: opportunity RSS runs regardless of web_search flag ───────────


def test_intel_scan_gathers_opportunity_rss_with_web_search_off(monkeypatch, db_isolated):
    """The full intel scan populates scan:opportunity via RSS even when web_search_enabled=False."""
    _common_stubs(monkeypatch)
    monkeypatch.setattr("backend.services.risk_signals.emit_risk_signals", lambda store: None)
    monkeypatch.setattr("backend.services.macro_exposure.currency_exposure", lambda **k: {})

    wl = {
        "config": {
            "enabled": True,
            "auto_scan_holdings": False,
            "web_search_enabled": False,
            "search_budget": 4,
        },
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
    monkeypatch.setattr(ia, "_build_macro_queries", lambda *a, **k: [])
    monkeypatch.setattr(
        ia,
        "_build_opportunity_queries",
        lambda *a: ["top movers this week", "sector rotation"],
    )

    # RSS returns different content for intel vs opportunity feeds so we can distinguish.
    rss_calls: list[str] = []

    def _fake_feed(url, *a, **kw):
        rss_calls.append(url)
        if "top%20movers" in url or "sector%20rotation" in url or "top+movers" in url:
            return [
                {
                    "url": f"https://news/opp-{len(rss_calls)}",
                    "title": "Opportunity via RSS",
                    "content": "market moving",
                    "published_date": "today",
                    "source": "Bloomberg",
                }
            ]
        return [
            {
                "url": "https://news/intel-topic",
                "title": "Semiconductors topic article",
                "content": "chips",
                "published_date": "today",
                "source": "Reuters",
            }
        ]

    monkeypatch.setattr(collect, "_fetch_feed", _fake_feed)

    # Paid search must NOT be consulted at all (web_search_enabled=False)
    def _boom_provider():
        raise AssertionError("paid search must not run when web_search_enabled=False")

    monkeypatch.setattr(collect, "get_search_provider", _boom_provider)

    # Stub movers fetch so we don't need yfinance
    monkeypatch.setattr("backend.services.prices.fetch_movers", lambda *a, **kw: {}, raising=False)

    captured: dict = {}

    async def _fake_submit(prompt, **kw):
        captured["prompt"] = prompt
        raise _Stop()

    monkeypatch.setattr(ia, "_submit_and_poll", _fake_submit)

    with pytest.raises(_Stop):
        asyncio.run(ia.run_intel_scan())

    # Both intel (topic) AND opportunity RSS content must have reached the prompt
    assert "Semiconductors topic article" in captured["prompt"], "intel RSS missing from prompt"
    assert "Opportunity via RSS" in captured["prompt"], (
        "opportunity RSS did not reach the prompt (free path broken)"
    )
    # Opportunity RSS feeds were fetched (the call list includes opportunity query URLs)
    opp_calls = [
        u
        for u in rss_calls
        if "top%20movers" in u
        or "sector%20rotation" in u
        or "top+movers" in u
        or "sector+rotation" in u
    ]
    assert opp_calls, "no opportunity RSS feed was fetched"


# ── helper stub ──────────────────────────────────────────────────────────────────


class _FakeProvider:
    def __init__(self, by_query=None):
        self.by_query = by_query or {}
        self.calls: list[str] = []

    async def search(self, q, max_results=5):
        self.calls.append(q)
        return list(self.by_query.get(q, []))
