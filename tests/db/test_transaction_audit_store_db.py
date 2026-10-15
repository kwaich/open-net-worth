"""TransactionAuditStore — DB-backed (plan 3 task 4). Full public-surface parity:
append/append_many (with the MAX_EVENTS cap-drop-oldest behavior), for_txn/has_any_for
(Python-side scans, ts-ascending), plus the inherited-JsonStore CRUD surface. This store
is checkpoint-EXCLUDED (forensic record) — proven here directly against the real
ai.checkpoint registry, not just asserted.
"""

from __future__ import annotations

from backend.db.transaction_audit_store import TransactionAuditStore


def _event(eid, txn_id="t1", ts="2026-01-01T00:00:00Z", **kw):
    return {
        "id": eid,
        "txn_id": txn_id,
        "op": "create",
        "ts": ts,
        "actor": "user",
        "surface": "web",
        **kw,
    }


def test_append_and_find_by_id(db_isolated):
    store = TransactionAuditStore()
    ev = store.append(_event("e1"))
    assert ev["id"] == "e1"
    assert store.find_by_id("e1") == ev


def test_append_many_batches_one_write(db_isolated):
    store = TransactionAuditStore()
    events = [_event(f"e{i}") for i in range(5)]
    store.append_many(events)
    assert store.count() == 5


def test_append_many_empty_is_noop(db_isolated):
    assert TransactionAuditStore().append_many([]) == []


def test_for_txn_returns_ts_ascending(db_isolated):
    store = TransactionAuditStore()
    store.append(_event("e3", txn_id="t1", ts="2026-01-03T00:00:00Z"))
    store.append(_event("e1", txn_id="t1", ts="2026-01-01T00:00:00Z"))
    store.append(_event("e2", txn_id="t1", ts="2026-01-02T00:00:00Z"))
    store.append(_event("other", txn_id="t2", ts="2026-01-01T00:00:00Z"))
    ids = [e["id"] for e in store.for_txn("t1")]
    assert ids == ["e1", "e2", "e3"]


def test_has_any_for(db_isolated):
    store = TransactionAuditStore()
    store.append(_event("e1", txn_id="t1"))
    assert store.has_any_for("t1") is True
    assert store.has_any_for("t2") is False


def test_cap_drops_oldest_by_seq(db_isolated, monkeypatch):
    """MAX_EVENTS caps the table, dropping the OLDEST (lowest seq) rows first —
    mirrors JsonStore's items[-MAX_EVENTS:] trim after append."""
    import backend.db.transaction_audit_store as mod

    monkeypatch.setattr(mod, "MAX_EVENTS", 3)
    store = TransactionAuditStore()
    for i in range(5):
        store.append(_event(f"e{i}"))
    assert store.count() == 3
    remaining = {e["id"] for e in store.all()}
    assert remaining == {"e2", "e3", "e4"}  # oldest two (e0, e1) dropped


def test_cap_applies_after_append_many_too(db_isolated, monkeypatch):
    import backend.db.transaction_audit_store as mod

    monkeypatch.setattr(mod, "MAX_EVENTS", 3)
    store = TransactionAuditStore()
    store.append_many([_event(f"e{i}") for i in range(5)])
    assert store.count() == 3


def test_generic_crud_surface(db_isolated):
    store = TransactionAuditStore()
    store.insert(_event("e1"))
    assert store.find_by_id("e1") is not None
    merged = store.update_by_id("e1", {"note": "x"})
    assert merged["note"] == "x"
    assert store.delete("e1") is True
    assert store.find_by_id("e1") is None


def test_excluded_from_checkpoints(db_isolated):
    """txn_audit is a forensic record — a checkpoint must never capture or restore it
    (ai.checkpoint._EXCLUDED_TABLES)."""
    from backend.ai import checkpoint as cp

    store = TransactionAuditStore()
    ctx, token = cp.begin_turn("s1", 0, "t")
    try:
        store.append(_event("e1"))
    finally:
        entry = cp.end_turn(ctx, token)
    # Either no checkpoint at all (nothing else touched), or txn_audit absent from it.
    assert entry is None or "txn_audit" not in entry.get("db_tables", [])


# ── SAVEPOINT isolation on a shared conn (fix round 1, plan 3 task 4) ─────────────
#
# services.audit.record_txn_event/record_txn_events are documented to NEVER RAISE — a
# provenance failure must not break the ledger write it documents. Riding a ledger
# choke's SHARED locked_unit_of_work transaction (this task) made that swallow
# dialect-unsafe: SQLite tolerates a failed statement inside an open transaction, but
# Postgres aborts the WHOLE session-level transaction on ANY statement failure, so a bare
# failing audit insert would poison the caller's transaction — the swallow would hide it,
# and the caller's own later COMMIT would then raise, rolling back a ledger write that had
# already logically succeeded. append/append_many now wrap their writes in
# conn.begin_nested() (a SAVEPOINT) whenever conn is given, mirroring
# ai.checkpoint.CheckpointContext.record_db_before's own proven Postgres-divergence fix.


