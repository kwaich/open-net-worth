"""
Unit tests for TargetsStore (P2-BU1 — savings targets).

Pure, fully isolated: TargetsStore is DB-backed (plan 4a task 3) — db_isolated gives each
test its own fresh, migrated database, so these never touch live data. No LLM, no network.
Mirrors test_commitments_store.py.
"""

from __future__ import annotations

import pytest

from backend.db.targets_store import TargetsStore


@pytest.fixture
def isolated(tmp_path, db_isolated):
    return tmp_path


def test_create_defaults_and_id(isolated):
    s = TargetsStore()
    t = s.create({"name": "Emergency fund", "target_amount": 12000, "target_date": "2026-12-31"})
    assert t["id"].startswith("tgt_")
    assert t["name"] == "Emergency fund"
    assert t["target_amount"] == 12000.0
    assert t["current_amount"] == 0.0
    assert t["status"] == "active"
    assert t["priority"] == 1
    assert t["currency"] == ""
    assert t["created_at"]
    assert t["linked_account_ids"] == [] and t["category"] is None


def test_create_uppercases_currency_and_coerces(isolated):
    s = TargetsStore()
    t = s.create(
        {
            "name": "Trip",
            "target_amount": "5000",
            "current_amount": "250.5",
            "currency": "usd",
            "target_date": "2026-08-01",
            "priority": "2",
        }
    )
    assert t["currency"] == "USD"
    assert t["target_amount"] == 5000.0 and t["current_amount"] == 250.5
    assert t["priority"] == 2


def test_create_requires_name(isolated):
    s = TargetsStore()
    with pytest.raises(ValueError, match="name is required"):
        s.create({"name": "  ", "target_amount": 100, "target_date": "2026-12-31"})


def test_create_rejects_nonpositive_amount(isolated):
    s = TargetsStore()
    with pytest.raises(ValueError, match="greater than 0"):
        s.create({"name": "X", "target_amount": 0, "target_date": "2026-12-31"})
    with pytest.raises(ValueError, match="greater than 0"):
        s.create({"name": "X", "target_amount": -5, "target_date": "2026-12-31"})


def test_update_whitelists_and_validates(isolated):
    s = TargetsStore()
    t = s.create({"name": "EF", "target_amount": 100, "target_date": "2026-12-31"})
    tid = t["id"]
    # immutable id/created_at dropped; status validated; floats coerced
    updated = s.update_target(
        tid,
        {
            "id": "hacked",
            "created_at": "1999-01-01",
            "target_amount": "200",
            "status": "bogus",
            "currency": "sgd",
        },
    )
    assert updated["id"] == tid  # id unchanged
    assert updated["created_at"] == t["created_at"]
    assert updated["target_amount"] == 200.0
    assert updated["status"] == "active"  # bogus rejected → unchanged
    assert updated["currency"] == "SGD"


def test_create_linked_account_ids_coerced_and_deduped(isolated):
    s = TargetsStore()
    t = s.create(
        {
            "name": "EF",
            "target_amount": 5000,
            "target_date": "2026-12-31",
            "linked_account_ids": ["acc-1", "", "acc-2", "acc-1", "  "],
        }
    )
    assert t["linked_account_ids"] == ["acc-1", "acc-2"]  # blanks dropped, deduped, order kept


def test_update_linked_account_ids_replaces_list(isolated):
    s = TargetsStore()
    t = s.create(
        {
            "name": "EF",
            "target_amount": 5000,
            "target_date": "2026-12-31",
            "linked_account_ids": ["acc-1"],
        }
    )
    upd = s.update_target(t["id"], {"linked_account_ids": ["acc-2", "acc-3", "acc-2"]})
    assert upd["linked_account_ids"] == ["acc-2", "acc-3"]
    # clearing to []
    cleared = s.update_target(t["id"], {"linked_account_ids": []})
    assert cleared["linked_account_ids"] == []


def test_update_status_completed(isolated):
    s = TargetsStore()
    t = s.create({"name": "EF", "target_amount": 100, "target_date": "2026-12-31"})
    updated = s.update_target(t["id"], {"status": "completed"})
    assert updated["status"] == "completed"


def test_update_missing_returns_none(isolated):
    s = TargetsStore()
    assert s.update_target("tgt-nope", {"name": "x"}) is None


def test_query_by_status_and_delete(isolated):
    s = TargetsStore()
    a = s.create({"name": "A", "target_amount": 10, "target_date": "2026-12-31"})
    b = s.create({"name": "B", "target_amount": 10, "target_date": "2026-12-31"})
    s.update_target(b["id"], {"status": "completed"})
    assert {t["id"] for t in s.query_targets(status="active")} == {a["id"]}
    assert {t["id"] for t in s.query_targets(status="completed")} == {b["id"]}
    assert len(s.query_targets()) == 2
    assert s.delete(a["id"]) is True
    assert s.find_by_id(a["id"]) is None
