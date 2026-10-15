"""monitoring/news_screen.py — the significance screen, THE volume-independent fast path for
material news (news-ingestion-latency.md §4, Phase 2).

Covers: filtering to monitor_news-watched subjects, clustering, the single-material-headline
case (the RFC's own headline test — ONE Bloomberg-style headline is enough, no volume needed),
a non-material headline stays silent, 6h per-subject cooldown dedup, per-tick cluster cap,
daily screen budget, and the LLM-seam-unavailable no-op (never crashes the poll).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone


def _evidence_row(subjects, title="Uber nears deal to acquire ONWAI SE", source="Bloomberg"):
    # published_at/first_seen are relative to test run time, not a hardcoded literal — a fixed
    # past date would eventually age past news_screen's own backlog-arrival age gate
    # (_is_backlog_arrival, owner incident 2026-08-29) and silently break every test here.
    now = datetime.now(timezone.utc)
    return {
        "id": f"ev_{title[:8]}",
        "url": f"https://example.com/{title[:8]}",
        "title": title,
        "source": source,
        "published_at": (now - timedelta(minutes=10)).isoformat(),
        "first_seen": now.isoformat(),
        "subjects": subjects,
        "via": "rss:yahoo",
        "seen_count": 1,
    }


def _stub(monkeypatch, tmp_path, db_isolated, *, items, llm_response=None):
    import backend.db.pending_analysis_store as pas_mod
    import backend.monitoring.news_screen as ns

    monkeypatch.setattr(ns, "read_watchlist", lambda: {"items": items, "config": {}})

    # news_screen_state is DB-backed now (Wave 5F, JSON->DB) — db_isolated already gives
    # it a fresh, empty row, no path monkeypatch needed.

    import backend.core.llm_seam as seam_mod

    complete_calls: list = []

    if isinstance(llm_response, BaseException):
        exc = llm_response

        async def _complete(
            prompt, *, system="", model="", max_tokens=2048, source="news_screen", **kw
        ):
            complete_calls.append({"prompt": prompt, "system": system})
            raise exc

    else:
        _resp = (
            llm_response
            if llm_response is not None
            else json.dumps(
                {"results": [{"index": 0, "material": True, "reason": "M&A talks confirmed"}]}
            )
        )

        async def _complete(
            prompt, *, system="", model="", max_tokens=2048, source="news_screen", **kw
        ):
            # **kw absorbs response_format/effort now threaded by the structured-output layer.
            complete_calls.append({"prompt": prompt, "system": system})
            return _resp

    monkeypatch.setattr(seam_mod, "_complete", _complete)
    return ns, pas_mod.PendingAnalysisStore(), complete_calls


_TOPIC_ITEM = [
    {
        "id": "wli-onwai",
        "type": "topic",
        "value": "ONWAI M&A",
        "label": "ONWAI M&A",
        "monitor_news": True,
        "note": "watch for a deal",
    }
]

_ASSET_ITEM = [
    {
        "id": "wli-aapl",
        "type": "ticker",
        "value": "AAPL",
        "label": "Apple",
        "instrument_id": "iid-aapl",
        "monitor_news": True,
        "asset_class": "equity",
    }
]


# ── the headline single-material-headline case (THE RFC's own requirement) ───────


def test_single_material_headline_fires_the_screen(tmp_path, monkeypatch, db_isolated):
    """ONE material headline is enough — no volume required. The RFC's own worked case."""
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)

    rows = [_evidence_row(["topic:wli-onwai", "scan:intel"])]
    asyncio.run(ns.handle_arrivals(rows))

    assert len(calls) == 1
    pending = pa_store.pending()
    assert len(pending) == 1
    row = pending[0]
    assert row["trigger"] == "material_news"
    assert row["subject"] == "topic:wli-onwai"
    assert row["signal_id"] == "news-topic-wli-onwai"  # SAME id family as news_spike
    assert row["payload"]["reason"] == "M&A talks confirmed"


def test_backlog_arrival_never_reaches_the_screen(tmp_path, monkeypatch, db_isolated):
    """Regression (owner incident 2026-08-29): a freshly-crawled but MONTHS-old article must
    never be screened as material_news — it's backlog, not a development. Reproduces the real
    incident shape (RFC-2822 date format, first-time-seen-by-us) with a synthetic subject."""
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)

    row = _evidence_row(["topic:wli-onwai", "scan:intel"])
    row["published_at"] = "Mon, 25 May 2026 07:00:00 GMT"  # months old, freshly first_seen
    asyncio.run(ns.handle_arrivals([row]))

    assert len(calls) == 0  # never reached the LLM at all
    assert pa_store.pending() == []


