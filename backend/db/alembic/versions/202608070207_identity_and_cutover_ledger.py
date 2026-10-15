"""identity (users/books/book_members) seeded 1/1/1 + cutover ledger

Revision ID: 0001
Revises:
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "202608070207_identity_and_cutover_ledger"
down_revision = None
branch_labels = None
depends_on = None

DEFAULT_USER_ID = "usr_000000000000"
DEFAULT_BOOK_ID = "book_000000000000"
_SEEDED_AT = "2026-08-07T00:00:00+00:00"  # fixed: revisions must be deterministic


def upgrade() -> None:
    users = op.create_table(
        "users",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, server_default=""),
        sa.Column("display_name", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.Text, nullable=False, server_default=""),
    )
    books = op.create_table(
        "books",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("base_currency", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.Text, nullable=False, server_default=""),
    )
    members = op.create_table(
        "book_members",
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), primary_key=True),
        sa.Column("role", sa.Text, nullable=False),
    )
    op.create_table(
        "json_cutovers",
        sa.Column("store", sa.Text, primary_key=True),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("source_hash", sa.Text, nullable=False, server_default=""),
        sa.Column("migrated_at", sa.Text, nullable=False),
        sa.Column("renamed", sa.Integer, nullable=False, server_default="0"),
    )
    op.bulk_insert(
        users,
        [{"id": DEFAULT_USER_ID, "email": "", "display_name": "Owner", "created_at": _SEEDED_AT}],
    )
    op.bulk_insert(
        books,
        [{"id": DEFAULT_BOOK_ID, "name": "My Book", "base_currency": "", "created_at": _SEEDED_AT}],
    )
    op.bulk_insert(
        members, [{"user_id": DEFAULT_USER_ID, "book_id": DEFAULT_BOOK_ID, "role": "owner"}]
    )


def downgrade() -> None:
    op.drop_table("json_cutovers")
    op.drop_table("book_members")
    op.drop_table("books")
    op.drop_table("users")
