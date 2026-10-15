"""Per-conversation approval mode (RFC ``2026-08-27-per-conversation-approval-mode``).

Three modes, shared verbatim with an Automation's ``permission_mode``: ``manual`` asks on
every approval-tier call, ``auto`` proceeds except on hardline operations, ``skip`` proceeds
regardless. It decides whether the OWNER IS ASKED — never what may be called.

The asymmetry that matters, and the reason this is not the Automation function with a
different context plugged in: **``manual`` is a NO-OP here.** An Automation in ``manual``
gates even a tool globally set to ``always_allow`` (a run must not inherit a permission
granted in an unrelated chat). In a conversation that would be exactly backwards — a chat
*Always allow* tap writes the GLOBAL row, so gating on it would break the app's most-used
permission control the moment this shipped. ``manual`` is what every EXISTING conversation
(and any unrecognised/unreadable stored value) resolves to, so it has to change nothing at
all for them; ``auto`` is what a genuinely brand-new conversation — no row ever
persisted — starts in instead (2026-09-04).
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import _confirm_gate as gate
from backend.core.conversation_approval import (
    ConversationApprovalContext,
    bind_conversation_approval,
    normalize_mode,
    reset_conversation_approval,
)
from backend.core.errors import ValidationError
from backend.services import conversation_approval
from backend.services.tool_permissions import CORE_CONNECTOR_ID, set_tool_permission

TOOL = "create_account"


def _verdict(tool: str, mode: str):
    token = bind_conversation_approval(ConversationApprovalContext(session_id="s1", mode=mode))
    try:
        return gate.conversation_gate_verdict(tool)
    finally:
        reset_conversation_approval(token)


# ── the modes ────────────────────────────────────────────────────────────────────────────


def test_manual_is_a_no_op(monkeypatch, db_isolated):
    """THE guard. ``None`` means "no opinion, use the global level" — NOT "gate".

    If this ever returns ``"gate"``, every tool the owner globally set to ``always_allow``
    starts carding again in ordinary chat, because a conversation is always in some mode and
    ``manual`` is the default. That would break the permission control people actually use,
    for every user, the moment the feature shipped.
    """
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    assert _verdict(TOOL, "manual") is None


def test_auto_allows_an_ordinary_write(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    assert _verdict(TOOL, "auto") == "allow"


def test_skip_allows_an_ordinary_write(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    assert _verdict(TOOL, "skip") == "allow"


def test_auto_still_gates_a_hardline_operation(monkeypatch, db_isolated):
    """``auto`` means "don't ask about the ordinary" — a hardline op whose target set cannot
    be verified after the fact is not ordinary. Same carve-out an ``auto`` Automation has."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    assert _verdict("delete_transactions_where", "auto") == "gate"
    assert _verdict("delete_transactions_where", "skip") == "allow"


def test_no_context_bound_means_no_opinion(monkeypatch, db_isolated):
    """An unbound context must not gate — a turn that never bound one behaves as before."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    assert gate.conversation_gate_verdict(TOOL) is None


# ── what OUTRANKS the mode ───────────────────────────────────────────────────────────────


def test_a_policy_tool_gates_in_every_mode(monkeypatch, db_isolated):
    """A conversation can author an Automation. An ``auto``/``skip`` conversation reading
    untrusted text could otherwise be steered into writing one with ``permission_mode=skip``
    and running it — the mode must not be able to mint a wider mode."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    for mode in ("manual", "auto", "skip"):
        assert _verdict("create_automation", mode) == "gate", mode
        assert _verdict("update_automations", mode) == "gate", mode


def test_an_explicit_ask_outranks_the_mode(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, TOOL, "needs_approval")
    assert _verdict(TOOL, "skip") == "gate"
    assert _verdict(TOOL, "auto") == "gate"


