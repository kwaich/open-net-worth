"""Security boundary tests for find_tools and external-MCP tool scoping.

These tests assert the structural boundary that prevents external MCP tools
(mcp__-prefixed) from leaking into headless or skill-scoped turns.

The boundary is enforced at two layers:
1. _active_tools() / _build_core_offer(): external MCP schemas are only
   appended when the name is already in the loaded set AND the turn is
   interactive (not headless, not skill-scoped).
2. _search_tools() in find_tools: checks _is_interactive_scope() before
   including external schemas in the search candidate pool. Fail-closed.
3. mcp/adapter.py::mcp_tool_descriptors(): derives from MCP_TOOL_NAMES
   (= HEADLESS_ALLOWED_TOOLS), independent of the external-MCP mechanism.
   Its output never contains mcp__-prefixed tools.

All tests are offline + isolated. ContextVars are reset between tests.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant
from backend.ai.tools import _skill_scope
from backend.ai.tools._extra_groups_scope import set_extra_groups, set_extra_names
from backend.ai.tools._groups import _is_interactive_scope, _search_tools

# A small explicit tool offer, standing in for what a headless caller now passes.
_SAMPLE_OFFER = {"get_net_worth", "get_transactions", "get_portfolio", "telegram_send_message"}

# ---------------------------------------------------------------------------
# Shared fake dispatcher that exposes one mcp__ tool
# ---------------------------------------------------------------------------

_FAKE_MCP_SCHEMA = {
    "name": "mcp__fake__tool",
    "description": "fake external mcp tool used in tests",
    "input_schema": {"type": "object", "properties": {}},
}


class _FakeDispatcher:
    def schemas(self):
        return [_FAKE_MCP_SCHEMA]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_scopes():
    """Reset all per-turn ContextVar state before/after each test."""
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())
    yield
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())


# ---------------------------------------------------------------------------
# 1. _is_interactive_scope: headless and skill scope are non-interactive
# ---------------------------------------------------------------------------


def test_is_interactive_scope_true_when_no_scope():
    """With no scope set, _is_interactive_scope() returns True."""
    assert _is_interactive_scope() is True


def test_is_interactive_scope_false_under_headless():
    """_is_interactive_scope() returns False when a headless scope is set."""
    _skill_scope.set_offer_scope(_SAMPLE_OFFER)
    assert _is_interactive_scope() is False


def test_is_interactive_scope_false_under_skill_scope():
    """_is_interactive_scope() returns False when a skill scope is set."""
    _skill_scope.set_offer_scope(["get_net_worth", "telegram_send_message"])
    assert _is_interactive_scope() is False


# ---------------------------------------------------------------------------
# 2. _active_tools does NOT include mcp__ tools under headless scope
# ---------------------------------------------------------------------------


def test_active_tools_excludes_mcp_under_headless_scope(monkeypatch):
    """Even if a mcp__ name is loaded, _active_tools excludes it under headless scope."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())
    # Load the mcp__ name via set_extra_names (as if find_tools had loaded it)
    set_extra_names(frozenset({"mcp__fake__tool"}))
    # Activate headless scope
    _skill_scope.set_offer_scope(_SAMPLE_OFFER)

    offered = {t["name"] for t in assistant._active_tools()}
    assert "mcp__fake__tool" not in offered, (
        "mcp__ tool must not appear in the headless offer, even when loaded in the extra-names set"
    )
    # Headless offer is still bounded by HEADLESS_ALLOWED_TOOLS
    assert offered <= _SAMPLE_OFFER


def test_active_tools_excludes_mcp_under_skill_scope(monkeypatch):
    """Even if a mcp__ name is loaded, _active_tools excludes it under skill scope."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())
    # Load the mcp__ name via set_extra_names
    set_extra_names(frozenset({"mcp__fake__tool"}))
    # Activate skill scope that explicitly lists the mcp__ name (the skill can't actually
    # broaden the offer to mcp__ tools because _build_core_offer is bypassed on skill path)
    _skill_scope.set_offer_scope(["get_net_worth", "mcp__fake__tool"])

    offered = {t["name"] for t in assistant._active_tools()}
    # Skill path: _active_tools uses _effective_scope_names(scope) and filters TOOL_SCHEMAS.
    # mcp__fake__tool is not in TOOL_SCHEMAS, so it can't appear.
    assert "mcp__fake__tool" not in offered, "mcp__ tool must not appear in the skill-scope offer"


# ---------------------------------------------------------------------------
# 3. _active_tools DOES include mcp__ tool under interactive scope (when loaded)
# ---------------------------------------------------------------------------


def test_active_tools_includes_mcp_under_interactive_scope_when_loaded(monkeypatch):
    """On the interactive path, a loaded mcp__ tool IS included in _active_tools."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())
    # Load the mcp__ name (simulates find_tools having searched and loaded it)
    set_extra_names(frozenset({"mcp__fake__tool"}))

    offered = {t["name"] for t in assistant._active_tools()}
    assert "mcp__fake__tool" in offered, (
        "mcp__ tool must appear on the interactive path when it is in the loaded set"
    )


