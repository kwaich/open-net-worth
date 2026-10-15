"""
API smoke tests for the targets router (P2-BU1).

Offline + isolated: a minimal FastAPI app mounts only the router under test. TargetsStore
is DB-backed (plan 4a task 3) — db_isolated gives each test its own fresh, migrated
database; the FX/baseline are monkeypatched into tmp_path. No LLM, no network, no full-app
lifespan.

The ``/api/spending/budgets``/``/api/budget-config``/``/api/spending/summary`` REST
endpoints this file used to also cover were retired in RFC
2026-08-22-pluggable-budget-engines.md Phase 4 — verified zero frontend consumers
(``grep -rn "spending/summary\\|budget-config\\|spending/budgets" frontend/src``). The
underlying ``services.budgets`` functions they wrapped are still live (``planning.py``'s
category-universe read and the category rename/merge hooks; the spending-limit AI setters were
retired 2026-09-26) — only the redundant REST surface is gone; see
``tests/test_budgets_service.py``/``tests/test_char_split_safety.py`` for their own coverage.

Run:
    .venv/bin/pytest tests/test_budgets_api.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.budgets import router as budgets_router
from backend.db.targets_store import TargetsStore
from backend.services import targets as ts


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(ts, "targets_store", TargetsStore())
    # bs (services.budgets) reads/writes the profile via the DB-backed ProfileStore now
    # (Wave 5) — db_isolated already gives this test a fresh, empty profile row; no
    # separate path redirect is needed.
    monkeypatch.setattr(ts.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ts, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(ts, "_TODAY", "2026-06-04")
    monkeypatch.setattr(ts.cashflow, "cash_balance_base", lambda base, *_a, **_k: 0.0)
    monkeypatch.setattr(ts, "_read_profile", lambda: {})
    app = FastAPI()
    app.include_router(budgets_router)
    return TestClient(app)


# ── targets CRUD (AC-11/15) ──────────────────────────────────────────────────


def test_targets_empty_state(client):
    assert client.get("/api/targets").json() == []
    alloc = client.get("/api/targets/allocation").json()
    assert alloc["total_required_monthly_base"] == 0.0
    assert alloc["targets"] == []


def test_targets_crud_happy_path(client):
    r = client.post(
        "/api/targets",
        json={
            "name": "Emergency fund",
            "target_amount": 12000,
            "current_amount": 2000,
            "target_date": "2026-12-04",
        },
    )
    assert r.status_code == 200
    body = r.json()
    tid = body["id"]
    assert body["remaining_base"] == 10000.0 and body["months_remaining"] == 6

    assert client.get(f"/api/targets/{tid}").json()["id"] == tid
    upd = client.put(f"/api/targets/{tid}", json={"current_amount": 5000})
    assert upd.json()["remaining_base"] == 7000.0
    assert client.delete(f"/api/targets/{tid}").json() == {"ok": True}
    assert client.get(f"/api/targets/{tid}").status_code == 404


def test_targets_validation_400(client):
    bad = client.post(
        "/api/targets", json={"name": "X", "target_amount": 0, "target_date": "2026-12-31"}
    )
    assert bad.status_code == 400


def test_targets_missing_404(client):
    assert client.get("/api/targets/tgt-nope").status_code == 404
    assert client.put("/api/targets/tgt-nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/targets/tgt-nope").status_code == 404


# ── linked_account_ids round-trip (P2-GO1) ────────────────────────────────────


def test_targets_linked_account_ids_roundtrip(client):
    from backend.db.account_store import AccountStore

    for acc_id in ("acc-1", "acc-2", "acc-3"):  # links must be real accounts (2026-10-13)
        AccountStore().create({"id": acc_id, "name": acc_id, "type": "cash", "currency": "SGD"})
    r = client.post(
        "/api/targets",
        json={
            "name": "EF",
            "target_amount": 12000,
            "target_date": "2026-12-04",
            "linked_account_ids": ["acc-1", "", "acc-2", "acc-1"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    tid = body["id"]
    assert body["linked_account_ids"] == ["acc-1", "acc-2"]  # blanks/dupes cleaned
    upd = client.put(f"/api/targets/{tid}", json={"linked_account_ids": ["acc-3"]})
    assert upd.json()["linked_account_ids"] == ["acc-3"]
    # the legacy singular key is no longer part of the shape
    assert "linked_account_id" not in body
    bad = client.put(f"/api/targets/{tid}", json={"linked_account_ids": ["acc-gone"]})
    assert bad.status_code == 400 and "acc-gone" in bad.json()["detail"]


# ── profile no longer carries goals (P2-GO1) ──────────────────────────────────


def test_profile_get_has_no_goals(db_isolated):
    # A standalone app mounting only the profile router, backed by a profile row that even
    # carries a legacy goals[] — read must strip it (db_isolated: no live data touched).
    from backend.api import profile as profile_api
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"name": "T", "goals": [{"name": "old", "target_amount": 9}]})

    app = FastAPI()
    app.include_router(profile_api.router)
    pc = TestClient(app)

    body = pc.get("/api/profile").json()
    assert "goals" not in body
    # a PUT that omits goals round-trips fine and never re-adds the field
    assert pc.put("/api/profile", json={"name": "T2"}).status_code == 200
    assert "goals" not in pc.get("/api/profile").json()
