"""The discovery gate for EXTERNAL-MCP tools: validate against the handshake's inputSchema.

A call to an external tool before its definition is loaded used to ALWAYS bounce; a weak
model then read the bounce as "no data" (a live automation closed a run on a bounced
``fetch_emails``). Now a read-classified tool whose arguments validate against its DISCOVERED
``inputSchema`` runs on the first call — through the normal external dispatch, so
``tool_permissions`` decides exactly as on a second call. Everything else still bounces.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.ai.assistant import _discovery_gate_verdict
from backend.core import mcp_external
from backend.core.tool_use_context import set_tool_use_id
from backend.db.mcp_client_store import McpClientStore
from backend.db.pending_plan_store import PendingPlanStore
from backend.mcp.client_manager import McpClientManager
from backend.services import tool_permissions
from tests.test_mcp_client import _cfg, _make_fake_urlopen, _set_level

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["query"],
    "additionalProperties": False,
}


@pytest.fixture
def gate(db_isolated, monkeypatch):
    store = McpClientStore()
    store.create(_cfg(tool_allowlist=[]))
    mgr = McpClientManager(store=store, pending=PendingPlanStore())
    captured: dict = {}
    monkeypatch.setattr("backend.mcp.client._urlopen", _make_fake_urlopen(captured))
    mcp_external.register_external_dispatcher(mgr)
    set_tool_use_id("toolu_gate_test")

    def discover(*tools: dict) -> None:
        store.record_discovery("drive", list(tools), "ok", None)

    yield mgr, discover, captured
    mcp_external.register_external_dispatcher(None)
    set_tool_use_id(None)


def _tool(name: str, schema=SEARCH_SCHEMA, **extra) -> dict:
    tool = {"name": name, "description": "d"}
    if schema is not None:
        tool["inputSchema"] = schema
    return {**tool, **extra}


def test_valid_read_args_execute_on_the_first_call(gate):
    _mgr, discover, _ = gate
    discover(_tool("fetch_emails"))
    assert _discovery_gate_verdict("mcp__drive__fetch_emails", {"query": "from:bank"}) == (
        True,
        "executed_external",
        None,
        True,
    )


def test_invalid_args_bounce_with_a_pointer_precise_problem(gate):
    _mgr, discover, _ = gate
    discover(_tool("fetch_emails"))
    for args in ({}, {"query": 5}, {"query": "x", "bogus": 1}):
        execute, outcome, problem, valid = _discovery_gate_verdict("mcp__drive__fetch_emails", args)
        assert (execute, outcome, valid) == (False, "bounced_invalid_args", False)
        assert problem and "fetch_emails" in problem


def test_no_discovered_schema_bounces_unchecked(gate):
    _mgr, discover, _ = gate
    discover(_tool("fetch_emails", schema=None))
    assert _discovery_gate_verdict("mcp__drive__fetch_emails", {"query": "x"}) == (
        False,
        "bounced_external",
        None,
        None,
    )
    assert _discovery_gate_verdict("mcp__drive__nope", {})[1] == "bounced_external"
    assert _discovery_gate_verdict("mcp__ghost__fetch_emails", {})[1] == "bounced_external"


def test_no_dispatcher_bounces(gate):
    mcp_external.register_external_dispatcher(None)
    assert _discovery_gate_verdict("mcp__drive__fetch_emails", {})[1] == "bounced_external"


def test_unusable_schema_bounces_unchecked(gate):
    _mgr, discover, _ = gate
    discover(_tool("fetch_emails", schema={"type": "not-a-type"}))
    verdict = _discovery_gate_verdict("mcp__drive__fetch_emails", {"query": "x"})
    assert verdict[:2] == (False, "bounced_external")


@pytest.mark.parametrize(
    "tool, extra",
    [
        ("send_email", {}),  # write-looking name
        ("place_order", {"annotations": {"readOnlyHint": True}}),  # a hint can't loosen
        ("fetch_emails", {"annotations": {"readOnlyHint": False}}),  # a hint can tighten
        ("fetch_emails", {"annotations": {"destructiveHint": True}}),
    ],
)
def test_write_classified_tools_never_execute_unseen(gate, tool, extra):
    _mgr, discover, _ = gate
    discover(_tool(tool, schema={"type": "object"}, **extra))
    execute, outcome, _problem, valid = _discovery_gate_verdict(f"mcp__drive__{tool}", {})
    assert (execute, outcome, valid) == (False, "bounced_write", True)


# ── permissions still govern the call the gate lets through ──────────────────────────


def _first_call(mgr, captured):
    assert _discovery_gate_verdict("mcp__drive__fetch_emails", {"query": "q"})[0] is True
    captured.pop("call_args", None)
    return asyncio.run(mgr.call_external_tool("mcp__drive__fetch_emails", {"query": "q"}))


def test_blocked_permission_still_refuses_after_the_gate_passes(gate):
    mgr, discover, captured = gate
    discover(_tool("fetch_emails"))
    _set_level("drive", "fetch_emails", "blocked")
    _text, proposed, _ = _first_call(mgr, captured)
    assert proposed is None and "call_args" not in captured


def test_needs_approval_still_stages_and_sends_nothing(gate):
    mgr, discover, captured = gate
    discover(_tool("fetch_emails"))
    assert tool_permissions.DEFAULT_LEVEL == "needs_approval"
    _text, proposed, _ = _first_call(mgr, captured)
    assert proposed is not None and "call_args" not in captured


def test_always_allow_fires(gate):
    mgr, discover, captured = gate
    discover(_tool("fetch_emails"))
    _set_level("drive", "fetch_emails", "always_allow")
    _text, proposed, _ = _first_call(mgr, captured)
    assert proposed is None and captured["call_args"]["arguments"] == {"query": "q"}
