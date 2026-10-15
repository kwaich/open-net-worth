"""
Unit tests for OwnedAssetsStore / OwnedAssetCategoriesStore (ADR
2026-09-06-assets-tab-faithful-port).

Pure, fully isolated: both stores are DB-backed (BookPayloadTableStore) — db_isolated
gives each test its own fresh, migrated database, so these never touch live data. No
LLM, no network.

Run:
    .venv/bin/pytest tests/test_owned_assets_store.py -v
"""

from __future__ import annotations

import threading
from datetime import date

import pytest

from backend.core.errors import ConflictError, ValidationError
from backend.db.owned_assets_store import OwnedAssetCategoriesStore, OwnedAssetsStore


@pytest.fixture
def isolated(db_isolated):
    return db_isolated


# ── create ───────────────────────────────────────────────────────────────────────────────


def test_create_defaults(isolated):
    store = OwnedAssetsStore()
    asset = store.create({"name": "88 Marina Rise", "category": "property", "currency": "sgd"})
    assert asset["id"].startswith("oa_")
    assert asset["name"] == "88 Marina Rise"
    assert asset["category"] == "property"
    assert asset["currency"] == "SGD"  # upper-cased
    assert asset["insured"] is False
    assert asset["maturity_date"] is None
    assert asset["share_pct"] == 100
    assert asset["archived"] is False
    assert asset["marks"] == []
    assert asset["docs"] == []
    assert asset["created_at"] and asset["updated_at"]


def test_create_and_update_typed_asset_details(isolated):
    """Catches the store keeping insurance/maturity as loose facts instead of fields."""
    store = OwnedAssetsStore()
    asset = store.create(
        {
            "name": "Joint sculpture",
            "category": "art",
            "insured": True,
            "maturity_date": "2031-06-17",
        }
    )
    assert asset["insured"] is True
    assert asset["maturity_date"] == "2031-06-17"

    updated = store.update_asset(asset["id"], {"insured": False, "maturity_date": "2032-01-05"})
    assert updated["insured"] is False
    assert updated["maturity_date"] == "2032-01-05"


@pytest.mark.parametrize("share", [150, -10, "abc", True, float("nan")])
def test_store_refuses_share_outside_0_to_100(isolated, share):
    """Defence in depth under the choke: an out-of-range share is refused, never clamped."""
    store = OwnedAssetsStore()
    with pytest.raises(ValidationError):
        store.create({"name": "Joint art", "category": "art", "share_pct": share})
    asset = store.create({"name": "Joint art", "category": "art", "share_pct": 50})
    with pytest.raises(ValidationError):
        store.update_asset(asset["id"], {"share_pct": share})
    assert [a["share_pct"] for a in store.all()] == [50]


def test_create_normalizes_facts(isolated):
    store = OwnedAssetsStore()
    asset = store.create(
        {
            "name": "Volvo XC60",
            "category": "vehicle",
            "facts": [["Registered", "Feb 2022"], "not-a-pair", ["COE expires", "Feb 2032"]],
        }
    )
    assert asset["facts"] == [["Registered", "Feb 2022"], ["COE expires", "Feb 2032"]]


def test_create_with_marks_sorts_newest_first(isolated):
    store = OwnedAssetsStore()
    asset = store.create(
        {
            "name": "Gold bars",
            "category": "metal",
            "marks": [
                {"date": "2023-11-01", "value": 34300, "note": "dealer invoice"},
                {"date": "2026-08-01", "value": 38400, "note": "spot"},
            ],
        }
    )
    dates = [m["date"] for m in asset["marks"]]
    assert dates == ["2026-08-01", "2023-11-01"]
    assert all(m["id"].startswith("mk_") for m in asset["marks"])


def test_create_same_day_marks_keep_list_order_deterministically(isolated):
    """(date, created_at, id), newest first, is the store's one ordering rule for marks.
    `create()` stamps created_at by list POSITION, so the first mark supplied sorts newest on
    a shared date rather than racing real timing; a full (date, created_at) tie falls to id."""
    store = OwnedAssetsStore()
    today = date.today().isoformat()
    asset = store.create(
        {
            "name": "Watch",
            "category": "watch",
            "marks": [{"date": today, "value": 15000}, {"date": today, "value": 10000}],
        }
    )
    assert [m["value"] for m in asset["marks"]] == [15000, 10000]

    stamp = "2026-01-01T09:00:00+00:00"
    tied = store.create(
        {
            "name": "Tied",
            "category": "watch",
            "marks": [
                {"id": "mk_aaaaaaaaaaaa", "date": "2026-01-01", "value": 1, "created_at": stamp},
                {"id": "mk_bbbbbbbbbbbb", "date": "2026-01-01", "value": 2, "created_at": stamp},
            ],
        }
    )
    assert [m["id"] for m in tied["marks"]] == ["mk_bbbbbbbbbbbb", "mk_aaaaaaaaaaaa"]
    assert all(m["edited_at"] is None for m in tied["marks"])


# ── update_asset (whitelist) ─────────────────────────────────────────────────────────────


