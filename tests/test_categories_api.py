"""
API smoke tests for the category endpoints on the ledger router:
POST /api/categories (create), PATCH /api/categories/{category_id} (relabel/merge), DELETE (delete).

Categories carry opaque ids (docs/adr/2026-07-05-category-opaque-ids.md) — an id is minted
server-side on create (any caller-supplied "category_id" is ignored), and PATCH takes
{new_label?, merge_into?} (no more new_key/merge). Fixtures seed the store with
BUILTIN_CATEGORIES so built-in operations work.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import categories as cat


@pytest.fixture
def client(db_isolated):

    ProfileStore().set_raw({"base_currency": "SGD"})

    # Seed the store so built-in operations work.
    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)

    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def test_create_then_relabel_then_delete(client):
    r = client.post("/api/categories", json={"label": "Startup", "kind": "expense"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    category_id = data["id"]
    assert category_id.startswith("cat_")

    r = client.patch(f"/api/categories/{category_id}", json={"new_label": "Venture"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["label"] == "Venture" and data["merged"] is False

    r = client.delete(f"/api/categories/{category_id}")
    assert r.status_code == 200 and r.json()["ok"]


def test_create_ignores_caller_supplied_key(client):
    # A caller-supplied "category_id" is silently ignored — the id is always minted server-side.
    r = client.post("/api/categories", json={"category_id": "food", "label": "Food"})
    assert r.status_code == 200
    data = r.json()
    assert data["id"] != "food"
    assert data["id"].startswith("cat_")


def test_create_duplicate_label_rejected(client):
    # Category labels are a flat user-facing namespace — a duplicate (case-insensitive) is
    # refused with 409 Conflict, so no two categories can share a name.
    r1 = client.post("/api/categories", json={"label": "Food"})
    r2 = client.post("/api/categories", json={"label": "Food"})
    assert r1.status_code == 200
    assert r2.status_code == 409
    r3 = client.post("/api/categories", json={"label": "  food "})
    assert r3.status_code == 409


def test_rename_merge_succeeds(client):
    startup_id = client.post("/api/categories", json={"label": "Startup"}).json()["id"]
    venture_id = client.post("/api/categories", json={"label": "Venture"}).json()["id"]

    r = client.patch(f"/api/categories/{startup_id}", json={"merge_into": venture_id})
    assert r.status_code == 200
    data = r.json()
    assert data["merged"] is True and data["merged_into"] == venture_id


def test_rename_structural_422(client):
    r = client.patch("/api/categories/split", json={"new_label": "Divided"})
    assert r.status_code == 422


def test_rename_unknown_key_404(client):
    r = client.patch("/api/categories/cat_ghost", json={"new_label": "Misc"})
    assert r.status_code == 404


def test_rename_relabels_builtin(client):
    # Former built-in spending categories are just rows — can be relabeled (id never changes).
    r = client.patch("/api/categories/cat_13095ba70000", json={"new_label": "Grub"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["category_id"] == "cat_13095ba70000"
    assert data["label"] == "Grub"
    assert data["merged"] is False


def test_delete_in_use_409_then_reassign(client, tmp_path):
    category_id = client.post("/api/categories", json={"label": "Startup"}).json()["id"]
    # transaction_store is DB-backed now (plan 3 task 4) — seed via its own replace_all
    # (wipes + reinserts, same "this IS the whole ledger" semantics as the old
    # transactions.json full-file overwrite), not a raw file write.
    cat.transaction_store.replace_all(
        [{"id": "t1", "kind": "expense", "date": "2026-06-01", "category": category_id}]
    )
    r = client.delete(f"/api/categories/{category_id}")
    assert r.status_code == 409  # in use, refused

    r = client.delete(f"/api/categories/{category_id}", params={"reassign": True})
    assert r.status_code == 200 and r.json()["reassigned"] == 1
    txns = cat.transaction_store.all()
    assert txns[0]["category"] is None  # reassigned to None (Uncategorized)


def test_delete_structural_422(client):
    r = client.delete("/api/categories/split")
    assert r.status_code == 422


def test_delete_builtin_spending_ok(client):
    # Former built-in spending categories are deletable — row is removed.
    r = client.delete("/api/categories/cat_13095ba70000")
    assert r.status_code == 200 and r.json()["category_id"] == "cat_13095ba70000"
    listed = client.get("/api/categories").json()
    assert "cat_13095ba70000" not in {c["id"] for c in listed}


def test_delete_reassign_to_unknown_target_422(client):
    category_id = client.post("/api/categories", json={"label": "Startup"}).json()["id"]
    cat.transaction_store.replace_all(
        [{"id": "t1", "kind": "expense", "date": "2026-06-01", "category": category_id}]
    )
    r = client.delete(
        f"/api/categories/{category_id}", params={"reassign": True, "reassign_to": "cat_ghost"}
    )
    assert r.status_code == 422


def test_create_discards_removed_taxable_default_field(client):
    """A stale client cannot put category-level tax metadata back into storage."""
    r = client.post(
        "/api/categories",
        json={"label": "Salary", "kind": "income", "taxable_default": False},
    )
    assert r.status_code == 200
    category_id = r.json()["id"]
    assert "taxable_default" not in r.json()
    assert "taxable_default" not in CategoryStore().get(category_id)


def test_removed_taxable_endpoint_is_not_registered(client):
    r = client.post(
        "/api/categories/cat_13095ba70000/taxable",
        json={"taxable_default": False},
    )
    assert r.status_code == 404


def test_create_refuses_a_group_and_creates_neither_it_nor_the_category(client):
    """A category belongs to no group; a ``group`` in the body is refused (400), never
    ignored or turned into a group named after the string."""
    from backend.db.envelope_groups_store import EnvelopeGroupsStore

    r = client.post("/api/categories", json={"label": "Brand New", "group": "Wants"})

    assert r.status_code == 400
    assert EnvelopeGroupsStore().all() == []
    assert all(c["label"] != "Brand New" for c in CategoryStore().all())


def test_create_never_creates_a_group(client):
    from backend.db.envelope_groups_store import EnvelopeGroupsStore

    assert client.post("/api/categories", json={"label": "Brand New"}).status_code == 200
    assert EnvelopeGroupsStore().all() == []
