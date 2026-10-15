"""Regression for I-INT2: repeated intel-scan LLM synthesis failures went silent for 6 days
in production — `_submit_and_poll` swallowed the `submit_batch`/`collect_batch` exception,
`run_intel_scan` returned 0 (indistinguishable from a genuinely quiet news day), and the
scheduler logged it as a normal run. Nothing ever surfaced to the user.

This pins the fix: `run_intel_scan` now routes its outcome through the SAME run-ledger
machinery agents/scheduled tasks already use (`services/run_ledger.record_run` — streak-dedup
failure alerts + recovered notes via `notify()`), so:
  - a submit/collect failure records an "error" run and fires exactly one alert (first-of-streak)
  - repeated consecutive failures do NOT spam (streak-dedup)
  - a subsequent successful scan fires a "recovered" note

Heavy deps (search, RSS, risk signals, model config) are stubbed; only `submit_batch`/
`collect_batch` (the real failure seam) and the run-ledger/notify wiring are exercised for real.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.core.llm_seam import BatchResult
from backend.db.evidence_store import EvidenceStore
from backend.db.run_history_store import RunHistoryStore
from backend.monitoring import collect
from backend.monitoring import intel_agent as ia
from backend.services import evidence as ev
from backend.services import run_ledger as rl


class _NoNews:
    async def search(self, q, max_results=5):
        return []


@pytest.fixture(autouse=True)
def _isolated_run_ledger(tmp_path, monkeypatch):
    """Redirect run_ledger's shared store to a tmp_path instance, mirroring
    tests/test_run_ledger.py's isolated_store fixture."""
    store = RunHistoryStore()
    store._path = tmp_path / "run_history.json"
    monkeypatch.setattr(rl, "_store", store)
    rl._group_suppress.clear()
    return store


def _stub_scan(monkeypatch):
    """Stub every ancillary dependency of run_intel_scan so only the submit/collect seam
    and the run-ledger wiring are under test. Callers must also depend on db_isolated —
    the evidence + IntelStoriesStore DB isolation this helper used to do inline (both are
    DB-backed now; db_isolated's engine redirect covers them for free)."""
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
    monkeypatch.setattr(collect, "_fetch_feed", lambda url, max_age=None: [])
    monkeypatch.setattr("backend.services.prices.fetch_movers", lambda *a, **kw: {}, raising=False)

    # Cached evidence (still within TTL) so the scan reaches the LLM step every time instead of
    # short-circuiting on "no evidence" (mirrors tests/test_intel_evidence_persists.py).
    ev.record_evidence(
        [
            {
                "url": "https://news/yesterday",
                "title": "Chip demand accelerating",
                "content": "still relevant context",
                "published_date": "yesterday",
                "_query": "semiconductors outlook",
            }
        ],
        subjects=["scan:intel"],
        kind="news",
        store=EvidenceStore(),
    )


def _patch_notify(monkeypatch):
    notified: list[str] = []

    async def _mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    monkeypatch.setattr(rl.notify_svc, "notify", _mock_notify)
    return notified


def _fail_submit_batch(monkeypatch):
    async def _boom(task, requests):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ia, "submit_batch", _boom)


def _succeed_submit_and_collect(monkeypatch):
    """Resolve in-run with one valid story."""
    captured: dict = {}

    async def _fake_submit(task, requests):
        captured["custom_id"] = requests[0].custom_id
        return "provider-handle-ok"

    async def _fake_collect(handle):
        payload = json.dumps(
            {
                "stories": [
                    {
                        "type": "topic",
                        "label": "Semiconductors",
                        "status": "monitoring",
                        "summary": "all quiet",
                        "new_timeline_event": {
                            "date": "2026-07-15",
                            "title": "Routine check",
                            "summary": "no new developments",
                            "sources": [
                                {
                                    "title": "Chip demand accelerating",
                                    "url": "https://news/yesterday",
                                }
                            ],
                        },
                    }
                ]
            }
        )
        return [BatchResult(custom_id=captured["custom_id"], text=payload)]

    monkeypatch.setattr(ia, "submit_batch", _fake_submit)
    monkeypatch.setattr(ia, "collect_batch", _fake_collect)


