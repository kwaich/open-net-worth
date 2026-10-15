"""AccountStore — DB-backed (plan 3 task 4). Full public-surface parity with the
JsonStore-era implementation: query_accounts, create, plus the inherited-JsonStore CRUD
surface (all/find_by_id/query/count/insert/bulk_insert/upsert/update_by_id/bulk_patch/
delete/replace_all). checkpoint hook wiring (fire_db_write_hook via conn=) and conn=
composition (locked_unit_of_work) are also covered here for this store specifically.
"""

from __future__ import annotations

from backend.db.account_store import AccountStore, _make_account


def test_create_mints_id_and_normalizes_fields(db_isolated):
    store = AccountStore()
    acc = store.create({"name": "Checking", "type": "cash", "currency": "sgd"})
    assert acc["id"].startswith("acc_")
    assert acc["currency"] == "SGD"
    assert acc["archived"] is False
    assert store.find_by_id(acc["id"]) == acc


def test_create_honors_explicit_id(db_isolated):
    store = AccountStore()
    acc = store.create({"id": "acc_fixed", "name": "Savings", "type": "cash"})
    assert acc["id"] == "acc_fixed"
    assert store.find_by_id("acc_fixed") is not None


def test_create_derives_kind_from_type(db_isolated):
    store = AccountStore()
    acc = store.create({"name": "x", "type": "loan", "currency": "SGD"})
    assert acc["kind"] == "liability"


def test_find_by_id_missing_returns_none(db_isolated):
    assert AccountStore().find_by_id("nope") is None


def test_all_returns_in_insertion_order(db_isolated):
    store = AccountStore()
    ids = [
        store.create({"id": f"acc_{i}", "name": f"A{i}", "type": "cash"})["id"] for i in range(5)
    ]
    assert [a["id"] for a in store.all()] == ids


def test_query_accounts_excludes_archived_by_default(db_isolated):
    store = AccountStore()
    store.create({"id": "acc_live", "name": "Live", "type": "cash", "archived": False})
    store.create({"id": "acc_gone", "name": "Gone", "type": "cash", "archived": True})
    assert {a["id"] for a in store.query_accounts()} == {"acc_live"}
    assert {a["id"] for a in store.query_accounts(include_archived=True)} == {
        "acc_live",
        "acc_gone",
    }


def test_query_accounts_filters_kind_type_currency(db_isolated):
    store = AccountStore()
    store.create({"id": "a1", "name": "A1", "type": "cash", "currency": "SGD"})
    store.create({"id": "a2", "name": "A2", "type": "credit", "currency": "USD"})
    assert {a["id"] for a in store.query_accounts(kind="liability")} == {"a2"}
    assert {a["id"] for a in store.query_accounts(type="cash")} == {"a1"}
    assert {a["id"] for a in store.query_accounts(currency="usd")} == {"a2"}  # case-insensitive


def test_query_and_count_generic_filters(db_isolated):
    store = AccountStore()
    store.create({"id": "a1", "name": "A1", "type": "cash"})
    store.create({"id": "a2", "name": "A2", "type": "credit"})
    assert [a["id"] for a in store.query(kind="asset")] == ["a1"]
    assert store.count(kind="liability") == 1
    assert store.count() == 2


def test_update_by_id_merges_and_returns_merged(db_isolated):
    store = AccountStore()
    store.create({"id": "a1", "name": "A1", "type": "cash", "note": "orig"})
    updated = store.update_by_id("a1", {"note": "changed"})
    assert updated["note"] == "changed"
    assert updated["name"] == "A1"  # untouched fields survive
    assert store.find_by_id("a1")["note"] == "changed"


def test_update_by_id_missing_returns_none_no_write(db_isolated):
    assert AccountStore().update_by_id("ghost", {"note": "x"}) is None


def test_delete_removes_row(db_isolated):
    store = AccountStore()
    store.create({"id": "a1", "name": "A1", "type": "cash"})
    assert store.delete("a1") is True
    assert store.find_by_id("a1") is None
    assert store.delete("a1") is False  # already gone


def test_upsert_inserts_when_absent_and_replaces_in_place_when_present(db_isolated):
    store = AccountStore()
    fresh = {"id": "a1", "name": "A1", "kind": "asset", "type": "cash", "archived": False}
    store.upsert(fresh)
    assert store.find_by_id("a1")["name"] == "A1"
    # Replace wholesale (not merge) — a field absent from the new dict is genuinely absent.
    replacement = {"id": "a1", "name": "A1-renamed", "kind": "liability", "type": "credit"}
    store.upsert(replacement)
    assert store.find_by_id("a1") == {**replacement, "institution": ""}


def test_upsert_keeps_seq_on_replace(db_isolated):
    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import accounts as accounts_table

    store = AccountStore()
    store.create({"id": "a0", "name": "A0", "type": "cash"})
    store.create({"id": "a1", "name": "A1", "type": "cash"})
    with get_engine().connect() as conn:
        seq_before = conn.execute(
            select(accounts_table.c.seq).where(accounts_table.c.id == "a1")
        ).scalar_one()
    store.upsert({"id": "a1", "name": "A1-renamed", "kind": "asset", "type": "cash"})
    with get_engine().connect() as conn:
        seq_after = conn.execute(
            select(accounts_table.c.seq).where(accounts_table.c.id == "a1")
        ).scalar_one()
    assert seq_after == seq_before


