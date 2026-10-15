"""News materiality-screen tick counters — owner ask 2026-09-01: "are we tracking the % of
news that processed and respective results/actions ... so we know if our process is
working properly and not suppress too many or too few news"."""

from __future__ import annotations

from backend.db.news_screen_observability_store import NewsScreenObservabilityStore
from backend.services import news_screen_observability


def test_record_tick_and_summary_round_trip(db_isolated):
    store = NewsScreenObservabilityStore()
    now = 2_000_000_000.0
    store.record_tick(10, 3, now=now)
    store.record_tick(4, 0, now=now + 1)

    summary = store.summary(hours=24, now=now + 10)
    assert summary["ticks"] == 2
    assert summary["candidates_total"] == 14
    assert summary["material_total"] == 3
    assert summary["suppressed_total"] == 11
    assert summary["material_rate"] == 3 / 14


def test_summary_excludes_ticks_outside_the_window(db_isolated):
    store = NewsScreenObservabilityStore()
    now = 2_000_000_000.0
    store.record_tick(10, 5, now=now - 25 * 3600)  # outside a 24h window
    store.record_tick(2, 1, now=now - 60)  # inside

    summary = store.summary(hours=24, now=now)
    assert summary["ticks"] == 1
    assert summary["candidates_total"] == 2
    assert summary["material_total"] == 1


def test_summary_with_zero_candidates_never_divides_by_zero(db_isolated):
    store = NewsScreenObservabilityStore()
    summary = store.summary(hours=24)
    assert summary["candidates_total"] == 0
    assert summary["material_rate"] is None


def test_bounded_growth_keeps_only_the_newest_ticks(db_isolated, monkeypatch):
    import backend.db.news_screen_observability_store as subject

    monkeypatch.setattr(subject, "_MAX_TICKS", 3)
    store = NewsScreenObservabilityStore()
    now = 2_000_000_000.0
    for i in range(5):
        store.record_tick(1, 1, now=now + i)

    doc = store.get()
    assert len(doc["ticks"]) == 3
    # The oldest two ticks were dropped — only the newest 3 survive.
    assert [t["ts"] for t in doc["ticks"]] == [now + 2, now + 3, now + 4]


def test_service_wrapper_never_raises_on_a_broken_store(db_isolated, monkeypatch):
    """Telemetry can never fail the screen itself — record_tick swallows errors, summary()
    degrades to a zeroed shape."""

    def _boom(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(NewsScreenObservabilityStore, "record_tick", _boom)
    monkeypatch.setattr(NewsScreenObservabilityStore, "summary", _boom)

    news_screen_observability.record_tick(5, 2)  # must not raise
    result = news_screen_observability.summary(hours=24)
    assert result["candidates_total"] == 0
    assert result["material_rate"] is None
