"""Money-core cutover (plan 3, task 3): revision 0007 absorbs accounts/transactions/
txn_audit/import_batches/statements/merge_journal as ONE referential unit.

Fixture-scale round-trip per store, the cross-file consistency pre-checks (dangling
opening_txn_id / dangling account ref both abort, naming the violation), deterministic
child-id re-derivation, the full `ensure_migrated` end-to-end path (all six renamed, all six
hashed into json_cutovers), the fresh-install path, and the up/down cycle — the same shape as
`test_evidence_cutover.py` / `test_instrument_store_db.py`'s cutover sections, extended to six
files landing together.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text

from backend.db import money_io as mio

_STORE_NAMES = (
    "accounts",
    "transactions",
    "txn_audit",
    "import_batches",
    "statements",
    "merge_journal",
)


def _set_database_url_for_test(url: str, monkeypatch) -> None:
    """FIX ROUND 1 (plan 3 task 5 review, FIX 2 — "false pg confidence"): the four
    ``ensure_migrated()``-driven tests below used to hardcode ``sqlite:///{tmp_path}/onw.db``
    for ``ONW_DATABASE_URL``, which meant they NEVER actually ran against Postgres even under
    CI's ``db-postgres`` job (that job sets ``ONW_TEST_DATABASE_URL``, which only the
    ``db_url`` FIXTURE — not a bare f-string — ever consults) — a false confidence gap this
    revision's own report failed to notice, since the pg docker run of `tests/db` reported
    "337/337" without anyone checking that these four tests were genuinely IN that count's
    dialect coverage. Callers now pass the ``db_url`` fixture value here instead of building
    their own sqlite literal. Postgres's ``db_url`` is the ONE shared CI server (unlike
    SQLite's fresh-tmp_path-per-test) — the SAME ``DROP SCHEMA public CASCADE; CREATE SCHEMA
    public`` scrub ``tests/db/conftest.py``'s own ``db_isolated``/``db_engine`` fixtures
    already perform, needed here too since a prior test in this file (or this same test
    re-run) would otherwise collide on the still-populated tables — reproduced directly: the
    first Postgres run after this fix landed genuinely exercised revision 0007's
    ``ensure_migrated()`` orchestration end-to-end (file rename, hash-guarded cutover rows,
    the dangling-reference abort, the downgrade cycle) against a live server for the first
    time — see the fix-round report for the pass/fail evidence."""
    if url.startswith("postgresql"):
        from sqlalchemy import create_engine, text

        scrub = create_engine(url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", url)


# ── Fixtures (deliberately cross-referential: acc_checking.opening_txn_id -> txn_open;
# txn_split/txn_historical -> acc_checking/acc_savings — the cross-file consistency check
# must pass on this set) ──────────────────────────────────────────────────────────────

ACCOUNTS = [
    {
        "id": "acc_checking",
        "name": "Checking",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "opening_txn_id": "txn_open",  # the account-with-opening-txn-id fixture
        "credit_limit": None,
        "last_four": "1234",
        "color": "#4287f5",
        "note": "primary account",
        "last_reconciled_at": None,
        "archived": False,
    },
    {
        "id": "acc_savings",
        "name": "Savings",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "opening_txn_id": None,
        "archived": False,
    },
]

TRANSACTIONS = [
    {
        "id": "txn_open",
        "date": "2026-01-01",
        "kind": "adjustment",
        "category": None,  # adjustment rows always carry category=None
        "from_account_id": None,
        "to_account_id": "acc_checking",
        "from_amount": None,
        "from_currency": None,
        "to_amount": 1000.0,
        "to_currency": "SGD",
        "base_amount": 1000.0,
        "base_currency": "SGD",
        "fx_rate": 1.0,
        "source": "manual",
        "description": "Starting Balance",
        "memo": "",
        "created_at": "2026-01-01T00:00:00Z",
        "tags": [],
        "splits": None,
        "external_refs": [],
        "merged_from": [],
        "merged_into": None,
        "cleared": True,
        "deleted": False,
    },
    {
        # the txn-with-splits-and-external_refs fixture, incl. a bare (source=None) ref
        "id": "txn_split",
        "date": "2026-01-05",
        "kind": "expense",
        "category": None,
        "from_account_id": "acc_checking",
        "to_account_id": None,
        "from_amount": 50.0,
        "from_currency": "SGD",
        "to_amount": None,
        "to_currency": None,
        "base_amount": 50.0,
        "base_currency": "SGD",
        "fx_rate": 1.0,
        "source": "csv_import",
        "description": "Supermarket",
        "memo": "weekly groceries",
        "created_at": "2026-01-05T00:00:00Z",
        "tags": ["groceries"],
        "splits": [
            {"category": "food", "amount": 30.0, "memo": "produce"},
            {"category": "household", "amount": 20.0, "memo": ""},
        ],
        "external_refs": [
            {"source": "csv", "ref": "row-1"},
            {"source": None, "ref": "bare-ref"},
        ],
        "merged_from": [],
        "merged_into": None,
        "cleared": False,
        "deleted": False,
    },
    {
        # the historical-shape fixture: created_at/memo/tags/merged_from KEYS ABSENT —
        # not None, genuinely missing, exactly the hazard-column scenario tables.py documents.
        "id": "txn_historical",
        "date": "2026-01-06",
        "kind": "income",
        "category": "salary",
        "from_account_id": None,
        "to_account_id": "acc_savings",
        "from_amount": None,
        "from_currency": None,
        "to_amount": 200.0,
        "to_currency": "SGD",
        "base_amount": 200.0,
        "base_currency": "SGD",
        "fx_rate": 1.0,
        "source": "manual",
        "description": "old-style row",
        "splits": None,
        "external_refs": [],
        "merged_into": None,
        "cleared": True,
        "deleted": False,
    },
]

TXN_AUDIT = [
    {
        "id": "evt1",
        "txn_id": "txn_split",
        "op": "create",
        "ts": "2026-01-05T00:00:01Z",
        "actor": "user",
        "surface": "web",
        "source": "csv_import",
        "session_id": None,
        "sender_id": None,
        "intent": None,
        "automation": None,
        "automation_id": None,
        "before": None,
        "after": {"id": "txn_split", "from_amount": 50.0},
        "note": None,
    },
]

IMPORT_BATCHES = [
    {
        "id": "imp1",
        "created_at": "2026-01-05T00:00:00Z",
        "description": "CSV import",
        "source_account_id": "acc_checking",
        "transaction_count": 1,
        "status": "active",
        "import_session_id": "sess1",
        "statement_ref_id": "stmt_with_rows",
        "balance_check": {"ok": True},
    },
]

STATEMENTS = [
    {
        # a statement WITH parsed_rows — the fat-payload extraction case
        "id": "stmt_with_rows",
        "created_at": "2026-01-04T00:00:00Z",
        "filename": "jan.pdf",
        "mime": "application/pdf",
        "size_bytes": 2048,
        "file_hash": "hash1",
        "file_path": "statements/stmt_with_rows.pdf",
        "kind": "pdf_pages",
        "upload_entry": {"kind": "pdf_pages", "pages": 2},
        "parsed_rows": [
            {
                "date": "2026-01-05",
                "amount": 50.0,
                "direction": "debit",
                "description": "Supermarket",
            },
        ],
        "parsed_at": "2026-01-04T01:00:00Z",
        "account_id": "acc_checking",
        "batch_ids": ["imp1"],
    },
    {
        # the DEAD-key case: `import_batch_ids`, never renamed to `batch_ids`
        "id": "stmt_dead_key",
        "created_at": "2026-01-03T00:00:00Z",
        "filename": "feb.csv",
        "mime": "text/csv",
        "size_bytes": 512,
        "file_hash": "hash2",
        "file_path": "statements/stmt_dead_key.csv",
        "kind": "text",
        "upload_entry": {"kind": "text", "text": "raw csv text"},
        "parsed_rows": None,
        "parsed_at": None,
        "account_id": None,
        "import_batch_ids": ["imp_old"],
    },
    {
        # neither key present at all
        "id": "stmt_neither",
        "created_at": "2026-01-02T00:00:00Z",
        "filename": "mar.png",
        "mime": "image/png",
        "size_bytes": 128,
        "file_hash": "hash3",
        "file_path": "statements/stmt_neither.png",
        "kind": "image",
        "upload_entry": {"kind": "image"},
        "parsed_rows": None,
        "parsed_at": None,
        "account_id": None,
    },
]

MERGE_JOURNAL = [
    {
        "absorbed_id": "txn_absorbed",
        "canonical_id": "txn_split",
        "canonical_before": {"id": "txn_split", "from_amount": 40.0},
        "absorbed_before": {"id": "txn_absorbed", "from_amount": 10.0},
        "become_transfer": False,
        "reason": "duplicate",
        "created_at": "2026-01-06T00:00:00Z",
    },
]


def _patch_all_sources_missing(monkeypatch, tmp_path):
    """Point every one of the six source seams at a file that does not exist, so a test
    exercising `command.upgrade`/`ensure_migrated` never accidentally reads whatever the
    ambient (session-shared) DATA_DIR happens to hold."""
    names = {
        "ACCOUNTS_SOURCE": "does_not_exist_accounts.json",
        "TRANSACTIONS_SOURCE": "does_not_exist_transactions.json",
        "TXN_AUDIT_SOURCE": "does_not_exist_txn_audit.json",
        "IMPORT_BATCHES_SOURCE": "does_not_exist_import_batches.json",
        "STATEMENTS_SOURCE": "does_not_exist_statements.json",
        "MERGE_JOURNAL_SOURCE": "does_not_exist_merge_journal.json",
    }
    for attr, filename in names.items():
        monkeypatch.setattr(
            f"backend.db.alembic.versions_evidence_source.{attr}", tmp_path / filename
        )


# ── Round-trip per store (fixture-scale absorb) ───────────────────────────────────────


def test_import_then_export_accounts_round_trips_exactly(db_engine):
    with db_engine.begin() as conn:
        n = mio.import_accounts(conn, ACCOUNTS)
    assert n == len(ACCOUNTS)
    with db_engine.connect() as conn:
        # ``color`` is a retired account field: the live builder drops it from the document.
        assert mio.export_accounts(conn) == [
            {k: v for k, v in account.items() if k != "color"} for account in ACCOUNTS
        ]


def test_import_then_export_transactions_round_trips_incl_splits_and_refs(db_engine):
    with db_engine.begin() as conn:
        mio.import_accounts(conn, ACCOUNTS)
        n = mio.import_transactions(conn, TRANSACTIONS)
    assert n == len(TRANSACTIONS)
    with db_engine.connect() as conn:
        assert mio.export_transactions(conn) == TRANSACTIONS
        mio.verify_transaction_children(conn)  # must not raise


def test_transaction_missing_hazard_keys_defaults_promoted_columns_payload_stays_bare(db_engine):
    """`txn_historical` genuinely lacks created_at/memo/tags/merged_from — the promoted
    columns must still land on their type-appropriate default, and payload must keep the
    true absence (not backfill it)."""
    with db_engine.begin() as conn:
        mio.import_accounts(conn, ACCOUNTS)
        mio.import_transactions(conn, TRANSACTIONS)
    with db_engine.connect() as conn:
        from backend.db import tables as t

        row = conn.execute(
            select(t.transactions).where(t.transactions.c.id == "txn_historical")
        ).one()
        assert (row.created_at, row.memo, row.tags, row.merged_from) == ("", "", [], [])
        for key in ("created_at", "memo", "tags", "merged_from"):
            assert key not in row.payload


def test_transaction_splits_and_refs_promoted_columns(db_engine):
    with db_engine.begin() as conn:
        mio.import_accounts(conn, ACCOUNTS)
        mio.import_transactions(conn, TRANSACTIONS)
    with db_engine.connect() as conn:
        from backend.db import tables as t

        splits = conn.execute(
            select(t.transaction_splits)
            .where(t.transaction_splits.c.txn_id == "txn_split")
            .order_by(t.transaction_splits.c.idx)
        ).fetchall()
        # PLAN 3 TASK 5 (revision 0008): `amount` is now Text, repr(float)-exact — `db_engine`
        # migrates to head (0008 included) BEFORE this test's own `import_transactions` call,
        # so this insert lands through the (now text-by-default) money_io builders.
        assert [(s.category, s.amount) for s in splits] == [("food", "30.0"), ("household", "20.0")]
        refs = conn.execute(
            select(t.transaction_external_refs)
            .where(t.transaction_external_refs.c.txn_id == "txn_split")
            .order_by(t.transaction_external_refs.c.idx)
        ).fetchall()
        assert [(r.source, r.ref) for r in refs] == [("csv", "row-1"), (None, "bare-ref")]


def test_verify_transaction_children_catches_drift_from_payload(db_engine):
    """`verify_transaction_children` is the second half of the transactions round-trip proof
    (the first half, `export_transactions(conn) == original`, only proves `payload` fidelity —
    it says nothing about whether the CHILD TABLES still agree with that payload). Simulate a
    child row drifting out of sync with its parent's payload (a hand edit, a future bug in some
    other write path) and confirm this is the check that catches it."""
    from sqlalchemy import update

    from backend.db import tables as t

    with db_engine.begin() as conn:
        mio.import_accounts(conn, ACCOUNTS)
        mio.import_transactions(conn, TRANSACTIONS)
    with db_engine.begin() as conn:
        conn.execute(
            update(t.transaction_splits)
            .where(t.transaction_splits.c.txn_id == "txn_split", t.transaction_splits.c.idx == 0)
            .values(amount=999.0)
        )
    with db_engine.connect() as conn:
        with pytest.raises(RuntimeError) as exc_info:
            mio.verify_transaction_children(conn)
    assert "txn_split" in str(exc_info.value)


def test_import_then_export_txn_audit_round_trips_exactly(db_engine):
    with db_engine.begin() as conn:
        mio.import_accounts(conn, ACCOUNTS)
        mio.import_transactions(conn, TRANSACTIONS)
        n = mio.import_txn_audit(conn, TXN_AUDIT)
    assert n == len(TXN_AUDIT)
    with db_engine.connect() as conn:
        assert mio.export_txn_audit(conn) == TXN_AUDIT


def test_import_then_export_import_batches_round_trips_exactly(db_engine):
    with db_engine.begin() as conn:
        n = mio.import_import_batches(conn, IMPORT_BATCHES)
    assert n == len(IMPORT_BATCHES)
    with db_engine.connect() as conn:
        assert mio.export_import_batches(conn) == IMPORT_BATCHES


def test_reinline_statement_derives_the_sidecar_path_from_the_column_id_not_payload(
    db_engine, tmp_path
):
    """Security review fix round 2, Fix 6: ``_reinline_statement`` used to derive the
    sidecar path from ``payload.get('id')`` — a value that, unlike the row's own PRIMARY
    KEY column, could in principle drift from it (a merge of caller ``changes`` over a
    reconstructed row, e.g. via ``StatementStore.update_by_id``). Every call site now
    passes the COLUMN id explicitly. Proven directly here: a payload whose OWN ``id`` key
    disagrees with the real row id must still resolve against the REAL id's sidecar, not a
    (nonexistent) one named after the payload's stale value."""
    real_id = "stmt_real_column_id"
    stale_payload_id = "stmt_stale_payload_id"
    sidecar = mio.statements_files_dir() / f"{real_id}.parsed.json"
    sidecar.write_text(json.dumps([{"amount": 42.0}]))

    payload = {"id": stale_payload_id, "filename": "x.pdf"}
    result = mio._reinline_statement(real_id, payload, has_parsed_rows=1)

    assert result["parsed_rows"] == [{"amount": 42.0}]
    # payload's own (stale) id is untouched — only the LOOKUP used the real column id.
    assert result["id"] == stale_payload_id