def test_backlog_arrival_with_missing_date_fails_open(tmp_path, monkeypatch, db_isolated):
    """An item with no published_at at all must still screen normally — an unknown age is not
    proof of staleness, and must never silently blind the pipeline to a genuinely fresh item."""
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)

    row = _evidence_row(["topic:wli-onwai", "scan:intel"])
    del row["published_at"]
    asyncio.run(ns.handle_arrivals([row]))

    assert len(calls) == 1


def test_asset_subject_material_headline_fires(tmp_path, monkeypatch, db_isolated):
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_ASSET_ITEM)

    rows = [_evidence_row(["asset:iid-aapl", "scan:intel"], title="Apple issues profit warning")]
    asyncio.run(ns.handle_arrivals(rows))

    pending = pa_store.pending()
    assert len(pending) == 1
    assert pending[0]["subject"] == "asset:iid-aapl"
    assert pending[0]["signal_id"] == "news-iid-aapl"
    assert pending[0]["ticker"] == "AAPL"


def test_non_material_headline_stays_silent(tmp_path, monkeypatch, db_isolated):
    resp = json.dumps({"results": [{"index": 0, "material": False, "reason": "generic chatter"}]})
    ns, pa_store, calls = _stub(
        monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM, llm_response=resp
    )

    rows = [
        _evidence_row(["topic:wli-onwai", "scan:intel"], title="Markets mixed amid Fed chatter")
    ]
    asyncio.run(ns.handle_arrivals(rows))

    assert len(calls) == 1  # still screened (that's the point — LLM decides)
    assert pa_store.pending() == []  # but nothing enqueued


def test_material_verdict_records_a_breakdown_tick(tmp_path, monkeypatch, db_isolated):
    """Owner ask 2026-09-01: track the candidates-vs-material breakdown per tick, no reasons
    needed — this is the wiring test; the store's own math is covered in
    test_news_screen_observability.py."""
    import backend.services.news_screen_observability as nso

    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)
    recorded: list[tuple[int, int]] = []
    monkeypatch.setattr(
        nso, "record_tick", lambda candidates, material: recorded.append((candidates, material))
    )

    rows = [_evidence_row(["topic:wli-onwai", "scan:intel"])]
    asyncio.run(ns.handle_arrivals(rows))

    assert recorded == [(1, 1)]  # 1 candidate screened, 1 ruled material


def test_non_material_verdict_records_zero_material_in_the_tick(tmp_path, monkeypatch, db_isolated):
    resp = json.dumps({"results": [{"index": 0, "material": False, "reason": "generic chatter"}]})
    import backend.services.news_screen_observability as nso

    ns, pa_store, calls = _stub(
        monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM, llm_response=resp
    )
    recorded: list[tuple[int, int]] = []
    monkeypatch.setattr(
        nso, "record_tick", lambda candidates, material: recorded.append((candidates, material))
    )

    rows = [
        _evidence_row(["topic:wli-onwai", "scan:intel"], title="Markets mixed amid Fed chatter")
    ]
    asyncio.run(ns.handle_arrivals(rows))

    assert recorded == [(1, 0)]  # screened, none material — suppressed, not silently dropped


def test_unwatched_subject_never_reaches_the_llm(tmp_path, monkeypatch, db_isolated):
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)

    rows = [_evidence_row(["asset:some-other-iid"])]  # not a monitor_news subject
    asyncio.run(ns.handle_arrivals(rows))

    assert calls == []
    assert pa_store.pending() == []


def test_monitor_news_false_item_excluded(tmp_path, monkeypatch, db_isolated):
    muted = [{**_TOPIC_ITEM[0], "monitor_news": False}]
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=muted)

    rows = [_evidence_row(["topic:wli-onwai"])]
    asyncio.run(ns.handle_arrivals(rows))

    assert calls == []
    assert pa_store.pending() == []


def test_empty_arrivals_batch_noop(tmp_path, monkeypatch, db_isolated):
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)
    asyncio.run(ns.handle_arrivals([]))
    assert calls == []


