"""Bounded-read tests for the `list_checkpoints` AI tool + `ai.checkpoint.count_checkpoints`.

Active checkpoints are hard-capped at MAX_CHECKPOINTS (50, oldest pruned) — so unlike a
genuinely unbounded log, "reach the rest" is "raise limit up to the true ceiling," never an
offset (there is nothing to page to beyond it). These tests pin: showing/total are always
present and honest, a truncated page carries a note naming the reachable next step, and an
untruncated page carries no note.

Isolated on a fresh checkpoints TABLE (plan 4e task 2 — the manifest moved off
CHECKPOINTS_INDEX onto CheckpointIndexStore; tests/conftest.py's autouse Tier-2 table clear
gives every test a clean slate, same as any other DB-backed store's tests, project rule:
never touch live data).
"""

from __future__ import annotations

import asyncio
import json

from backend.ai import checkpoint as ckpt
from backend.ai.tools import execute_tool
from backend.db.checkpoint_index_store import CheckpointIndexStore


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _seed(n: int, session_id: str = "s1"):
    entries = [
        {
            "id": f"cp{i}",
            "session_id": session_id,
            "created_at": f"2026-06-{(i % 28) + 1:02d}T00:00:00Z",
            "message_preview": f"turn {i}",
            "files": {},
            "status": "active",
            "kind": "turn",
        }
        for i in range(n)
    ]
    CheckpointIndexStore().bulk_insert(entries)


def test_list_checkpoints_reports_showing_and_total_when_truncated():
    _seed(10)

    res, proposed = call("list_checkpoints", limit=3)
    assert proposed is None
    assert res["ok"] is True
    assert res["showing"] == 3
    assert res["total"] == 10
    assert len(res["checkpoints"]) == 3
    assert "note" in res
    assert "50" in res["note"] or "limit" in res["note"].lower()


def test_list_checkpoints_no_note_when_not_truncated():
    _seed(2)

    res, _ = call("list_checkpoints", limit=20)
    assert res["showing"] == 2
    assert res["total"] == 2
    assert "note" not in res


def test_list_checkpoints_raising_limit_reaches_the_rest():
    """The note's own advice — raise limit up to total — actually works."""
    _seed(10)

    page, _ = call("list_checkpoints", limit=3)
    total = page["total"]
    full, _ = call("list_checkpoints", limit=total)
    assert full["showing"] == total
    assert "note" not in full


def test_count_checkpoints_matches_list_length_when_unbounded():
    _seed(7)

    assert ckpt.count_checkpoints() == 7
    assert len(ckpt.list_checkpoints(limit=1000)) == 7


def test_count_checkpoints_excludes_non_active_by_default():
    entries = [
        {
            "id": "active1",
            "session_id": "s1",
            "created_at": "2026-06-01T00:00:00Z",
            "message_preview": "m",
            "files": {},
            "status": "active",
            "kind": "turn",
        },
        {
            "id": "restored1",
            "session_id": "s1",
            "created_at": "2026-06-02T00:00:00Z",
            "message_preview": "m",
            "files": {},
            "status": "restored",
            "kind": "turn",
        },
    ]
    CheckpointIndexStore().bulk_insert(entries)

    assert ckpt.count_checkpoints() == 1
    assert ckpt.count_checkpoints(include_orphaned=True) == 2