def test_import_then_export_statements_round_trips_exactly(db_engine):
    with db_engine.begin() as conn:
        n = mio.import_statements(conn, STATEMENTS)
    assert n == len(STATEMENTS)
    with db_engine.connect() as conn:
        assert mio.export_statements(conn) == STATEMENTS


def test_statements_promoted_columns_batch_ids_dead_key_and_has_parsed_rows(db_engine):
    with db_engine.begin() as conn:
        mio.import_statements(conn, STATEMENTS)
    with db_engine.connect() as conn:
        from backend.db import tables as t

        with_rows = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_with_rows")
        ).one()
        assert with_rows.batch_ids == ["imp1"]
        assert with_rows.has_parsed_rows == 1
        sidecar = mio.statements_files_dir() / "stmt_with_rows.parsed.json"
        assert sidecar.exists()
        assert "parsed_rows" not in with_rows.payload  # extracted out
        assert with_rows.payload["filename"] == "jan.pdf"  # everything else stays

        dead_key = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_dead_key")
        ).one()
        assert dead_key.batch_ids == ["imp_old"]  # read from the DEAD key
        assert dead_key.has_parsed_rows == 0
        assert dead_key.payload["import_batch_ids"] == ["imp_old"]  # payload stays verbatim
        assert "parsed_rows" in dead_key.payload  # None was never stripped (not extracted)
        assert dead_key.payload["parsed_rows"] is None

        neither = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_neither")
        ).one()
        assert neither.batch_ids == []
        assert neither.has_parsed_rows == 0


