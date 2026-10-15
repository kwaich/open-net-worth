"""Shared chat confirm seam — ``confirm.handle_confirm_action`` (unified-chat-channels Slice 1).

Pins the generalized confirm/apply path promoted out of Telegram's callback handler, now
shared by Telegram inline keyboards AND Slack Block Kit buttons:
  * confirm → applies through ``apply_change`` (the ONE choke point — no new write path)
    + a session note;
  * discard → no apply + a session note;
  * a stale / unknown change id → no apply (single-use);
  * an apply failure → ``status="failed"`` with the error (the change is consumed);
  * an unknown action → ``status="ignored"`` (nothing done).

No network / no LLM — the apply choke point + the session-note writer are stubbed.
"""

from __future__ import annotations

import pytest

from backend.connectors import pending_changes
from backend.connectors.chat import confirm


@pytest.fixture
def pending(monkeypatch):
    """A stored pending change + stubbed apply + session-note so nothing real is written."""
    applied: list[dict] = []
    notes: list[str] = []

    async def _fake_apply(change, session_id=None, **_kw):
        applied.append(change)

    def _fake_note(session_id, note):
        notes.append(note)

    monkeypatch.setattr(confirm, "apply_change", _fake_apply)
    monkeypatch.setattr(confirm, "inject_session_note", _fake_note)

    change = {"type": "add", "display": {"symbol": "BTC"}}
    change_id = pending_changes.store("sess-1", change)
    return {"change_id": change_id, "applied": applied, "notes": notes}


@pytest.mark.asyncio
async def test_confirm_applies_through_choke_point(pending):
    out = await confirm.handle_confirm_action("confirm", pending["change_id"])
    assert out.status == "applied"
    assert "BTC" in out.message
    assert len(pending["applied"]) == 1  # routed through apply_change (the choke point)
    assert len(pending["notes"]) == 1
    # Single-use: the change is gone.
    assert pending_changes.pop(pending["change_id"]) is None


@pytest.mark.asyncio
async def test_discard_does_not_apply(pending):
    out = await confirm.handle_confirm_action("discard", pending["change_id"])
    assert out.status == "discarded"
    assert "BTC" in out.message
    assert pending["applied"] == []  # NEVER applied
    assert len(pending["notes"]) == 1


@pytest.mark.asyncio
async def test_stale_change_id_is_noop():
    out = await confirm.handle_confirm_action("confirm", "does-not-exist")
    assert out.status == "stale"


@pytest.mark.asyncio
async def test_apply_failure_reported_and_change_consumed(monkeypatch):
    def _boom(change, session_id=None, **_kw):
        raise RuntimeError("ledger locked")

    monkeypatch.setattr(confirm, "apply_change", _boom)
    monkeypatch.setattr(confirm, "inject_session_note", lambda *a: None)

    cid = pending_changes.store("sess-2", {"type": "add", "display": {"symbol": "ETH"}})
    out = await confirm.handle_confirm_action("confirm", cid)
    assert out.status == "failed"
    assert "ledger locked" in (out.error or "")
    # The change was popped (single-use) even though apply failed.
    assert pending_changes.pop(cid) is None


@pytest.mark.asyncio
async def test_unknown_action_ignored(pending):
    out = await confirm.handle_confirm_action("frobnicate", pending["change_id"])
    assert out.status == "ignored"
    assert pending["applied"] == []


# ── IDOR: a BOUND change can only be applied by its PROPOSER ──────────────────────


@pytest.fixture
def bound_pending(monkeypatch):
    """A pending change BOUND to proposer (slack, C1, U-alice) + stubbed apply/session-note."""
    applied: list[dict] = []

    async def _fake_apply(change, session_id=None, **_kw):
        applied.append(change)

    monkeypatch.setattr(confirm, "apply_change", _fake_apply)
    monkeypatch.setattr(confirm, "inject_session_note", lambda *a: None)

    change = {"type": "add", "display": {"symbol": "BTC"}}
    change_id = pending_changes.store(
        "sess-1", change, connector="slack", channel_id="C1", sender_id="U-alice"
    )
    return {"change_id": change_id, "applied": applied}


