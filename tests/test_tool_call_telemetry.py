"""Per-tool-call telemetry — the record, the seam, the bounding, and the safety rails.

Covers the four properties the instrument is worthless without:
  * a call from EVERY surface is recorded, with the right origin;
  * the rolling cap holds (it fires on every tool call — unbounded would be a bug);
  * a telemetry failure NEVER fails a tool call;
  * nothing sensitive is written — sizes and shapes only, no argument values, no result
    bodies, no error messages.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import call_log
from backend.ai.tools.registry import _REGISTRY, Tool, dispatch
from backend.db import tool_call_log_store as store_mod
from backend.db.tool_call_log_store import MAX_TOOL_CALLS, ToolCallLogStore


@pytest.fixture
def telemetry(db_isolated, monkeypatch):
    """An isolated ring + a drained buffer, with the background flusher kept out of it.

    Tests drive ``flush_now()`` explicitly so assertions never race the daemon thread.
    """
    monkeypatch.setattr(call_log, "_ensure_flusher", lambda: None)
    call_log._drain()
    call_log._turn_counts.clear()
    call_log._turn_state.clear()
    yield ToolCallLogStore
    call_log._drain()


@pytest.fixture
def fake_tools(monkeypatch):
    """Register throwaway tools: a read, a writer, and one that raises."""

    async def _read(_input):
        return json.dumps({"rows": [1, 2, 3]}), None

    async def _write(_input):
        return json.dumps({"ok": True}), None

    async def _boom(_input):
        raise RuntimeError("account 9021 balance 12,345.67 SGD")

    monkeypatch.setitem(_REGISTRY, "tl_read", Tool("tl_read", "", {}, _read))
    monkeypatch.setitem(_REGISTRY, "tl_write", Tool("tl_write", "", {}, _write, ("ledger",)))
    monkeypatch.setitem(_REGISTRY, "tl_boom", Tool("tl_boom", "", {}, _boom))
    return None


def _call(name, payload=None):
    return asyncio.run(dispatch(name, payload if payload is not None else {}))


# ── The record ───────────────────────────────────────────────────────────────────────


def test_records_one_row_per_dispatch_with_sizes(telemetry, fake_tools):
    _call("tl_read", {"limit": 50})
    call_log.flush_now()

    rows = telemetry().all_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "tl_read"
    assert row["ok"] is True
    assert row["error_class"] is None
    assert row["mutated"] is False
    assert row["domains"] == []
    assert row["result_chars"] == len(json.dumps({"rows": [1, 2, 3]}, separators=(",", ":")))
    assert row["input_chars"] == len(json.dumps({"limit": 50}))
    assert row["duration_ms"] >= 0.0


def test_write_records_domains_and_mutated_flag(telemetry, fake_tools):
    _call("tl_write", {})
    call_log.flush_now()

    row = telemetry().all_rows()[0]
    assert row["mutated"] is True
    assert row["domains"] == ["ledger"]


def test_the_invoked_name_is_recorded_alongside_the_canonical_one(telemetry, fake_tools):
    """There are no aliases any more (ADR 2026-08-25-tool-names-are-final), so the two
    columns agree — but the pair is kept, because a tool that ABSORBED an older one still
    needs to record which shape it was asked for."""
    _call("tl_read", {})
    call_log.flush_now()

    row = telemetry().all_rows()[0]
    assert row["tool"] == "tl_read", "aggregation must key on the canonical name"
    assert row["alias_used"] in (None, "", "tl_read")


def test_unknown_tool_and_validation_failures_are_classified(telemetry, fake_tools, monkeypatch):
    from backend.ai.tools import registry

    _call("tl_no_such_tool", {})

    schema = {"type": "object", "properties": {"amount": {"type": "number"}}}
    monkeypatch.setitem(
        _REGISTRY, "tl_typed", Tool("tl_typed", "", schema, _REGISTRY["tl_read"].handler)
    )
    registry._validator_for.cache_clear()
    _call("tl_typed", {"amount": "twelve dollars"})
    call_log.flush_now()

    classes = {r["tool"]: r["error_class"] for r in telemetry().all_rows()}
    assert classes["tl_no_such_tool"] == "unknown_tool"
    assert classes["tl_typed"] == "validation"
    # The handler never ran on either path, so nothing can have been written.
    assert all(r["domains"] == [] for r in telemetry().all_rows())


def test_handler_exception_is_recorded_and_re_raised(telemetry, fake_tools):
    with pytest.raises(RuntimeError):
        _call("tl_boom", {})
    call_log.flush_now()

    row = telemetry().all_rows()[0]
    assert row["ok"] is False
    assert row["error_class"] == "exception:RuntimeError"


# ── Surfaces ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("ui", "web"),
        ("chat:web", "web"),
        ("chat:telegram", "connector:telegram"),
        ("chat:slack", "connector:slack"),
        ("mcp:api_token", "mcp"),
        ("mcp:server:abc", "mcp"),
        ("agent:agt_123", "agent:agt_123"),
        ("recurring:task_9", "scheduled"),
        ("import:csv", "import"),
    ],
)
def test_each_surface_records_its_own_origin(telemetry, fake_tools, surface, expected):
    from backend.core.audit_context import clear_audit_scope, set_audit_scope

    token = set_audit_scope("ai", surface)
    try:
        _call("tl_read", {})
    finally:
        clear_audit_scope(token)
    call_log.flush_now()

    assert telemetry().all_rows()[0]["origin"] == expected


def test_uninstrumented_path_records_unknown_not_a_guess(telemetry, fake_tools):
    from backend.core.audit_context import clear_audit_scope

    clear_audit_scope()
    _call("tl_read", {})
    call_log.flush_now()

    assert telemetry().all_rows()[0]["origin"] == "unknown"


def test_inbound_mcp_read_is_attributed_through_the_real_adapter(telemetry, fake_tools):
    """Regression: MCP READS returned before any scope was set, recording 'unknown'.

    Driven through ``mcp.adapter.call_tool`` rather than by setting the scope by hand —
    setting it by hand is exactly what hid this gap, on the surface the task singled out
    as the one a seam must not miss.
    """
    from backend.mcp import adapter

    asyncio.run(adapter.call_tool("tl_read", {}, surface="api_token"))
    call_log.flush_now()

    row = telemetry().all_rows()[0]
    assert row["origin"] == "mcp"
    assert row["mutated"] is False


def test_inbound_mcp_write_is_attributed_and_carries_its_checkpoint(telemetry, fake_tools):
    from backend.mcp import adapter

    asyncio.run(adapter.call_tool("tl_write", {}, surface="api_token"))
    call_log.flush_now()

    row = telemetry().all_rows()[0]
    assert row["origin"] == "mcp"
    assert row["mutated"] is True
    assert row["domains"] == ["ledger"]
    assert row["checkpoint_id"], "a mutating call must record the checkpoint it is undoable from"


def test_skill_scope_wins_over_the_hosting_surface(telemetry, fake_tools):
    from backend.ai.tools import _skill_scope
    from backend.core.audit_context import clear_audit_scope, set_audit_scope

    token = set_audit_scope("ai", "chat:web")
    snapshot = _skill_scope.snapshot_scope()
    try:
        _skill_scope.set_offer_scope(["tl_read"])
        _skill_scope.set_active_bundle({"skill_id": "skl_42"})
        _call("tl_read", {})
    finally:
        _skill_scope.restore_scope(snapshot)
        clear_audit_scope(token)
    call_log.flush_now()

    assert telemetry().all_rows()[0]["origin"] == "skill:skl_42"


@pytest.mark.parametrize("model_invoked", [False, True], ids=["direct", "automatic"])
def test_plain_inline_skill_activation_is_attributed_to_skill_origin(
    telemetry, fake_tools, model_invoked
):
    from backend.ai.tools import _skill_scope
    from backend.ai.tools.skills import activate_skill_scope
    from backend.core.audit_context import clear_audit_scope, set_audit_scope

    token = set_audit_scope("ai", "chat:web")
    snapshot = _skill_scope.snapshot_scope()
    try:
        activate_skill_scope(
            {
                "id": f"plain-{'automatic' if model_invoked else 'direct'}",
                "allowed_tools": ["tl_read"],
            },
            model_invoked=model_invoked,
        )
        _call("tl_read", {})
    finally:
        _skill_scope.restore_scope(snapshot)
        clear_audit_scope(token)
    call_log.flush_now()

    assert telemetry().all_rows()[0]["origin"] == "skill"


# ── Turn grouping ────────────────────────────────────────────────────────────────────


def test_turn_and_call_indexes_group_a_sequence(telemetry, fake_tools):
    from backend.core.chat_session import clear_session_id, set_session_id

    set_session_id("sess_a")
    try:
        call_log.begin_turn("sess_a")
        _call("tl_read", {})
        _call("tl_write", {})
        call_log.begin_turn("sess_a")
        _call("tl_read", {})
    finally:
        clear_session_id()
    call_log.flush_now()

    rows = sorted(telemetry().all_rows(), key=lambda r: (r["turn_index"], r["call_index"]))
    assert [(r["turn_index"], r["call_index"]) for r in rows] == [(0, 0), (0, 1), (1, 0)]


def test_a_surface_with_no_turn_is_standalone_not_a_fabricated_turn(telemetry, fake_tools):
    _call("tl_read", {})
    call_log.flush_now()

    row = telemetry().all_rows()[0]
    assert row["turn_index"] == -1
    assert row["call_index"] == 0


# ── Bounding ─────────────────────────────────────────────────────────────────────────


def test_ring_is_capped_and_keeps_the_newest(telemetry):
    store = telemetry()
    store.append_many([{"tool": f"t{i}"} for i in range(MAX_TOOL_CALLS + 250)])

    rows = store.all_rows()
    assert len(rows) == MAX_TOOL_CALLS
    # append_many receives oldest-first and stores newest-first, so the LAST input row
    # must be the first stored row and the earliest ones must have been evicted.
    assert rows[0]["tool"] == f"t{MAX_TOOL_CALLS + 249}"
    assert not any(r["tool"] == "t0" for r in rows)


def test_in_memory_buffer_is_bounded(telemetry, fake_tools, monkeypatch):
    """A store that cannot be written must not grow the process without bound."""
    monkeypatch.setattr(call_log, "_MAX_BUFFERED", 10)
    monkeypatch.setattr(call_log, "_buffer", type(call_log._buffer)(maxlen=10))
    for _ in range(50):
        _call("tl_read", {})
    assert len(call_log._buffer) <= 10


# ── Safety rails ─────────────────────────────────────────────────────────────────────


def test_telemetry_failure_never_fails_the_tool_call(telemetry, fake_tools, monkeypatch):
    def _explode(**_kwargs):
        raise RuntimeError("telemetry backend is down")

    monkeypatch.setattr(call_log, "record_call", _explode)

    text, proposed = _call("tl_read", {})
    assert json.loads(text) == {"rows": [1, 2, 3]}
    assert proposed is None


def test_a_broken_store_re_buffers_rather_than_discarding(telemetry, fake_tools, monkeypatch):
    _call("tl_read", {})
    monkeypatch.setattr(
        store_mod.ToolCallLogStore,
        "append_many",
        lambda self, rows: (_ for _ in ()).throw(OSError("read-only fs")),
    )
    assert call_log.flush_now() == 0
    assert len(call_log._buffer) == 1, "a failed write must not silently drop the measurement"


def test_no_argument_values_or_result_bodies_are_persisted(telemetry, fake_tools):
    """The load-bearing privacy test: real financial values must never reach the ring."""
    secret_payload = {
        "account_id": "acct_private_9021",
        "amount": 41233.19,
        "payee": "Dr Weber Oncology",
        "api_key": "sk-live-do-not-log",
        "note": "biopsy co-pay",
    }
    _call("tl_write", secret_payload)
    with pytest.raises(RuntimeError):
        _call("tl_boom", secret_payload)
    call_log.flush_now()

    raw = json.dumps(telemetry().all_rows())
    for leaked in (
        "acct_private_9021",
        "41233.19",
        "Dr Weber Oncology",
        "sk-live-do-not-log",
        "biopsy",
        "balance",  # the exception message text
    ):
        assert leaked not in raw, f"{leaked!r} leaked into the telemetry ring"

    # ...while the SIZE of that same payload is recorded, which is the whole point.
    assert all(r["input_chars"] == len(json.dumps(secret_payload)) for r in telemetry().all_rows())


def test_the_ring_is_checkpoint_excluded_and_not_portable():
    """Runtime telemetry: a rollback must not rewrite it, an export must not ship it."""
    from backend.ai.checkpoint import _EXCLUDED_PATHS
    from backend.core.paths import TOOL_CALL_LOG_JSON
    from backend.core.portable import PORTABLE_FILES

    assert TOOL_CALL_LOG_JSON.resolve() in _EXCLUDED_PATHS
    assert TOOL_CALL_LOG_JSON not in PORTABLE_FILES


def test_the_ring_table_is_checkpoint_excluded_and_not_bridged_portable():
    """DB mirror of the test above (plan 4c): tool_call_log is now DB-backed
    (``PayloadTableStore``) — ``ai.checkpoint._EXCLUDED_TABLES`` is the table-level
    counterpart to ``_EXCLUDED_PATHS``, and ``portable_bridge.BRIDGED`` is the
    table-level counterpart to ``core.portable.PORTABLE_FILES`` (a filename only ever
    reaches the export/import bundle once it is BOTH). A rollback must still not
    rewrite the ring; an export/reset must still not ship or restore it — it is wired
    into neither mechanism."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES
    from backend.db import portable_bridge

    assert "tool_call_log" in _EXCLUDED_TABLES
    assert "tool_call_log.json" not in portable_bridge.BRIDGED
