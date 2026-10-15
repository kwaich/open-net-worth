"""wave5f cache-state schema: digest_state, news_screen_state, memory_consolidate_state
— three more Wave 5F stores. Schema-only, no data absorption (0037) — and, like Wave
5D2a's own 0028, this revision adds NO new tables at all.

All three land as NEW namespace rows in the EXISTING `runtime_state` table (created by
revision 0024, Wave 5C) — the same namespaced-singleton shape `harness_state`/
`app_config` already use, sharing its checkpoint-EXCLUDED table-level status with no new
entry needed anywhere (`ai.checkpoint._policy._EXCLUDED_TABLES` already names
`runtime_state` as a whole table). None of the three is a credential, so none joins
`secret_config` — see `backend/db/tables.py`'s own `runtime_state` comment.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-10
"""

revision = "202608110416_wave5f_cache_state_schema"
down_revision = "202608110415_wave5f_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: every Wave 5F cache-state store reuses `runtime_state`, a table Wave 5C
    already created — see this module's own docstring. Kept as its own revision (rather
    than folded into 0037) purely to preserve this migration family's uniform
    one-schema-then-one-cutover shape per wave, matching every prior wave's pairing
    (0028/0029's own precedent)."""


def downgrade() -> None:
    """No-op for the same reason `upgrade` is — there is no DDL here to undo."""
