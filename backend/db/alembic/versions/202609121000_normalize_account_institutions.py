"""Normalize account platform strings into shared institution records.

Revision ID: 202609121000_normalize_account_institutions
Revises: 202609120900_remove_account_color
Create Date: 2026-09-12
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "202609121000_normalize_account_institutions"
down_revision = "202609120900_remove_account_color"
branch_labels = None
depends_on = None
# Read by the tests (tests/db/conftest.py::reversible_tip): a chain walk never downgrades across it.
irreversible = True


def _payload(raw: object, account_id: str) -> dict:
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise RuntimeError(f"account {account_id!r} has unreadable payload")
    return value


def _normal(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _institution_id(book_id: str, normalized_name: str, taken: set[str]) -> str:
    digest = hashlib.sha256(f"{book_id}:{normalized_name}".encode()).hexdigest()
    for width in range(12, len(digest) + 1, 2):
        candidate = f"inst_{digest[:width]}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise RuntimeError("could not mint an institution id during migration")


def upgrade() -> None:
    op.create_table(
        "institutions",
        sa.Column("book_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", "normalized_name"),
    )
    op.create_index("ix_institutions_book_seq", "institutions", ["book_id", "seq"], unique=True)

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, book_id, payload FROM accounts ORDER BY seq")
    ).fetchall()
    resolved: dict[tuple[str, str], str] = {}
    taken: set[str] = set()
    next_seq: dict[str, int] = {}
    for account_id, book_id, raw_payload in rows:
        payload = _payload(raw_payload, account_id)
        legacy = payload.pop("platform", "")
        normalized = _normal(legacy)
        if normalized:
            key = (book_id, normalized)
            institution_id = resolved.get(key)
            if institution_id is None:
                institution_id = _institution_id(book_id, normalized, taken)
                resolved[key] = institution_id
                seq = next_seq.get(book_id, 0)
                next_seq[book_id] = seq + 1
                name = " ".join(str(legacy).split())
                conn.execute(
                    sa.text(
                        "INSERT INTO institutions (book_id, seq, id, normalized_name, payload) "
                        "VALUES (:book_id, :seq, :id, :normalized_name, :payload)"
                    ),
                    {
                        "book_id": book_id,
                        "seq": seq,
                        "id": institution_id,
                        "normalized_name": normalized,
                        "payload": json.dumps({"id": institution_id, "name": name}),
                    },
                )
            payload["institution_id"] = institution_id
        else:
            payload.pop("institution_id", None)
        if "platform" in payload:
            raise RuntimeError(f"account {account_id!r} still has a platform after migration")
        conn.execute(
            sa.text("UPDATE accounts SET payload = :payload WHERE id = :id"),
            {"id": account_id, "payload": json.dumps(payload)},
        )


def downgrade() -> None:
    raise RuntimeError("account institution normalization is intentionally irreversible")
