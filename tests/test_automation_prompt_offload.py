"""An Automation turn builds its system prompt in a worker thread, not on the event loop.

`build_system_prompt` embeds the operator's current net worth so the model sees the same
number the dashboard does, and `ledger.net_worth` folds the ENTIRE transaction table to get
it. All three call sites in `automation_runner_adapter` are `async def`, so that fold ran on
the loop once per turn and held it there — `core.loop_watch` named it live on 2026-08-11
17:29 (turns._pump → _events → build_system_prompt → net_worth → transaction_store.all),
during the run that left the whole app unresponsive.

The context assertion is the one that would fail silently in production rather than loudly
here: the prompt is built inside a bound audit scope, run plan and email consumer, and a
thread hop that dropped them would not raise — it would mis-attribute whatever the turn
writes next. `asyncio.to_thread` copies the context; this pins that it keeps doing so.

Run:
    .venv/bin/python3 -m pytest tests/test_automation_prompt_offload.py -q
"""

from __future__ import annotations

import threading

from backend.ai import automation_runner_adapter as adapter


class _Ctx:
    model = "claude-sonnet-4-6"
    base_url = None


async def test_system_prompt_is_built_off_the_event_loop(monkeypatch):
    seen: dict = {}

    def fake_build(*, model=None, base_url=None):
        seen["thread"] = threading.current_thread().name
        seen["model"] = model
        seen["base_url"] = base_url
        return "PROMPT"

    monkeypatch.setattr(adapter, "build_system_prompt", fake_build)

    assert await adapter._system_prompt(_Ctx()) == "PROMPT"
    # to_thread dispatches to the default executor ("asyncio_N"); inline would be MainThread.
    assert seen["thread"].startswith("asyncio_"), (
        f"system prompt built on {seen['thread']} — expected a worker thread"
    )
    assert seen["model"] == "claude-sonnet-4-6"
    assert seen["base_url"] is None


async def test_audit_scope_survives_the_thread_hop(monkeypatch):
    """The prompt is built inside a bound audit scope; the hop must not drop it.

    A dropped scope does not raise — it silently unattributes the writes the turn goes on to
    make, which is exactly the provenance the ledger audit trail depends on.
    """
    from backend.core.audit_context import clear_audit_scope, get_audit_scope, set_audit_scope

    seen: dict = {}

    def fake_build(*, model=None, base_url=None):
        seen["scope"] = get_audit_scope()
        return "PROMPT"

    monkeypatch.setattr(adapter, "build_system_prompt", fake_build)

    token = set_audit_scope(
        actor="ai",
        surface="automation",
        session_id="sess-1",
        automation="Nightly close",
        automation_id="auto-1",
    )
    try:
        await adapter._system_prompt(_Ctx())
    finally:
        clear_audit_scope(token)

    scope = seen["scope"]
    assert scope is not None, "audit scope was lost crossing into the worker thread"
    assert scope.automation_id == "auto-1"
    assert scope.surface == "automation"
