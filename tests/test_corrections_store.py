"""
Unit tests for CorrectionStore (learn-from-corrections signal log — RFC P2-SP2).

Pure, offline, fully isolated: the store path is monkeypatched into a pytest tmp_path
so these never touch live backend/data (per the "no destructive tests on live data"
rule). No LLM, no network. Pattern from tests/test_rule_store.py.

Run:
    .venv/bin/pytest tests/test_corrections_store.py -v
"""

from __future__ import annotations

import pytest

from backend.db import correction_store
from backend.db.correction_store import MAX_CORRECTIONS, CorrectionStore


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    return tmp_path


def _event(payee="STARBUCKS", to="food", created_at=None):
    return {
        "payee_key": payee.lower(),
        "payee_sample": payee,
        "txn_id": "t1",
        "from_category": "shopping",
        "to_category": to,
        "kind": "expense",
        "created_at": created_at,
    }


# ── record: id + defaults ───────────────────────────────────────────────────────


def test_record_appends_with_id_and_timestamp(isolated):
    s = CorrectionStore()
    e = s.record(_event())
    assert e["id"]  # uuid stamped
    assert e["payee_key"] == "starbucks"
    assert e["to_category"] == "food"
    assert e["created_at"]  # stamped when absent
    assert s.events() == [e]


def test_record_caps_oldest_dropped(isolated, monkeypatch):
    # Shrink the cap so the test is cheap.
    monkeypatch.setattr(correction_store, "MAX_CORRECTIONS", 3)
    s = CorrectionStore()
    for i in range(5):
        s.record(_event(payee=f"P{i}", created_at=f"2026-06-0{i + 1}T00:00:00Z"))
    events = s.events()
    assert len(events) == 3
    # oldest (P0, P1) dropped; newest kept
    assert [e["payee_key"] for e in events] == ["p2", "p3", "p4"]


def test_max_corrections_default_is_sane():
    assert MAX_CORRECTIONS >= 100


# ── recent: date filter ─────────────────────────────────────────────────────────


def test_recent_filters_by_date(isolated):
    s = CorrectionStore()
    s.record(_event(created_at="2026-01-01T00:00:00Z"))  # old
    s.record(_event(created_at="2026-06-01T00:00:00Z"))  # recent
    recent = s.recent("2026-05-01T00:00:00Z")
    assert len(recent) == 1
    assert recent[0]["created_at"] == "2026-06-01T00:00:00Z"


# ── dismissals: add + read + idempotent ──────────────────────────────────────────


def test_add_dismissal_and_read(isolated):
    s = CorrectionStore()
    d = s.add_dismissal("starbucks", "food")
    assert d["type"] == "dismissal"
    assert d["payee_key"] == "starbucks"
    assert d["target_category"] == "food"
    assert d["dismissed_at"]
    dismissals = s.dismissals()
    assert len(dismissals) == 1 and dismissals[0]["payee_key"] == "starbucks"


def test_add_dismissal_idempotent(isolated):
    s = CorrectionStore()
    s.add_dismissal("starbucks", "food")
    s.add_dismissal("starbucks", "food")  # same pair → not re-added
    assert len(s.dismissals()) == 1
    # a different target IS a distinct dismissal
    s.add_dismissal("starbucks", "transport")
    assert len(s.dismissals()) == 2


def test_events_excludes_dismissals(isolated):
    s = CorrectionStore()
    s.record(_event())
    s.add_dismissal("starbucks", "food")
    # events() returns only correction events; dismissals() only dismissals
    assert len(s.events()) == 1
    assert len(s.dismissals()) == 1
    assert s.recent("2000-01-01T00:00:00Z") == s.events()
