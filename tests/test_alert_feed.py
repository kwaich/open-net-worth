"""services/alert_feed — the push-worthy alerts the desktop raises as banners.

Stories are written through the real choke (``apply_story_change``) using the monitors' own
builders, so the feed is proven against what they actually persist. No LLM, no network.

Run:
    .venv/bin/python3 -m pytest tests/test_alert_feed.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.desktop_api import router
from backend.api.desktop_transport import SESSION_HEADER, DesktopSession, DesktopTransportMiddleware
from backend.monitoring import analyst, crash_watch, watch_analyst
from backend.services import alert_feed
from backend.services.intel_stories import apply_story_change, set_state

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _iso(**ago) -> str:
    return (NOW - timedelta(**ago)).isoformat()


def _crash(ticker: str, at: str) -> dict:
    return crash_watch.build_signal(
        ticker=ticker,
        label=f"{ticker} Corp",
        currency="USD",
        current=90.0,
        prev=100.0,
        mv=-10.0,
        value_base=9000.0,
        drop_base=1000.0,
        base="USD",
        now=at,
        instrument_id=f"ins_{ticker.lower()}",
    )


def _price_alert(at: str) -> dict:
    return {
        "id": "price-alert-ins_zzz-k1",
        "type": "price_alert",
        "instrument_id": "ins_zzz",
        "label": "ZZZ Inc — price alert",
        "ticker": "ZZZ",
        "status": "action_needed",
        "current_summary": "ZZZ Inc has risen above 50.",
        "portfolio_impact": "",
        "action_items": [],
        "created_at": at,
        "updated_at": at,
    }


def _watch_story() -> dict:
    return {
        "id": "watch-amd",
        "type": "asset",
        "label": "AMD",
        "ticker": "AMD",
        "status": "monitoring",
        "current_summary": "",
        "created_at": _iso(days=5),
        "updated_at": _iso(days=5),
    }


def _explain(story: dict, at: str) -> None:
    result = {
        "what_happened": "AMD fell 9% after a delay",
        "why": "A supplier delayed its next chip.",
        "what_it_means": "It sits inside your buy zone.",
        "worth_weighing": ["peer reaction"],
        "source_ids": [0],
    }
    why = {"sources": [{"title": "Wire", "url": "https://example.test/a"}]}
    watch_analyst._persist(story, "company_move", "AMD:company_move:2026-10-03", result, why, at)


def test_price_alert_and_crash_watch_fires_are_events(db_isolated):
    apply_story_change(_price_alert(_iso(minutes=5)))
    apply_story_change(_crash("AAA", _iso(minutes=2)))
    events = alert_feed.recent_alerts(now=NOW)
    assert [e["kind"] for e in events] == ["crash_watch", "price_alert"]
    assert events[0]["title"] == "AAA Corp — sharp drop"
    assert "down 10.0%" in events[0]["summary"]
    assert events[1]["title"] == "ZZZ Inc — price alert"


def test_a_refire_is_a_new_event_and_reading_does_not_change_the_id(db_isolated):
    apply_story_change(_price_alert(_iso(minutes=30)))
    first = alert_feed.recent_alerts(now=NOW)[0]["id"]
    set_state("price-alert-ins_zzz-k1", read=True)
    assert alert_feed.recent_alerts(now=NOW)[0]["id"] == first
    apply_story_change(_price_alert(_iso(minutes=1)))
    assert alert_feed.recent_alerts(now=NOW)[0]["id"] != first


def test_explained_watch_alert_is_an_event_carrying_what_and_why(db_isolated):
    story = _watch_story()
    apply_story_change(story)
    _explain(story, _iso(minutes=3))
    (event,) = alert_feed.recent_alerts(now=NOW)
    assert event["kind"] == "watch_alert"
    assert event["title"] == "AMD fell 9% after a delay"
    assert event["summary"] == "A supplier delayed its next chip."


def test_a_story_that_was_never_explained_or_pushed_is_not_an_event(db_isolated):
    apply_story_change(_watch_story())
    assert alert_feed.recent_alerts(now=NOW) == []


def test_pushed_analysis_is_recorded_even_with_no_external_channel(db_isolated):
    apply_story_change(_watch_story())
    parsed = {
        "headline": "AMD wins a cloud deal",
        "why": "A hyperscaler signed a multi-year order.",
    }
    asyncio.run(analyst._push_analysis("AMD", "AMD", parsed, story_id="watch-amd"))
    (event,) = alert_feed.recent_alerts()
    assert event["kind"] == "analysis"
    assert event["title"] == "AMD: AMD wins a cloud deal"
    assert event["summary"] == "A hyperscaler signed a multi-year order."


def test_analysis_push_log_is_bounded_and_survives_story_updates(db_isolated):
    apply_story_change(_watch_story())
    for i in range(alert_feed._PUSH_LOG_KEEP + 3):
        alert_feed.record_analysis_push("watch-amd", title=f"t{i}", summary="s")
    assert len(alert_feed.recent_alerts()) == alert_feed._PUSH_LOG_KEEP
    set_state("watch-amd", read=True)
    assert len(alert_feed.recent_alerts()) == alert_feed._PUSH_LOG_KEEP


def test_recording_for_a_missing_story_is_a_noop(db_isolated):
    alert_feed.record_analysis_push("nope", title="t", summary="s")
    assert alert_feed.recent_alerts() == []


def test_window_dismissed_and_limit(db_isolated):
    apply_story_change(_price_alert(_iso(hours=30)))
    assert alert_feed.recent_alerts(now=NOW) == []
    for i in range(4):
        apply_story_change(_crash(f"T{i}", _iso(minutes=i + 1)))
    assert [e["title"] for e in alert_feed.recent_alerts(now=NOW, limit=2)] == [
        "T0 Corp — sharp drop",
        "T1 Corp — sharp drop",
    ]
    set_state("crash-watch-ins_t0", dismissed=True)
    assert "T0 Corp — sharp drop" not in [e["title"] for e in alert_feed.recent_alerts(now=NOW)]


def test_endpoint_is_desktop_only_and_bounded(db_isolated):
    apply_story_change(_crash("AAA", datetime.now(timezone.utc).isoformat()))
    bare = FastAPI()
    bare.include_router(router)
    assert TestClient(bare).get("/api/desktop/alerts").status_code == 403
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(
        DesktopTransportMiddleware, session=DesktopSession("x" * 48, "desktop-test-instance", 8097)
    )
    client = TestClient(app, base_url="http://127.0.0.1:8097", headers={SESSION_HEADER: "x" * 48})
    body = client.get("/api/desktop/alerts").json()
    assert [e["kind"] for e in body] == ["crash_watch"]
    assert set(body[0]) == {"id", "kind", "title", "summary", "created_at"}
    assert client.get("/api/desktop/alerts?limit=0").status_code == 422
    assert client.get("/api/desktop/alerts?limit=500").status_code == 422