def test_update_asset_whitelists_fields(isolated):
    store = OwnedAssetsStore()
    asset = store.create(
        {
            "name": "Rolex",
            "category": "watch",
            "marks": [{"date": "2025-01-01", "value": 14800}],
        }
    )
    updated = store.update_asset(
        asset["id"],
        {
            "name": "Rolex Submariner",
            "marks": [{"date": "2026-01-01", "value": 99999}],  # must be dropped
            "value": 1,  # not a real field — must be dropped
            "automation_id": "auto_hacked",  # retired field must be dropped
            "archived": True,  # disposal field — must be dropped
        },
    )
    assert updated["name"] == "Rolex Submariner"
    assert updated["marks"] == asset["marks"]  # untouched
    assert "automation_id" not in updated
    assert updated["archived"] is False


def test_update_asset_drops_retired_review_fields(isolated):
    store = OwnedAssetsStore()
    asset = store.create({"name": "Watch", "category": "watch"})
    updated = store.update_asset(asset["id"], {"cadence_months": 7})
    assert "cadence_months" not in updated


def test_update_asset_missing_id_returns_none(isolated):
    store = OwnedAssetsStore()
    assert store.update_asset("oa_doesnotexist", {"name": "x"}) is None


# ── marks ────────────────────────────────────────────────────────────────────────────────


def test_add_mark_prepends_and_reorders(isolated):
    store = OwnedAssetsStore()
    asset = store.create(
        {
            "name": "Gold",
            "category": "metal",
            "marks": [{"date": "2026-01-01", "value": 100}],
        }
    )
    updated = store.add_mark(asset["id"], {"date": "2026-06-01", "value": 120, "note": "spot"})
    assert [m["date"] for m in updated["marks"]] == ["2026-06-01", "2026-01-01"]
    assert updated["marks"][0]["value"] == 120

    # a BACKDATED mark still lands in the right slot, not just prepended raw
    backdated = store.add_mark(asset["id"], {"date": "2025-01-01", "value": 80})
    assert [m["date"] for m in backdated["marks"]] == ["2026-06-01", "2026-01-01", "2025-01-01"]


def test_add_mark_missing_asset_returns_none(isolated):
    store = OwnedAssetsStore()
    assert store.add_mark("oa_nope", {"value": 1}) is None


def _two_values(store: OwnedAssetsStore) -> dict:
    return store.create(
        {
            "name": "Gold",
            "category": "metal",
            "marks": [
                {"date": "2026-01-01", "value": 100, "note": "spot"},
                {"date": "2026-06-01", "value": 120},
            ],
        }
    )


def test_correct_mark_keeps_id_and_created_at(isolated):
    store = OwnedAssetsStore()
    asset = _two_values(store)
    target = next(m for m in asset["marks"] if m["date"] == "2026-01-01")

    corrected = store.correct_mark(
        asset["id"], target["id"], {"value": 110, "date": "2026-07-01", "note": "typo fixed"}
    )
    fixed = next(m for m in corrected["marks"] if m["id"] == target["id"])
    assert (fixed["value"], fixed["date"], fixed["note"]) == (110, "2026-07-01", "typo fixed")
    assert fixed["created_at"] == target["created_at"]
    assert fixed["edited_at"]
    assert [m["date"] for m in corrected["marks"]] == ["2026-07-01", "2026-06-01"]  # re-sorted

    # A body identical to what is stored is a no-op: no stamp, no write.
    assert store.correct_mark(asset["id"], target["id"], {"value": 110}) == corrected
    assert store.correct_mark(asset["id"], "mk_gone", {"value": 1}) is None


def test_remove_mark_refuses_last(isolated):
    store = OwnedAssetsStore()
    asset = _two_values(store)
    after = store.remove_mark(asset["id"], asset["marks"][0]["id"])
    assert [m["value"] for m in after["marks"]] == [100]
    assert store.remove_mark(asset["id"], "mk_gone") is None
    with pytest.raises(ConflictError):
        store.remove_mark(asset["id"], after["marks"][0]["id"])
    assert len(store.find_by_id(asset["id"])["marks"]) == 1


def test_duplicate_mark_ids_are_corrected_and_removed_one_at_a_time(isolated):
    """Security review S4: correct merged every mark sharing the id into one, and remove
    dropped them all — checking "is this the last value?" before filtering, so a duplicate
    pair could leave an asset with no value at all."""
    store = OwnedAssetsStore()
    asset = store.create(
        {
            "name": "Gold",
            "category": "metal",
            "marks": [
                {"id": "mk_dup", "date": "2026-01-01", "value": 100},
                {"id": "mk_dup", "date": "2026-02-01", "value": 110},
            ],
        }
    )
    corrected = store.correct_mark(asset["id"], "mk_dup", {"value": 120})
    assert sorted(m["value"] for m in corrected["marks"]) == [100, 120]

    assert len(store.remove_mark(asset["id"], "mk_dup")["marks"]) == 1
    with pytest.raises(ConflictError):
        store.remove_mark(asset["id"], "mk_dup")
    assert len(store.find_by_id(asset["id"])["marks"]) == 1


