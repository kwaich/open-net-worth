"""_fix_dangling_tool_uses must tell an AWAITING-APPROVAL pause apart from a crash-orphan.

RFC 2026-07-22 (approval-as-pending-tool_use), stage 2 — the crux. A result-less tool_use is
ambiguous: it is either a call paused for the user's approval (its card is on screen) or a call
that crashed mid-flight. The healer stubs the latter ("did not complete") to keep the next API
call valid; it MUST NOT stub the former, or a reload/new-tab clobbers the live approval.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant
from backend.db.pending_plan_store import PendingPlanStore


@pytest.fixture
def pending_path(monkeypatch):
    # pending_plans is DB-backed now (Wave 5D2b, JSON->DB) and isolated per test by
    # conftest's autouse Tier-2 table clear — no per-store monkeypatch needed any more.
    # Kept as a named fixture (now a no-op) so every existing call site is unchanged.
    del monkeypatch
    return None


def _assistant_with(tool_ids):
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tid, "name": "add_transactions", "input": {}}
                for tid in tool_ids
            ],
        }
    ]


def _stubbed_ids(history):
    return [
        b["tool_use_id"]
        for m in history
        if m.get("role") == "user"
        for b in m.get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]


def test_a_crash_orphan_is_still_stubbed(pending_path):
    history = _assistant_with(["toolu_crashed"])
    assistant._fix_dangling_tool_uses(history)
    assert _stubbed_ids(history) == ["toolu_crashed"]


def test_an_awaiting_approval_call_is_NOT_stubbed(pending_path):
    PendingPlanStore().stage_pending("toolu_pending", "add_transactions", {"amount": 1})
    history = _assistant_with(["toolu_pending"])
    assistant._fix_dangling_tool_uses(history)
    # No stub appended — the pending call survives the reload intact (the whole point).
    assert _stubbed_ids(history) == []
    assert len(history) == 1


def test_mixed_only_the_orphan_is_stubbed(pending_path):
    PendingPlanStore().stage_pending("toolu_pending", "add_transactions", {"amount": 1})
    history = _assistant_with(["toolu_pending", "toolu_crashed"])
    assistant._fix_dangling_tool_uses(history)
    assert _stubbed_ids(history) == ["toolu_crashed"]


def test_fails_safe_and_stubs_when_the_pending_store_is_unreadable(pending_path, monkeypatch):
    # Crash recovery must never itself raise: a broken store degrades to the pre-RFC behavior
    # (stub everything), never leaving a real dangling tool_use that would 400 the next call.
    def _boom(*a, **k):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(PendingPlanStore, "get_pending", _boom)
    history = _assistant_with(["toolu_x"])
    assistant._fix_dangling_tool_uses(history)
    assert _stubbed_ids(history) == ["toolu_x"]
