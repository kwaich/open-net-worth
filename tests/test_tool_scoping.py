"""Tests for the default-slim interactive tool offer + the find_tools escape hatch.

Tool GROUPS are gone (the hand-maintained TOOL_GROUPS dict and the ``groups=``
load path were removed to match Claude Code's ToolSearch shape). What survives —
and is pinned here — is the model that actually governs the offer:

- The default interactive offer is exactly CORE_TOOL_NAMES.
- ``rare_tool_names()`` (registry - CORE) never leaks into that offer.
- ``find_tools(query=…)`` loads individual NAMES → _active_tools includes them
  within the same ContextVar context.
- Session-meta persistence round-trip for ``extra_tool_names``.
- ONW_FULL_TOOL_OFFER=1 restores the full offer.
- Headless / skill scope offers are UNCHANGED by anything find_tools loaded.
- External schema sort (deterministic order).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import assistant
from backend.ai.tools import TOOL_SCHEMAS, _skill_scope
from backend.ai.tools._extra_groups_scope import (
    get_extra_names,
    set_extra_names,
)
from backend.ai.tools._groups import CORE_TOOL_NAMES, rare_tool_names

# A small explicit tool offer, standing in for what a headless caller now passes.
_SAMPLE_OFFER = {"get_net_worth", "get_transactions", "get_portfolio", "telegram_send_message"}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_scopes():
    """Reset all per-turn ContextVar state before/after each test."""
    _skill_scope.clear_scope()
    set_extra_names(frozenset())
    yield
    _skill_scope.clear_scope()
    set_extra_names(frozenset())


# ---------------------------------------------------------------------------
# 1. The derived rare set
# ---------------------------------------------------------------------------


def test_rare_tool_names_is_registry_minus_core():
    """'Rare' is DERIVED, not curated: exactly registry - CORE_TOOL_NAMES."""
    live = frozenset(t["name"] for t in TOOL_SCHEMAS)
    assert rare_tool_names() == live - CORE_TOOL_NAMES


def test_rare_and_core_are_disjoint_by_construction():
    assert rare_tool_names() & CORE_TOOL_NAMES == frozenset()


def test_rare_and_core_partition_the_registry():
    """Every registered tool is either core or rare — no tool falls through the gap.

    Under the old TOOL_GROUPS model 124 of 286 tools were in neither set; the
    derivation closes that hole by construction.
    """
    live = frozenset(t["name"] for t in TOOL_SCHEMAS)
    assert rare_tool_names() | CORE_TOOL_NAMES == live


# ---------------------------------------------------------------------------
# 2. Default interactive offer
# ---------------------------------------------------------------------------


def test_default_offer_excludes_rare_tools():
    offered = {t["name"] for t in assistant._active_tools()}
    leaked = offered & rare_tool_names()
    assert leaked == set(), f"rare tools in default offer: {sorted(leaked)}"


def test_default_offer_includes_find_tools():
    """find_tools must always be offered so the model can discover the rest."""
    offered = {t["name"] for t in assistant._active_tools()}
    assert "find_tools" in offered


def test_default_offer_is_exactly_core_tool_names():
    offered = {t["name"] for t in assistant._active_tools()}
    assert offered == CORE_TOOL_NAMES, (
        f"Default offer deviates from CORE_TOOL_NAMES.\n"
        f"Extra: {sorted(offered - CORE_TOOL_NAMES)}\n"
        f"Missing: {sorted(CORE_TOOL_NAMES - offered)}"
    )


@pytest.mark.parametrize(
    "tool_name",
    ["create_agent", "email_send_message", "get_model_config", "create_skill", "run_code"],
)
def test_default_offer_excludes_admin_tools(tool_name):
    offered = {t["name"] for t in assistant._active_tools()}
    assert tool_name not in offered


# ---------------------------------------------------------------------------
# 3. find_tools(query=...) loads names → _active_tools includes them
# ---------------------------------------------------------------------------


async def _acall(tool_name, **inp):
    from backend.ai.tools import execute_tool

    text, proposed = await execute_tool(tool_name, inp)
    return json.loads(text), proposed


def test_find_tools_query_loads_name_into_offer():
    async def _inner():
        before = {t["name"] for t in assistant._active_tools()}
        assert "simulate_retirement" not in before

        res, proposed = await _acall("find_tools", query="retirement simulation")
        assert proposed is None
        loaded_names = {item["name"] for item in res["loaded"]}
        assert "simulate_retirement" in loaded_names

        after = {t["name"] for t in assistant._active_tools()}
        assert "simulate_retirement" in after

    asyncio.run(_inner())


def test_find_tools_query_is_additive_across_calls():
    async def _inner():
        await _acall("find_tools", query="retirement simulation")
        # 'tax loss harvest' was dropped (Task 87 — the tax tools are catalog-hidden and
        # deliberately unreachable via find_tools).
        await _acall("find_tools", query="realized gains losses")
        offered = {t["name"] for t in assistant._active_tools()}
        assert "simulate_retirement" in offered
        assert "get_realized_pnl" in offered

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# 4. Session meta persistence round-trip (extra_tool_names)
# ---------------------------------------------------------------------------


def test_upsert_session_meta_stores_extra_tool_names(tmp_path, monkeypatch):
    # sessions is DB-backed now (Wave 5E, JSON->DB) and isolated per test by conftest's
    # autouse Tier-2 table clear — no per-store monkeypatch needed any more. The old
    # process-local _INDEX_LOCK is retired too (one sessions-row upsert is its own atomic
    # DB transaction — db/session_store.py's own docstring).
    sid = "test-session-names"
    assistant.upsert_session_meta(
        sid, title="Test", extra_tool_names=["simulate_retirement", "get_rebalance_plan"]
    )

    idx = assistant._read_index()
    entry = next((e for e in idx if e["id"] == sid), None)
    assert entry is not None
    assert set(entry["extra_tool_names"]) == {"simulate_retirement", "get_rebalance_plan"}


def test_upsert_session_meta_updates_extra_tool_names(tmp_path, monkeypatch):
    sid = "test-session-names-update"
    assistant.upsert_session_meta(sid, extra_tool_names=["simulate_retirement"])
    assistant.upsert_session_meta(sid, extra_tool_names=["simulate_retirement", "get_report"])

    idx = assistant._read_index()
    entry = next(e for e in idx if e["id"] == sid)
    assert set(entry["extra_tool_names"]) == {"simulate_retirement", "get_report"}


def test_old_meta_without_extra_tool_names_parses_fine(tmp_path, monkeypatch):
    """Old session meta entries (no extra_tool_names key) load cleanly as empty."""
    from backend.db.session_store import SessionStore

    # Seed a legacy-shaped meta row directly (bypasses upsert_session_meta's own field
    # list) — the DB-era way to simulate "a session meta entry predating this field".
    SessionStore().upsert_meta(
        "old-session", {"title": "Old", "created_at": "x", "updated_at": "x"}
    )
    idx = assistant._read_index()
    entry = next(e for e in idx if e["id"] == "old-session")
    assert entry.get("extra_tool_names") is None  # missing field → safe default []
    assert (entry.get("extra_tool_names") or []) == []


# ---------------------------------------------------------------------------
# 5. ONW_FULL_TOOL_OFFER=1 restores full offer
# ---------------------------------------------------------------------------


def test_full_tool_offer_env_restores_all_tools(monkeypatch):
    monkeypatch.setenv("ONW_FULL_TOOL_OFFER", "1")
    offered = assistant._active_tools()
    # Must equal TOOL_SCHEMAS exactly (same objects in same order)
    assert offered == TOOL_SCHEMAS


def test_full_tool_offer_absent_gives_slim(monkeypatch):
    monkeypatch.delenv("ONW_FULL_TOOL_OFFER", raising=False)
    offered = {t["name"] for t in assistant._active_tools()}
    assert rare_tool_names() & offered == set()


# ---------------------------------------------------------------------------
# 6. Headless / skill scope offers UNCHANGED by loaded names
# ---------------------------------------------------------------------------


def test_headless_scope_unaffected_by_rare_logic():
    """Headless scope takes priority; the offered set is exactly HEADLESS_ALLOWED_TOOLS."""
    _skill_scope.set_offer_scope(_SAMPLE_OFFER)
    offered = {t["name"] for t in assistant._active_tools()}
    assert offered == _SAMPLE_OFFER


def test_headless_scope_unaffected_even_with_names_loaded():
    """Even with every rare tool loaded by name, headless takes priority and never widens."""
    set_extra_names(rare_tool_names())
    _skill_scope.set_offer_scope(_SAMPLE_OFFER)
    offered = {t["name"] for t in assistant._active_tools()}
    assert offered == _SAMPLE_OFFER


def test_skill_scope_unaffected_by_loaded_names():
    """Skill scope path is also independent of anything find_tools loaded."""
    set_extra_names(rare_tool_names())
    _skill_scope.set_offer_scope(["get_net_worth", "telegram_send_message"])
    offered = {t["name"] for t in assistant._active_tools()}
    assert offered <= {"get_net_worth", "telegram_send_message"} | {
        "list_skills",
        "activate_skill",
        "create_skill",
        "update_skill",
        "delete_skill",
        "apply_skill_change",
    }


# ---------------------------------------------------------------------------
# 7. Extra-names ContextVar mechanics
# ---------------------------------------------------------------------------


def test_extra_names_starts_empty():
    set_extra_names(frozenset())
    assert get_extra_names() == frozenset()


# ---------------------------------------------------------------------------
# 8. External schema sort (deterministic order)
# ---------------------------------------------------------------------------


def test_external_tool_schemas_sorted(monkeypatch):
    """_external_tool_schemas() output is sorted by tool name."""
    unsorted_schemas = [
        {"name": "mcp__z__tool", "description": "z", "input_schema": {}},
        {"name": "mcp__a__tool", "description": "a", "input_schema": {}},
        {"name": "mcp__m__tool", "description": "m", "input_schema": {}},
    ]

    class FakeDispatcher:
        def schemas(self):
            return unsorted_schemas

    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: FakeDispatcher())

    result = assistant._external_tool_schemas()
    names = [s["name"] for s in result]
    assert names == sorted(names), f"external schemas not sorted: {names}"


def test_external_tool_schemas_empty_when_no_dispatcher(monkeypatch):
    import backend.core.mcp_external as mcp_ext

    monkeypatch.setattr(mcp_ext, "get_external_dispatcher", lambda: None)
    assert assistant._external_tool_schemas() == []