# ── clustering ────────────────────────────────────────────────────────────────────


def test_syndicated_copies_cluster_into_one_screen_call():
    from backend.monitoring.news_screen import _cluster_rows

    rows = [
        _evidence_row(["topic:t1"], title="Uber nears deal to acquire ONWAI SE"),
        _evidence_row(["topic:t1"], title="Uber nears deal to acquire ONWAI SE - Reuters"),
        _evidence_row(["topic:t1"], title="Totally different unrelated story"),
    ]
    clusters = _cluster_rows(rows)
    assert len(clusters) == 2  # the two near-duplicate copies merge; the unrelated one is separate
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]


def test_cluster_rows_handles_blank_titles():
    from backend.monitoring.news_screen import _cluster_rows

    rows = [_evidence_row(["topic:t1"], title=""), _evidence_row(["topic:t1"], title="")]
    clusters = _cluster_rows(rows)
    assert len(clusters) == 2  # blank titles never "match" each other


# ── cooldown dedup (INT12: cluster-aware, not subject-blanket) ────────────────────


def test_cooldown_suppresses_re_screen_of_same_cluster(tmp_path, monkeypatch, db_isolated):
    """A re-syndicated copy of the SAME story (high title-token overlap) within the cooldown
    window is suppressed — the cluster was already screened."""
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)

    rows = [
        _evidence_row(["topic:wli-onwai"], title="Uber Announces Acquisition Offer for ONWAI SE")
    ]
    asyncio.run(ns.handle_arrivals(rows))
    assert len(calls) == 1
    assert len(pa_store.pending()) == 1

    # A near-duplicate copy of the SAME story (a syndicated re-run) must not re-screen.
    rows2 = [
        _evidence_row(
            ["topic:wli-onwai"],
            title="Uber Announces Acquisition Offer for ONWAI SE - Reuters",
        )
    ]
    asyncio.run(ns.handle_arrivals(rows2))
    assert len(calls) == 1  # no second LLM call
    assert len(pa_store.pending()) == 1  # no second enqueue


def test_distinct_cluster_screens_within_cooldown_window(tmp_path, monkeypatch, db_isolated):
    """INT12 live-incident fix: a genuinely NEW development on the SAME subject (a distinct
    story, not a re-syndication) must still screen within the 6h cooldown window — pins the
    2026-07-16 Prosus/Uber-ONWAI incident where the old blanket cooldown wrongly suppressed it.

    INT14b: the SECOND distinct cluster's enqueue now COALESCES into the still-pending row from
    the first (per-subject coalescing) instead of spawning a second row — the LLM call count
    (the screening decision itself) is unaffected; only the row count changes."""
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)

    rows = [
        _evidence_row(["topic:wli-onwai"], title="Uber Announces Acquisition Offer for ONWAI SE")
    ]
    asyncio.run(ns.handle_arrivals(rows))
    assert len(calls) == 1
    assert len(pa_store.pending()) == 1

    # A DISTINCT development on the same subject (low token overlap) — must still screen.
    rows2 = [
        _evidence_row(
            ["topic:wli-onwai"],
            title="Prosus gives irrevocable undertaking to sell its stake in ONWAI SE",
        )
    ]
    asyncio.run(ns.handle_arrivals(rows2))
    assert len(calls) == 2  # a second LLM call for the distinct cluster
    assert len(pa_store.pending()) == 1  # coalesced into the SAME still-pending row (INT14b)
    assert pa_store.pending()[0]["payload"]["headline"] == (
        "Prosus gives irrevocable undertaking to sell its stake in ONWAI SE"
    )  # the marker is the MOST RECENT material_news trigger's headline


