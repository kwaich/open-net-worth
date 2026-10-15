"""Money-core schema (plan 3, task 2): accounts, transactions, transaction_splits,
transaction_external_refs, txn_audit, import_batches, statements_meta, merge_journal.

Per-table create + FK-enforcement, CASCADE (splits/refs), the RESTRICT probe
(transactions -> accounts), CheckConstraint violations (transfer-legs, boolean domains),
and the two dedup/ordering unique indexes — the Plan-1/2 test pattern (test_tables.py,
test_evidence_cutover.py) applied to the money core. `db_isolated` runs against the REAL
engine singleton (backend.db.engine.get_engine()), whose connect-event listener already
sets `PRAGMA foreign_keys=ON` for every SQLite connection — nothing extra needed here.

FIX ROUND 1 (reviewer, against the live data file, read-only) added: `accounts.kind`
coverage; `payload`-column presence on `transactions`/`transaction_splits`/
`transaction_external_refs` (the verbatim-payload ruling); the hazard-column
`server_default` coverage on `transactions`/`statements_meta`; and the deliberate
NULL-source dedup-index gap test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from backend.db import engine as eng
from backend.db import tables as t

BOOK = t.DEFAULT_BOOK_ID


# ── accounts ───────────────────────────────────────────────────────────────────


def test_accounts_create_and_book_fk_enforced(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.accounts).values(
                id="acc1",
                book_id=BOOK,
                seq=0,
                name="Checking",
                kind="asset",
                type="cash",
                currency="SGD",
                opening_txn_id=None,
                data={"id": "acc1"},
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.accounts).where(t.accounts.c.id == "acc1")).one()
        assert (row.name, row.kind, row.currency, row.archived) == ("Checking", "asset", "SGD", 0)

    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.accounts).values(
                    id="acc2",
                    book_id="book_nope",
                    seq=1,
                    name="X",
                    kind="asset",
                    type="cash",
                    currency="SGD",
                    data={},
                )
            )


def test_accounts_book_seq_unique(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.accounts).values(
                id="acc1",
                book_id=BOOK,
                seq=0,
                name="A",
                kind="asset",
                type="cash",
                currency="SGD",
                data={},
            )
        )
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.accounts).values(
                    id="acc2",
                    book_id=BOOK,
                    seq=0,
                    name="B",
                    kind="asset",
                    type="cash",
                    currency="SGD",
                    data={},
                )
            )


def test_accounts_kind_not_null_enforced(db_isolated):
    """`kind` is FIX ROUND 1's promoted addition (net-worth math's only load-bearing field,
    per CLAUDE.md) — a bare insert omitting it must fail, not silently default."""
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.accounts).values(
                    id="acc1",
                    book_id=BOOK,
                    seq=0,
                    name="A",
                    type="cash",
                    currency="SGD",
                    data={},
                )
            )


# ── transactions ───────────────────────────────────────────────────────────────


def _make_account(c, acc_id: str, seq: int = 0, kind: str = "asset") -> None:
    c.execute(
        insert(t.accounts).values(
            id=acc_id,
            book_id=BOOK,
            seq=seq,
            name=acc_id,
            kind=kind,
            type="cash",
            currency="SGD",
            data={},
        )
    )


def _base_txn(**overrides) -> dict:
    row = dict(
        id="txn1",
        book_id=BOOK,
        seq=0,
        date="2026-08-07",
        kind="expense",
        category=None,
        from_account_id=None,
        to_account_id=None,
        source="manual",
        description="",
        memo="",
        created_at="2026-08-07T00:00:00Z",
        tags=[],
        merged_from=[],
        payload={"id": "txn1"},
    )
    row.update(overrides)
    return row


def test_transactions_create_and_book_fk_enforced(db_isolated):
    with eng.get_engine().begin() as c:
        _make_account(c, "acc1")
        c.execute(insert(t.transactions).values(**_base_txn(from_account_id="acc1")))
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.transactions).where(t.transactions.c.id == "txn1")).one()
        assert (row.kind, row.cleared, row.deleted, row.category) == ("expense", 0, 0, None)

    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(insert(t.transactions).values(**_base_txn(id="txn2", book_id="book_nope")))


def test_transactions_payload_round_trips_verbatim(db_isolated):
    """RULING (fix round 1): `payload` is the round-trip source of truth — it must carry the
    row EXACTLY as given, including a key no promoted column mirrors, and distinguish that
    from what the promoted columns derive."""
    verbatim = {
        "id": "txn1",
        "date": "2026-08-07",
        "kind": "expense",
        # a real key some historical rows carry that no promoted column exists for
        "note_from_a_retired_field": "kept verbatim regardless",
    }
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn(payload=verbatim)))
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.transactions).where(t.transactions.c.id == "txn1")).one()
        assert row.payload == verbatim


def test_transactions_hazard_columns_default_when_omitted(db_isolated):
    """FIX ROUND 1 (Important 1): `created_at`/`memo`/`tags`/`merged_from` are still NOT NULL
    query-surface columns, but a write that omits them (the shape task 3's absorb takes for a
    row whose source key is genuinely ABSENT) must not fail — it lands on the type-appropriate
    default, with the true absence recorded only in `payload`."""
    minimal = {
        "id": "txn_minimal",
        "book_id": BOOK,
        "seq": 0,
        "date": "2026-08-07",
        "kind": "expense",
        "source": "manual",
        "description": "",
        "payload": {"id": "txn_minimal", "date": "2026-08-07"},  # note: no created_at/memo/tags
    }
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**minimal))
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.transactions).where(t.transactions.c.id == "txn_minimal")).one()
        assert (row.created_at, row.memo, row.tags, row.merged_from) == ("", "", [], [])
        assert "created_at" not in row.payload  # the true absence lives only in payload


def test_transactions_book_seq_unique(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn()))
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(insert(t.transactions).values(**_base_txn(id="txn2")))


def test_transactions_from_account_fk_enforced(db_isolated):
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(insert(t.transactions).values(**_base_txn(from_account_id="acc_nope")))


def test_transactions_account_delete_restricted_while_referenced(db_isolated):
    """The RESTRICT probe: an account referenced by a (non-deleted) transaction cannot be
    deleted — the `delete_account` 409-while-referenced invariant, now DB-enforced."""
    with eng.get_engine().begin() as c:
        _make_account(c, "acc1")
        c.execute(
            insert(t.transactions).values(**_base_txn(kind="expense", from_account_id="acc1"))
        )
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(t.accounts.delete().where(t.accounts.c.id == "acc1"))
    # unreferenced account deletes cleanly
    with eng.get_engine().begin() as c:
        _make_account(c, "acc2", seq=1)
    with eng.get_engine().begin() as c:
        c.execute(t.accounts.delete().where(t.accounts.c.id == "acc2"))
    with eng.get_engine().connect() as c:
        assert c.execute(select(t.accounts).where(t.accounts.c.id == "acc2")).first() is None


def test_transactions_transfer_requires_both_legs_check(db_isolated):
    with eng.get_engine().begin() as c:
        _make_account(c, "acc1")
        _make_account(c, "acc2", seq=1)
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.transactions).values(
                    **_base_txn(kind="transfer", from_account_id="acc1", to_account_id=None)
                )
            )
    # both legs set -> the check passes
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.transactions).values(
                **_base_txn(kind="transfer", from_account_id="acc1", to_account_id="acc2")
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.transactions).where(t.transactions.c.id == "txn1")).one()
        assert (row.from_account_id, row.to_account_id) == ("acc1", "acc2")


@pytest.mark.parametrize("column", ["cleared", "deleted", "needs_review", "base_amount_pinned"])
def test_transactions_boolean_check_constraints_reject_out_of_domain(db_isolated, column):
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(insert(t.transactions).values(**_base_txn(**{column: 2})))


# ── transaction_splits ───────────────────────────────────────────────────────────


def test_transaction_splits_payload_round_trips_verbatim(db_isolated):
    """RULING (fix round 1): payload holds the embedded split dict exactly as it appeared in
    the parent's `splits` list, before `split_id`/`idx` synthesis."""
    embedded = {"category": "food", "amount": 10.0, "memo": ""}
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn()))
        c.execute(
            insert(t.transaction_splits).values(
                split_id="spl1",
                book_id=BOOK,
                txn_id="txn1",
                idx=0,
                amount=10.0,
                category="food",
                memo="",
                payload=embedded,
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(
            select(t.transaction_splits).where(t.transaction_splits.c.split_id == "spl1")
        ).one()
        assert row.payload == embedded


def test_transaction_splits_cascade_on_txn_delete(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn()))
        c.execute(
            insert(t.transaction_splits).values(
                split_id="spl1",
                book_id=BOOK,
                txn_id="txn1",
                idx=0,
                amount=10.0,
                category="food",
                memo="",
                payload={"category": "food", "amount": 10.0, "memo": ""},
            )
        )
        c.execute(
            insert(t.transaction_splits).values(
                split_id="spl2",
                book_id=BOOK,
                txn_id="txn1",
                idx=1,
                amount=5.0,
                category="transport",
                memo="",
                payload={"category": "transport", "amount": 5.0, "memo": ""},
            )
        )
        c.execute(t.transactions.delete().where(t.transactions.c.id == "txn1"))
    with eng.get_engine().connect() as c:
        remaining = c.execute(
            select(t.transaction_splits).where(t.transaction_splits.c.txn_id == "txn1")
        ).fetchall()
        assert remaining == []


def test_transaction_splits_txn_fk_enforced(db_isolated):
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.transaction_splits).values(
                    split_id="spl1",
                    book_id=BOOK,
                    txn_id="txn_nope",
                    idx=0,
                    amount=10.0,
                    category="food",
                    memo="",
                    payload={"category": "food", "amount": 10.0, "memo": ""},
                )
            )