def test_an_UNDECIDED_tool_is_not_treated_as_an_explicit_ask(monkeypatch, db_isolated):
    """The same trap the Automation ladder has: ``needs_approval`` is ALSO the default for an
    undecided tool, so keying on the effective level would gate nearly everything and make
    ``auto``/``skip`` silently do nothing."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    assert gate.effective_level(TOOL) == "needs_approval"  # defaulted, not stored
    assert _verdict(TOOL, "skip") == "allow"


def test_a_global_always_allow_passes_without_the_mode_widening_anything(monkeypatch, db_isolated):
    """A tool the owner globally allowed returns ``None`` — it never reaches the mode at
    all, which is what keeps ``manual`` a no-op for it."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: False)
    set_tool_permission(CORE_CONNECTOR_ID, TOOL, "always_allow")
    for mode in ("manual", "auto", "skip"):
        assert _verdict(TOOL, mode) is None, mode


# ── an Automation still speaks for its own run ───────────────────────────────────────────


def test_an_automation_context_wins_over_the_conversations_mode(monkeypatch, db_isolated):
    """Both contexts can be bound (a run has a session like any turn). ``should_gate`` reads
    the Automation's verdict first, so a run's own authority stays the only one that speaks
    — otherwise a `skip` conversation could silently widen a `manual` Automation."""
    from backend.core.automation_runner import (
        AutomationApprovalContext,
        bind_automation_approval,
        reset_automation_approval,
    )

    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    conv = bind_conversation_approval(ConversationApprovalContext(session_id="s1", mode="skip"))
    auto = bind_automation_approval(
        AutomationApprovalContext(automation_id="a", run_id="r", session_id="s1", mode="manual")
    )
    try:
        assert gate.automation_gate_verdict(TOOL) == "gate"
    finally:
        reset_automation_approval(auto)
        reset_conversation_approval(conv)


# ── storage ──────────────────────────────────────────────────────────────────────────────


def test_a_brand_new_session_with_no_row_defaults_to_auto(db_isolated):
    """No row at all = never persisted = a brand-new conversation. Distinct from an existing
    conversation whose row never stored a mode, which stays `manual` (see the next test)."""
    assert conversation_approval.get_mode("never-seen") == "auto"


def test_an_existing_session_that_never_stored_a_mode_stays_manual(db_isolated):
    """A row that already exists (e.g. imported from before this default existed) but never
    stored a mode must NOT pick up `auto` — only a genuinely new row does that. Uses the
    legacy-absorb path (`import_session_row`) rather than `SessionStore.upsert_meta`, since
    the live write path now bakes `auto` into every row it creates fresh."""
    from backend.db.engine import get_engine
    from backend.db.session_io import import_session_row

    with get_engine().begin() as conn:
        import_session_row(
            conn,
            {"id": "has-history", "meta": {"id": "has-history", "message_count": 3}},
        )
    assert conversation_approval.get_mode("has-history") == "manual"


def test_set_then_get_round_trips(db_isolated):
    assert conversation_approval.set_mode("s1", "skip") == "skip"
    assert conversation_approval.get_mode("s1") == "skip"


def test_an_unknown_mode_is_REFUSED_on_write(db_isolated):
    """Coerced on read, refused on write: a typo in a request must be reported, not silently
    rounded to ``manual`` and left looking as if it took."""
    with pytest.raises(ValidationError):
        conversation_approval.set_mode("s1", "yolo")


def test_an_unreadable_or_garbage_stored_value_reads_as_manual(db_isolated):
    """Fails toward ASKING. Never toward skip."""
    assert normalize_mode(None) == "manual"
    assert normalize_mode("") == "manual"
    assert normalize_mode("SKIP") == "skip"  # case-folded, still recognised
    assert normalize_mode("nonsense") == "manual"
    assert normalize_mode({"a": 1}) == "manual"


