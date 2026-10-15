"""Tests for find_tools search-quality telemetry (ai/tools/discovery_log.py).

Covers: classification (miss / core_only / ok), the greppable WARNING marker on a
miss, the capped JSONL ring, the recent()/miss_rate() readers, and that the live
find_tools handler records a miss for a vocabulary-gap query.
"""

from __future__ import annotations

import logging

import pytest

from backend.ai.tools import discovery_log


@pytest.fixture
def tmp_log(db_isolated):
    """The telemetry ring is DB-backed now (Wave 5B: JSON->DB, ToolDiscoveryLogStore /
    the tool_discovery_log table) — db_isolated gives it a fresh, isolated sqlite file;
    read events back via the store, not a file."""
    from backend.db.tool_discovery_log_store import ToolDiscoveryLogStore

    return ToolDiscoveryLogStore()


def test_classify_miss_core_only_ok():
    assert discovery_log._classify([], []) == "miss"
    assert discovery_log._classify(["get_portfolio"], []) == "core_only"
    assert discovery_log._classify(["simulate_retirement"], ["simulate_retirement"]) == "ok"


def test_miss_is_persisted_and_logged(tmp_log, caplog):
    with caplog.at_level(logging.WARNING, logger="backend.ai.tools.discovery_log"):
        discovery_log.record_query("does not match anything", [], [])
    # Greppable WARNING marker emitted.
    assert any("find_tools_miss" in r.message for r in caplog.records)
    # Persisted event with kind=miss.
    events = tmp_log.recent(50)
    assert len(events) == 1
    assert events[0]["kind"] == "miss"
    assert events[0]["query"] == "does not match anything"
    assert events[0]["hits"] == []


def test_core_only_and_ok_persisted(tmp_log):
    discovery_log.record_query("portfolio", ["get_portfolio"], [])
    discovery_log.record_query("retire", ["simulate_retirement"], ["simulate_retirement"])
    kinds = [e["kind"] for e in tmp_log.recent(50)]
    assert kinds == ["core_only", "ok"]


def test_ring_is_capped(tmp_log, monkeypatch):
    # _MAX_EVENTS moved with the ring itself (Wave 5B: JSON->DB) — it's now
    # ToolDiscoveryLogStore's module-level MAX_EVENTS, consulted fresh inside
    # append() on every call, so patching the module attribute is enough.
    monkeypatch.setattr("backend.db.tool_discovery_log_store.MAX_EVENTS", 5)
    for i in range(8):
        discovery_log.record_query(f"q{i}", ["simulate_retirement"], ["simulate_retirement"])
    events = tmp_log.recent(50)
    assert len(events) == 5
    # Oldest evicted — last 5 remain in order.
    assert [e["query"] for e in events] == ["q3", "q4", "q5", "q6", "q7"]


def test_recent_and_miss_rate(tmp_log):
    discovery_log.record_query("a", [], [])  # miss
    discovery_log.record_query("b", ["simulate_retirement"], ["simulate_retirement"])  # ok
    discovery_log.record_query("c", ["get_portfolio"], [])  # core_only
    assert len(discovery_log.recent(50)) == 3
    summary = discovery_log.miss_rate()
    assert summary["total"] == 3
    assert summary["by_kind"]["miss"] == 1
    assert summary["miss_rate"] == pytest.approx(1 / 3)


def test_record_never_raises_on_bad_path(monkeypatch):
    """Telemetry must never break find_tools — a broken persistence path is swallowed.

    The ring moved to the tool_discovery_log table (Wave 5B: JSON->DB); the old
    unwritable-file-path simulation no longer has any effect (nothing reads
    backend.paths.TOOL_DISCOVERY_LOG_JSON anymore), so this breaks the DB write
    itself instead — same guarantee (record_query's try/except in _append_event),
    the real remaining failure mode.
    """
    from backend.db.tool_discovery_log_store import ToolDiscoveryLogStore

    def boom(self, event):
        raise RuntimeError("simulated db write failure")

    monkeypatch.setattr(ToolDiscoveryLogStore, "append", boom)
    # Should not raise despite the broken persistence layer.
    discovery_log.record_query("q", [], [])


@pytest.mark.asyncio
async def test_find_tools_records_miss_for_vocabulary_gap(tmp_log):
    """The live find_tools handler records a miss when a query matches nothing."""
    from backend.ai.tools._extra_groups_scope import set_extra_groups, set_extra_names
    from backend.ai.tools._groups import find_tools

    set_extra_groups(frozenset())
    set_extra_names(frozenset())
    await find_tools({"query": "zzzznotarealcapabilityxyz"})
    events = tmp_log.recent(50)
    assert events, "find_tools should have recorded a query event"
    assert events[-1]["kind"] == "miss"
    assert events[-1]["query"] == "zzzznotarealcapabilityxyz"