def test_transaction_splits_book_txn_idx_unique(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn()))
        c.execute(
            insert(t.transaction_splits).values(
                split_id="spl1",
                book_id=BOOK,
                txn_id="txn1",
                idx=0,
                amount=10.0,
                category="food",
                memo="",
                payload={"category": "food", "amount": 10.0, "memo": ""},
            )
        )
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.transaction_splits).values(
                    split_id="spl2",
                    book_id=BOOK,
                    txn_id="txn1",
                    idx=0,
                    amount=5.0,
                    category="transport",
                    memo="",
                    payload={"category": "transport", "amount": 5.0, "memo": ""},
                )
            )


# ── transaction_external_refs ─────────────────────────────────────────────────


def test_transaction_external_refs_payload_round_trips_verbatim(db_isolated):
    """RULING (fix round 1): payload holds the embedded ext-ref dict exactly as it appeared in
    the parent's `external_refs` list, before `ref_id`/`idx` synthesis."""
    embedded = {"source": "email", "ref": "msg-abc"}
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn()))
        c.execute(
            insert(t.transaction_external_refs).values(
                ref_id="ref1",
                book_id=BOOK,
                txn_id="txn1",
                idx=0,
                source="email",
                ref="msg-abc",
                payload=embedded,
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(
            select(t.transaction_external_refs).where(
                t.transaction_external_refs.c.ref_id == "ref1"
            )
        ).one()
        assert row.payload == embedded


def test_transaction_external_refs_cascade_on_txn_delete(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn()))
        c.execute(
            insert(t.transaction_external_refs).values(
                ref_id="ref1",
                book_id=BOOK,
                txn_id="txn1",
                idx=0,
                source="email",
                ref="msg-abc",
                payload={"source": "email", "ref": "msg-abc"},
            )
        )
        c.execute(t.transactions.delete().where(t.transactions.c.id == "txn1"))
    with eng.get_engine().connect() as c:
        remaining = c.execute(
            select(t.transaction_external_refs).where(
                t.transaction_external_refs.c.txn_id == "txn1"
            )
        ).fetchall()
        assert remaining == []


def test_transaction_external_refs_txn_fk_enforced(db_isolated):
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.transaction_external_refs).values(
                    ref_id="ref1",
                    book_id=BOOK,
                    txn_id="txn_nope",
                    idx=0,
                    source="email",
                    ref="msg-abc",
                    payload={"source": "email", "ref": "msg-abc"},
                )
            )