def test_conn_given_wraps_writes_in_a_savepoint(db_isolated, monkeypatch):
    """Proven directly by spying on Connection.begin_nested, not inferred from a passing
    test: append(conn=<shared>) must take a savepoint; append(conn=None) — its own short,
    independent transaction that nothing else shares — must NOT (there is nothing a
    savepoint would protect there, and the fix explicitly leaves that path unchanged)."""
    from sqlalchemy import Connection

    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import txn_audit

    calls: list[str] = []
    real_begin_nested = Connection.begin_nested

    def _spy(self, *a, **kw):
        calls.append("begin_nested")
        return real_begin_nested(self, *a, **kw)

    monkeypatch.setattr(Connection, "begin_nested", _spy)
    store = TransactionAuditStore()

    with locked_unit_of_work(txn_audit) as conn:
        store.append(_event("e1"), conn=conn)
    assert calls == ["begin_nested"], "append(conn=<shared>) must savepoint-isolate its write"

    calls.clear()
    store.append(_event("e2"))  # conn=None — unchanged path, own transaction
    assert calls == [], "append(conn=None) must NOT take a savepoint — nothing to protect"


def test_a_failing_append_on_a_shared_conn_leaves_it_usable_for_more_statements(db_isolated):
    """The literal proof the savepoint exists for: force a GENUINE DATABASE-LEVEL insert
    failure (a real primary-key collision — id "e1" already exists — not a pre-SQL Python
    raise, which never reaches the server at all and so cannot exercise Postgres's real
    whole-transaction-abort behavior), catch it exactly as services.audit's own swallow
    would, then confirm a SUBSEQUENT, unrelated statement on the SAME shared conn still
    succeeds and the whole transaction still commits. SQLite tolerates a failed
    mid-transaction statement even without a savepoint, so this specific assertion
    doesn't discriminate the fix on SQLite alone — the real proof is the SAME test
    exercised against Postgres by the dialect run (tests/db, both dialects), where an
    unprotected failure DOES abort the whole transaction (empirically reproduced: this
    exact test, pre-fix, fails against a real postgres:16 container with
    `InFailedSqlTransaction` on the second `store.append` — pure SQLite RED-verification
    is not enough for this class of bug)."""
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import txn_audit

    store = TransactionAuditStore()
    store.append(_event("e1"))  # pre-existing row — a genuine id-collision target

    with locked_unit_of_work(txn_audit) as conn:
        try:
            store.append(_event("e1"), conn=conn)  # real PK collision -> DB-level IntegrityError
        except Exception:
            pass  # mirrors services.audit.record_txn_event's own best-effort swallow
        store.append(_event("e2"), conn=conn)  # the subsequent statement that must still work

    assert {e["id"] for e in store.all()} == {"e1", "e2"}


def test_create_transaction_commits_the_ledger_write_even_when_the_audit_append_fails(
    db_isolated, monkeypatch
):
    """End-to-end, through the REAL choke: services.ledger.create_transaction wraps the
    transaction insert AND the audit append in ONE locked_unit_of_work. Forcing the audit
    insert to raise must NOT lose the ledger write — it must commit exactly as if the
    audit call had succeeded, no exception escapes create_transaction, and the system
    keeps working normally for the NEXT call (proof the engine wasn't left broken)."""
    from backend.services import audit as audit_service
    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    acct = svc.accounts.create({"name": "Checking", "kind": "asset", "type": "cash"})

    # A GENUINE database-level failure, not a pre-SQL Python raise (which never reaches
    # the server and so cannot exercise Postgres's real whole-transaction-abort behavior
    # — see the sibling test's docstring for why that distinction matters here). Pre-seed
    # a txn_audit row under a KNOWN id, then pin services.audit's id minter to return that
    # SAME id ONCE — the choke's own audit INSERT then collides with it for real; every
    # later mint (the second create below) falls through to the real minter normally.
    TransactionAuditStore().append(_event("evt_collision"))
    real_new_id = audit_service.new_id
    collided = {"n": 0}

    def _new_id_first_call_collides(prefix):
        collided["n"] += 1
        return "evt_collision" if collided["n"] == 1 else real_new_id(prefix)

    monkeypatch.setattr(audit_service, "new_id", _new_id_first_call_collides)

    stored = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-01-01",
            "description": "coffee",
            "from_account_id": acct["id"],
            "from_amount": 4.5,
            "from_currency": "SGD",
        }
    )  # must not raise

    assert svc.transactions.find_by_id(stored["id"]) is not None  # the ledger write committed
    assert (
        TransactionAuditStore().has_any_for(stored["id"]) is False
    )  # the audit write genuinely failed

    # The engine is not left broken — a SECOND, ordinary create (audit succeeds this time)
    # works normally.
    second = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-01-02",
            "description": "lunch",
            "from_account_id": acct["id"],
            "from_amount": 12.0,
            "from_currency": "SGD",
        }
    )
    assert svc.transactions.find_by_id(second["id"]) is not None
    assert TransactionAuditStore().has_any_for(second["id"]) is True
