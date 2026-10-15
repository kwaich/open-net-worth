"""ConnectorSnapshotStore — the bank/broker sibling of EmailSnapshotStore (RFC
connectors-as-data-sources §C). Bare CRUD over the generic (seq, id, payload) shape,
``id_field="key"`` where ``key = f"{source}:{ref}"``.
"""

from __future__ import annotations

import pytest

from backend.db.connector_snapshot_store import ConnectorSnapshotStore, snapshot_key


@pytest.fixture
def store(db_engine):
    return ConnectorSnapshotStore(engine=db_engine)


def _row(source="plaid", ref="txn_1", **over) -> dict:
    row = {
        "key": snapshot_key(source, ref),
        "source": source,
        "ref": ref,
        "provider_account_id": "acc_abc",
        "raw": {"id": ref, "amount": -12.5},
        "normalized": {
            "date": "2026-09-01",
            "amount": -12.5,
            "currency": "USD",
            "description": "Coffee",
            "account": "acc_abc",
        },
        "content_hash": "deadbeef",
        "first_seen_at": "2026-09-01T00:00:00+00:00",
        "last_seen_at": "2026-09-01T00:00:00+00:00",
        "changed_at": "2026-09-01T00:00:00+00:00",
        "missed_fetches": 0,
        "last_missed_at": None,
        "referenced": False,
    }
    row.update(over)
    return row


def test_snapshot_key_format():
    assert snapshot_key("plaid", "txn_1") == "plaid:txn_1"


def test_insert_and_get_roundtrip(store):
    store.insert(_row())
    row = store.get("plaid", "txn_1")
    assert row is not None
    assert row["provider_account_id"] == "acc_abc"
    assert row["normalized"]["description"] == "Coffee"


def test_get_returns_none_for_unknown_pair(store):
    assert store.get("plaid", "nope") is None


def test_by_source_scopes_to_one_provider(store):
    store.insert(_row(source="plaid", ref="a"))
    store.insert(_row(source="plaid", ref="b"))
    store.insert(_row(source="simplefin", ref="c"))
    refs = {r["ref"] for r in store.by_source("plaid")}
    assert refs == {"a", "b"}


def test_update_by_id_patches_in_place(store):
    store.insert(_row())
    updated = store.update_by_id(snapshot_key("plaid", "txn_1"), {"referenced": True})
    assert updated["referenced"] is True
    assert store.get("plaid", "txn_1")["referenced"] is True


def test_delete_removes_the_row(store):
    store.insert(_row())
    assert store.delete(snapshot_key("plaid", "txn_1")) is True
    assert store.get("plaid", "txn_1") is None


def test_same_ref_different_source_is_a_distinct_row(store):
    """The key is source:ref, not ref alone — two providers may reuse the same ref."""
    store.insert(_row(source="plaid", ref="123"))
    store.insert(_row(source="simplefin", ref="123"))
    assert store.get("plaid", "123") is not None
    assert store.get("simplefin", "123") is not None
    assert len(store.all()) == 2
