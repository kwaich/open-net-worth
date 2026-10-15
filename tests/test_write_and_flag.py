import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _iso(db_isolated):
    """Isolate the ledger stores (DB-backed, plan 3 task 4) so created accounts/txns
    don't leak into the shared test DATA_DIR (a leftover account breaks the 'new user'
    briefing test)."""


def test_pre_start_expense_is_flagged_not_dropped():
    a = client.post(
        "/api/accounts",
        json={
            "name": "FlagCard",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -100,
            "initial_balance_date": "2026-05-31",
        },
    ).json()
    r = client.post(
        "/api/transactions",
        json={
            "date": "2026-04-05",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 50,
            "from_currency": "SGD",
        },
    )
    assert r.status_code == 200, r.text
    warnings = r.json().get("warnings") or []
    assert any(
        w["type"] == "before_tracking_start" and w["account_id"] == a["id"] for w in warnings
    )


def test_on_start_txn_has_no_flag():
    a = client.post(
        "/api/accounts",
        json={
            "name": "CleanCard",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -100,
            "initial_balance_date": "2026-05-31",
        },
    ).json()
    r = client.post(
        "/api/transactions",
        json={
            "date": "2026-06-01",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 50,
            "from_currency": "SGD",
        },
    )
    assert r.status_code == 200, r.text
    assert not (r.json().get("warnings") or [])