def test_import_then_export_merge_journal_round_trips_exactly(db_engine):
    with db_engine.begin() as conn:
        mio.import_accounts(conn, ACCOUNTS)
        mio.import_transactions(conn, TRANSACTIONS)
        n = mio.import_merge_journal(conn, MERGE_JOURNAL)
    assert n == len(MERGE_JOURNAL)
    with db_engine.connect() as conn:
        assert mio.export_merge_journal(conn) == MERGE_JOURNAL


def test_missing_source_files_are_a_clean_empty_import(db_isolated):
    from backend.db.engine import get_engine

    with get_engine().connect() as conn:
        assert mio.export_accounts(conn) == []
        assert mio.export_transactions(conn) == []
        assert mio.export_txn_audit(conn) == []
        assert mio.export_import_batches(conn) == []
        assert mio.export_statements(conn) == []
        assert mio.export_merge_journal(conn) == []


# ── Cross-file consistency pre-checks (abort loudly, naming the violation) ──────────


def test_dangling_opening_txn_id_aborts_naming_the_account_and_the_pointer():
    bad_accounts = [
        {"id": "acc_bad", "name": "Bad", "kind": "asset", "opening_txn_id": "txn_missing"}
    ]
    with pytest.raises(RuntimeError) as exc_info:
        mio.verify_cross_file_consistency(bad_accounts, [])
    message = str(exc_info.value)
    assert "acc_bad" in message
    assert "txn_missing" in message


