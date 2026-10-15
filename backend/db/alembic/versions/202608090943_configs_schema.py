"""configs schema: settings, payees, automations, alerts, dashboard_layouts, memory,
news_sources, statement_formats, tool_grants, signals, intel_signals — the settings
primitive + 11 config stores (plan 4d, task 1). Schema only — no data absorption (a later
task cuts the 12 JsonStore subclasses over to these tables; `memory_settings`/
`preferred_models` become ROWS in `settings` at that point, not tables of their own).

`settings` is the spec's "small settings table keyed by namespace" (spec:205): ONE row per
namespace, `payload` the whole config dict verbatim, NO book_id — read/written by
`backend.db.payload_store.NamespacedSettingsStore`.

`payees`/`automations`/`alerts` mirror `book_payload_table()` exactly (book_id FK/seq/id/
payload, unique `ix_<name>_book_seq`) — Tier-1, spec:139-140, identical structure to 0009's
planning block / 0011's investments block. `dashboard_layouts` is a per-book singleton keyed
BY book_id (mirrors `plan_state`/`watchlists`) — also Tier-1. `memory` is the plain Tier-2
`payload_table()` shape (seq/id/payload, NO book_id): user_id-scoped (spec:276), not
book_id — family mode adds a user_id column in a LATER migration.
`news_sources`/`statement_formats`/`tool_grants`/`signals`/`intel_signals` are also the plain
Tier-2 `payload_table()` shape — Tier-3 installation config/reference data, no book_id.

NO cross-table FK between config tables — the only FK anywhere in this revision is each
book-scoped table's own `book_id -> books.id` (payees/automations/alerts/dashboard_layouts),
matching every prior wave.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op

revision = "202608090943_configs_schema"
down_revision = "202608090747_logs_cutover"
branch_labels = None
depends_on = None

# The 3 tables built straight from book_payload_table("<name>")'s shape — see tables.py.
_BOOK_PAYLOAD_TABLES = (
    "payees",
    "automations",
    "alerts",
)

# The 6 tables built straight from payload_table("<name>")'s shape — see tables.py.
_PLAIN_PAYLOAD_TABLES = (
    "memory",
    "news_sources",
    "statement_formats",
    "tool_grants",
    "signals",
    "intel_signals",
)


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("namespace", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )

    for name in _BOOK_PAYLOAD_TABLES:
        op.create_table(
            name,
            sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_book_seq", name, ["book_id", "seq"], unique=True)

    op.create_table(
        "dashboard_layouts",
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )

    for name in _PLAIN_PAYLOAD_TABLES:
        op.create_table(
            name,
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_seq", name, ["seq"], unique=True)


def downgrade() -> None:
    for name in reversed(_PLAIN_PAYLOAD_TABLES):
        op.drop_table(name)
    op.drop_table("dashboard_layouts")
    for name in reversed(_BOOK_PAYLOAD_TABLES):
        op.drop_table(name)
    op.drop_table("settings")
