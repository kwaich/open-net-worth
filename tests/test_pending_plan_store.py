"""PendingPlanStore CRUD — the token-bound staged-args queue behind the ledger confirm gate.

The store IS the security mechanism: the confirming call executes what is stored here, not
what the model re-sends. These tests pin the properties that makes that safe (single-use,
tool-bound, capped) and the wiring that keeps the queue out of checkpoints.

Expiry is split and the split is load-bearing: a legacy minted-TOKEN row is a snapshot of a
resolved *preview* and keeps its 15-minute TTL, while an AWAITING-APPROVAL row — a question put
to a human — never expires at all (ADR ``2026-07-30-approvals-never-expire``).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.pending_plan_store import MAX_PENDING, PendingPlanStore


@pytest.fixture
def store(tmp_path):
    # pending_plans is DB-backed now (Wave 5D2b, JSON->DB) — conftest's autouse Tier-2
    # table clear already isolates it per test, no path kwarg needed.
    return PendingPlanStore()


# The minted-token API (``stage``/``pop_for_tool``) is DELETED — every gated tool stages by
# its own ``tool_use_id`` now (ADR 2026-08-25-one-confirm-mechanism-for-every-tool). The
# properties it guaranteed still matter, so they are re-targeted at the surviving path
# rather than dropped with it.


def test_stage_pending_round_trips_through_peek(store):
    store.stage_pending("toolu_a", "add_transactions", {"amount": 12.4}, change_type="txn_create")
    entry = store.peek("toolu_a")
    assert entry["tool"] == "add_transactions"
    assert entry["args"] == {"amount": 12.4}
    assert entry["change_type"] == "txn_create"


def test_pop_is_single_use(store):
    store.stage_pending("toolu_b", "add_transactions", {"amount": 12.4}, change_type="txn_create")
    assert store.pop_pending_for_tool("toolu_b", "add_transactions")["args"] == {"amount": 12.4}
    assert store.pop_pending_for_tool("toolu_b", "add_transactions") is None


def test_pop_refuses_a_different_tool_and_leaves_the_plan_staged(store):
    store.stage_pending("toolu_c", "add_transactions", {"amount": 12.4}, change_type="txn_create")
    assert store.pop_pending_for_tool("toolu_c", "update_transactions") is None
    assert store.peek("toolu_c") is not None, "a cross-tool attempt consumed the plan"
    assert store.pop_pending_for_tool("toolu_c", "add_transactions") is not None


def test_pop_refuses_an_unknown_or_blank_id(store):
    assert store.pop_pending_for_tool("", "add_transactions") is None
    assert store.pop_pending_for_tool("nope", "add_transactions") is None


def test_an_awaiting_plan_never_expires(store):
    """Age is NOT a refusal reason on this path (ADR 2026-07-30-approvals-never-expire):
    however long a card sits, ✓ executes exactly the arguments it displayed. The minted-token
    path that DID expire is deleted."""
    store.stage_pending("toolu_d", "add_transactions", {"amount": 12.4}, change_type="txn_create")
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    # pending_plans is DB-backed now (Wave 5D2b, JSON->DB) — mutate through the store's
    # own raw dict I/O (_read_raw/_write_raw), the direct DB-era equivalent of the old
    # read-mutate-write-the-whole-file dance.
    raw = store._read_raw()
    raw["toolu_d"]["created_at"] = stale
    store._write_raw(raw)
    assert store.pop_pending_for_tool("toolu_d", "add_transactions") is not None


def test_the_queue_is_capped(store):
    ids = [f"toolu_cap_{i}" for i in range(MAX_PENDING + 10)]
    for i, tid in enumerate(ids):
        store.stage_pending(tid, "add_transactions", {"n": i}, change_type="t")
    live = [t for t in ids if store.peek(t) is not None]
    assert len(live) <= MAX_PENDING
    assert store.peek(ids[-1]) is not None, "the newest plan must survive the trim"


@pytest.mark.skip(
    reason="PendingPlanStore is DB-backed now (pending_plans table, Wave 5D2b) — there is no "
    "DB-level analogue of 'corrupt bytes on disk' to re-target this at (a JSON column either "
    "deserializes or the row/table itself is broken); pop_for_tool's own _read_raw() call has "
    "no try/except either, so there is no fail-soft code path left to exercise at all. "
    "Flagged for owner disposition, not deleted."
)
def test_a_corrupt_file_degrades_to_no_pending_plan(store):
    store._path.write_text("{not json")
    # Fail-SOFT is deliberate here (unlike a JsonStore): an unreadable queue must make the
    # CONFIRM refuse rather than raise. It holds no user data and nothing is committed until a
    # plan is redeemed, so the worst case is a card that has to be asked again — even now that
    # an awaiting row is durable (ADR 2026-07-30), losing one cannot lose a write.
    assert store.pop_for_tool("anything", "add_transactions") is None


@pytest.mark.skip(
    reason="PendingPlanStore is DB-backed now (pending_plans table, Wave 5D2b) — there is no "
    "longer a per-store pending_plans.json file to chmod; the whole onw.db already carries "
    "-rw------- via backend/db/engine.py's connect handler, covered by "
    "tests/db/test_engine_posture.py::test_sqlite_db_file_is_owner_only_after_connect. "
    "Flagged for owner: delete outright, or is a DB-file-permission equivalent worth a test."
)
def test_the_file_is_chmod_600(store):
    store.stage_pending("toolu_chmod", "add_transactions", {"amount": 1}, change_type="t")
    assert oct(store._path.stat().st_mode)[-3:] == "600"


# ── Wiring (these use the REAL path constant, which the gate tests monkeypatch away) ──


@pytest.mark.skip(
    reason="PendingPlanStore is DB-backed now (pending_plans table, Wave 5D2b) — there is no "
    "_path attribute and no file location (DATA_DIR vs CACHE_DIR) left to assert on; a DB row "
    "has no filesystem home. Flagged for owner disposition, not deleted."
)
def test_default_path_resolves_into_the_data_dir_not_the_cache():
    """An awaiting-approval row is durable state a live transcript depends on (ADR
    ``2026-07-30-approvals-never-expire``), so it belongs beside the sessions it pauses. It
    lived in CACHE_DIR back when every row expired in 15 minutes; a disposable-cache home for
    a durable pause is the same bug in a different costume."""
    from backend.paths import CACHE_DIR, DATA_DIR, PENDING_PLANS_JSON

    assert PENDING_PLANS_JSON.parent == DATA_DIR
    assert PENDING_PLANS_JSON.parent != CACHE_DIR
    assert PendingPlanStore()._path == PENDING_PLANS_JSON


@pytest.mark.skip(
    reason="PendingPlanStore is DB-backed now (pending_plans table, Wave 5D2b) — "
    "_migrate_from_cache_dir no longer exists; the one-time CACHE_DIR->DATA_DIR file adoption "
    "this test pinned was replaced by the alembic cutover itself (revision 0031), which "
    "absorbs a stale pre-Wave-5D2b file into the table once, at migration time, not "
    "per-construction. Flagged for owner disposition, not deleted."
)
def test_the_legacy_cache_dir_queue_is_adopted_in_place(tmp_path, monkeypatch):
    """The move must not drop the card a user is looking at while the server restarts."""
    from backend.db import pending_plan_store as mod

    legacy_dir = tmp_path / "cache"
    legacy_dir.mkdir()
    new_path = tmp_path / "data" / "pending_plans.json"
    new_path.parent.mkdir()
    (legacy_dir / "pending_plans.json").write_text(
        json.dumps({"toolu_live": {"tool": "delete_transactions", "state": "awaiting_approval"}})
    )
    monkeypatch.setattr("backend.core.paths.CACHE_DIR", legacy_dir)

    mod._migrate_from_cache_dir(new_path)

    assert PendingPlanStore(path=new_path).get_pending("toolu_live") is not None
    assert not (legacy_dir / "pending_plans.json").exists(), "the legacy file was left behind"
    mod._migrate_from_cache_dir(new_path)  # idempotent — a second boot must not raise


def test_the_queue_is_checkpoint_excluded():
    """It is written INSIDE an interactive turn's CheckpointContext (stage persists, confirm
    pops), so without an explicit exclusion every preview would mint a checkpoint.
    pending_plans is DB-backed now (Wave 5D2b, JSON->DB) — _EXCLUDED_TABLES is the DB
    mirror of _EXCLUDED_PATHS."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "pending_plans" in _EXCLUDED_TABLES