def test_dangling_account_ref_aborts_naming_the_transaction_and_the_account():
    bad_txns = [
        {
            "id": "txn_bad",
            "date": "2026-01-01",
            "kind": "expense",
            "from_account_id": "acc_missing",
            "to_account_id": None,
        }
    ]
    with pytest.raises(RuntimeError) as exc_info:
        mio.verify_cross_file_consistency([], bad_txns)
    message = str(exc_info.value)
    assert "txn_bad" in message
    assert "acc_missing" in message


def test_dangling_to_account_id_ref_aborts_naming_the_transaction_and_the_account():
    """PLAN 3 TASK 4 CONTROLLER INCLUSION (task 3 review): the existing dangling-account
    test above only exercises `from_account_id` — `verify_cross_file_consistency` checks
    BOTH leg fields in the same loop (``for field in ("from_account_id", "to_account_id")``),
    so a dangling `to_account_id` (e.g. an income row, or the to-leg of a transfer) needs
    its OWN regression proof rather than trusting the shared loop by inspection alone."""
    bad_txns = [
        {
            "id": "txn_bad_to",
            "date": "2026-01-01",
            "kind": "income",
            "from_account_id": None,
            "to_account_id": "acc_missing_to",
        }
    ]
    with pytest.raises(RuntimeError) as exc_info:
        mio.verify_cross_file_consistency([], bad_txns)
    message = str(exc_info.value)
    assert "txn_bad_to" in message
    assert "acc_missing_to" in message


