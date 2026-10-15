"""Push-delivery retry queue — regression tests for the 2026-07-16 incident.

Two material analyst pushes were silently dropped: notify() returned {"sent": 0} for
every channel (Telegram was down/mid-reconnect) and the whole chain treated that as
success — nothing logged, nothing retried, the row marked done. These tests pin the
fix (backend.monitoring.push_retry + backend.db.pending_push_store):

  (a) a zero-delivery push is queued, persisted, and a later drain that succeeds is
      logged "recovered" with no further retries
  (b) 3 exhausted retries log exactly ONE WARNING and drain the queue
  (c) a first-try success at the call site leaves no retry residue
  (d) a persisted retry entry survives a fresh PendingPushStore instantiation
      (simulates a process restart — the whole point of a durable queue over an
      in-memory asyncio timer)

Plus: the two producer call sites (analyst._push_analysis, price_monitor's
_push_external_alert) actually wire into this queue on zero delivery; a malformed
row (code-review SF-1) can't abort the rest of a drain tick.

PendingPushStore is DB-backed (plan 4e task 1) — db_isolated redirects it (and every
other unpinned store) to a fresh, empty database; no per-store isolation needed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_malformed_row(payload: dict) -> None:
    """Insert a row whose PAYLOAD has no "id" key — the DB-era equivalent of the old
    JsonStore-era 'freely construct a dict missing "id"' malformed row. ``pending_pushes.id``
    is now a real NOT NULL primary-key column (``payload_store.payload_table``), so a row
    can no longer omit an id at the STORAGE layer the way a schemaless JSON list once
    tolerated — only the byte-for-byte PAYLOAD that ``PendingPushStore.all()``/``due()``
    return can still lack it, which is exactly what SF-1's malformed-row handling is being
    tested against. Bypasses ``PendingPushStore.insert`` (which derives the id COLUMN from
    ``payload["id"]`` and would hit the identical NOT NULL violation) and writes the row
    straight through the table instead."""
    from sqlalchemy import func, insert, select

    from backend.core.ids import new_id
    from backend.db.engine import get_engine
    from backend.db.tables import pending_pushes

    with get_engine().begin() as conn:
        seq = (
            conn.execute(select(func.coalesce(func.max(pending_pushes.c.seq), -1))).scalar_one() + 1
        )
        conn.execute(insert(pending_pushes).values(seq=seq, id=new_id("ppush"), payload=payload))


def test_zero_delivery_enqueues_then_recovers(db_isolated, monkeypatch, caplog):
    """A zero-delivery push is queued; a later drain that succeeds logs 'recovered'
    and removes the row — no further retries scheduled."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.push_retry as pr_mod
    import backend.services.notify as notify_mod

    delivered = {"telegram": {"sent": 0}}
    assert not pr_mod.delivered_ok(delivered)

    pr_mod.enqueue_push_retry("Analysis — ONWAI", ["telegram"], "ONWAI Inc", "ONWAI")

    store = pps_mod.PendingPushStore()
    pending = store.all()
    assert len(pending) == 1
    row = pending[0]
    assert row["message"] == "Analysis — ONWAI"
    assert row["channels"] == ["telegram"]
    assert row["attempts"] == 0
    # Message is persisted (survives restart) — not held in memory only.
    assert pps_mod.PendingPushStore().find_by_id(row["id"])["message"] == "Analysis — ONWAI"

    # Force the row due now (bypass the real 60s delay for the test).
    store.update_by_id(row["id"], {"next_attempt_at": _iso_now()})

    async def _notify_ok(message, channels=None, title=None):
        return {"telegram": {"sent": 1}}

    monkeypatch.setattr(notify_mod, "notify", _notify_ok)

    caplog.set_level(logging.INFO)
    asyncio.run(pr_mod.drain_pending_pushes("test"))

    assert pps_mod.PendingPushStore().all() == []
    recovered = [r for r in caplog.records if "recovered" in r.message]
    assert len(recovered) == 1
    assert "ONWAI" in recovered[0].message

    # No further retries: draining again is a no-op (queue already empty).
    caplog.clear()
    asyncio.run(pr_mod.drain_pending_pushes("test"))
    assert pps_mod.PendingPushStore().all() == []
    assert not any("recovered" in r.message or "exhausted" in r.message for r in caplog.records)