@pytest.mark.asyncio
async def test_proposer_can_confirm_bound_change(bound_pending):
    """The SAME user who proposed it (matching connector + sender_id) may apply — the channel
    need not match (the card may have been DM'd)."""
    out = await confirm.handle_confirm_action(
        "confirm",
        bound_pending["change_id"],
        connector="slack",
        channel_id="D-alice-dm",  # a DIFFERENT channel (DM) than the bound C1 — still fine
        sender_id="U-alice",
    )
    assert out.status == "applied"
    assert len(bound_pending["applied"]) == 1


@pytest.mark.asyncio
async def test_other_user_cannot_apply_someone_elses_change(bound_pending):
    """IDOR closed: User B tapping User A's bound change is REFUSED — nothing applied AND the
    change is LEFT IN PLACE (peek-checked, not popped) so the real proposer can still confirm."""
    out = await confirm.handle_confirm_action(
        "confirm",
        bound_pending["change_id"],
        connector="slack",
        channel_id="C1",
        sender_id="U-bob",  # a DIFFERENT sender than the proposer U-alice
    )
    assert out.status == "ignored"
    assert bound_pending["applied"] == []  # NEVER applied
    # Still present — the real proposer can confirm it.
    assert pending_changes.peek(bound_pending["change_id"]) is not None
    out2 = await confirm.handle_confirm_action(
        "confirm",
        bound_pending["change_id"],
        connector="slack",
        channel_id="C1",
        sender_id="U-alice",
    )
    assert out2.status == "applied"
    assert len(bound_pending["applied"]) == 1


@pytest.mark.asyncio
async def test_cross_connector_tap_refused(bound_pending):
    """A tap from a different CONNECTOR (even same sender id string) can't apply a bound change."""
    out = await confirm.handle_confirm_action(
        "confirm",
        bound_pending["change_id"],
        connector="telegram",  # bound to slack
        channel_id="C1",
        sender_id="U-alice",
    )
    assert out.status == "ignored"
    assert bound_pending["applied"] == []


@pytest.mark.asyncio
async def test_unbound_web_change_applies_without_identity(pending):
    """A web/SSE change stored UNBOUND (no proposer) applies with no chat-identity check — the
    authenticated operator confirms in their own browser (back-compat for the non-chat path)."""
    out = await confirm.handle_confirm_action("confirm", pending["change_id"])
    assert out.status == "applied"
    assert len(pending["applied"]) == 1


# ── apply_change dispatch: a staged card resolves by its tool_use_id ──────────────────


@pytest.mark.asyncio
async def test_record_income_card_resolves_by_tool_use_id_not_by_its_dict(monkeypatch):
    """``record_income`` used to be hand-dispatched here: ``apply_change`` rewrote the change
    type and passed the tapped dict straight to ``income_service.apply_income_change``. That
    needed a per-type translation table, and the one type it forgot ("record_income" ->
    "record") was a live bug — every confirm on a proposed dividend failed.

    It resolves by ``tool_use_id`` now, like every other staged card: the server re-runs the
    TOOL on the args IT stored, so the tapped dict never reaches a service at all and there
    is no translation left to get wrong."""
    from backend.services import income as income_service

    def _must_not_run(change):  # pragma: no cover - the assertion is that it never fires
        raise AssertionError("the tapped dict must never reach the income service")

    monkeypatch.setattr(income_service, "apply_income_change", _must_not_run)

    seen: dict = {}

    async def _fake_resolve(change, session_id, decision, *, target=None):
        seen.update({"id": change.get("tool_use_id"), "decision": decision})
        return {"ok": True}

    monkeypatch.setattr(confirm, "_resolve_via_pending_tool_use", _fake_resolve)

    await confirm.apply_change(
        {
            "type": "record_income",
            "tool_use_id": "toolu_income",
            "grant_tool": "record_income",
            "position_id": "pos-1",
            "gross_amount": 10.0,
            "display": {"symbol": "AAPL"},
        },
        "sess-1",
    )

    assert seen == {"id": "toolu_income", "decision": "approve"}
