"""
Store CRUD + cap + streak tests for RunHistoryStore.

All tests use an isolated tmp_path store — no real data files are touched.
No LLM / network calls; no 'integration' marker needed.
"""

from __future__ import annotations

import pytest

from backend.db.run_history_store import MAX_RUNS_PER_ENTITY, RunHistoryStore


@pytest.fixture()
def store(db_isolated):
    return RunHistoryStore()


def _run(kind: str, entity_id: str, status: str = "ok", **kw) -> dict:
    return {
        "kind": kind,
        "entity_id": entity_id,
        "entity_name": f"name-{entity_id}",
        "started_at": "2026-01-01T10:00:00",
        "status": status,
        "summary": "test run",
        "trigger": "manual",
        **kw,
    }


# ── Append + for_entity ────────────────────────────────────────────────────────


def test_append_and_for_entity_newest_first(store):
    store.append(_run("agent", "a1", started_at="2026-01-01T10:00:00"))
    store.append(_run("agent", "a1", started_at="2026-01-01T11:00:00"))
    rows = store.for_entity("agent", "a1")
    assert len(rows) == 2
    assert rows[0]["started_at"] == "2026-01-01T11:00:00"
    assert rows[1]["started_at"] == "2026-01-01T10:00:00"


def test_for_entity_isolates_foreign_rows(store):
    store.append(_run("agent", "a1"))
    store.append(_run("agent", "a2"))
    store.append(_run("task", "t1"))

    assert len(store.for_entity("agent", "a1")) == 1
    assert len(store.for_entity("agent", "a2")) == 1
    assert len(store.for_entity("task", "t1")) == 1
    # No leakage across entities
    assert store.for_entity("agent", "a1")[0]["entity_id"] == "a1"


def test_for_entity_excludes_streak_records(store):
    store.append(_run("agent", "a1"))
    store.set_streak_state("agent", "a1", {"failing": True, "fail_count": 1})
    rows = store.for_entity("agent", "a1")
    # Streak record must NOT appear in for_entity results
    assert all(r.get("kind") != "__streak__" for r in rows)
    assert len(rows) == 1


# ── Per-entity cap ─────────────────────────────────────────────────────────────


def test_per_entity_cap_at_50(store):
    for i in range(60):
        store.append(_run("agent", "a1", started_at=f"2026-01-01T{i:02d}:00:00"))
    rows = store.for_entity("agent", "a1", limit=100)
    assert len(rows) == MAX_RUNS_PER_ENTITY  # capped at 50


def test_cap_keeps_newest(store):
    for i in range(60):
        store.append(_run("agent", "a1", started_at=f"2026-01-{i + 1:02d}T00:00:00"))
    rows = store.for_entity("agent", "a1", limit=100)
    # Newest run should be day 60
    assert (
        rows[0]["started_at"].startswith("2026-01-60")
        or rows[0]["started_at"] > rows[-1]["started_at"]
    )
    assert len(rows) == 50


def test_cap_leaves_other_entity_untouched(store):
    for i in range(60):
        store.append(_run("agent", "a1", started_at=f"2026-01-{i + 1:02d}T00:00:00"))
    for i in range(3):
        store.append(_run("agent", "b1", started_at=f"2026-06-0{i + 1}T00:00:00"))

    a1_rows = store.for_entity("agent", "a1", limit=100)
    b1_rows = store.for_entity("agent", "b1", limit=100)
    assert len(a1_rows) == 50
    assert len(b1_rows) == 3  # B's rows untouched


# ── Streak companion CRUD ─────────────────────────────────────────────────────


def test_streak_state_returns_none_when_absent(store):
    assert store.streak_state("agent", "x") is None


def test_set_streak_state_upserts_by_deterministic_id(store):
    s1 = store.set_streak_state("agent", "a1", {"failing": True, "fail_count": 1})
    s2 = store.set_streak_state("agent", "a1", {"fail_count": 2})
    assert s1["id"] == s2["id"]  # same deterministic id
    assert store.streak_state("agent", "a1")["fail_count"] == 2


def test_set_streak_state_does_not_duplicate(store):
    store.set_streak_state("agent", "a1", {"failing": True})
    store.set_streak_state("agent", "a1", {"failing": False})
    all_items = store.all()
    streak_items = [
        r for r in all_items if r.get("kind") == "__streak__" and r.get("entity_id") == "a1"
    ]
    assert len(streak_items) == 1


# ── recent_failing ────────────────────────────────────────────────────────────


def test_recent_failing_filters_by_failing_and_window(store):
    store.set_streak_state(
        "agent", "a1", {"failing": True, "first_error_at": "2026-06-11T10:00:00"}
    )
    store.set_streak_state(
        "agent", "a2", {"failing": False, "first_error_at": "2026-06-11T10:00:00"}
    )
    store.set_streak_state("task", "t1", {"failing": True, "first_error_at": "2020-01-01T00:00:00"})

    # window starts at 2026-06-11 → only a1 matches
    result = store.recent_failing("2026-06-01T00:00:00")
    entity_ids = {r["entity_id"] for r in result}
    assert "a1" in entity_ids
    assert "a2" not in entity_ids
    assert "t1" not in entity_ids