def test_archived_account_ref_is_not_dangling():
    """'archived OK' — an archived account still satisfies a transaction's leg reference."""
    archived_accounts = [{"id": "acc_old", "name": "Closed", "kind": "asset", "archived": True}]
    txns = [
        {
            "id": "txn_ok",
            "date": "2026-01-01",
            "kind": "expense",
            "from_account_id": "acc_old",
            "to_account_id": None,
        }
    ]
    mio.verify_cross_file_consistency(archived_accounts, txns)  # must not raise


def test_opening_txn_id_none_is_never_dangling():
    accounts = [{"id": "acc_new", "name": "New", "kind": "asset", "opening_txn_id": None}]
    mio.verify_cross_file_consistency(accounts, [])  # must not raise


# ── Deterministic child-id re-derivation ─────────────────────────────────────────────


def test_split_and_ref_ids_are_deterministic_across_two_independent_absorbs(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine

    from backend.db import tables as t
    from backend.db.alembic_config import make_config

    captured = []
    for i in range(2):
        url = f"sqlite:///{tmp_path}/db{i}.db"
        command.upgrade(make_config(url), "head")
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                mio.import_accounts(conn, ACCOUNTS)
                mio.import_transactions(conn, TRANSACTIONS)
            with engine.connect() as conn:
                split_ids = sorted(r.split_id for r in conn.execute(select(t.transaction_splits)))
                ref_ids = sorted(
                    r.ref_id for r in conn.execute(select(t.transaction_external_refs))
                )
            captured.append((split_ids, ref_ids))
        finally:
            engine.dispose()

    assert captured[0] == captured[1]
    assert captured[0][0] and captured[0][1]  # non-empty on both sides — the check is real


# ── Full `ensure_migrated` end-to-end (all six renamed, all six hashed) ─────────────


def test_full_migration_absorbs_all_six_and_renames(tmp_path, db_url, monkeypatch):
    sources = {
        "ACCOUNTS_SOURCE": ("accounts.json", ACCOUNTS, mio.export_accounts),
        "TRANSACTIONS_SOURCE": ("transactions.json", TRANSACTIONS, mio.export_transactions),
        "TXN_AUDIT_SOURCE": ("txn_audit.json", TXN_AUDIT, mio.export_txn_audit),
        "IMPORT_BATCHES_SOURCE": ("import_batches.json", IMPORT_BATCHES, mio.export_import_batches),
        "STATEMENTS_SOURCE": ("statements.json", STATEMENTS, mio.export_statements),
        "MERGE_JOURNAL_SOURCE": ("merge_journal.json", MERGE_JOURNAL, mio.export_merge_journal),
    }
    for attr, (filename, fixture, _exporter) in sources.items():
        path = tmp_path / filename
        path.write_text(json.dumps(fixture))
        monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)

    _set_database_url_for_test(db_url, monkeypatch)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    with eng.get_engine().connect() as conn:
        for attr, (_filename, fixture, exporter) in sources.items():
            if attr == "STATEMENTS_SOURCE":
                # Revision 0045 (unify-raw-file-storage, already on main before this
                # revision's own chain point) rewrites every pre-existing statement's
                # file_path from the legacy "statements/<name>" form to "files/statements/
                # <name>" as part of THIS SAME from-scratch chain — a real, intentional
                # correction, not a round-trip bug. The fixture above is the legacy JSON
                # cutover SOURCE content (written pre-migration, deliberately kept in its
                # original "statements/..." shape as the realistic input); the FINAL
                # exported state after the full chain correctly reflects 0045's rewrite.
                expected = [{**row, "file_path": f"files/{row['file_path']}"} for row in fixture]
                assert exporter(conn) == expected
                continue
            if attr == "ACCOUNTS_SOURCE":
                # Revision 202609071200_seven_account_types collapses the 26 legacy account
                # types into the seven-value vocabulary as part of THIS SAME from-scratch
                # chain — checking/savings both land on "cash". Same shape as the statements
                # carve-out above: an intentional correction, not a round-trip bug. Revision
                # 202609120900_remove_account_color likewise strips the legacy ``color`` (the
                # fixture keeps one, so the cutover is proven to preserve it until then).
                expected = [
                    {**{k: v for k, v in row.items() if k != "color"}, "type": "cash"}
                    for row in fixture
                ]
                assert exporter(conn) == expected
                continue
            assert exporter(conn) == fixture

    for attr, (filename, _fixture, _exporter) in sources.items():
        src = tmp_path / filename
        assert not src.exists()
        assert len(list(tmp_path.glob(f"{filename}.migrated-*"))) == 1

    from backend.db.tables import json_cutovers

    with eng.get_engine().connect() as conn:
        rows = {r.store: r for r in conn.execute(select(json_cutovers))}
    for store in _STORE_NAMES:
        assert rows[store].renamed == 1
        assert rows[store].source_hash

    eng.dispose_engine()