def test_transaction_external_refs_source_ref_index_is_non_unique_by_design(db_isolated):
    """RENAMED + REWRITTEN, plan 3 task 4 (was `..._source_ref_unique`, and asserted the
    OPPOSITE — that a second row sharing (book_id, source, ref) raises IntegrityError).
    That assumption was WRONG: `services.ledger.merge_transactions` legitimately leaves
    the SAME authoritative (non-null-source) external ref on both an absorbed row and the
    canonical row that inherited it — a real, INTENDED post-merge state (ADR 2026-07-21;
    the mechanism is unchanged by this task, only now actually exercised by a real store
    test — see `db/transaction_store.py`'s `by_external_ref` docstring and ADR
    2026-08-07-external-ref-index-non-unique). A UNIQUE constraint here made every
    ordinary dedup-engine merge involving an authoritative ref raise. The index
    (`ix_transaction_external_refs_book_source_ref`) is now `unique=False` — a lookup
    accelerant only; `transaction_store.by_external_ref`'s Python tuple-compare is — and
    always was — the sole dedup authority (see the null-source test above, which
    documented the SAME "index doesn't enforce it" fact for one case before this task
    found it was actually the general rule)."""
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn(id="txn1")))
        c.execute(insert(t.transactions).values(**_base_txn(id="txn2", seq=1)))
        c.execute(
            insert(t.transaction_external_refs).values(
                ref_id="ref1",
                book_id=BOOK,
                txn_id="txn1",
                idx=0,
                source="email",
                ref="msg-abc",
                payload={"source": "email", "ref": "msg-abc"},
            )
        )
        # a SECOND row on a DIFFERENT txn sharing the SAME (source, ref) — must NOT raise;
        # this is exactly the shape merge_transactions produces on every real merge that
        # inherits an authoritative ref.
        c.execute(
            insert(t.transaction_external_refs).values(
                ref_id="ref2",
                book_id=BOOK,
                txn_id="txn2",
                idx=0,
                source="email",
                ref="msg-abc",
                payload={"source": "email", "ref": "msg-abc"},
            )
        )
    with eng.get_engine().connect() as c:
        rows = c.execute(
            select(t.transaction_external_refs).where(
                t.transaction_external_refs.c.ref == "msg-abc"
            )
        ).fetchall()
        assert len(rows) == 2


