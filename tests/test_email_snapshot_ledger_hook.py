"""EM1 — the ledger write-choke promote hook.

``create_transaction`` (and ``bulk_create_transactions``) promote a stashed source-email
snapshot to referenced=True the moment a row with ``external_source="email"`` (external_ref=<message_id>) is
booked, so the snapshot survives the unreferenced-row prune. The hook is BEST-EFFORT — a
promote failure must never fail or roll back the write.

Isolated: transactions/accounts/email_snapshots are all DB-backed — db_isolated
redirects them to a fresh, empty database.
"""

from __future__ import annotations

from backend.core.email_ids import internal_email_id
from backend.core.txn_sources import primary_ref, primary_source
from backend.services import email_snapshots
from backend.services import ledger as svc


def _isolate(tmp_path, monkeypatch):
    """Caller must also request the ``db_isolated`` fixture (accounts/transactions/
    email_snapshots are all DB-backed now, plan 3 task 4 / plan 4e task 1) — it redirects
    the process-wide DB singleton before this runs, so svc.transactions/svc.accounts/
    email_snapshots._snapshots need no per-instance path-sync here."""
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    acct = svc.accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "credit", "currency": "USD"}
    )
    return acct["id"]


def _expense(account_id: str, *, external_id: str | None = None) -> dict:
    data = {
        "date": "2026-07-01",
        "kind": "expense",
        "from_account_id": account_id,
        "from_amount": 12.5,
        "from_currency": "USD",
    }
    if external_id is not None:
        # Provenance split (2026-07-20): the test still passes the legacy combined string for
        # convenience; store it as the two structured fields the ledger now reads. For an
        # ``email`` source, external_ref is the SHORT INTERNAL id the model books (a hash of
        # the raw Message-ID), not the raw header — hash it here so it matches the stash key.
        source, _, ref = external_id.partition(":")
        data["external_refs"] = [
            {
                "source": source or None,
                "ref": internal_email_id(ref) if source == "email" else ref,
            }
        ]
    return data


# ── create_transaction promotes a matching stashed snapshot ──────────────────


def test_create_transaction_promotes_stashed_snapshot(tmp_path, monkeypatch, db_isolated):
    account_id = _isolate(tmp_path, monkeypatch)
    email_snapshots.stash_snapshots(
        [
            {
                "message_id": "<m-1@x>",
                "from": "a@x.com",
                "subject": "s",
                "date": "2026-07-01",
                "body_text": "b",
            }
        ]
    )
    store = email_snapshots._snapshots
    # Stored under the SHORT INTERNAL id key (internal_email_id of the raw header).
    assert store.get(internal_email_id("<m-1@x>"))["referenced"] is False

    svc.create_transaction(_expense(account_id, external_id="email:<m-1@x>"))

    assert store.get(internal_email_id("<m-1@x>"))["referenced"] is True


def test_create_transaction_without_email_source_never_touches_snapshots(
    tmp_path, monkeypatch, db_isolated
):
    account_id = _isolate(tmp_path, monkeypatch)
    svc.create_transaction(_expense(account_id))  # no external_id at all
    svc.create_transaction(_expense(account_id, external_id="recurring:task-1:2026-07-01"))
    # No snapshot store file should even need to exist — nothing to assert-false on, just
    # confirm neither call raised (best-effort no-op path).


def test_create_transaction_promote_failure_does_not_fail_the_write(
    tmp_path, monkeypatch, db_isolated
):
    """A monkeypatched promote_for_email that raises must NOT block the transaction
    write — this is the load-bearing best-effort guarantee (a corrupt snapshot store can
    never take down the ledger)."""
    account_id = _isolate(tmp_path, monkeypatch)

    def _boom(source, ref):
        raise RuntimeError("snapshot store is on fire")

    monkeypatch.setattr(email_snapshots, "promote_for_email", _boom)

    stored = svc.create_transaction(_expense(account_id, external_id="email:<m-2@x>"))
    assert primary_source(stored) == "email"
    assert primary_ref(stored) == internal_email_id("<m-2@x>")
    assert len(svc.transactions.all()) == 1