def test_full_migration_aborts_on_dangling_opening_txn_id(tmp_path, db_url, monkeypatch):
    """A dangling opening_txn_id in the real source files must abort the WHOLE migration,
    not just the accounts absorb — proves the pre-check runs before any store is touched."""
    bad_accounts = [
        {"id": "acc_broken", "name": "Broken", "kind": "asset", "opening_txn_id": "txn_ghost"}
    ]
    (tmp_path / "accounts.json").write_text(json.dumps(bad_accounts))
    (tmp_path / "transactions.json").write_text(json.dumps([]))
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.ACCOUNTS_SOURCE", tmp_path / "accounts.json"
    )
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.TRANSACTIONS_SOURCE",
        tmp_path / "transactions.json",
    )
    _patch_remaining_sources_missing(monkeypatch, tmp_path)
    _set_database_url_for_test(db_url, monkeypatch)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")

    from backend.core.errors import MigrationError
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    with pytest.raises(MigrationError) as exc_info:
        ensure_migrated()
    assert "acc_broken" in str(exc_info.value)
    assert "txn_ghost" in str(exc_info.value)
    # fail-closed: the source file was never renamed (nothing was absorbed)
    assert (tmp_path / "accounts.json").exists()

    eng.dispose_engine()


def _patch_remaining_sources_missing(monkeypatch, tmp_path):
    for attr, filename in (
        ("TXN_AUDIT_SOURCE", "no_txn_audit.json"),
        ("IMPORT_BATCHES_SOURCE", "no_import_batches.json"),
        ("STATEMENTS_SOURCE", "no_statements.json"),
        ("MERGE_JOURNAL_SOURCE", "no_merge_journal.json"),
    ):
        monkeypatch.setattr(
            f"backend.db.alembic.versions_evidence_source.{attr}", tmp_path / filename
        )