# ── Failure surfaces exactly one alert ────────────────────────────────────────


def test_intel_scan_submit_failure_records_error_run_and_alerts(tmp_path, monkeypatch, db_isolated):
    _stub_scan(monkeypatch)
    _fail_submit_batch(monkeypatch)
    notified = _patch_notify(monkeypatch)

    result = asyncio.run(ia.run_intel_scan())

    assert result == 0
    runs = rl.list_runs("system_job", "intel_scan")
    assert len(runs) == 1
    assert runs[0]["status"] == "error"
    assert "connection refused" in runs[0]["error"]  # full error stored locally, unredacted

    assert len(notified) == 1
    msg = notified[0]
    assert "Intel scan" in msg
    assert "AI provider" in msg or "Settings" in msg


def test_manual_scan_reports_synthesis_failure(tmp_path, monkeypatch, db_isolated):
    _stub_scan(monkeypatch)
    _fail_submit_batch(monkeypatch)
    _patch_notify(monkeypatch)

    with pytest.raises(ia.IntelScanIncomplete):
        asyncio.run(ia.run_intel_scan(raise_on_incomplete=True))


def test_intel_scan_three_consecutive_failures_alert_once(tmp_path, monkeypatch, db_isolated):
    """Streak-dedup regression: 3 consecutive scan failures fire exactly ONE alert."""
    _stub_scan(monkeypatch)
    _fail_submit_batch(monkeypatch)
    notified = _patch_notify(monkeypatch)

    for _ in range(3):
        asyncio.run(ia.run_intel_scan())

    runs = rl.list_runs("system_job", "intel_scan")
    assert len(runs) == 3
    assert all(r["status"] == "error" for r in runs)
    assert len(notified) == 1  # first-of-streak only


# ── Recovery after a failure streak ───────────────────────────────────────────


def test_intel_scan_recovers_after_failure_streak(tmp_path, monkeypatch, db_isolated):
    _stub_scan(monkeypatch)
    notified = _patch_notify(monkeypatch)

    _fail_submit_batch(monkeypatch)
    asyncio.run(ia.run_intel_scan())

    _succeed_submit_and_collect(monkeypatch)
    result = asyncio.run(ia.run_intel_scan())

    assert result == 1
    runs = rl.list_runs("system_job", "intel_scan")
    assert runs[0]["status"] == "ok"  # newest-first
    assert runs[1]["status"] == "error"

    assert len(notified) == 2  # failure alert + recovered note
    assert any("normally again" in m or "recovered" in m.lower() for m in notified)


# ── A run-ledger write failure must never surface as a scan failure ──────────


def test_intel_scan_survives_record_run_failure_on_ok_path(tmp_path, monkeypatch, db_isolated):
    """REGRESSION (verify-gate finding): record_run's own durable write (RunHistoryStore.append)
    is NOT internally guarded — it can raise (disk full, permissions). A failure there must not
    escape run_intel_scan and misreport an already-persisted successful scan as broken (which
    would 500 POST /api/intel/scan and error the scan_intel tool)."""
    _stub_scan(monkeypatch)
    _succeed_submit_and_collect(monkeypatch)

    async def _boom(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rl, "record_run", _boom)

    result = asyncio.run(ia.run_intel_scan())

    assert result == 1  # the story was still persisted and counted normally


def test_intel_scan_survives_record_run_failure_on_error_path(tmp_path, monkeypatch, db_isolated):
    """Same guard on the error-recording branch: a record_run failure while recording the
    scan's OWN submit-batch failure must not raise a second, different exception out of
    run_intel_scan — it should still cleanly return 0."""
    _stub_scan(monkeypatch)
    _fail_submit_batch(monkeypatch)

    async def _boom(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rl, "record_run", _boom)

    result = asyncio.run(ia.run_intel_scan())

    assert result == 0
