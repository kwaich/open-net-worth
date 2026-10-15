"""backend/services/aggregation/lunch_flow_pending.py — in-memory, TTL-bounded, single-use
flow staging. Mirrors tests/test_mcp_oauth_pending.py's own shape for the same properties.
"""

from __future__ import annotations

from backend.services.aggregation import lunch_flow_pending as pending


def setup_function():
    pending._reset_for_tests()


def teardown_function():
    pending._reset_for_tests()


def _stage(**over) -> str:
    kwargs = {"redirect_uri": "https://onw.example/api/lunch-flow/oauth/callback"}
    kwargs.update(over)
    return pending.stage(**kwargs)


def test_stage_then_pop_returns_the_staged_flow():
    state = _stage()
    flow = pending.pop(state)
    assert flow is not None
    assert flow.redirect_uri == "https://onw.example/api/lunch-flow/oauth/callback"


def test_pop_is_single_use_a_replay_gets_nothing():
    state = _stage()
    assert pending.pop(state) is not None
    assert pending.pop(state) is None


def test_pop_of_an_unknown_state_returns_none():
    assert pending.pop("never-issued") is None


def test_pop_of_an_expired_flow_returns_none():
    now = 1_000_000.0
    state = _stage(now=now)
    assert pending.pop(state, now=now + pending.TTL_SECONDS + 1) is None


def test_get_status_before_completion_is_pending():
    state = _stage()
    assert pending.get_status(state) == {"status": "pending", "error": None}


def test_get_status_after_ok_completion():
    state = _stage()
    pending.pop(state)
    pending.record_status(state, "ok", None)
    assert pending.get_status(state) == {"status": "ok", "error": None}


def test_get_status_after_error_completion_carries_the_message():
    state = _stage()
    pending.pop(state)
    pending.record_status(state, "error", "Lunch Flow declined or cancelled the sign-in.")
    assert pending.get_status(state) == {
        "status": "error",
        "error": "Lunch Flow declined or cancelled the sign-in.",
    }


def test_get_status_for_an_unknown_state_is_a_generic_error_not_an_oracle():
    result = pending.get_status("never-issued")
    assert result["status"] == "error"
    assert "expired" in result["error"].lower() or "unknown" in result["error"].lower()


def test_get_status_never_returns_a_token():
    state = _stage()
    pending.pop(state)
    pending.record_status(state, "ok", None)
    status = pending.get_status(state)
    assert "access_token" not in status
    assert "code" not in status
    assert set(status.keys()) == {"status", "error"}


def test_outstanding_count_is_capped():
    now = 2_000_000.0
    states = [_stage(now=now + i) for i in range(pending.MAX_OUTSTANDING + 5)]
    assert len(pending._pending) <= pending.MAX_OUTSTANDING
    # the newest stays live; the very first minted was evicted first (oldest expires_at)
    assert pending.pop(states[-1], now=now) is not None