def test_exhausted_after_three_retries_single_warning(db_isolated, monkeypatch, caplog):
    """3 consecutive drain failures exhaust the retry budget: exactly ONE WARNING,
    queue drained (no fourth attempt)."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.push_retry as pr_mod
    import backend.services.notify as notify_mod

    async def _notify_fail(message, channels=None, title=None):
        return {"telegram": {"sent": 0}}

    monkeypatch.setattr(notify_mod, "notify", _notify_fail)

    pr_mod.enqueue_push_retry("Analysis — AAPL", ["telegram"], "Apple", "AAPL")
    store = pps_mod.PendingPushStore()
    row_id = store.all()[0]["id"]

    caplog.set_level(logging.INFO)
    for _ in range(len(pr_mod.RETRY_DELAYS_SEC)):
        store.update_by_id(row_id, {"next_attempt_at": _iso_now()})
        asyncio.run(pr_mod.drain_pending_pushes("test"))

    assert pps_mod.PendingPushStore().all() == []
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "exhausted" in r.message]
    assert len(warnings) == 1
    assert "AAPL" in warnings[0].message

    # A fourth drain tick is a no-op — the row is gone, nothing left to retry.
    caplog.clear()
    asyncio.run(pr_mod.drain_pending_pushes("test"))
    assert not any("exhausted" in r.message for r in caplog.records)


def test_malformed_row_does_not_abort_the_rest_of_the_tick(db_isolated, monkeypatch, caplog):
    """SF-1: a row missing 'id' (a malformed/corrupt entry) raises inside its own
    per-row handling but must NOT abort the rest of the tick — the other due row is
    still processed and recovers normally."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.push_retry as pr_mod
    import backend.services.notify as notify_mod

    store = pps_mod.PendingPushStore()
    # Malformed row: payload has no "id" key — see _insert_malformed_row for why this
    # bypasses PendingPushStore.insert (the real id COLUMN is NOT NULL).
    _insert_malformed_row(
        {
            "message": "malformed",
            "channels": ["telegram"],
            "attempts": 0,
            "next_attempt_at": _iso_now(),
        }
    )
    pr_mod.enqueue_push_retry("Analysis — AAPL", ["telegram"], "Apple", "AAPL")
    good_row_id = next(r["id"] for r in store.all() if "id" in r)
    store.update_by_id(good_row_id, {"next_attempt_at": _iso_now()})

    async def _notify_ok(message, channels=None, title=None):
        return {"telegram": {"sent": 1}}

    monkeypatch.setattr(notify_mod, "notify", _notify_ok)

    caplog.set_level(logging.INFO)
    asyncio.run(pr_mod.drain_pending_pushes("test"))  # must not raise

    # The valid row recovered; the malformed row's own failure was logged, not raised.
    recovered = [r for r in caplog.records if "recovered" in r.message]
    assert len(recovered) == 1
    assert "AAPL" in recovered[0].message

    failed = [r for r in caplog.records if "row processing failed" in r.message]
    assert len(failed) == 1

    # The malformed row is still sitting in the store (never removed/rescheduled) —
    # only the well-formed one was cleared.
    remaining = pps_mod.PendingPushStore().all()
    assert len(remaining) == 1
    assert "id" not in remaining[0]


