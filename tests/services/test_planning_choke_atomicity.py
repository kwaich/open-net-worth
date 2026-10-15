"""The planning choke seam (storage plan 4a, task 5): the genuinely multi-table planning
writes — envelope lifecycle ops (``envelopes`` + ``budget_assignments``) and category
delete/merge (``categories`` + ``transactions``) and group delete (``envelope_groups`` +
``envelopes``) — now compose through
``backend.db.engine.locked_unit_of_work`` exactly like the 7 money chokes from plan 3
(``services/ledger.py``). Mirrors ``tests/db/test_choke_seam.py``'s pattern: a forced mid-op
failure must roll BOTH tables back (never "first write persists, second write never
happens"), and two threads composing the SAME tables in opposite argument order must never
deadlock (global alphabetical lock order, proven generically in test_choke_seam.py — proven
again here for THIS choke's specific table set).

Every test that could hang on a real deadlock bug runs its risky section on a background
thread with a bounded ``join(timeout=...)`` — a regression must fail loud within seconds,
never hang the suite (project hang-safety rule)."""

from __future__ import annotations

import threading
import time

import pytest

from backend.db.category_store import CategoryStore
from backend.db.engine import locked_unit_of_work
from backend.db.envelope_groups_store import EnvelopeGroupsStore
from backend.db.profile_store import ProfileStore
from backend.db.tables import categories, envelope_groups
from backend.db.tables import transactions as transactions_table
from backend.db.transaction_store import TransactionStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning as planning_service

