"""PriceMovementStore — CRUD + the per-instrument newest-first prefix scan."""

from __future__ import annotations

from datetime import date, timedelta

from backend.db.price_movement_store import (
    _MAX_SESSIONS_PER_INSTRUMENT,
    PriceMovementStore,
    note_id,
)


def _sessions(n: int, *, start: date = date(2020, 1, 1)):
    """`n` distinct, sequential session-date strings — cheap stand-ins for real trading
    days (weekends/holidays don't matter here, only that each is a distinct, orderable id)."""
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_upsert_get_and_prefix_scan_newest_first(db_isolated):
    store = PriceMovementStore()
    for day in ("2026-09-16", "2026-09-18", "2026-09-17"):
        store.upsert_note({"instrument_id": "i1", "session_date": day, "close": 1.0})
    store.upsert_note(
        {"instrument_id": "i10", "session_date": "2026-09-18", "close": 9.0}
    )  # prefix trap

    assert store.get_note("i1", "2026-09-17")["close"] == 1.0
    assert store.get_note("i1", "2026-09-19") is None
    days = [n["session_date"] for n in store.for_instrument("i1")]
    assert days == ["2026-09-18", "2026-09-17", "2026-09-16"]
    assert [n["session_date"] for n in store.for_instrument("i1", limit=1)] == ["2026-09-18"]
    assert all(n["instrument_id"] == "i1" for n in store.for_instrument("i1"))


def test_upsert_replaces_the_same_session_in_place(db_isolated):
    store = PriceMovementStore()
    store.upsert_note({"instrument_id": "i1", "session_date": "2026-09-18", "close": 1.0})
    store.upsert_note(
        {"instrument_id": "i1", "session_date": "2026-09-18", "close": 2.0, "summary": "s"}
    )
    rows = store.for_instrument("i1")
    assert (
        len(rows) == 1 and rows[0]["close"] == 2.0 and rows[0]["id"] == note_id("i1", "2026-09-18")
    )


# ── Retention: never more than _MAX_SESSIONS_PER_INSTRUMENT rows per instrument ─────────


def test_upsert_note_trims_sessions_beyond_the_cap(db_isolated):
    """`for_instrument`'s one reader never asks past 200 — a 201st+ session is pure growth
    with no reader, swept the moment it arrives."""
    store = PriceMovementStore()
    days = _sessions(_MAX_SESSIONS_PER_INSTRUMENT + 3)
    for day in days:
        store.upsert_note({"instrument_id": "i1", "session_date": day, "close": 1.0})

    rows = store.for_instrument("i1", limit=10_000)

    assert len(rows) == _MAX_SESSIONS_PER_INSTRUMENT
    kept_days = {n["session_date"] for n in rows}
    newest = set(days[-_MAX_SESSIONS_PER_INSTRUMENT:])
    assert kept_days == newest
    # The oldest 3 are genuinely gone, not just excluded from this read.
    for stale_day in days[:3]:
        assert store.get_note("i1", stale_day) is None


def test_upsert_note_stays_under_the_cap_is_a_noop(db_isolated):
    store = PriceMovementStore()
    days = _sessions(5)
    for day in days:
        store.upsert_note({"instrument_id": "i1", "session_date": day, "close": 1.0})

    assert len(store.for_instrument("i1")) == 5


def test_upsert_note_trim_does_not_cross_the_prefix_trap(db_isolated):
    """`i1` and `i10` share a literal string prefix — the cap's own id-prefix scan must
    respect the same colon boundary `for_instrument` already relies on, or trimming `i1`
    would eat into `i10`'s notes."""
    store = PriceMovementStore()
    for day in _sessions(_MAX_SESSIONS_PER_INSTRUMENT + 5):
        store.upsert_note({"instrument_id": "i1", "session_date": day, "close": 1.0})
    for day in _sessions(5, start=date(2021, 1, 1)):
        store.upsert_note({"instrument_id": "i10", "session_date": day, "close": 9.0})

    assert len(store.for_instrument("i1", limit=10_000)) == _MAX_SESSIONS_PER_INSTRUMENT
    assert len(store.for_instrument("i10")) == 5