# ── composed (conn=) callers must not self-deadlock ────────────────────────────


def test_create_transaction_composed_does_not_self_deadlock_and_still_promotes(
    tmp_path, monkeypatch, db_isolated
):
    """Storage plan 4e task 1 finding: EmailSnapshotStore became DB-backed, so a caller
    that composes ``create_transaction`` into its OWN still-open ``locked_unit_of_work``
    (e.g. a trade choke's income/proceeds leg, ``conn=...``) used to hit SQLite's
    'database is locked' the moment the promote hook tried to open a SECOND write
    transaction against an engine still holding the outer one's write lock — the exact
    self-deadlock class ``_after_outer_commit`` already fixed for payee/automation writes
    (plan 4d task 2). The fix routes the promote through that SAME seam: this proves the
    composed path no longer raises, and that the promote still lands, deferred to AFTER
    the outer transaction commits."""
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import transactions as transactions_table
    from backend.db.tables import txn_audit as txn_audit_table

    account_id = _isolate(tmp_path, monkeypatch)
    email_snapshots.stash_snapshots(
        [
            {
                "message_id": "<m-composed@x>",
                "from": "a@x.com",
                "subject": "s",
                "date": "2026-07-01",
                "body_text": "b",
            }
        ]
    )
    store = email_snapshots._snapshots
    assert store.get(internal_email_id("<m-composed@x>"))["referenced"] is False

    with locked_unit_of_work(transactions_table, txn_audit_table) as conn:
        stored = svc.create_transaction(
            _expense(account_id, external_id="email:<m-composed@x>"), conn=conn
        )
        # Still inside the outer transaction: the deferred promote must not have run yet
        # (on_commit fires strictly after commit) — proves the defer is real, not a no-op.
        assert store.get(internal_email_id("<m-composed@x>"))["referenced"] is False

    # After commit: the deferred promote has now fired exactly once.
    assert store.get(internal_email_id("<m-composed@x>"))["referenced"] is True
    assert primary_source(stored) == "email"


# ── bulk_create_transactions covers the same hook ─────────────────────────────


def test_bulk_create_transactions_promotes_stashed_snapshots(tmp_path, monkeypatch, db_isolated):
    account_id = _isolate(tmp_path, monkeypatch)
    email_snapshots.stash_snapshots(
        [
            {
                "message_id": "<m-a@x>",
                "from": "a@x.com",
                "subject": "s1",
                "date": "2026-07-01",
                "body_text": "b1",
            },
            {
                "message_id": "<m-b@x>",
                "from": "a@x.com",
                "subject": "s2",
                "date": "2026-07-02",
                "body_text": "b2",
            },
        ]
    )
    store = email_snapshots._snapshots
    assert store.get(internal_email_id("<m-a@x>"))["referenced"] is False
    assert store.get(internal_email_id("<m-b@x>"))["referenced"] is False

    svc.bulk_create_transactions(
        [
            _expense(account_id, external_id="email:<m-a@x>"),
            _expense(account_id, external_id="email:<m-b@x>"),
        ]
    )

    assert store.get(internal_email_id("<m-a@x>"))["referenced"] is True
    assert store.get(internal_email_id("<m-b@x>"))["referenced"] is True


def test_bulk_create_transactions_promote_failure_does_not_fail_the_batch(
    tmp_path, monkeypatch, db_isolated
):
    account_id = _isolate(tmp_path, monkeypatch)

    def _boom(source, ref):
        raise RuntimeError("snapshot store is on fire")

    monkeypatch.setattr(email_snapshots, "promote_for_email", _boom)

    created, skipped = svc.bulk_create_transactions(
        [_expense(account_id, external_id="email:<m-c@x>")]
    )
    assert len(created) == 1
    assert skipped == []