def _race_reads(monkeypatch) -> None:
    """Hold every writer at its read until a second writer has read too — so a read taken
    OUTSIDE the table lock is provably stale by the time it is written back. With the lock
    held across the whole read-modify-write the second writer can't read yet: the wait times
    out and each writer proceeds in turn."""
    barrier = threading.Barrier(2, timeout=1)
    original = OwnedAssetsStore.find_by_id

    def racing_find(self, id_val, *, conn=None):
        row = original(self, id_val, conn=conn)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return row

    monkeypatch.setattr(OwnedAssetsStore, "find_by_id", racing_find)


def _run_together(*calls) -> None:
    threads = [threading.Thread(target=fn, args=args) for fn, *args in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_concurrent_add_mark_keeps_both(isolated, monkeypatch):
    asset = OwnedAssetsStore().create(
        {"name": "Gold", "category": "metal", "marks": [{"date": "2026-01-01", "value": 100}]}
    )
    _race_reads(monkeypatch)
    store = OwnedAssetsStore()
    _run_together(
        (store.add_mark, asset["id"], {"date": "2026-02-01", "value": 110}),
        (store.add_mark, asset["id"], {"date": "2026-03-01", "value": 120}),
    )
    monkeypatch.undo()
    values = sorted(m["value"] for m in store.find_by_id(asset["id"])["marks"])
    assert values == [100, 110, 120]


def test_concurrent_doc_writes_keep_both(isolated, monkeypatch):
    asset = OwnedAssetsStore().create({"name": "Policy", "category": "policy"})
    _race_reads(monkeypatch)
    store = OwnedAssetsStore()
    _run_together(
        (store.add_doc, asset["id"], {"ref_id": "att_a", "name": "a.pdf"}),
        (store.add_doc, asset["id"], {"ref_id": "att_b", "name": "b.pdf"}),
    )
    monkeypatch.undo()
    refs = sorted(d["ref_id"] for d in store.find_by_id(asset["id"])["docs"])
    assert refs == ["att_a", "att_b"]


# ── photo / docs ─────────────────────────────────────────────────────────────────────────


def test_set_photo(isolated):
    store = OwnedAssetsStore()
    asset = store.create({"name": "Watch", "category": "watch"})
    updated = store.set_photo(asset["id"], "att_abc123")
    assert updated["photo_ref"] == "att_abc123"


def test_doc_lifecycle(isolated):
    store = OwnedAssetsStore()
    asset = store.create({"name": "Policy", "category": "policy"})
    updated = store.add_doc(asset["id"], {"ref_id": "att_1", "name": "statement.pdf"})
    assert len(updated["docs"]) == 1
    assert updated["docs"][0]["lucius_readable"] is True

    updated = store.update_doc(asset["id"], "att_1", {"lucius_readable": False})
    assert updated["docs"][0]["lucius_readable"] is False

    assert store.update_doc(asset["id"], "att_missing", {"name": "x"}) is None

    updated = store.remove_doc(asset["id"], "att_1")
    assert updated["docs"] == []
    assert store.remove_doc(asset["id"], "att_1") is None  # already gone


# ── archive / query ──────────────────────────────────────────────────────────────────────


def test_archive_sets_disposal_fields(isolated):
    store = OwnedAssetsStore()
    asset = store.create({"name": "Camera", "category": "other"})
    updated = store.archive(
        asset["id"],
        {
            "disposed_on": "2026-09-01",
            "disposal_proceeds": 500.0,
            "disposal_txn_id": "txn_1",
            "disposal_account_id": "acc_1",
        },
    )
    assert updated["archived"] is True
    assert updated["disposed_on"] == "2026-09-01"
    assert updated["disposal_proceeds"] == 500.0
    assert updated["disposal_txn_id"] == "txn_1"
    assert updated["disposal_account_id"] == "acc_1"


def test_archive_clears_loan_link(isolated):
    store = OwnedAssetsStore()
    asset = store.create({"name": "Car", "category": "vehicle", "loan_account_id": "acc_loan"})
    archived = store.archive(asset["id"], {"disposed_on": "2026-09-01"})
    assert archived["loan_account_id"] is None


def test_query_assets_filters_archived_and_category(isolated):
    store = OwnedAssetsStore()
    a1 = store.create({"name": "Car", "category": "vehicle"})
    store.create({"name": "Watch", "category": "watch"})
    store.archive(a1["id"], {"disposed_on": "2026-01-01"})

    assert [a["name"] for a in store.query_assets()] == ["Watch"]
    assert len(store.query_assets(include_archived=True)) == 2
    assert [a["name"] for a in store.query_assets(category="watch")] == ["Watch"]


# ── categories ───────────────────────────────────────────────────────────────────────────


def test_category_create_mints_key(isolated):
    store = OwnedAssetCategoriesStore()
    cat = store.create("Vinyl & hi-fi")
    assert cat["key"].startswith("c_vinyl")
    assert cat["label"] == "Vinyl & hi-fi"
    assert cat["custom"] is True
    assert "cadence_months" not in cat


def test_category_create_idempotent_case_insensitive(isolated):
    store = OwnedAssetCategoriesStore()
    first = store.create("Camera gear")
    second = store.create("camera gear")
    assert first["key"] == second["key"]
    assert len(store.all()) == 1
