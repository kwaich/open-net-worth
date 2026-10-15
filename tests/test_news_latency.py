"""services/news_latency.py — per-source publish→arrival lag aggregation
(news-ingestion-latency.md §5) + the GET /api/intel/source-latency endpoint + the
get_news_latency AI tool.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.evidence_store import EvidenceStore


@pytest.fixture
def store(db_isolated):
    return EvidenceStore()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _row(rid, via, published_at, first_seen):
    return {
        "id": rid,
        "url": f"https://e/{rid}",
        "title": "t",
        "via": via,
        "published_at": published_at,
        "first_seen": first_seen,
        "subjects": ["asset:i1"],
        "expires_at": _iso(datetime.now(timezone.utc) + timedelta(days=30)),
    }


def test_lag_stats_computes_median_and_p75(store):
    from backend.services.news_latency import lag_stats

    now = datetime.now(timezone.utc)
    # rss:yahoo — 3 rows with lags 0.5h, 1.0h, 2.0h
    rows = [
        _row("a", "rss:yahoo", _iso(now - timedelta(hours=1.5)), _iso(now - timedelta(hours=1.0))),
        _row("b", "rss:yahoo", _iso(now - timedelta(hours=2.0)), _iso(now - timedelta(hours=1.0))),
        _row("c", "rss:yahoo", _iso(now - timedelta(hours=3.0)), _iso(now - timedelta(hours=1.0))),
    ]
    store.merge_hits(rows)

    stats = lag_stats(days=7, store=store)
    assert "rss:yahoo" in stats
    assert stats["rss:yahoo"]["n"] == 3
    assert stats["rss:yahoo"]["median_hours"] == 1.0


def test_lag_stats_separates_by_source(store):
    from backend.services.news_latency import lag_stats

    now = datetime.now(timezone.utc)
    rows = [
        _row("a", "rss:yahoo", _iso(now - timedelta(hours=1.25)), _iso(now - timedelta(hours=1))),
        _row(
            "b", "rss:google_news", _iso(now - timedelta(hours=4)), _iso(now - timedelta(hours=1))
        ),
    ]
    store.merge_hits(rows)
    stats = lag_stats(store=store)
    assert set(stats) == {"rss:yahoo", "rss:google_news"}
    assert stats["rss:google_news"]["median_hours"] > stats["rss:yahoo"]["median_hours"]


def test_lag_stats_excludes_rows_outside_the_day_window(store):
    from backend.services.news_latency import lag_stats

    now = datetime.now(timezone.utc)
    old_row = _row(
        "old", "rss:yahoo", _iso(now - timedelta(days=10, hours=1)), _iso(now - timedelta(days=10))
    )
    store.merge_hits([old_row])
    stats = lag_stats(days=7, store=store)
    assert "rss:yahoo" not in stats


def test_lag_stats_excludes_unparseable_timestamps(store):
    from backend.services.news_latency import lag_stats

    now = datetime.now(timezone.utc)
    row = _row("a", "rss:custom", "garbage-date", _iso(now))
    store.merge_hits([row])
    assert lag_stats(store=store) == {}


def test_lag_stats_excludes_negative_lag_clock_skew(store):
    from backend.services.news_latency import lag_stats

    now = datetime.now(timezone.utc)
    # published AFTER first_seen — clock skew noise
    row = _row("a", "rss:custom", _iso(now), _iso(now - timedelta(hours=1)))
    store.merge_hits([row])
    assert lag_stats(store=store) == {}


def test_lag_stats_empty_store(db_isolated):
    from backend.services.news_latency import lag_stats

    assert lag_stats() == {}


def test_lag_stats_defaults_unknown_via_bucket(store):
    from backend.services.news_latency import lag_stats

    now = datetime.now(timezone.utc)
    row = {
        "id": "a",
        "url": "https://e/a",
        "title": "t",
        "published_at": _iso(now - timedelta(hours=1)),
        "first_seen": _iso(now),
        "subjects": ["asset:i1"],
        "expires_at": _iso(now + timedelta(days=30)),
    }  # no "via" key at all
    store.merge_hits([row])
    stats = lag_stats(store=store)
    assert "unknown" in stats


# ── REST endpoint ─────────────────────────────────────────────────────────────────


def test_source_latency_endpoint(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.api.intel_latency as api_mod

    monkeypatch.setattr(
        api_mod,
        "lag_stats",
        lambda days=7: {"rss:yahoo": {"n": 1, "median_hours": 0.3, "p75_hours": 0.3}},
    )

    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)

    resp = client.get("/api/intel/source-latency")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7
    assert body["sources"]["rss:yahoo"]["n"] == 1


def test_source_latency_endpoint_days_param_clamped(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.api.intel_latency as api_mod

    received = {}
    monkeypatch.setattr(
        api_mod, "lag_stats", lambda days=7: received.setdefault("days", days) or {}
    )

    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)

    resp = client.get("/api/intel/source-latency?days=200")
    assert resp.status_code == 422  # out of the 1-90 bound


# ── AI tool ───────────────────────────────────────────────────────────────────────


def test_get_news_latency_tool(monkeypatch):
    import backend.ai.tools.intel as intel_tools
    import backend.services.news_latency as nl_mod

    monkeypatch.setattr(
        nl_mod,
        "lag_stats",
        lambda days=7: {"atom:edgar": {"n": 2, "median_hours": 0.1, "p75_hours": 0.2}},
    )

    import asyncio

    result_json, _ = asyncio.run(intel_tools.get_news_latency({"days": 3}))
    result = json.loads(result_json)
    assert result["days"] == 3
    assert result["sources"]["atom:edgar"]["n"] == 2


def test_get_news_latency_tool_clamps_days(monkeypatch):
    import backend.ai.tools.intel as intel_tools
    import backend.services.news_latency as nl_mod

    received = {}
    monkeypatch.setattr(nl_mod, "lag_stats", lambda days=7: received.setdefault("days", days) or {})

    import asyncio

    asyncio.run(intel_tools.get_news_latency({"days": 999}))
    assert received["days"] == 90  # clamped to the max


def test_get_news_latency_runs_lag_stats_off_the_event_loop(monkeypatch):
    """SF-4 (code review): lag_stats (a full evidence.json scan) must run via asyncio.to_thread,
    not inline on the event loop — the exact 2026-07-06 Plan-tab-stall class. Proven by
    recording which thread lag_stats actually executed on."""
    import asyncio
    import threading

    import backend.ai.tools.intel as intel_tools
    import backend.services.news_latency as nl_mod

    seen_thread = {}

    def _fake_lag_stats(days=7):
        seen_thread["ident"] = threading.get_ident()
        return {}

    monkeypatch.setattr(nl_mod, "lag_stats", _fake_lag_stats)

    async def _run():
        return threading.get_ident(), await intel_tools.get_news_latency({"days": 3})

    loop_thread_ident, _ = asyncio.run(_run())

    assert "ident" in seen_thread
    assert seen_thread["ident"] != loop_thread_ident  # ran on a worker thread, not the loop