def test_it_is_stored_on_the_conversation_not_in_the_permission_table(db_isolated):
    """The point of the design. A grant-store row is durable, global and invisible from the
    composer — putting it there would let "skip while I do this one thing" become "skip
    forever"."""
    from backend.db.session_store import SessionStore
    from backend.db.tool_permission_store import ToolPermissionStore

    conversation_approval.set_mode("s1", "skip")
    assert (SessionStore().get_meta("s1") or {}).get("permission_mode") == "skip"
    assert not [r for r in ToolPermissionStore().all() if "permission_mode" in json.dumps(r)]


# ── the security property ────────────────────────────────────────────────────────────────


def test_no_tool_can_set_the_mode():
    """HUMAN-ONLY, made checkable rather than merely intended.

    A conversation reads untrusted text all day. If a model could set its own mode, a prompt
    injection in a fetched page or an email body would be an approvals-off switch. This
    sweeps every registered tool's schema for the parameter and the service's own writer.
    """
    import backend.ai.tools  # noqa: F401  (populates the registry)
    from backend.ai.tools.registry import build_tool_schemas

    offenders = [
        s["name"]
        for s in build_tool_schemas()
        if "permission_mode" in json.dumps(s.get("input_schema") or {})
    ]
    # `create_automation`/`update_automations`/`preview_automation` legitimately take a
    # permission_mode — an AUTOMATION's, which is gated by AUTOMATION_POLICY_TOOLS on every
    # call. No tool may take the CONVERSATION's.
    from backend.ai.tools._confirm_gate import AUTOMATION_POLICY_TOOLS

    unexpected = [
        n for n in offenders if n not in set(AUTOMATION_POLICY_TOOLS) | {"preview_automation"}
    ]
    assert not unexpected, (
        f"these tools can set a permission_mode and are not policy-gated: {unexpected}"
    )


