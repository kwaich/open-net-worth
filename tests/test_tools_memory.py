"""Unit tests for backend/ai/tools/memory.py.

Covers the four tool handlers — remember_fact, forget_fact, agent_remember_fact,
agent_forget_fact — with an emphasis on:

  • Global memory toggle gating (OFF → refuse without writing, ON → succeed)
  • Per-agent memory toggle: effective AND of global × agent setting
  • Happy paths with real store writes verified
  • No-active-agent guard for agent-scoped tools
  • Memory disabled for agent guard
  • forget_fact / agent_forget_fact partial-match logic

All tests are isolated to tmp_path via monkeypatching. No LLM or network calls.
forget_fact requires an LLM call (remove_fact is async + calls provider.complete);
we monkeypatch _provider in backend.ai.memory so no real LLM is hit.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

import backend.ai.memory as memory_mod
import backend.db.memory_settings_store as settings_store_mod
import backend.db.memory_store as memory_store_mod

# ── helpers ────────────────────────────────────────────────────────────────────


def _call_tool(tool_name: str, **inp):
    """Execute an AI tool by name and return (parsed_result, proposed_change)."""
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool(tool_name, inp))
    return json.loads(text), proposed


def _make_fact(content: str, fid: str | None = None) -> dict:
    now = "2026-01-01T00:00:00"
    return {
        "id": fid or str(uuid.uuid4()),
        "content": content,
        "tier": "durable",
        "source": "explicit",
        "expires_at": None,
        "created_at": now,
        "updated_at": now,
    }


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated(tmp_path, db_isolated):
    """Redirect shared-memory stores to a fresh tmp directory."""
    return tmp_path


class _FakeProvider:
    def __init__(self, target_id: str = "none"):
        self.target_id = target_id
        self.call_count = 0

    async def complete(self, *a, **kw):
        self.call_count += 1
        return json.dumps({"id": self.target_id})


# ══════════════════════════════════════════════════════════════════════════════
# remember_fact
# ══════════════════════════════════════════════════════════════════════════════


class TestRememberFact:
    def test_blocked_when_global_memory_off(self, isolated):
        """remember_fact must return ok=False with a message when memory is OFF."""
        settings_store_mod.MemorySettingsStore().set_enabled(False)
        result, proposed = _call_tool("remember_fact", content="Wants to retire at 50")
        assert result["ok"] is False
        assert "message" in result
        assert proposed is None

    def test_blocked_does_not_write(self, isolated):
        """When memory is OFF, no fact must be written to the store."""
        settings_store_mod.MemorySettingsStore().set_enabled(False)
        _call_tool("remember_fact", content="Wants to retire at 50")
        assert memory_store_mod.MemoryStore().all_facts() == []

    def test_succeeds_when_global_memory_on(self, isolated):
        """remember_fact must return ok=True and the saved content when memory is ON."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        result, proposed = _call_tool("remember_fact", content="Prefers SGD as base currency")
        assert result["ok"] is True
        assert "saved" in result
        assert "SGD" in result["saved"]
        assert proposed is None

    def test_writes_to_store_when_on(self, isolated):
        """Fact must be retrievable from the MemoryStore after remember_fact."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        _call_tool("remember_fact", content="Owns rental property in Johor Bahru")
        facts = memory_store_mod.MemoryStore().all_facts()
        assert any("Johor Bahru" in f.get("content", "") for f in facts)

    def test_durable_tier_default(self, isolated):
        """No tier argument → fact is stored with tier=durable."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        _call_tool("remember_fact", content="Risk tolerance is moderate")
        facts = memory_store_mod.MemoryStore().all_facts()
        assert facts[0]["tier"] == "durable"

    def test_time_bound_tier_stored(self, isolated):
        """tier=time_bound is persisted correctly."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        _call_tool(
            "remember_fact",
            content="Expects to sell ONWAI after Q3 vest",
            tier="time_bound",
            expires_at="2026-09-30",
        )
        facts = memory_store_mod.MemoryStore().all_facts()
        assert facts[0]["tier"] == "time_bound"
        assert facts[0]["expires_at"] == "2026-09-30"

    def test_multiple_facts_accumulate(self, isolated):
        """Calling remember_fact twice must result in two stored facts."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        _call_tool("remember_fact", content="Fact one")
        _call_tool("remember_fact", content="Fact two")
        assert len(memory_store_mod.MemoryStore().all_facts()) == 2


# ══════════════════════════════════════════════════════════════════════════════
# forget_fact
# ══════════════════════════════════════════════════════════════════════════════


class TestForgetFact:
    def test_blocked_when_global_memory_off(self, isolated):
        """forget_fact must return ok=False with a message when memory is OFF."""
        settings_store_mod.MemorySettingsStore().set_enabled(False)
        # Pre-populate a fact that would otherwise be a match.
        memory_store_mod.MemoryStore().insert(_make_fact("risk tolerance is moderate", "f1"))
        result, proposed = _call_tool("forget_fact", description="risk tolerance")
        assert result["ok"] is False
        assert "message" in result
        assert proposed is None

    def test_blocked_does_not_remove_fact(self, isolated):
        """When memory is OFF, the pre-existing fact must not be removed."""
        settings_store_mod.MemorySettingsStore().set_enabled(False)
        memory_store_mod.MemoryStore().insert(_make_fact("risk tolerance is moderate", "f1"))
        _call_tool("forget_fact", description="risk tolerance")
        assert any(f["id"] == "f1" for f in memory_store_mod.MemoryStore().all_facts())

    def test_succeeds_and_removes_fact(self, isolated, monkeypatch):
        """forget_fact returns ok=True when memory is ON and the LLM identifies a match."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        memory_store_mod.MemoryStore().insert(_make_fact("risk tolerance is moderate", "target-1"))

        fake = _FakeProvider(target_id="target-1")
        monkeypatch.setattr(memory_mod, "_provider", lambda: fake)

        result, proposed = _call_tool("forget_fact", description="risk tolerance")
        assert result["ok"] is True
        assert proposed is None
        # Fact must actually be gone.
        remaining = memory_store_mod.MemoryStore().all_facts()
        assert not any(f["id"] == "target-1" for f in remaining)

    def test_returns_false_when_no_match(self, isolated, monkeypatch):
        """forget_fact returns ok=False when the LLM returns 'none'."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        memory_store_mod.MemoryStore().insert(_make_fact("something unrelated", "f2"))

        monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProvider(target_id="none"))

        result, proposed = _call_tool("forget_fact", description="totally unknown fact")
        assert result["ok"] is False

    def test_no_facts_returns_false(self, isolated, monkeypatch):
        """forget_fact returns ok=False when the store is empty."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProvider())

        result, _ = _call_tool("forget_fact", description="anything")
        assert result["ok"] is False


# ══════════════════════════════════════════════════════════════════════════════
# agent_remember_fact
# ══════════════════════════════════════════════════════════════════════════════