envelope_store = envelopes_service.envelope_store
assignments_store = envelopes_service.assignments_store


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolated DB (``db_isolated``) — every code path under test, including profile (DB-backed
    now, Wave 5 JSON->DB), lands on it. Mirrors ``tests/test_categories_service.py``'s own
    ``env`` fixture. automations is DB-backed now (plan 4d task 2) — db_isolated already
    covers it, no more per-store monkeypatch/pre-seed needed."""
    ProfileStore().set_raw({"base_currency": "SGD"})
    return tmp_path


def _txn(txn_id: str, category: str | None, **extra) -> dict:
    row = {
        "id": txn_id,
        "date": "2026-06-01",
        "kind": "expense",
        "category": category,
        "source": "manual",
        "description": "test",
    }
    row.update(extra)
    return row


# ── 1. envelope merge: envelopes + budget_assignments ──────────────────────────────


def test_envelope_merge_is_atomic_across_envelopes_and_assignments(env, monkeypatch):
    """A forced failure on the LAST write (the source's hard-delete, ADR
    2026-09-26-deleting-an-envelope-deletes-it) must roll back the EARLIER writes too (the
    assignment money move, the target's category patch) — one transaction, not several."""
    src = envelopes_service.create_envelope("Source")
    dst = envelopes_service.create_envelope("Target")
    assignments_store.set_assigned("2026-06", src["id"], 50.0)
    assignments_store.set_assigned("2026-06", dst["id"], 10.0)
    original_map = assignments_store.month_map("2026-06")
    original_dst_cats = list(envelope_store.find_by_id(dst["id"])["category_ids"])

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-merge")

    monkeypatch.setattr(envelope_store, "delete", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-merge"):
        envelopes_service.merge_envelope(src["id"], dst["id"])

    # Rolled back: the source envelope must still EXIST (the hard-delete never committed)...
    assert envelope_store.find_by_id(src["id"]) is not None
    # ...the target's category patch must not have landed either...
    assert envelope_store.find_by_id(dst["id"])["category_ids"] == original_dst_cats
    # ...and the money that _move_assigned already wrote inside the SAME transaction must
    # not have persisted either (this is the whole point: one transaction, not several).
    assert assignments_store.month_map("2026-06") == original_map


def test_envelope_merge_succeeds_and_persists_when_not_interrupted(env):
    """Sanity/regression companion to the rollback test above: the happy path still moves
    the money AND hard-deletes the source in one call, unchanged by the conn= composition."""
    src = envelopes_service.create_envelope("Source")
    dst = envelopes_service.create_envelope("Target")
    assignments_store.set_assigned("2026-06", src["id"], 50.0)
    assignments_store.set_assigned("2026-06", dst["id"], 10.0)

    result = envelopes_service.merge_envelope(src["id"], dst["id"])

    assert result["months_moved"] == 1
    assert envelope_store.find_by_id(src["id"]) is None
    assert assignments_store.assigned("2026-06", src["id"]) == 0.0
    assert assignments_store.assigned("2026-06", dst["id"]) == 60.0


# ── 2. envelope delete / force-delete: envelopes + budget_assignments ──────────────


def test_delete_envelope_is_atomic_across_envelopes_and_assignments(env, monkeypatch):
    """A forced failure on the hard-delete write must roll back the assignment zeroing that
    already ran inside the SAME transaction — the envelope must still exist afterward."""
    envelope = envelopes_service.create_envelope("Groceries")
    assignments_store.set_assigned("2026-06", envelope["id"], 75.0)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-delete")

    monkeypatch.setattr(envelope_store, "delete", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-delete"):
        envelopes_service.delete_envelope(envelope["id"])

    assert assignments_store.assigned("2026-06", envelope["id"]) == 75.0
    assert envelope_store.find_by_id(envelope["id"]) is not None


def test_add_category_is_atomic_across_envelopes_and_assignments(env, monkeypatch):
    """add_category (a sweep finding — not named in the brief's binding list) folds a
    category-keyed legacy assignment remnant into the envelope's own slot; that fold and the
    envelope's category_ids patch must commit together."""
    envelope = envelopes_service.create_envelope("Groceries")
    CategoryStore().insert({"id": "cat_link001", "label": "Link", "kind": "expense"})
    assignments_store.set_assigned("2026-06", "cat_link001", 25.0)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-add-category")

    monkeypatch.setattr(envelope_store, "update_envelope", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-add-category"):
        envelopes_service.add_category(envelope["id"], "cat_link001")

    assert envelope_store.find_by_id(envelope["id"])["category_ids"] == []
    assert assignments_store.assigned("2026-06", "cat_link001") == 25.0
    assert assignments_store.assigned("2026-06", envelope["id"]) == 0.0


# ── 3. category delete / merge: categories + transactions ──────────────────────────


def test_category_delete_is_atomic_across_categories_and_transactions(env, monkeypatch):
    CategoryStore().insert({"id": "cat_del001", "label": "Del", "kind": "expense"})
    TransactionStore().insert(_txn("txn_del001", "cat_del001"))

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-category-delete")

    monkeypatch.setattr(CategoryStore, "delete", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-category-delete"):
        categories_service.delete_category("cat_del001", reassign=True)

    # Rolled back: the transaction must still name the deleted-in-progress category...
    assert TransactionStore().find_by_id("txn_del001")["category"] == "cat_del001"
    # ...and the category row itself must still exist (the delete never committed).
    assert CategoryStore().get("cat_del001") is not None


def test_category_merge_is_atomic_across_categories_and_transactions(env, monkeypatch):
    CategoryStore().insert({"id": "cat_src001", "label": "Src", "kind": "expense"})
    CategoryStore().insert({"id": "cat_dst001", "label": "Dst", "kind": "expense"})
    TransactionStore().insert(_txn("txn_merge001", "cat_src001"))

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-category-merge")

    monkeypatch.setattr(CategoryStore, "delete", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-category-merge"):
        categories_service.rename_category("cat_src001", merge_into="cat_dst001")

    assert TransactionStore().find_by_id("txn_merge001")["category"] == "cat_src001"
    assert CategoryStore().get("cat_src001") is not None


# ── 3b. create_category: a pure label — it can never create or touch a group ───────


def test_create_category_never_creates_or_touches_a_group(env):
    existing = EnvelopeGroupsStore().create({"name": "Home"})

    result = categories_service.create_category("Groceries", kind="expense")

    assert CategoryStore().get(result["id"]) is not None
    assert EnvelopeGroupsStore().all() == [existing]


def test_create_category_no_longer_accepts_a_group(env):
    with pytest.raises(TypeError):
        categories_service.create_category("Groceries", kind="expense", group="Home")  # type: ignore[call-arg]

    assert CategoryStore().all() == []
    assert EnvelopeGroupsStore().all() == []


# ── 4. move_category / group delete: envelopes (+ envelope_groups) ─────────────────


def test_move_category_is_atomic_across_the_envelope_rows(env, monkeypatch):
    src_group = EnvelopeGroupsStore().create({"name": "Src"})
    dst_group = EnvelopeGroupsStore().create({"name": "Dst"})
    envelope = envelopes_service.create_envelope("Movable", group_id=src_group["id"])
    sibling = envelopes_service.create_envelope("Sibling", group_id=dst_group["id"])
    envelope_store.update_envelope(sibling["id"], {"order": 5})  # moving to index 0 renumbers it
    sibling = envelope_store.find_by_id(sibling["id"])
    real_update = envelope_store.update_envelope
    calls = {"n": 0}

    def _fail_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure mid-move-category")
        return real_update(*args, **kwargs)

    monkeypatch.setattr(envelope_store, "update_envelope", _fail_second)

    with pytest.raises(RuntimeError, match="simulated failure mid-move-category"):
        planning_service.apply_budget_change(
            {
                "type": "move_category",
                "category": envelope["id"],
                "group_id": dst_group["id"],
                "index": 0,
            }
        )

    # The first row's patch rolled back with the failed second one.
    assert envelope_store.find_by_id(envelope["id"])["group_id"] == src_group["id"]
    assert envelope_store.find_by_id(sibling["id"])["order"] == sibling["order"]


def test_group_delete_is_atomic_across_groups_and_envelopes(env, monkeypatch):
    group = EnvelopeGroupsStore().create({"name": "Doomed"})
    envelope = envelopes_service.create_envelope("Keeper", group_id=group["id"])

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-group-delete")

    monkeypatch.setattr(EnvelopeGroupsStore, "delete", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-group-delete"):
        planning_service.apply_budget_change({"type": "group_delete", "group_id": group["id"]})

    # The envelope's move into Others rolled back with the failed group delete.
    assert envelope_store.find_by_id(envelope["id"])["group_id"] == group["id"]
    assert EnvelopeGroupsStore().find_by_id(group["id"]) is not None


# ── 5. conn= additive contract: omitting it stays byte-identical ───────────────────


def test_update_envelope_conn_omitted_still_locks_and_commits_independently(env):
    envelope = envelopes_service.create_envelope("Plain")
    updated = envelope_store.update_envelope(envelope["id"], {"name": "Renamed"})
    assert updated["name"] == "Renamed"
    assert envelope_store.find_by_id(envelope["id"])["name"] == "Renamed"


def test_update_group_conn_omitted_still_locks_and_commits_independently(env):
    group = EnvelopeGroupsStore().create({"name": "Plain"})
    updated = EnvelopeGroupsStore().update_group(group["id"], {"name": "Renamed"})
    assert updated["name"] == "Renamed"
    assert EnvelopeGroupsStore().find_by_id(group["id"])["name"] == "Renamed"


# ── 6. deadlock-free cross-table composition (mirrors tests/db/test_choke_seam.py) ─


def test_locked_unit_of_work_is_deadlock_free_for_categories_group_txn(db_isolated):
    """Two threads composing (categories, envelope_groups, transactions) in OPPOSITE
    argument order must both succeed, never deadlock — locked_unit_of_work normalizes lock
    acquisition to global alphabetical order regardless of the order tables are passed in.
    A threading.Barrier forces both threads to start together; a brief sleep while holding
    all three locks widens the window a lock-ordering bug would need to actually deadlock
    in. Bounded joins make a regression fail loud in seconds, never hang the suite."""
    category_store = CategoryStore()
    groups_store = EnvelopeGroupsStore()
    txn_store = TransactionStore()

    barrier = threading.Barrier(2)
    order: list[str] = []
    errors: list[BaseException] = []

    def _writer(tables, label):
        try:
            barrier.wait(timeout=10)
            with locked_unit_of_work(*tables) as conn:
                order.append(f"{label}-start")
                time.sleep(0.05)
                category_store.insert(
                    {"id": f"cat_{label}", "label": label, "kind": "expense"}, conn=conn
                )
                groups_store.insert(
                    {
                        "id": f"grp_{label}",
                        "name": label,
                        "collapsed": False,
                        "order": 0,
                        "is_others": False,
                    },
                    conn=conn,
                )
                txn_store.insert(_txn(f"txn_{label}", None), conn=conn)
                order.append(f"{label}-end")
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    t1 = threading.Thread(
        target=_writer, args=((categories, envelope_groups, transactions_table), "t1")
    )
    t2 = threading.Thread(
        target=_writer, args=((transactions_table, envelope_groups, categories), "t2")
    )
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "deadlock: a writer thread never finished"
    assert errors == []
    # Serialized, never interleaved: proof each transaction genuinely held ALL THREE locks
    # for its entire body, not just one at a time.
    assert order in (
        ["t1-start", "t1-end", "t2-start", "t2-end"],
        ["t2-start", "t2-end", "t1-start", "t1-end"],
    )
    assert {c["id"] for c in category_store.all()} == {"cat_t1", "cat_t2"}
    assert {g["id"] for g in groups_store.all()} == {"grp_t1", "grp_t2"}
    assert {t["id"] for t in txn_store.all()} == {"txn_t1", "txn_t2"}
