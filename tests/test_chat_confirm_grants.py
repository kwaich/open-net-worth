"""Tool-permission grants over a chat connector (RFC 2026-07-20 approval-and-review-model).

A channel must not be quietly weaker than web (ADR ``2026-07-19-confirm-gate-surface-matrix``),
so the four choices web offers — allow once / this session / always / deny — all reach
``confirm.handle_confirm_action`` and behave the way the web card behaves. What is pinned here:

  * all three allow scopes APPLY the change (they differ only in how long the approval lasts);
  * the grant is recorded BEFORE the apply, so a failed apply cannot leave an ungranted tool
    looking granted, and the model is never told anything before the human-authenticated write;
  * a FAILED grant still applies (the user approved THIS call) but says so — the one thing
    that must never happen is a user believing they hold a permission they do not;
  * ``deny`` applies NOTHING and feeds back an explicit human-decision refusal, not a generic
    discard — a bare "refused" is what makes this model retry or confabulate a reason;
  * a grant action on a non-permission card is IGNORED rather than applied;
  * an ordinary confirm/discard card is completely unaffected by any of it.

No network / no LLM / no store — the apply choke point, the session-note writer and the grant
store are all stubbed.
"""

from __future__ import annotations

import pytest

from backend.connectors import pending_changes
from backend.connectors.chat import confirm, grants

TOOL = "add_transactions"


@pytest.fixture
def gated(monkeypatch):
    """A stored PERMISSION card (one carrying ``grant_tool``) + every side effect stubbed."""
    applied: list[dict] = []
    notes: list[str] = []
    recorded: list[tuple] = []

    async def _fake_apply(change, session_id=None, **_kw):
        applied.append(change)

    def _fake_note(session_id, note):
        notes.append(note)

    def _fake_record(tool_name, scope, *, session_id=None):
        recorded.append((tool_name, scope, session_id))
        return True

    monkeypatch.setattr(confirm, "apply_change", _fake_apply)
    monkeypatch.setattr(confirm, "inject_session_note", _fake_note)
    monkeypatch.setattr(grants, "record_grant", _fake_record)

    change = {"type": "add", "display": {"symbol": "BTC"}, "grant_tool": TOOL}
    change_id = pending_changes.store("sess-1", change)
    return {
        "change_id": change_id,
        "applied": applied,
        "notes": notes,
        "recorded": recorded,
    }


@pytest.mark.parametrize(
    "action,scope",
    [("allow_once", "once"), ("allow_always", "always")],
)
@pytest.mark.asyncio
async def test_every_allow_scope_applies_and_records_that_scope(gated, action, scope):
    out = await confirm.handle_confirm_action(action, gated["change_id"])
    assert out.status == "applied"
    assert len(gated["applied"]) == 1  # both scopes apply — they differ only in duration
    assert gated["recorded"] == [(TOOL, scope, "sess-1")]


@pytest.mark.asyncio
async def test_the_removed_session_action_is_ignored(gated):
    """A stale client tapping the retired button must not apply the change silently."""
    out = await confirm.handle_confirm_action("allow_session", gated["change_id"])
    assert out.status != "applied"
    assert gated["applied"] == []
    assert gated["recorded"] == []


@pytest.mark.asyncio
async def test_allow_once_stores_nothing_durable():
    """``once`` is a real, complete outcome — approved and deliberately not persisted."""
    assert grants.record_grant(TOOL, "once") is True


@pytest.mark.asyncio
async def test_grant_is_recorded_before_the_apply(monkeypatch, gated):
    """Order is the security property: the permission is written from the human tap FIRST, so
    the model is never told the user said yes before the grant actually exists."""
    order: list[str] = []

    async def _apply(change, session_id=None, **_kw):
        order.append("apply")

    def _record(tool_name, scope, *, session_id=None):
        order.append("grant")
        return True

    monkeypatch.setattr(confirm, "apply_change", _apply)
    monkeypatch.setattr(grants, "record_grant", _record)

    await confirm.handle_confirm_action("allow_always", gated["change_id"])
    assert order == ["grant", "apply"]


@pytest.mark.asyncio
async def test_a_failed_grant_still_applies_but_says_so(monkeypatch, gated):
    """The user approved THIS call and the card showed what it does, so it proceeds. Only the
    STANDING permission failed — and a user who believes they hold a permission they do not is
    worse off than one who is asked again, so the acknowledgement must name it."""
    monkeypatch.setattr(grants, "record_grant", lambda *a, **k: False)

    out = await confirm.handle_confirm_action("allow_always", gated["change_id"])
    assert out.status == "applied"
    assert len(gated["applied"]) == 1
    assert "Couldn't save" in out.message


