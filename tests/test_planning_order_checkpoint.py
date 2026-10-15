"""
Checkpoint-capture test for Planning ROW ordering (undoable inside an AI turn).

Reordering categories within a group (``reorder_categories``) rewrites ``envelope.order`` on
each row's envelope (RFC envelope-category-full-split.md §Backend 5: envelope.group_id/order
is the ONLY placement authority — group-store's own "categories" lists are dead for budgeting
and no longer touched by this op). For the reorder to be undoable as one unit inside a Lucius
turn, the envelope table must be snapshotted PRE-write. ``EnvelopeStore`` is DB-backed (plan
4a task 2) — it writes through ``BookPayloadTableStore`` → ``core.io.fire_db_write_hook`` →
the checkpoint's ``write_log`` row-level snapshot (spec D7), not the old file-blob mechanism.
This pins that an intra-group reorder inside an active turn captures the ``envelopes`` table
with its OLD order, so a restore reverts it.

Fully isolated: checkpoint + the planning stores' paths monkeypatched into tmp_path.
"""

from __future__ import annotations

import pytest

from backend.ai import checkpoint as ckpt
from backend.core import io
from backend.services import envelopes as envelopes_service
from backend.services import planning as planning


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    blobs = tmp_path / "checkpoints" / "blobs"
    index = tmp_path / "checkpoints" / "index.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    blobs.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ckpt, "DATA_DIR", data_dir)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_BLOBS", blobs)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", index)

    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"base_currency": "SGD"})

    # The envelope service holds its OWN store instances. Full-split (RFC full-split
    # §Backend 3) deleted the pseudo-row fallback, so cat_13095ba70000/cat_db5642ab0000/cat_6ea706940000
    # need REAL 1:1 envelopes to render/reorder at all. budget_assignments is DB-backed now
    # (plan 4a task 4) too — like envelopes/categories, it rides the shared default engine
    # here (this fixture never isolates the DB, only the checkpoint blob dirs), auto-cleared
    # between tests by tests/conftest.py's autouse fixture; this file's own assertions never
    # touch assigned amounts, only envelope/group order.

    envelopes_service.ensure_envelopes_migrated()

    prev = io._write_hook
    ckpt.register_write_hook(ckpt._snapshot_active)
    yield tmp_path
    io.register_write_hook(prev)


def test_reorder_categories_is_checkpoint_covered(isolated):
    grp = planning.apply_budget_change({"type": "group_create", "name": "Essentials"})["group"]
    for cat in ["cat_13095ba70000", "cat_db5642ab0000", "cat_6ea706940000"]:
        planning.apply_budget_change(
            {
                "type": "move_category",
                "category": envelopes_service.env_of()[cat],
                "group_id": grp["id"],
            }
        )
    table = planning.budget_month("2026-06")
    rows_in_group = next(g for g in table["groups"] if g["id"] == grp["id"])["categories"]
    order_before = [c["category_ids"][0] for c in rows_in_group]
    assert order_before == ["cat_13095ba70000", "cat_db5642ab0000", "cat_6ea706940000"]
    # reorder_categories takes envelope ids (docs/adr/2026-07-05-envelope-id-native.md) —
    # resolve each row's own id from the SAME table it just rendered.
    env_id_by_cat = {c["category_ids"][0]: c["envelope_id"] for c in rows_in_group}

    ctx, token = ckpt.begin_turn("sess-order", anchor_history_len=0, message_preview="reorder")
    try:
        planning.apply_budget_change(
            {
                "type": "reorder_categories",
                "group_id": grp["id"],
                "categories": [
                    env_id_by_cat["cat_6ea706940000"],
                    env_id_by_cat["cat_db5642ab0000"],
                    env_id_by_cat["cat_13095ba70000"],
                ],
            }
        )
    finally:
        entry = ckpt.end_turn(ctx, token)

    # The envelope table was captured (DB-backed, plan 4a task 2) → the reorder is undoable as
    # one unit (envelope.order is the ONLY placement authority now — group-store's own
    # "categories" list is untouched).
    assert entry is not None
    assert "envelopes" in entry["db_tables"]

    # Restoring reverts to the PRE-write order (still the original member sequence) — the
    # DB-era equivalent of decoding the old file blob snapshot.
    ckpt.restore(entry["id"], mode="data")
    restored_table = planning.budget_month("2026-06")
    restored_rows = next(g for g in restored_table["groups"] if g["id"] == grp["id"])["categories"]
    restored_order = [c["category_ids"][0] for c in restored_rows]
    assert restored_order == ["cat_13095ba70000", "cat_db5642ab0000", "cat_6ea706940000"]


def test_category_groups_not_excluded_from_checkpoints(isolated):
    assert (isolated / "data" / "category_groups.json").resolve() not in ckpt._EXCLUDED_PATHS