def test_transaction_external_refs_source_nullable(db_isolated):
    """A missing/empty source stays None (a bare no-prefix ref) — real documented domain
    state, not an omission."""
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn()))
        c.execute(
            insert(t.transaction_external_refs).values(
                ref_id="ref1",
                book_id=BOOK,
                txn_id="txn1",
                idx=0,
                source=None,
                ref="bare-ref",
                payload={"source": None, "ref": "bare-ref"},
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(
            select(t.transaction_external_refs).where(
                t.transaction_external_refs.c.ref_id == "ref1"
            )
        ).one()
        assert row.source is None


def test_transaction_external_refs_null_source_duplicate_not_caught_by_index(db_isolated):
    """Originally FIX ROUND 1 (Important 3) — task 2 found this specific case (SQL
    NULL<>NULL means a unique index does NOT reject two rows sharing (source=NULL, the
    same ref), on either dialect, standard ANSI behavior). PLAN 3 TASK 4 SUPERSEDES the
    "unique index, null-source is its one gap" framing: the index
    (`ix_transaction_external_refs_book_source_ref`) is now `unique=False` OUTRIGHT — task
    4's own store tests found a SEPARATE, non-null-source case a unique constraint here
    would break (services.ledger.merge_transactions legitimately leaves the same
    authoritative external ref on both the absorbed and canonical rows after a merge; see
    tables.py's long comment on this index and ADR 2026-08-07-external-ref-index-non-unique).
    This null-source case is now just ONE instance of the general rule, not a special
    exception to a unique constraint — kept as its own regression test since it was the
    ORIGINAL discovery and remains a real, valid scenario. The app-level tuple-compare in
    `transaction_store.by_external_ref` (`(None, "x") == (None, "x")` IS True in Python,
    unlike SQL) is — and always was — the sole dedup authority; this index is a lookup
    accelerant only, for every case, not an enforcement mechanism for any of them."""
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn(id="txn1")))
        c.execute(insert(t.transactions).values(**_base_txn(id="txn2", seq=1)))
        c.execute(
            insert(t.transaction_external_refs).values(
                ref_id="ref1",
                book_id=BOOK,
                txn_id="txn1",
                idx=0,
                source=None,
                ref="dup-ref",
                payload={"source": None, "ref": "dup-ref"},
            )
        )
        # a SECOND (NULL, "dup-ref") pair, on a different txn — must NOT raise
        c.execute(
            insert(t.transaction_external_refs).values(
                ref_id="ref2",
                book_id=BOOK,
                txn_id="txn2",
                idx=0,
                source=None,
                ref="dup-ref",
                payload={"source": None, "ref": "dup-ref"},
            )
        )
    with eng.get_engine().connect() as c:
        rows = c.execute(
            select(t.transaction_external_refs).where(
                t.transaction_external_refs.c.ref == "dup-ref"
            )
        ).fetchall()
        assert len(rows) == 2


