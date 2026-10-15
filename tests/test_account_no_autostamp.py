import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _iso(db_isolated):
    """Isolate ledger stores so created accounts don't leak into the shared DATA_DIR."""


def test_zero_balance_autostamps_today():
    r = client.post(
        "/api/accounts",
        json={
            "name": "Fresh Wallet",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("initial_balance_date")
    assert r.json().get("opening_txn_id")


def test_explicit_date_preserved():
    r = client.post(
        "/api/accounts",
        json={
            "name": "Card A",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -100,
            "initial_balance_date": "2026-04-01",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("initial_balance_date") == "2026-04-01"


def test_nonzero_balance_no_date_still_anchors():
    # A real opening balance with no date given → anchor at today (needs a curtain).
    r = client.post(
        "/api/accounts",
        json={
            "name": "Savings A",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 5000,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("initial_balance_date")  # non-empty