# ── approval-as-pending-tool_use (RFC 2026-07-22) — the tool_use_id-keyed path ──


def test_stage_pending_is_keyed_by_tool_use_id_and_read_non_destructively(store):
    store.stage_pending("toolu_1", "add_transactions", {"amount": 12.4}, session_id="s1")
    entry = store.get_pending("toolu_1")
    assert entry and entry["args"] == {"amount": 12.4} and entry["session_id"] == "s1"
    # non-destructive: reading it does NOT consume it (the healer reads it every reload)
    assert store.get_pending("toolu_1") is not None


def test_pop_pending_is_single_use_so_two_tabs_cannot_double_approve(store):
    store.stage_pending("toolu_1", "add_transactions", {"amount": 12.4})
    first = store.pop_pending("toolu_1")
    second = store.pop_pending("toolu_1")
    assert first["args"] == {"amount": 12.4}
    assert second is None  # the second approver gets nothing — no double-execute
    assert store.get_pending("toolu_1") is None


def test_every_row_is_an_awaiting_approval(store):
    """There is no second row shape left. The store used to hold minted-token previews
    alongside awaiting-approval rows, and ``get_pending`` had to tell them apart by
    ``state``; the token path is deleted (ADR 2026-08-25-one-confirm-mechanism-for-every-tool)
    so every row is an approval and the discrimination has nothing left to discriminate."""
    store.stage_pending("toolu_only", "add_transactions", {"amount": 1})
    assert store.get_pending("toolu_only") is not None
    assert [e["tool_use_id"] for e in store.list_awaiting()] == ["toolu_only"]