def test_drain_caps_rows_per_tick(db_isolated, monkeypatch):
    """SF-1: a burst of due rows is capped at _MAX_DRAIN_PER_TICK per drain call —
    one tick must not be unbounded (mirrors run_analysis_sweep's own _PER_SWEEP_CAP)."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.push_retry as pr_mod
    import backend.services.notify as notify_mod

    for i in range(pr_mod._MAX_DRAIN_PER_TICK + 3):
        pr_mod.enqueue_push_retry(f"Analysis — T{i}", ["telegram"], f"Ticker{i}", f"T{i}")
    store = pps_mod.PendingPushStore()
    now_iso = _iso_now()
    for row in store.all():
        store.update_by_id(row["id"], {"next_attempt_at": now_iso})

    calls: list = []

    async def _notify_ok(message, channels=None, title=None):
        calls.append(message)
        return {"telegram": {"sent": 1}}

    monkeypatch.setattr(notify_mod, "notify", _notify_ok)

    asyncio.run(pr_mod.drain_pending_pushes("test"))

    assert len(calls) == pr_mod._MAX_DRAIN_PER_TICK
    remaining = pps_mod.PendingPushStore().all()
    assert len(remaining) == 3  # the overflow, left for the next tick


def test_persisted_retry_survives_restart(db_isolated, monkeypatch):
    """A queued retry, read back via a BRAND NEW PendingPushStore instance (simulating
    a fresh process after a restart/hot-reload), is still there and still drainable —
    the incident's whole point: an in-memory timer would have been cancelled."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.push_retry as pr_mod
    import backend.services.notify as notify_mod

    pr_mod.enqueue_push_retry("Analysis — ONWAI", ["telegram"], "ONWAI Inc", "ONWAI")

    # "Restart": nothing but a fresh store instance touches the row in the DB.
    fresh_store = pps_mod.PendingPushStore()
    rows = fresh_store.all()
    assert len(rows) == 1
    fresh_store.update_by_id(rows[0]["id"], {"next_attempt_at": _iso_now()})

    async def _notify_ok(message, channels=None, title=None):
        return {"telegram": {"sent": 1}}

    monkeypatch.setattr(notify_mod, "notify", _notify_ok)

    asyncio.run(pr_mod.drain_pending_pushes("analyst"))

    assert pps_mod.PendingPushStore().all() == []


def test_analyst_push_zero_delivery_logs_and_enqueues(db_isolated, monkeypatch, caplog):
    """analyst._push_analysis: a zero-delivery notify() result is logged at INFO
    (the incident's original blind spot) and queued for retry — never silently
    dropped."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.analyst as analyst_mod
    import backend.services.notify as notify_mod

    monkeypatch.setattr(
        analyst_mod,
        "read_watchlist",
        lambda: {"config": {"notify_routes": {"news": ["telegram"]}}},
    )

    async def _notify_zero(message, channels=None, title=None):
        return {"telegram": {"sent": 0}}

    monkeypatch.setattr(notify_mod, "notify", _notify_zero)

    caplog.set_level(logging.INFO)
    asyncio.run(
        analyst_mod._push_analysis(
            "ONWAI Inc", "ONWAI", {"why": "M&A headline", "impact": "", "actions": []}
        )
    )

    pushed_logs = [r for r in caplog.records if "pushed analysis" in r.message]
    assert len(pushed_logs) == 1  # delivered dict is always logged, even on failure

    pending = pps_mod.PendingPushStore().all()
    assert len(pending) == 1
    assert pending[0]["ticker"] == "ONWAI"


def test_analyst_push_full_delivery_no_retry_residue(db_isolated, monkeypatch):
    """analyst._push_analysis: a first-try full-delivery push leaves nothing queued."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.analyst as analyst_mod
    import backend.services.notify as notify_mod

    monkeypatch.setattr(
        analyst_mod,
        "read_watchlist",
        lambda: {"config": {"notify_routes": {"news": ["telegram"]}}},
    )

    async def _notify_ok(message, channels=None, title=None):
        return {"telegram": {"sent": 1}}

    monkeypatch.setattr(notify_mod, "notify", _notify_ok)

    asyncio.run(
        analyst_mod._push_analysis(
            "Apple", "AAPL", {"why": "Earnings", "impact": "", "actions": []}
        )
    )

    assert pps_mod.PendingPushStore().all() == []


def test_run_analysis_sweep_drains_pending_pushes_every_tick(db_isolated, monkeypatch):
    """run_analysis_sweep calls drain_pending_pushes on EVERY tick, independent of
    whether there are pending crash rows to analyze — the sweep is the shared drain
    point for both analyst and price_monitor's zero-delivery pushes.

    db_isolated (not the old PENDING_ANALYSIS_JSON monkeypatch) is what gives
    PendingAnalysisStore an empty, isolated table so the sweep's own work is a no-op
    and this test only proves the drain call happens regardless."""
    import backend.monitoring.analyst as analyst_mod

    calls: list[str] = []

    async def _fake_drain(source):
        calls.append(source)

    monkeypatch.setattr(analyst_mod.push_retry, "drain_pending_pushes", _fake_drain)

    count = asyncio.run(analyst_mod.run_analysis_sweep())

    assert count == 0
    assert calls == ["analyst"]


