"""Tests for the uncategorized-summary service function and API endpoint.

Verifies:
- count + total only includes top-level None-category expense/income (not split, not transfer)
- split transactions (category="split" sentinel) are excluded even when their lines are blank
- period filter (date_from / date_to) works
- deleted transactions are excluded
- the endpoint returns 200 with the correct shape
- the ``limit`` peek list respects most-recent-first ordering
"""

from __future__ import annotations

import pytest

from backend.db.transaction_store import TransactionStore
from backend.models.transaction import SPLIT

# ── Deterministic book ─────────────────────────────────────────────────────────

_TXNS = [
    # 1. No-category expense (None) — should count
    {
        "id": "unc-1",
        "date": "2024-02-01",
        "kind": "expense",
        "category": None,
        "description": "Mystery spend",
        "from_account_id": "acct",
        "from_amount": 50.0,
        "from_currency": "SGD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "tags": [],
        "deleted": False,
    },
    # 2. No-category income (None) — should count
    {
        "id": "unc-2",
        "date": "2024-03-10",
        "kind": "income",
        "category": None,
        "description": "Unknown payment in",
        "from_account_id": None,
        "from_amount": None,
        "from_currency": None,
        "to_account_id": "acct",
        "to_amount": 100.0,
        "to_currency": "SGD",
        "tags": [],
        "deleted": False,
    },
    # 3. Categorized expense (food) — must NOT count
    {
        "id": "cat-1",
        "date": "2024-02-15",
        "kind": "expense",
        "category": "food",
        "description": "Hawker lunch",
        "from_account_id": "acct",
        "from_amount": 8.0,
        "from_currency": "SGD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "tags": [],
        "deleted": False,
    },
    # 4. Split expense — top-level category is the SPLIT sentinel, NOT uncategorized — must NOT count
    {
        "id": "split-1",
        "date": "2024-02-20",
        "kind": "expense",
        "category": SPLIT,
        "description": "Supermarket run",
        "from_account_id": "acct",
        "from_amount": 60.0,
        "from_currency": "SGD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "tags": [],
        "deleted": False,
        "splits": [
            {"category": "food", "amount": 40.0},
            {"category": None, "amount": 20.0},  # one split line has no category
        ],
    },
    # 5. Transfer — must NOT count (transfers are always None-category, never "unsorted")
    {
        "id": "transfer-1",
        "date": "2024-02-25",
        "kind": "transfer",
        "category": None,
        "description": "Move money",
        "from_account_id": "acct",
        "from_amount": 200.0,
        "from_currency": "SGD",
        "to_account_id": "acct2",
        "to_amount": 200.0,
        "to_currency": "SGD",
        "tags": [],
        "deleted": False,
    },
    # 6. Deleted no-category expense — must NOT count
    {
        "id": "deleted-unc",
        "date": "2024-02-05",
        "kind": "expense",
        "category": None,
        "description": "Deleted mystery",
        "from_account_id": "acct",
        "from_amount": 999.0,
        "from_currency": "SGD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "tags": [],
        "deleted": True,
    },
]


# ── Service-level tests ────────────────────────────────────────────────────────


