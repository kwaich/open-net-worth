"""
Mention-registry coverage for the `asset` EntityDescriptor (ADR 2026-09-06-assets-tab-
faithful-port, decision 11) — backend/services/mentions/descriptors/new_types.py.

Real registry, real DB (db_isolated): no store/provider mocking, so this exercises the
actual `services.owned_assets.list_assets()` wiring end to end, not a fake row.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.entity_types import router as entity_types_router
from backend.api.mentionables import router as mentionables_router
from backend.services import owned_assets as owned_assets_service


@pytest.fixture
def isolated(db_isolated):
    return db_isolated


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(entity_types_router)
    app.include_router(mentionables_router)
    return TestClient(app)


def test_entity_types_lists_asset(client):
    resp = client.get("/api/entity-types")
    assert resp.status_code == 200
    types = {t["type"] for t in resp.json()}
    assert "asset" in types


def test_asset_field_schema_excludes_marks_and_docs(client):
    entry = next(t for t in client.get("/api/entity-types").json() if t["type"] == "asset")
    keys = {f["key"] for f in entry["field_schema"]}
    assert keys == {"name", "category", "value", "valued_on", "share_pct", "notes"}
    assert "marks" not in keys and "docs" not in keys and "facts" not in keys


def test_mentionables_finds_created_asset(client, isolated):
    owned_assets_service.apply_owned_asset_change(
        {"op": "create", "name": "88 Marina Rise", "category": "property", "value": 1_150_000}
    )
    resp = client.get("/api/mentionables", params={"q": "Marina Rise", "types": "asset"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["label"] == "88 Marina Rise" and i["type"] == "asset" for i in items)


def test_mentionables_excludes_archived_asset(client, isolated):
    from backend.db.account_store import AccountStore

    cash = AccountStore().create({"name": "DBS", "type": "cash", "kind": "asset"})
    asset = owned_assets_service.apply_owned_asset_change(
        {"op": "create", "name": "Old Scooter", "category": "vehicle", "value": 2000}
    )
    owned_assets_service.apply_owned_asset_change(
        {"op": "dispose", "id": asset["id"], "proceeds": 2000, "account_id": cash["id"]}
    )
    resp = client.get("/api/mentionables", params={"q": "Old Scooter", "types": "asset"})
    assert resp.json()["items"] == []