def test_price_monitor_push_zero_delivery_enqueues_retry(db_isolated, monkeypatch):
    """price_monitor._push_external_alert gets the SAME zero-delivery retry treatment
    as analyst._push_analysis (INT6 symmetry requirement) — a small, shared change."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.price_monitor as pm_mod
    import backend.services.notify as notify_mod

    async def _notify_zero(message, channels=None, title=None):
        return {"telegram": {"sent": 0}}

    monkeypatch.setattr(notify_mod, "notify", _notify_zero)

    signal = {
        "label": "Apple — price alert",
        "summary": "Apple dropped 15%.",
        "portfolio_impact": "",
        "action_items": [],
    }
    asyncio.run(pm_mod._push_external_alert(signal, ["telegram"]))

    pending = pps_mod.PendingPushStore().all()
    assert len(pending) == 1
    assert pending[0]["channels"] == ["telegram"]


def test_price_monitor_push_full_delivery_no_retry_residue(db_isolated, monkeypatch):
    """price_monitor._push_external_alert: first-try success leaves no retry residue."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.price_monitor as pm_mod
    import backend.services.notify as notify_mod

    async def _notify_ok(message, channels=None, title=None):
        return {"telegram": {"sent": 1}}

    monkeypatch.setattr(notify_mod, "notify", _notify_ok)

    signal = {"label": "Apple — price alert", "summary": "Apple dropped 15%."}
    asyncio.run(pm_mod._push_external_alert(signal, ["telegram"]))

    assert pps_mod.PendingPushStore().all() == []


def _capture_notify(monkeypatch, result: dict) -> list[dict]:
    """Stub notify(): external channels answer ``result``; every call is recorded."""
    import backend.services.notify as notify_mod

    calls: list[dict] = []

    async def _notify(message, channels=None, title=None):
        calls.append({"message": message, "channels": channels, "title": title})
        if channels == ["in_app"]:
            return {"in_app": {"sent": 1}}
        return {c: result for c in channels or []}

    monkeypatch.setattr(notify_mod, "notify", _notify)
    return calls


def test_every_channel_off_reports_in_app_now_and_never_queues(db_isolated, monkeypatch):
    """A channel turned off or removed is unregistered, so notify() skips it. Retrying can't
    reach it — the user is told in-app at once, and nothing is queued."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.price_monitor as pm_mod

    calls = _capture_notify(monkeypatch, {"skipped": "unknown_channel"})
    signal = {"label": "Take-profit zone", "summary": "VOO rose above 790."}
    asyncio.run(pm_mod._push_external_alert(signal, ["email", "webhook"]))

    assert pps_mod.PendingPushStore().all() == []
    notices = [c for c in calls if c["channels"] == ["in_app"]]
    assert len(notices) == 1
    assert notices[0]["title"] == "Alert not delivered"
    assert "Take-profit zone" in notices[0]["message"]
    assert "off or removed" in notices[0]["message"]


def test_one_channel_off_but_another_delivered_is_not_reported(db_isolated, monkeypatch):
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.push_retry as pr_mod

    calls = _capture_notify(monkeypatch, {})
    delivered = {"telegram": {"sent": 1}, "email": {"skipped": "unknown_channel"}}
    asyncio.run(pr_mod.handle_push_result(delivered, "msg", ["telegram", "email"], "Alert"))

    assert calls == []
    assert pps_mod.PendingPushStore().all() == []


def test_exhausted_retry_reports_in_app_once(db_isolated, monkeypatch):
    """A channel that stays down (e.g. Telegram's bot stopped: sent 0) is retried, and when
    the retries run out the user gets ONE in-app notice instead of a silent drop."""
    import backend.db.pending_push_store as pps_mod
    import backend.monitoring.push_retry as pr_mod

    calls = _capture_notify(monkeypatch, {"sent": 0})
    pr_mod.enqueue_push_retry("VOO alert", ["telegram"], "Take-profit zone", "VOO")
    store = pps_mod.PendingPushStore()
    row_id = store.all()[0]["id"]
    for _ in range(len(pr_mod.RETRY_DELAYS_SEC)):
        store.update_by_id(row_id, {"next_attempt_at": _iso_now()})
        asyncio.run(pr_mod.drain_pending_pushes("test"))

    notices = [c for c in calls if c["channels"] == ["in_app"]]
    assert len(notices) == 1
    assert "Take-profit zone" in notices[0]["message"]
    assert "after 3 tries" in notices[0]["message"]