@pytest.fixture
def patched_service(tmp_path, monkeypatch, db_isolated):
    """Isolate the service's transaction store + FX onto a deterministic in-memory book.
    TransactionStore is DB-backed now (plan 3 task 4) — db_isolated redirects it to a
    fresh, empty database; from/to_account_id are real foreign keys, so the referenced
    "acct"/"acct2" ids need stub Account rows first (tests.conftest.seed_stub_accounts).
    """
    from backend.services import ledger as svc
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts(_TXNS)
    store = TransactionStore()
    for t in _TXNS:
        store.insert(dict(t))

    monkeypatch.setattr(svc, "transactions", store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    # Patch load_fx_cache inside the service module's import of fx_cache.
    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    return svc


def test_count_excludes_categorized_split_transfer_deleted(patched_service):
    result = patched_service.uncategorized_summary()
    # Only unc-1 (expense 50) and unc-2 (income 100) should count.
    assert result["count"] == 2


def test_total_base_sums_correctly(patched_service):
    result = patched_service.uncategorized_summary()
    # 50 + 100 = 150 SGD
    assert result["total_base"] == 150.0


def test_base_currency_returned(patched_service):
    result = patched_service.uncategorized_summary()
    assert result["base_currency"] == "SGD"


def test_period_filter_date_from(patched_service):
    # date_from=2024-03-01 should pick up only unc-2 (2024-03-10)
    result = patched_service.uncategorized_summary(date_from="2024-03-01")
    assert result["count"] == 1
    assert result["total_base"] == 100.0


def test_period_filter_date_to(patched_service):
    # date_to=2024-02-28 should pick up only unc-1 (2024-02-01)
    result = patched_service.uncategorized_summary(date_to="2024-02-28")
    assert result["count"] == 1
    assert result["total_base"] == 50.0


def test_period_filter_date_range(patched_service):
    # Full February only → unc-1
    result = patched_service.uncategorized_summary(date_from="2024-02-01", date_to="2024-02-28")
    assert result["count"] == 1
    assert result["total_base"] == 50.0


def test_period_filter_empty_range(patched_service):
    result = patched_service.uncategorized_summary(date_from="2025-01-01", date_to="2025-01-31")
    assert result["count"] == 0
    assert result["total_base"] == 0.0


def test_recent_peek_most_recent_first(patched_service):
    result = patched_service.uncategorized_summary(limit=5)
    ids = [r["id"] for r in result["recent"]]
    # unc-2 (2024-03-10) is more recent than unc-1 (2024-02-01)
    assert ids == ["unc-2", "unc-1"]


def test_recent_peek_limit_respected(patched_service):
    result = patched_service.uncategorized_summary(limit=1)
    assert len(result["recent"]) == 1
    assert result["recent"][0]["id"] == "unc-2"


def test_recent_peek_omitted_when_limit_zero(patched_service):
    result = patched_service.uncategorized_summary(limit=0)
    assert result["recent"] == []


def test_recent_peek_shape(patched_service):
    result = patched_service.uncategorized_summary(limit=5)
    for item in result["recent"]:
        assert "id" in item
        assert "date" in item
        assert "description" in item
        assert "amount_base" in item


def test_split_top_level_excluded(patched_service):
    # The split-1 txn has category=SPLIT and a split line with category=UNCATEGORIZED.
    # Neither the txn itself nor the line should contribute to the summary.
    result = patched_service.uncategorized_summary()
    ids = [r["id"] for r in result["recent"]]
    assert "split-1" not in ids
    assert result["count"] == 2  # only unc-1 + unc-2


# ── API endpoint smoke tests ──────────────────────────────────────────────────


@pytest.fixture
def api_client(tmp_path, monkeypatch, db_isolated):
    """Wire the endpoint under TestClient with an isolated book. TransactionStore is
    DB-backed now (plan 3 task 4) — db_isolated redirects it to a fresh, empty database;
    from/to_account_id are real foreign keys, so seed_stub_accounts creates the
    referenced "acct"/"acct2" ids first."""
    from fastapi.testclient import TestClient

    from backend.api import ledger as route_mod
    from backend.services import ledger as svc
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts(_TXNS)
    store = TransactionStore()
    for t in _TXNS:
        store.insert(dict(t))

    monkeypatch.setattr(svc, "transactions", store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "load_fx_cache", lambda: {"SGD": 1.0})

    # Build a minimal FastAPI app that mounts only the ledger router with auth disabled.
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(route_mod.router)
    return TestClient(app, raise_server_exceptions=True)


def test_endpoint_200_default(api_client):
    resp = api_client.get("/api/transactions/uncategorized-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["total_base"] == 150.0
    assert data["base_currency"] == "SGD"
    assert isinstance(data["recent"], list)


def test_endpoint_date_from_filter(api_client):
    resp = api_client.get(
        "/api/transactions/uncategorized-summary", params={"date_from": "2024-03-01"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["total_base"] == 100.0


def test_endpoint_limit_zero_omits_recent(api_client):
    resp = api_client.get("/api/transactions/uncategorized-summary", params={"limit": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["recent"] == []


def test_endpoint_response_shape(api_client):
    resp = api_client.get("/api/transactions/uncategorized-summary")
    data = resp.json()
    assert set(data.keys()) >= {"count", "total_base", "base_currency", "recent"}