def test_full_migration_is_a_clean_empty_absorb_when_all_six_sources_missing(
    tmp_path, db_url, monkeypatch
):
    """Fresh install: none of the six money files exist — head is reached, zero rows,
    zero cutover ledger entries for the money stores."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    _set_database_url_for_test(db_url, monkeypatch)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    with eng.get_engine().connect() as conn:
        assert mio.export_accounts(conn) == []
        assert mio.export_transactions(conn) == []
        assert mio.export_txn_audit(conn) == []
        assert mio.export_import_batches(conn) == []
        assert mio.export_statements(conn) == []
        assert mio.export_merge_journal(conn) == []

        from sqlalchemy import func

        from backend.db.tables import json_cutovers

        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store.in_(_STORE_NAMES))
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


# ── Downgrade cycle ───────────────────────────────────────────────────────────────────

_MONEY_TABLES = (
    "accounts",
    "transactions",
    "transaction_splits",
    "transaction_external_refs",
    "txn_audit",
    "import_batches",
    "statements_meta",
    "merge_journal",
)


def _row_count(conn, table_name: str) -> int:
    return conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()


def test_downgrade_to_0006_deletes_absorbed_rows_but_keeps_tables(tmp_path, db_url, monkeypatch):
    """PLAN 3 TASK 4 CONTROLLER INCLUSION (task 3 review): strengthened to populate ALL
    SIX stores via the REAL cutover path (real source JSON files + `ensure_migrated()`,
    exactly like `test_full_migration_absorbs_all_six_and_renames`) before downgrading —
    not `mio.import_*` called directly against an already-upgraded DB, and not just
    accounts/transactions/merge_journal. The prior version's direct-import shortcut never
    exercised revision 0007's OWN `upgrade()` function at all (only `money_io`'s import
    helpers in isolation) and left txn_audit/import_batches/statements permanently empty
    for this test — a bug in 0007's downgrade that failed to clear one of THOSE three
    tables would have passed unnoticed, since deleting an already-empty table always
    trivially "succeeds". Populating all six for real closes that gap."""
    from alembic import command
    from sqlalchemy import create_engine, inspect

    from backend.db import tables as t
    from backend.db.alembic_config import make_config
    from backend.db.engine import database_url
    from tests.db.conftest import reversible_tip

    sources = {
        "ACCOUNTS_SOURCE": ("accounts.json", ACCOUNTS),
        "TRANSACTIONS_SOURCE": ("transactions.json", TRANSACTIONS),
        "TXN_AUDIT_SOURCE": ("txn_audit.json", TXN_AUDIT),
        "IMPORT_BATCHES_SOURCE": ("import_batches.json", IMPORT_BATCHES),
        "STATEMENTS_SOURCE": ("statements.json", STATEMENTS),
        "MERGE_JOURNAL_SOURCE": ("merge_journal.json", MERGE_JOURNAL),
    }
    for attr, (filename, fixture) in sources.items():
        path = tmp_path / filename
        path.write_text(json.dumps(fixture))
        monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)

    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()

    # The REAL cutover path — revision 0007's own upgrade(), all six — then stop at the
    # reversible tip: head's chain refuses a downgrade (the connectors table), and the live
    # `tables` descriptions no longer match old schemas (`accounts.data` was `payload`), so the
    # row checks below count with bare SQL.
    cfg = make_config(database_url())
    command.upgrade(cfg, reversible_tip(database_url()))

    with eng.get_engine().connect() as conn:
        for table_name in _MONEY_TABLES:
            assert _row_count(conn, table_name) > 0, (
                f"{table_name} must be populated before this test's downgrade assertion "
                "means anything"
            )
    eng.dispose_engine()

    command.downgrade(cfg, "202608071952_money_core_schema")

    engine = create_engine(db_url)
    try:
        names = inspect(engine).get_table_names()
        for table_name in (
            "accounts",
            "transactions",
            "transaction_splits",
            "transaction_external_refs",
            "txn_audit",
            "import_batches",
            "statements_meta",
            "merge_journal",
        ):
            assert table_name in names  # 0006's tables survive — 0007 creates none
        with engine.connect() as conn:
            for table_name in _MONEY_TABLES:
                assert _row_count(conn, table_name) == 0, (
                    f"{table_name} must be emptied by 0007's downgrade"
                )
            cutover_rows = conn.execute(
                select(t.json_cutovers).where(t.json_cutovers.c.store.in_(_STORE_NAMES))
            ).fetchall()
            assert cutover_rows == []
    finally:
        engine.dispose()

    eng.dispose_engine()
    command.upgrade(cfg, "head")  # re-upgrading cleanly must still work
    eng.dispose_engine()