# ── txn_audit ──────────────────────────────────────────────────────────────────


def test_txn_audit_create_and_book_fk_enforced(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(insert(t.transactions).values(**_base_txn()))
        c.execute(
            insert(t.txn_audit).values(
                id="evt1",
                book_id=BOOK,
                seq=0,
                txn_id="txn1",
                at="2026-08-07T00:00:00Z",
                actor="user",
                surface="web",
                payload={"id": "evt1", "op": "create"},
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.txn_audit).where(t.txn_audit.c.id == "evt1")).one()
        assert (row.actor, row.surface, row.txn_id) == ("user", "web", "txn1")

    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.txn_audit).values(
                    id="evt2",
                    book_id="book_nope",
                    seq=1,
                    txn_id="txn1",
                    at="2026-08-07T00:00:00Z",
                    actor="user",
                    surface="web",
                    payload={},
                )
            )


def test_txn_audit_txn_id_has_no_fk_forensic_record_outlives_the_row(db_isolated):
    """Deliberate: txn_id carries NO FK — a forensic record must never be cascade-vulnerable,
    and must remain insertable even against an id with no (or no longer any) matching row."""
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.txn_audit).values(
                id="evt1",
                book_id=BOOK,
                seq=0,
                txn_id="txn_never_existed",
                at="2026-08-07T00:00:00Z",
                actor="system",
                surface="unknown",
                payload={},
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.txn_audit).where(t.txn_audit.c.id == "evt1")).one()
        assert row.txn_id == "txn_never_existed"


def test_txn_audit_book_seq_unique(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.txn_audit).values(
                id="evt1",
                book_id=BOOK,
                seq=0,
                txn_id="txn1",
                at="2026-08-07T00:00:00Z",
                actor="system",
                surface="unknown",
                payload={},
            )
        )
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.txn_audit).values(
                    id="evt2",
                    book_id=BOOK,
                    seq=0,
                    txn_id="txn1",
                    at="2026-08-07T00:00:00Z",
                    actor="system",
                    surface="unknown",
                    payload={},
                )
            )


# ── import_batches ─────────────────────────────────────────────────────────────


def test_import_batches_create_and_book_fk_enforced(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.import_batches).values(
                id="imp1",
                book_id=BOOK,
                seq=0,
                created_at="2026-08-07T00:00:00Z",
                description="PocketPay Apr 2026",
                transaction_count=3,
                status="active",
                payload={"id": "imp1"},
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.import_batches).where(t.import_batches.c.id == "imp1")).one()
        assert (row.status, row.transaction_count) == ("active", 3)

    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.import_batches).values(
                    id="imp2",
                    book_id="book_nope",
                    seq=1,
                    created_at="2026-08-07T00:00:00Z",
                    description="X",
                    transaction_count=0,
                    status="active",
                    payload={},
                )
            )


