"""CRUD for ``RejectedIdentityStore`` — the memory behind ADR 2026-09-02-a-deleted-identity-
is-remembered. Keyed on (source, ref, from_account_id, to_account_id): one email that
legitimately books two legs on two routes yields two records, and deleting one leg must not
reject the other."""

from __future__ import annotations

from backend.db.rejected_identity_store import RejectedIdentityStore, make_rejection_id


def _entry(ref="aaaa1111cccc", frm="acc_a", to=None, txn_id="txn_1", **over):
    return {
        "source": "email",
        "ref": ref,
        "from_account_id": frm,
        "to_account_id": to,
        "txn_id": txn_id,
        "date": "2026-08-30",
        "description": "RIDGELINE HOTEL",
        "amount": 250.0,
        "currency": "SGD",
        **over,
    }


def test_remember_stamps_a_deterministic_id_and_a_timestamp(db_isolated):
    store = RejectedIdentityStore()
    [rec] = store.remember([_entry()])
    assert rec["id"] == make_rejection_id("email", "aaaa1111cccc", "acc_a", None)
    assert rec["rejected_at"].endswith("Z")
    assert store.find_by_id(rec["id"])["txn_id"] == "txn_1"


def test_the_same_identity_twice_is_one_record(db_isolated):
    store = RejectedIdentityStore()
    store.remember([_entry(txn_id="txn_1")])
    store.remember([_entry(txn_id="txn_2")])
    assert store.total_count() == 1
    assert store.all()[0]["txn_id"] == "txn_2", "the latest delete wins"


def test_movement_is_part_of_the_key(db_isolated):
    store = RejectedIdentityStore()
    store.remember([_entry(frm="acc_a", to="acc_b"), _entry(frm="acc_a", to="acc_c")])
    assert store.total_count() == 2
    grouped = store.index()
    assert len(grouped[("email", "aaaa1111cccc")]) == 2


def test_forget_and_forget_for_txns(db_isolated):
    store = RejectedIdentityStore()
    a, b = store.remember([_entry(ref="r1", txn_id="txn_1"), _entry(ref="r2", txn_id="txn_2")])
    assert store.forget(a["id"]) is True
    assert store.forget(a["id"]) is False
    assert store.forget_for_txns(["txn_2", "txn_nope"]) == 1
    assert store.total_count() == 0


def test_recent_is_newest_first_and_paged(db_isolated):
    store = RejectedIdentityStore()
    store.remember([_entry(ref="r1", rejected_at="2026-01-01T00:00:00Z")])
    store.remember([_entry(ref="r2", rejected_at="2026-02-01T00:00:00Z")])
    store.remember([_entry(ref="r3", rejected_at="2026-03-01T00:00:00Z")])
    assert [r["ref"] for r in store.recent(2)] == ["r3", "r2"]
    assert [r["ref"] for r in store.recent(2, offset=2)] == ["r1"]
