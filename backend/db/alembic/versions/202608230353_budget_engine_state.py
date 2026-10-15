"""budget_engine_state: the default per-(book, engine) configuration store for a pluggable
budget engine that doesn't own a bespoke store of its own.

## Why

``.claude/rfcs/2026-08-22-pluggable-budget-engines.md`` ("Decision A" + "Decision E") rules
that switching the "home" budgeting lens must never touch another engine's state — envelope's
``budget_assignments``/``profile.category_targets`` stay alive untouched while a different
lens is home, so switching back is instant and lossless. That property "maps directly onto
storage": each engine either keeps its existing bespoke store (envelope:
``envelopes``/``budget_assignments``/``profile.category_targets``; category-caps:
``profile.monthly_budgets`` — NEITHER migrates here, both stay exactly where they are) or, if
it owns no bespoke store, gets one row in this shared table. Phase 1's only consumer is the
50/30/20 engine (``payload = {"targets": {"needs": .., "wants": .., "savings": ..},
"mapping": {cat_id: bucket}}``), but the table itself stays engine-agnostic — ``payload`` is
opaque JSON, keyed only by ``(book_id, engine_id)``.

Schema-only, mirroring revision 0009's own ``budget_assignments``/``plan_state`` create_table
shape: the DDL is written directly in this file rather than ``from backend.db.tables import
budget_engine_state`` — a migration must stay correct even after some future change to the
live ORM table (revision 0048's own docstring names this rule; every prior ``create_table``
revision in this history already follows it). There is no prior JSON file and no existing row
to absorb, so this is create-only — no cutover step, nothing stamped in ``json_cutovers``.

``id`` is a deterministic surrogate, ``f"{book_id}:{engine_id}"`` (both components colon-free
— ``book_<12hex>`` / a short engine slug like ``"proportional"``), exactly
``budget_assignments``'s own ``f"{book_id}:{month}:{category}"`` pattern — book-prefixed so
two books can never collide on the same engine's row, and a keyed relation has no insertion
order (no ``seq`` column, matching ``budget_assignments``). ``payload`` stays raw JSON, never
promoted to a decimal-Text column: it holds target PERCENTAGES and a category-to-bucket
MAPPING — configuration, not ledger money summed into net worth (the same ``plan_state``/
``profile`` precedent that keeps FIRE-projection and profile amounts raw). ``budget_
assignments`` remains the only money-promoted planning store.

Checkpoint-INCLUDED (NOT added to ``ai.checkpoint._policy._EXCLUDED_TABLES``) and portable
(``db/portable_bridge.py`` BRIDGED + ``core.portable.PORTABLE_FILES``) — durable user
configuration, not a runtime log or cache: a lens-config edit must revert on ``/rewind`` and
travel with an export/import bundle like every other planning store.

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608230353_budget_engine_state"
down_revision = "202608222205_coingecko_key_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_engine_state",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("engine_id", sa.Text, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_budget_engine_state_book_id", "budget_engine_state", ["book_id"])


def downgrade() -> None:
    op.drop_table("budget_engine_state")