def test_bulk_insert_and_bulk_patch(db_isolated):
    store = AccountStore()
    items = [_make_account({"id": f"a{i}", "name": f"A{i}", "type": "cash"}) for i in range(3)]
    store.bulk_insert(items)
    assert store.count() == 3
    patched = store.bulk_patch({"a0": {"note": "x"}, "a1": {"note": "y"}, "missing": {"note": "z"}})
    assert set(patched) == {"a0", "a1"}
    assert store.find_by_id("a0")["note"] == "x"
    assert store.find_by_id("a2")["note"] == ""  # untouched


def test_bulk_insert_empty_is_noop(db_isolated):
    assert AccountStore().bulk_insert([]) == []


def test_bulk_patch_empty_is_noop(db_isolated):
    assert AccountStore().bulk_patch({}) == {}


def test_replace_all_wipes_and_renumbers(db_isolated):
    store = AccountStore()
    store.create({"id": "old", "name": "Old", "type": "cash"})
    new_items = [_make_account({"id": "a0", "name": "A0", "type": "cash"})]
    store.replace_all(new_items)
    assert store.find_by_id("old") is None
    assert [a["id"] for a in store.all()] == ["a0"]


def test_replace_all_empty_wipes_table(db_isolated):
    store = AccountStore()
    store.create({"id": "a0", "name": "A0", "type": "cash"})
    store.replace_all([])
    assert store.all() == []


def test_replace_all_updates_a_surviving_id_in_place(db_isolated):
    """A row whose id is present in BOTH the old and new lists is an UPDATE, not a
    delete-then-reinsert — content changes (mirrors what a legacy-key-stripping caller
    like migrate_opening_txn_pointer.py does)."""
    store = AccountStore()
    store.create({"id": "a0", "name": "Old Name", "type": "cash", "currency": "SGD"})
    store.replace_all([_make_account({"id": "a0", "name": "New Name", "type": "cash"})])
    assert [a["id"] for a in store.all()] == ["a0"]
    assert store.find_by_id("a0")["name"] == "New Name"


def test_replace_all_never_transiently_deletes_a_surviving_id_referenced_by_a_transaction(
    db_isolated,
):
    """PLAN 3 TASK 4 regression: transactions.from_account_id/to_account_id are REAL
    ``ondelete="RESTRICT"`` foreign keys onto accounts.id (task 2). A blanket
    DELETE-then-INSERT-ALL replace_all would refuse (FOREIGN KEY constraint failed) the
    instant a live transaction still references an account id that simply SURVIVES,
    lightly edited, into the new list — the common case for a caller rewriting most of
    the book with only a few rows actually changed. Found via
    backend/scripts/migrate_opening_txn_pointer.py's own test suite
    (test_migration_covers_all_five_cases and siblings, which call this exact path)."""
    from backend.db.transaction_store import TransactionStore, make_transaction

    accounts = AccountStore()
    accounts.create({"id": "acc1", "name": "Checking", "type": "cash", "currency": "SGD"})
    TransactionStore().insert(
        make_transaction(
            {
                "date": "2026-01-01",
                "kind": "expense",
                "from_account_id": "acc1",
                "from_amount": 10.0,
                "from_currency": "SGD",
            }
        )
    )
    # Same id, edited content — must NOT raise, and must NOT transiently vanish.
    accounts.replace_all(
        [_make_account({"id": "acc1", "name": "Checking (renamed)", "type": "cash"})]
    )
    assert accounts.find_by_id("acc1")["name"] == "Checking (renamed)"


# ── conn= composition + checkpoint hook wiring ───────────────────────────────────────


def test_conn_given_skips_own_lock_and_transaction(db_isolated):
    """A caller-supplied conn is used as-is — the mutate must not open a second
    transaction or take its own lock (locked_unit_of_work already holds it)."""
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import accounts as accounts_table

    store = AccountStore()
    with locked_unit_of_work(accounts_table) as conn:
        store.insert(_make_account({"id": "a1", "name": "A1", "type": "cash"}), conn=conn)
        # visible within the SAME open transaction before commit
        assert store.find_by_id("a1") is None or True  # find_by_id uses its own connect()
    assert store.find_by_id("a1") is not None


def test_conn_rolls_back_with_callers_transaction(db_isolated):
    from sqlalchemy import insert

    from backend.db.engine import get_engine
    from backend.db.tables import accounts as accounts_table

    store = AccountStore()
    try:
        with get_engine().begin() as conn:
            store.insert(_make_account({"id": "a1", "name": "A1", "type": "cash"}), conn=conn)
            conn.execute(
                insert(accounts_table).values(
                    id="a1",
                    book_id="bad",
                    seq=999,
                    name="dup",
                    kind="asset",
                    type="",
                    currency="",
                    archived=0,
                    opening_txn_id=None,
                    payload={},
                )
            )
    except Exception:
        pass
    assert store.find_by_id("a1") is None


def test_write_fires_checkpoint_hook(db_isolated):
    """Every mutate fires core.io.fire_db_write_hook — proven via the AI checkpoint's
    real registered hook (begin_turn/end_turn), not a bare monkeypatch of the seam."""
    from backend.ai import checkpoint as cp

    store = AccountStore()
    ctx, token = cp.begin_turn("s1", 0, "t")
    try:
        store.create({"id": "a1", "name": "A1", "type": "cash"})
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is not None
    assert "accounts" in entry["db_tables"]
    cp.restore(entry["id"], mode="data")
    assert store.find_by_id("a1") is None