def test_the_service_is_the_only_writer():
    """Nothing under ``ai/`` or ``mcp/`` may write the mode — the import ban that keeps the
    sweep above honest as new code lands."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "backend"
    offenders = []
    for area in ("ai", "mcp"):
        for path in (root / area).rglob("*.py"):
            if "conversation_approval.set_mode" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))
    assert not offenders, f"the model-facing layer must not write the approval mode: {offenders}"


# ── end to end, through the real route ───────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient

    from backend.main import app

    # NOT `with TestClient(app)`: entering the context runs the app lifespan, which calls
    # configure_logging() and leaves `backend` with propagate=False, poisoning caplog for the
    # rest of the session (tests/conftest.py::_restore_backend_logger exists because of it).
    # These routes need no lifespan.
    return TestClient(app)


def test_the_route_round_trips_and_defaults_to_auto_for_a_new_session(db_isolated):
    c = _client()
    assert c.get("/api/sessions/s-rt/permission-mode").json()["mode"] == "auto"
    assert (
        c.put("/api/sessions/s-rt/permission-mode", json={"mode": "skip"}).json()["mode"] == "skip"
    )
    assert c.get("/api/sessions/s-rt/permission-mode").json()["mode"] == "skip"


def test_the_route_refuses_an_unknown_mode(db_isolated):
    c = _client()
    assert c.put("/api/sessions/s-bad/permission-mode", json={"mode": "yolo"}).status_code == 422
    # …and the refusal left nothing behind — still reads as a brand-new session's default.
    assert c.get("/api/sessions/s-bad/permission-mode").json()["mode"] == "auto"


def test_the_mode_is_scoped_to_ONE_conversation(db_isolated):
    """It must not leak sideways — that is the whole reason it lives on the session row."""
    c = _client()
    c.put("/api/sessions/s-a/permission-mode", json={"mode": "skip"})
    assert c.get("/api/sessions/s-b/permission-mode").json()["mode"] == "auto"


def test_a_turn_binds_the_stored_mode(db_isolated, monkeypatch):
    """The wiring proof: `turns._drive` reads the mode once and binds it for the turn, so a
    tool dispatched inside that turn sees it. Without this the storage and the gate are two
    halves that never meet."""
    import asyncio

    from backend.ai import turns

    conversation_approval.set_mode("s-bound", "skip")
    seen = {}

    async def _events(_turn_id):
        from backend.core.conversation_approval import get_conversation_approval

        ctx = get_conversation_approval()
        seen["mode"] = ctx.mode if ctx else None
        seen["session"] = ctx.session_id if ctx else None
        if False:
            yield {}

    asyncio.run(
        turns.run_inline(
            "s-bound",
            "chat",
            _events,
            error_event=lambda exc: {"type": "error", "message": str(exc)},
            cost_surface="web",
        )
    )
    assert seen == {"mode": "skip", "session": "s-bound"}


def test_the_binding_does_not_outlive_the_turn(db_isolated):
    """A ContextVar left set would make the NEXT turn on another session inherit this mode."""
    import asyncio

    from backend.ai import turns
    from backend.core.conversation_approval import get_conversation_approval

    conversation_approval.set_mode("s-leak", "skip")

    async def _events(_turn_id):
        if False:
            yield {}

    asyncio.run(
        turns.run_inline(
            "s-leak",
            "chat",
            _events,
            error_event=lambda exc: {"type": "error", "message": str(exc)},
            cost_surface="web",
        )
    )
    assert get_conversation_approval() is None


# ── an Automation run's conversation ─────────────────────────────────────────────────────


def test_an_automation_runs_conversation_reports_the_AUTOMATIONS_mode(db_isolated, monkeypatch):
    """Owner-reported, 2026-08-27: opening an Automation run showed ``manual``.

    A run is an ordinary durable conversation, so it has a session row like any other. The
    picker read that row — never set, so ``manual`` — while the run was really executing in
    ``auto``. Worse, the gate consults the Automation's verdict FIRST and only falls through
    to the conversation's, so the value shown was not merely wrong, it was INERT: changing it
    wrote something nothing reads.
    """
    monkeypatch.setattr(
        conversation_approval,
        "owning_automation_mode",
        lambda sid: "auto" if sid == "run-1" else None,
    )
    conversation_approval.set_mode("ordinary", "manual")
    assert conversation_approval.get_mode("run-1") == "auto"
    assert conversation_approval.get_mode("ordinary") == "manual"


def test_writing_the_mode_on_an_automation_run_is_REFUSED(db_isolated, monkeypatch):
    """The Automation editor owns it. Accepting the write would store a value the gate never
    reads — the silent no-op this whole fix is about."""
    monkeypatch.setattr(
        conversation_approval,
        "owning_automation_mode",
        lambda sid: "auto" if sid == "run-1" else None,
    )
    with pytest.raises(ValidationError):
        conversation_approval.set_mode("run-1", "skip")
    # …and an ordinary conversation is unaffected.
    assert conversation_approval.set_mode("ordinary", "skip") == "skip"


def test_the_route_reports_editability(db_isolated, monkeypatch):
    monkeypatch.setattr(
        conversation_approval,
        "owning_automation_mode",
        lambda sid: "auto" if sid == "run-1" else None,
    )
    c = _client()
    run = c.get("/api/sessions/run-1/permission-mode").json()
    assert run["mode"] == "auto" and run["editable"] is False and run["governed_by"] == "automation"
    ordinary = c.get("/api/sessions/chat-1/permission-mode").json()
    assert ordinary["editable"] is True and ordinary["governed_by"] == "conversation"


def test_an_undeterminable_owner_falls_back_to_the_session(db_isolated, monkeypatch):
    """Never raises: if the run lookup blows up, behave as before rather than failing a read
    the composer makes on every conversation open."""

    def _boom(_sid):
        raise RuntimeError("store down")

    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", _boom, raising=False
    )
    conversation_approval.set_mode("s-fallback", "skip")
    assert conversation_approval.get_mode("s-fallback") == "skip"