def test_import_batches_book_seq_unique(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.import_batches).values(
                id="imp1",
                book_id=BOOK,
                seq=0,
                created_at="2026-08-07T00:00:00Z",
                description="A",
                transaction_count=1,
                status="active",
                payload={},
            )
        )
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.import_batches).values(
                    id="imp2",
                    book_id=BOOK,
                    seq=0,
                    created_at="2026-08-07T00:00:00Z",
                    description="B",
                    transaction_count=1,
                    status="active",
                    payload={},
                )
            )


# ── statements_meta ────────────────────────────────────────────────────────────


def _base_statement(**overrides) -> dict:
    row = dict(
        id="stmt1",
        book_id=BOOK,
        seq=0,
        created_at="2026-08-07T00:00:00Z",
        filename="apr.pdf",
        mime="application/pdf",
        size_bytes=1024,
        file_hash="deadbeef",
        file_path="statements/stmt1.pdf",
        kind="pdf_pages",
        batch_ids=[],
        payload={"id": "stmt1"},
    )
    row.update(overrides)
    return row


def test_statements_meta_create_and_book_fk_enforced(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(insert(t.statements_meta).values(**_base_statement()))
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.statements_meta).where(t.statements_meta.c.id == "stmt1")).one()
        assert (row.filename, row.meta_unavailable, row.import_meta) == ("apr.pdf", 0, None)

    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.statements_meta).values(
                    **_base_statement(id="stmt2", book_id="book_nope", seq=1)
                )
            )


def test_statements_meta_book_seq_unique(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(insert(t.statements_meta).values(**_base_statement()))
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(insert(t.statements_meta).values(**_base_statement(id="stmt2")))


def test_statements_meta_meta_unavailable_check_constraint(db_isolated):
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(insert(t.statements_meta).values(**_base_statement(meta_unavailable=2)))
    # 0/1 both pass
    with eng.get_engine().begin() as c:
        c.execute(insert(t.statements_meta).values(**_base_statement(meta_unavailable=1)))


def test_statements_meta_batch_ids_defaults_when_omitted(db_isolated):
    """FIX ROUND 1 (Important 1): `batch_ids` stays NOT NULL but defaults to an empty JSON
    array when the source row's own key is absent — mirrors the `transactions` hazard columns."""
    minimal = _base_statement()
    del minimal["batch_ids"]
    with eng.get_engine().begin() as c:
        c.execute(insert(t.statements_meta).values(**minimal))
    with eng.get_engine().connect() as c:
        row = c.execute(select(t.statements_meta).where(t.statements_meta.c.id == "stmt1")).one()
        assert row.batch_ids == []


# ── merge_journal ──────────────────────────────────────────────────────────────


def test_merge_journal_create_and_book_fk_enforced(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.merge_journal).values(
                absorbed_id="txn_absorbed",
                book_id=BOOK,
                seq=0,
                canonical_id="txn_canon",
                created_at="2026-08-07T00:00:00Z",
                payload={"absorbed_id": "txn_absorbed", "canonical_id": "txn_canon"},
            )
        )
    with eng.get_engine().connect() as c:
        row = c.execute(
            select(t.merge_journal).where(t.merge_journal.c.absorbed_id == "txn_absorbed")
        ).one()
        assert row.canonical_id == "txn_canon"

    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.merge_journal).values(
                    absorbed_id="txn_absorbed2",
                    book_id="book_nope",
                    seq=1,
                    canonical_id="txn_canon",
                    created_at="2026-08-07T00:00:00Z",
                    payload={},
                )
            )


def test_merge_journal_book_seq_unique(db_isolated):
    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.merge_journal).values(
                absorbed_id="a1",
                book_id=BOOK,
                seq=0,
                canonical_id="c1",
                created_at="2026-08-07T00:00:00Z",
                payload={},
            )
        )
    with pytest.raises(IntegrityError):
        with eng.get_engine().begin() as c:
            c.execute(
                insert(t.merge_journal).values(
                    absorbed_id="a2",
                    book_id=BOOK,
                    seq=0,
                    canonical_id="c2",
                    created_at="2026-08-07T00:00:00Z",
                    payload={},
                )
            )
