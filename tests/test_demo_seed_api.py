"""POST /api/demo/seed — build the fabricated demo book over HTTP.

The safety contract this endpoint has always carried: it can NEVER overwrite real data. The
fixture-copy implementation kept that promise by only filling absent/empty FILES; the builder
keeps it by refusing outright when the book already holds accounts, transactions or positions.
This asserts the refusal, and that a real book is left byte-for-byte untouched by the attempt.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client() -> TestClient:
    """A minimal app with only the demo router mounted — avoids the SPA catch-all
    mount in main.py shadowing the route."""
    from fastapi import FastAPI

    from backend.api.demo import router as demo_router

    app = FastAPI()
    app.include_router(demo_router)
    return TestClient(app)


@pytest.mark.usefixtures("db_isolated")
def test_seed_into_empty_book_builds_it():
    r = _client().post("/api/demo/seed")
    assert r.status_code == 200
    body = r.json()
    assert body["accounts"] >= 10
    assert body["transactions"] > 1000
    assert body["positions"] > 0

    from backend.db.account_store import AccountStore

    assert AccountStore().count() == body["accounts"] + 0


@pytest.mark.usefixtures("db_isolated")
def test_seed_refuses_a_book_that_already_has_data():
    from backend.db.account_store import AccountStore

    AccountStore().create(
        {"name": "My Real Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )

    r = _client().post("/api/demo/seed")
    assert r.status_code == 409
    assert "already has data" in r.json()["detail"]

    rows = AccountStore().all()
    assert len(rows) == 1
    assert rows[0]["name"] == "My Real Bank"