@pytest.mark.asyncio
async def test_deny_applies_nothing_and_returns_a_human_decision_refusal(gated):
    out = await confirm.handle_confirm_action("deny", gated["change_id"])
    assert out.status == "discarded"
    assert gated["applied"] == []  # NEVER applied
    assert gated["recorded"] == []  # and nothing granted
    note = gated["notes"][0]
    # The S2 shape (§15.6): whose decision, that it is not a capability gap, what to do next.
    assert "USER'S DECISION" in note
    assert "not a tool failure" in note
    assert "Do not retry" in note
    assert TOOL in note


@pytest.mark.asyncio
async def test_denial_message_never_reads_as_a_capability_limit():
    """The documented failure this prevents: told only "refused", this model confabulates a
    mechanism — inventing why the app cannot do the thing rather than reporting a user choice."""
    msg = grants.denial_message(TOOL)
    assert "the app can do this" in msg
    assert "chose not to" in msg


@pytest.mark.asyncio
async def test_grant_action_on_a_non_permission_card_is_ignored(monkeypatch):
    """Acting on a malformed instruction is exactly the class of bug a permission system must
    not have — a card with no ``grant_tool`` has nothing to grant against, so nothing happens."""
    applied: list[dict] = []

    async def _fake_apply(change, session_id=None, **_kw):
        applied.append(change)

    monkeypatch.setattr(confirm, "apply_change", _fake_apply)
    monkeypatch.setattr(confirm, "inject_session_note", lambda *a: None)

    change_id = pending_changes.store("sess-2", {"type": "add", "display": {"symbol": "BTC"}})
    out = await confirm.handle_confirm_action("allow_always", change_id)
    assert out.status == "ignored"
    assert applied == []


@pytest.mark.asyncio
async def test_ordinary_confirm_and_discard_are_untouched(monkeypatch):
    """The permission vocabulary is ADDITIVE — a card that is not gating a tool keeps the exact
    two-way behaviour it had, including its acknowledgement wording."""
    applied: list[dict] = []

    async def _fake_apply(change, session_id=None, **_kw):
        applied.append(change)

    monkeypatch.setattr(confirm, "apply_change", _fake_apply)
    monkeypatch.setattr(confirm, "inject_session_note", lambda *a: None)

    cid = pending_changes.store("s", {"type": "add", "display": {"symbol": "BTC"}})
    out = await confirm.handle_confirm_action("confirm", cid)
    assert out.status == "applied" and out.message.startswith("✓ Applied")

    cid2 = pending_changes.store("s", {"type": "add", "display": {"symbol": "BTC"}})
    out2 = await confirm.handle_confirm_action("discard", cid2)
    assert out2.status == "discarded" and out2.message.startswith("✗ Discarded")
    assert len(applied) == 1


def test_record_grant_never_raises_when_the_store_is_missing(monkeypatch):
    """``GrantStore`` is built by a concurrent lane. Until it lands (or if it ever fails), a
    confirm tap represents a decision the user ALREADY made — losing it to an ImportError
    would be a worse outcome than an unsaved grant, so this degrades instead of raising."""
    assert grants.record_grant(TOOL, "nonsense-scope") is False


def test_record_grant_ACTUALLY_PERSISTS_to_the_tool_permission_store(db_isolated):
    """The test this file was missing, and the reason two blocking defects shipped.

    The old assertion was `record_grant(...) in (True, False)` — a tautology, true whether
    the store works or not. Under it, connectors/chat/grants.py imported
    `backend.db.grant_store.GrantStore` (the EQUITY-COMP store, which has no .grant method),
    raised AttributeError, swallowed it in the bare except and returned False. 73 grant tests
    passed while a Telegram user tapping "Always" persisted nothing.

    This asserts the EFFECT, not the absence of an exception. ToolPermissionStore (the
    absorbed ToolGrantStore's successor, revision 0040 — see
    `tests/db/test_tool_permission_store.py`) is DB-backed (PayloadTableStore) —
    db_isolated redirects the process-wide engine singleton to a fresh, empty database for
    the test (tool_permissions is checkpoint-EXCLUDED, which does not affect this per-test
    DB isolation)."""
    from backend.db.tool_permission_store import ToolPermissionStore

    store = ToolPermissionStore()
    assert store.all() == []

    assert grants.record_grant(TOOL, "always") is True
    assert [(g.get("tool"), g.get("level")) for g in store.all()] == [(TOOL, "always_allow")]


def test_grant_ack_names_the_scope_and_the_revocation_surface():
    """The grant table is checkpoint-EXCLUDED, so Settings is the ONLY way to undo a grant
    (§16.1) — a permission system with no visible state is one the user can only interact with
    by being surprised."""
    always = grants.grant_ack(TOOL, "always", stored=True)
    assert "Always allowed" in always
    assert "any amount" in always  # a grant is unbounded (§13.3b) — never let it read narrower
    assert "Settings › Permissions" in always

    once = grants.grant_ack(TOOL, "once", stored=True)
    assert "Allowed once" in once
