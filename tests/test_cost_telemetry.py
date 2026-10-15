"""Tests for cost-telemetry correctness (lane/cost-telemetry).

Covers:
- cost_tracker.record includes surface field when set via cost_context
- _record_cost in assistant reads cost_context surface
- scheduled sessions (scheduled-<id>) are filtered from list_sessions
- session file is cleaned up after a simulated scheduled run
"""

from __future__ import annotations

import uuid

# ---------------------------------------------------------------------------
# cost_context ContextVar
# ---------------------------------------------------------------------------


def test_cost_context_set_get_clear():
    from backend.core.cost_context import clear_cost_surface, get_cost_surface, set_cost_surface

    assert get_cost_surface() is None
    set_cost_surface("web")
    assert get_cost_surface() == "web"
    clear_cost_surface()
    assert get_cost_surface() is None


def test_cost_context_connector_surface():
    from backend.core.cost_context import clear_cost_surface, get_cost_surface, set_cost_surface

    set_cost_surface("connector:telegram")
    assert get_cost_surface() == "connector:telegram"
    clear_cost_surface()


def test_cost_context_scheduled_surface():
    from backend.core.cost_context import clear_cost_surface, get_cost_surface, set_cost_surface

    set_cost_surface("scheduled")
    assert get_cost_surface() == "scheduled"
    clear_cost_surface()


# ---------------------------------------------------------------------------
# cost_tracker.record writes surface field when provided
# ---------------------------------------------------------------------------


def test_record_includes_surface(db_isolated):
    from backend.ai.cost_tracker import record
    from backend.db.cost_log_store import CostLogStore

    record("chat", "claude-sonnet-4-6", 100, 50, surface="web", cost_usd=0.001)

    records = CostLogStore().all()
    assert len(records) == 1
    assert records[0]["surface"] == "web"


def test_record_omits_surface_when_none(db_isolated):
    from backend.ai.cost_tracker import record
    from backend.db.cost_log_store import CostLogStore

    record("chat", "claude-sonnet-4-6", 100, 50, surface=None, cost_usd=0.001)

    records = CostLogStore().all()
    assert len(records) == 1
    assert "surface" not in records[0]


def test_record_surface_different_entrypoints(db_isolated):
    from backend.ai.cost_tracker import record
    from backend.db.cost_log_store import CostLogStore

    record("chat", "claude-sonnet-4-6", 100, 50, surface="web", cost_usd=0.001)
    record("chat", "claude-haiku-4-5", 80, 20, surface="connector:telegram", cost_usd=0.0001)
    record("chat", "claude-sonnet-4-6", 50, 10, surface="scheduled", cost_usd=0.0005)

    records = CostLogStore().all()
    surfaces = [r.get("surface") for r in records]
    assert "web" in surfaces
    assert "connector:telegram" in surfaces
    assert "scheduled" in surfaces


# ---------------------------------------------------------------------------
# scheduled-<id> sessions are filtered from list_sessions
# ---------------------------------------------------------------------------


def test_is_internal_session_exact():
    from backend.ai.assistant import _is_internal_session

    assert _is_internal_session("scheduled") is True


def test_is_internal_session_prefix():
    from backend.ai.assistant import _is_internal_session

    assert _is_internal_session("scheduled-abc12345") is True
    assert _is_internal_session("scheduled-00000000") is True


def test_is_internal_session_user_session():
    from backend.ai.assistant import _is_internal_session

    assert _is_internal_session(str(uuid.uuid4())) is False
    assert _is_internal_session("my-conversation") is False


def test_list_sessions_excludes_scheduled_prefix(db_isolated):
    """scheduled-<id> sessions must NOT appear in list_sessions output. Sessions are
    DB-backed now (backend/db/session_store.py) — seed via the real upsert_session_meta
    choke instead of a per-file write."""
    from backend.ai.assistant import list_sessions, upsert_session_meta

    sched_id = f"scheduled-{str(uuid.uuid4())[:8]}"
    user_id = str(uuid.uuid4())

    upsert_session_meta(sched_id, title="run report", message_count=1)
    upsert_session_meta(user_id, title="hello", message_count=1)

    sessions = list_sessions()

    ids = [s["id"] for s in sessions]
    assert user_id in ids
    assert sched_id not in ids


def test_list_sessions_excludes_legacy_scheduled(db_isolated):
    """The legacy 'scheduled' exact-id must also be excluded."""
    from backend.ai.assistant import list_sessions, upsert_session_meta

    upsert_session_meta("scheduled", title="run report", message_count=1)

    sessions = list_sessions()

    assert not any(s["id"] == "scheduled" for s in sessions)


# ---------------------------------------------------------------------------
# Scheduled session cleanup — file gone after run, not in list_sessions
# ---------------------------------------------------------------------------


def test_scheduled_session_cleanup(db_isolated):
    """Simulates a scheduled runner: the session ROW must be gone after the run. Sessions
    are DB-backed now — one row (meta+transcript) replaces the old per-file
    _session_file/_delete_session_file pair; main.py's own finally block now calls
    assistant.clear_session(session_id) directly (see backend/main.py), which this mirrors."""
    from backend.ai.assistant import clear_session, save_session_durable
    from backend.db.session_store import SessionStore

    short_id = str(uuid.uuid4())[:8]
    session_id = f"scheduled-{short_id}"

    # Simulate a session being written during the run.
    save_session_durable(session_id, [{"role": "user", "content": "test"}])
    assert SessionStore().get_meta(session_id) is not None

    # Simulate cleanup (mirrors main.py's finally block: clear_session(session_id)).
    clear_session(session_id)
    assert SessionStore().get_meta(session_id) is None