def test_list_awaiting(store):
    store.stage_pending("toolu_1", "add_transactions", {"amount": 1})
    assert [e["tool_use_id"] for e in store.list_awaiting()] == ["toolu_1"]


# ── An awaiting approval NEVER expires (ADR 2026-07-30-approvals-never-expire) ────────────
#
# REGRESSION GUARD. These three pin the bug that motivated the ADR: a card sat on screen for 22
# minutes, the 15-minute TTL + the 5-minutely sweep deleted its record, and the ✓ came back
# "this approval is no longer pending" after the UI had already painted it approved.


def _age(store, key: str) -> None:
    # pending_plans is DB-backed now (Wave 5D2b, JSON->DB) — mutate through the store's
    # own raw dict I/O, the direct DB-era equivalent of the old file read-mutate-write.
    raw = store._read_raw()
    raw[key]["created_at"] = "2020-01-01T00:00:00+00:00"
    store._write_raw(raw)


def test_an_aged_awaiting_row_still_reads_as_pending_and_still_redeems(store):
    store.stage_pending("toolu_1", "add_transactions", {"amount": 12.4})
    _age(store, "toolu_1")
    assert store.get_pending("toolu_1") is not None
    entry = store.pop_pending_for_tool("toolu_1", "add_transactions")
    assert entry and entry["args"] == {"amount": 12.4}, "the ✓ must execute what the card showed"


def test_an_aged_awaiting_row_survives_a_later_stage(store):
    """``stage_pending`` prunes on write — the prune must skip awaiting rows for age."""
    store.stage_pending("toolu_1", "add_transactions", {"amount": 1})
    _age(store, "toolu_1")
    store.stage_pending("toolu_2", "add_transactions", {"amount": 3})
    assert store.get_pending("toolu_1") is not None, "a later write reaped a live approval"


def test_cap_overflow_keeps_the_newest_approvals(store):
    """Overflow is the ONE thing that can still discard a live approval. It used to have an
    expendable class to spend first (minted-token previews); with those gone the cap simply
    trims oldest-first, and the newest approval must always survive."""
    for i in range(MAX_PENDING + 5):
        store.stage_pending(f"toolu_{i}", "add_transactions", {"n": i})
    live = [i for i in range(MAX_PENDING + 5) if store.get_pending(f"toolu_{i}") is not None]
    assert len(live) <= MAX_PENDING
    assert store.get_pending(f"toolu_{MAX_PENDING + 4}") is not None
