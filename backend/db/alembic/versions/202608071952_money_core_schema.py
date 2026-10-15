"""money core schema: accounts, transactions (+ splits + external_refs), txn_audit,
import_batches, statements_meta, merge_journal — the tables the owner's real ledger
will live in (plan 3, task 2). Schema only — no data absorption (that is revision 0007).

CheckConstraint names are wrapped in `sa.schema.conv(...)`, pre-expanded to the FINAL
`ck_%(table_name)s_%(constraint_name)s` form `backend.db.tables.metadata`'s naming
convention produces. This is NOT optional here: `env.py` configures the MigrationContext
with `target_metadata=tables.metadata`, and `op.create_table` — proven empirically, not
assumed — picks up THAT metadata's naming convention for every constraint it creates in
THIS migration too (visible even on primary keys: an unnamed PK here renders
`CONSTRAINT pk_<table> PRIMARY KEY (...)`, not a bare `PRIMARY KEY (...)`). A plain already
-expanded string name would therefore be run through the "ck" convention A SECOND TIME —
`ck_transactions_transfer_has_both_legs` becomes
`ck_transactions_ck_transactions_transfer_has_both_legs` — which is exactly what landed in
the database on the first attempt at this file, caught by `test_alembic_check_is_clean`
before this revision ever shipped. `conv()` marks a name as final so it is never
reprocessed, however many times it is copied/reattached; tables.py wraps the SAME final
strings the same way, on its side, so the two sides always agree.

FIX ROUND 1 (reviewer, against the live data file, read-only): `accounts` gains a promoted
`kind` column; `transactions`/`transaction_splits`/`transaction_external_refs` each gain a
`payload JSON NOT NULL` column (the verbatim-payload + promoted-columns pattern, matching
`accounts` — a ruling that supersedes the original brief's "transactions is fully promoted,
no payload" instruction); `transactions.memo`/`created_at`/`tags`/`merged_from` and
`statements_meta.batch_ids` gain type-appropriate `server_default`s. Full rationale lives in
`tables.py`'s comments for each table — this migration only needs to match them exactly.
Edited THIS revision in place rather than adding a new one: 0006 has never absorbed data
(revision 0007 does not exist yet), so there is no shipped state to preserve compatibility
with.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "202608071952_money_core_schema"
down_revision = "202608071033_instruments_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),  # fix round 1: promoted, see tables.py
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("archived", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("opening_txn_id", sa.Text, nullable=True),  # NO FK — circular; services own it
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_accounts_book_seq", "accounts", ["book_id", "seq"], unique=True)

    op.create_table(
        "transactions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("date", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=True),
        sa.Column(
            "from_account_id",
            sa.Text,
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "to_account_id",
            sa.Text,
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("from_amount", sa.Float, nullable=True),
        sa.Column("to_amount", sa.Float, nullable=True),
        sa.Column("base_amount", sa.Float, nullable=True),
        sa.Column("fx_rate", sa.Float, nullable=True),
        sa.Column("original_amount", sa.Float, nullable=True),
        sa.Column("from_currency", sa.Text, nullable=True),
        sa.Column("to_currency", sa.Text, nullable=True),
        sa.Column("base_currency", sa.Text, nullable=True),
        sa.Column("original_currency", sa.Text, nullable=True),
        sa.Column("ticker", sa.Text, nullable=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("memo", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("payee_id", sa.Text, nullable=True),
        sa.Column("batch_id", sa.Text, nullable=True),
        sa.Column("merged_into", sa.Text, nullable=True),  # NO FK — self-referential circularity
        sa.Column("created_at", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("reconciled_at", sa.Text, nullable=True),
        sa.Column("cleared", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("deleted", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("taxable", sa.Integer, nullable=True),
        sa.Column("needs_review", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("base_amount_pinned", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("quantity", sa.Float, nullable=True),
        sa.Column("tags", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("merged_from", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        # fix round 1 (ruling): verbatim-payload pattern, same as accounts — see tables.py
        sa.Column("payload", sa.JSON, nullable=False),
        sa.CheckConstraint(
            "kind <> 'transfer' OR (from_account_id IS NOT NULL AND to_account_id IS NOT NULL)",
            name=sa.schema.conv("ck_transactions_transfer_has_both_legs"),
        ),
        sa.CheckConstraint("cleared IN (0,1)", name=sa.schema.conv("ck_transactions_cleared_bool")),
        sa.CheckConstraint("deleted IN (0,1)", name=sa.schema.conv("ck_transactions_deleted_bool")),
        sa.CheckConstraint(
            "taxable IS NULL OR taxable IN (0,1)",
            name=sa.schema.conv("ck_transactions_taxable_bool"),
        ),
        sa.CheckConstraint(
            "needs_review IN (0,1)", name=sa.schema.conv("ck_transactions_needs_review_bool")
        ),
        sa.CheckConstraint(
            "base_amount_pinned IN (0,1)",
            name=sa.schema.conv("ck_transactions_base_amount_pinned_bool"),
        ),
    )
    op.create_index("ix_transactions_book_seq", "transactions", ["book_id", "seq"], unique=True)
    op.create_index("ix_transactions_book_date", "transactions", ["book_id", "date"])
    op.create_index(
        "ix_transactions_book_from_account", "transactions", ["book_id", "from_account_id"]
    )
    op.create_index("ix_transactions_book_to_account", "transactions", ["book_id", "to_account_id"])

    op.create_table(
        "transaction_splits",
        sa.Column("split_id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column(
            "txn_id", sa.Text, sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("memo", sa.Text, nullable=False),
        # fix round 1 (ruling): verbatim embedded-split-dict payload — see tables.py
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(
        "ix_transaction_splits_book_txn_idx",
        "transaction_splits",
        ["book_id", "txn_id", "idx"],
        unique=True,
    )

    op.create_table(
        "transaction_external_refs",
        sa.Column("ref_id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column(
            "txn_id", sa.Text, sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("source", sa.Text, nullable=True),
        sa.Column("ref", sa.Text, nullable=False),
        # fix round 1 (ruling): verbatim embedded-ext-ref-dict payload — see tables.py
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(
        "ix_transaction_external_refs_book_txn", "transaction_external_refs", ["book_id", "txn_id"]
    )
    op.create_index(
        # PLAN 3 TASK 4: non-unique — see tables.py's own long comment on this same index
        # for why a UNIQUE constraint here is incompatible with a real, intended domain
        # state (services.ledger.merge_transactions legitimately leaves the SAME
        # non-null-source external ref on both the absorbed and canonical rows after a
        # merge). Edited in place on this already-landed revision — 0006 has still never
        # absorbed a real user's data, so there is no shipped/applied state to protect.
        "ix_transaction_external_refs_book_source_ref",
        "transaction_external_refs",
        ["book_id", "source", "ref"],
        unique=False,
    )

    op.create_table(
        "txn_audit",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("txn_id", sa.Text, nullable=False),  # NO FK — forensic record, see tables.py
        sa.Column("at", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("surface", sa.Text, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_txn_audit_book_seq", "txn_audit", ["book_id", "seq"], unique=True)
    op.create_index("ix_txn_audit_book_txn", "txn_audit", ["book_id", "txn_id"])

    op.create_table(
        "import_batches",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("source_account_id", sa.Text, nullable=True),
        sa.Column("transaction_count", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("import_session_id", sa.Text, nullable=True),
        sa.Column("statement_ref_id", sa.Text, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_import_batches_book_seq", "import_batches", ["book_id", "seq"], unique=True)
    op.create_index("ix_import_batches_book_status", "import_batches", ["book_id", "status"])
    op.create_index(
        "ix_import_batches_book_session", "import_batches", ["book_id", "import_session_id"]
    )

    op.create_table(
        "statements_meta",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("mime", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, nullable=True),
        sa.Column("parsed_at", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=True),
        sa.Column("meta_unavailable", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("import_meta", sa.JSON, nullable=True),
        sa.Column("batch_ids", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("parsed_rows_path", sa.Text, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.CheckConstraint(
            "meta_unavailable IN (0,1)",
            name=sa.schema.conv("ck_statements_meta_meta_unavailable_bool"),
        ),
    )
    op.create_index(
        "ix_statements_meta_book_seq", "statements_meta", ["book_id", "seq"], unique=True
    )
    op.create_index("ix_statements_meta_book_account", "statements_meta", ["book_id", "account_id"])
    op.create_index(
        "ix_statements_meta_book_file_hash", "statements_meta", ["book_id", "file_hash"]
    )

    op.create_table(
        "merge_journal",
        sa.Column("absorbed_id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("canonical_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_merge_journal_book_seq", "merge_journal", ["book_id", "seq"], unique=True)
    op.create_index("ix_merge_journal_book_canonical", "merge_journal", ["book_id", "canonical_id"])


def downgrade() -> None:
    op.drop_table("merge_journal")
    op.drop_table("statements_meta")
    op.drop_table("import_batches")
    op.drop_table("txn_audit")
    op.drop_table("transaction_external_refs")
    op.drop_table("transaction_splits")
    op.drop_table("transactions")
    op.drop_table("accounts")