def test_active_tools_does_not_include_unloaded_mcp_under_interactive_scope(monkeypatch):
    """A mcp__ tool NOT in the loaded set is absent from the interactive offer."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())
    # Do NOT load the mcp__ name — extra_names is empty

    offered = {t["name"] for t in assistant._active_tools()}
    assert "mcp__fake__tool" not in offered, (
        "mcp__ tool must NOT appear on the interactive path when not in the loaded set"
    )


# ---------------------------------------------------------------------------
# 4. _search_tools excludes mcp__ tools under headless scope
# ---------------------------------------------------------------------------


def test_search_tools_excludes_mcp_under_headless(monkeypatch):
    """_search_tools does not surface mcp__ tools when a headless scope is active."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())
    _skill_scope.set_offer_scope(_SAMPLE_OFFER)

    results = _search_tools("fake external mcp")
    assert "mcp__fake__tool" not in results, (
        "_search_tools must not surface mcp__ tools under headless scope (fail-closed)"
    )


def test_search_tools_excludes_mcp_under_skill_scope(monkeypatch):
    """_search_tools does not surface mcp__ tools when a skill scope is active."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())
    _skill_scope.set_offer_scope(["get_net_worth"])

    results = _search_tools("fake external mcp")
    assert "mcp__fake__tool" not in results, (
        "_search_tools must not surface mcp__ tools under skill scope (fail-closed)"
    )


def test_search_tools_surfaces_mcp_under_interactive_scope(monkeypatch):
    """_search_tools surfaces mcp__ tools when the turn is interactive."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())

    results = _search_tools("fake external mcp")
    assert "mcp__fake__tool" in results, (
        "_search_tools should surface mcp__ tools on the interactive path"
    )


# ---------------------------------------------------------------------------
# 5. mcp_tool_descriptors is unaffected by the external-MCP mechanism
# ---------------------------------------------------------------------------


def test_mcp_tool_descriptors_unaffected_by_find_tools_model():
    """mcp_tool_descriptors() derives from the registered TOOL_SCHEMAS.

    It is independent of the CORE_TOOL_NAMES / find_tools / extra_names model.
    Critically, it never contains mcp__-prefixed tools (those are external to the
    instance, not part of the registered TOOL_SCHEMAS) — the INBOUND surface must never
    leak the OUTBOUND external-MCP tools, which is a trust-boundary property, not an
    authorization tier, so removing inbound tiering leaves it untouched.
    """
    from backend.mcp.adapter import mcp_tool_descriptors

    result = mcp_tool_descriptors()
    result_names = {t["name"] for t in result}

    # No mcp__-prefixed tools ever appear in mcp_tool_descriptors
    mcp_prefixed = {n for n in result_names if n.startswith("mcp__")}
    assert mcp_prefixed == set(), (
        f"mcp_tool_descriptors returned mcp__-prefixed names: {mcp_prefixed}"
    )

    # All tool names come from the registered TOOL_SCHEMAS (internal tools only)
    from backend.ai.tools import TOOL_SCHEMAS

    all_internal = {t["name"] for t in TOOL_SCHEMAS}
    assert result_names <= all_internal, (
        "mcp_tool_descriptors returned names outside the internal TOOL_SCHEMAS registry"
    )


def test_mcp_tool_descriptors_not_affected_by_extra_names(monkeypatch):
    """Loading mcp__ names via set_extra_names doesn't change mcp_tool_descriptors output."""
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: _FakeDispatcher())
    # Simulate find_tools having loaded an external mcp tool
    set_extra_names(frozenset({"mcp__fake__tool"}))

    from backend.mcp.adapter import mcp_tool_descriptors

    result_names = {t["name"] for t in mcp_tool_descriptors()}
    assert "mcp__fake__tool" not in result_names, (
        "mcp_tool_descriptors must not include externally-loaded mcp__ tools"
    )