def test_subject_floor_suppresses_fourth_distinct_cluster(tmp_path, monkeypatch, db_isolated):
    """Even distinct clusters are bounded: once _SUBJECT_SCREEN_FLOOR distinct clusters have
    screened for a subject within the window, a further (4th) distinct cluster falls back to a
    blanket cooldown — bounds alert volume against adversarial cluster-splitting spam.

    INT14b: all 3 screened clusters COALESCE into ONE still-pending row (per-subject coalescing),
    so `recent_material_events` — not a raw pending-row count — is what must keep the floor
    correct: it flattens `payload.material_events` across the row instead of counting rows, so 3
    distinct screen EVENTS on one row still correctly trip the floor before a 4th."""
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)
    assert ns._SUBJECT_SCREEN_FLOOR == 3

    headlines = [
        "Uber Announces Acquisition Offer for ONWAI SE",
        "Prosus gives irrevocable undertaking to sell its stake in ONWAI SE",
        "ONWAI SE board recommends shareholders accept the Uber bid",
        "Regulators clear the Uber-ONWAI SE merger conditionally",
    ]
    for headline in headlines:
        asyncio.run(ns.handle_arrivals([_evidence_row(["topic:wli-onwai"], title=headline)]))

    # Only the first 3 distinct clusters screened — the 4th hit the subject-level floor pre-LLM.
    assert len(calls) == 3
    assert len(pa_store.pending()) == 1  # coalesced into ONE row (INT14b)
    assert len(pa_store.pending()[0]["payload"]["material_events"]) == 3  # 3 distinct events
    # Daily budget only charged for the clusters actually screened.
    assert ns._remaining_daily_budget() == ns._DAILY_SCREEN_CAP - 3


def test_stopword_and_subject_name_exclusion_prevents_false_suppression(
    tmp_path, monkeypatch, db_isolated
):
    """SF-2 pinned scenario: "Apple Q3 miss" vs "Apple Q3 beat" share the subject's own name +
    generic filler but report OPPOSITE developments. Raw token overlap ({apple, q3} / 3 = 0.67)
    would wrongly clear the 0.6 threshold and suppress the second as a re-syndication; excluding
    the subject's name + a small stopword set drops the shared tokens to just {q3} (0.5), so both
    screen."""
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_ASSET_ITEM)

    rows = [_evidence_row(["asset:iid-aapl"], title="Apple Q3 miss")]
    asyncio.run(ns.handle_arrivals(rows))
    assert len(calls) == 1
    assert len(pa_store.pending()) == 1

    rows2 = [_evidence_row(["asset:iid-aapl"], title="Apple Q3 beat")]
    asyncio.run(ns.handle_arrivals(rows2))
    assert len(calls) == 2  # NOT suppressed as a false re-syndication match
    # INT14b: coalesces into the SAME still-pending row rather than a second one.
    assert len(pa_store.pending()) == 1
    assert len(pa_store.pending()[0]["payload"]["material_events"]) == 2


def test_legacy_row_without_cluster_tokens_does_not_crash_or_suppress(
    tmp_path, monkeypatch, db_isolated
):
    """A pre-INT12 material_news row with no `cluster_tokens` in its payload (legacy data, before
    this fingerprint existed) must not crash the cooldown gate and must not be treated as a
    fingerprint match — an empty/missing fingerprint simply can't suppress anything, the safe
    default (mirrors PendingAnalysisStore's own `recent_material_events` CRUD tests).

    INT14b: the legacy row is also still PENDING (never claimed/done), so the newly-screened
    headline COALESCES into it rather than adding a second row."""
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_ASSET_ITEM)

    # A legacy-shaped row: trigger=material_news, no cluster_tokens key at all.
    pa_store.add(
        subject="asset:iid-aapl",
        ticker="AAPL",
        signal_id="news-iid-aapl",
        trigger="material_news",
        payload={"label": "Apple", "headline": "Apple names new CFO", "reason": "exec change"},
    )

    rows = [_evidence_row(["asset:iid-aapl"], title="Apple issues a profit warning")]
    asyncio.run(ns.handle_arrivals(rows))  # must not raise

    assert len(calls) == 1  # the genuinely new headline still screens, unaffected by the legacy row
    assert len(pa_store.pending()) == 1  # coalesced into the legacy row (INT14b)
    assert pa_store.pending()[0]["payload"]["headline"] == "Apple issues a profit warning"


# ── per-tick cluster cap + daily budget ────────────────────────────────────────────


def test_per_tick_cluster_cap(tmp_path, monkeypatch, db_isolated):
    from backend.monitoring.news_screen import _MAX_CLUSTERS_PER_TICK

    items = [
        {
            "id": f"wli-t{i}",
            "type": "topic",
            "value": f"Topic {i}",
            "label": f"Topic {i}",
            "monitor_news": True,
        }
        for i in range(_MAX_CLUSTERS_PER_TICK + 3)
    ]
    resp = json.dumps(
        {
            "results": [
                {"index": i, "material": True, "reason": "r"} for i in range(_MAX_CLUSTERS_PER_TICK)
            ]
        }
    )
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=items, llm_response=resp)

    rows = [
        _evidence_row([f"topic:wli-t{i}"], title=f"Distinct headline about topic {i}")
        for i in range(_MAX_CLUSTERS_PER_TICK + 3)
    ]
    asyncio.run(ns.handle_arrivals(rows))

    assert len(calls) == 1
    # Only _MAX_CLUSTERS_PER_TICK subjects were screened this tick.
    assert len(pa_store.pending()) == _MAX_CLUSTERS_PER_TICK


def test_daily_budget_exhausted_skips_screen_entirely(tmp_path, monkeypatch, db_isolated):
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)
    monkeypatch.setattr(ns, "_remaining_daily_budget", lambda: 0)

    rows = [_evidence_row(["topic:wli-onwai"])]
    asyncio.run(ns.handle_arrivals(rows))

    assert calls == []
    assert pa_store.pending() == []


def test_daily_budget_increments_by_screened_cluster_count(tmp_path, monkeypatch, db_isolated):
    ns, pa_store, calls = _stub(monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM)

    rows = [_evidence_row(["topic:wli-onwai"])]
    asyncio.run(ns.handle_arrivals(rows))

    assert ns._remaining_daily_budget() == ns._DAILY_SCREEN_CAP - 1


def test_daily_budget_not_charged_when_llm_unavailable(tmp_path, monkeypatch, db_isolated):
    ns, pa_store, calls = _stub(
        monkeypatch,
        tmp_path,
        db_isolated,
        items=_TOPIC_ITEM,
        llm_response=RuntimeError("no provider"),
    )
    rows = [_evidence_row(["topic:wli-onwai"])]
    asyncio.run(ns.handle_arrivals(rows))
    assert ns._remaining_daily_budget() == ns._DAILY_SCREEN_CAP  # nothing charged on failure


# ── LLM-seam-unavailable no-op ──────────────────────────────────────────────────────


def test_llm_unavailable_noops_cleanly_never_raises(tmp_path, monkeypatch, db_isolated):
    import backend.core.llm_seam as seam_mod

    ns, pa_store, _ = _stub(
        monkeypatch,
        tmp_path,
        db_isolated,
        items=_TOPIC_ITEM,
        llm_response=seam_mod.LLMUnavailable("x"),
    )
    rows = [_evidence_row(["topic:wli-onwai"])]
    asyncio.run(ns.handle_arrivals(rows))  # must not raise
    assert pa_store.pending() == []


def test_garbage_llm_response_skipped_as_nothing_material(tmp_path, monkeypatch, db_isolated):
    """An unparseable reply (no valid JSON even after the structured layer's reask) → complete_model
    returns None → the flow SKIPS (nothing enqueued), rather than shipping a raw/garbled body."""
    ns, pa_store, calls = _stub(
        monkeypatch, tmp_path, db_isolated, items=_TOPIC_ITEM, llm_response="not json"
    )
    rows = [_evidence_row(["topic:wli-onwai"])]
    asyncio.run(ns.handle_arrivals(rows))
    assert len(calls) >= 1  # screened (then reasked); still nothing usable
    assert pa_store.pending() == []


def test_unexpected_exception_in_handle_arrivals_never_raises(tmp_path, monkeypatch, db_isolated):
    """Belt-and-suspenders: handle_arrivals itself must never propagate, even if something
    inside blows up unexpectedly (core.news_arrivals.emit also isolates it, but this module
    guards itself too)."""
    import backend.monitoring.news_screen as ns

    def _boom():
        raise RuntimeError("watchlist read failed")

    monkeypatch.setattr(ns, "read_watchlist", _boom)
    asyncio.run(ns.handle_arrivals([_evidence_row(["topic:t1"])]))  # must not raise


# ── consumer registration ─────────────────────────────────────────────────────────


def test_register_wires_onto_news_arrivals_seam():
    import backend.core.news_arrivals as na
    import backend.monitoring.news_screen as ns

    na._CONSUMERS.clear()
    ns.register()
    assert na.registered_consumers().get("news_screen") is ns.handle_arrivals
    na._CONSUMERS.clear()
