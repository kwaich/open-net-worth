"""wave5d2a schema: auth, model_config, app_config, email_ingest — the four Wave 5D2a
(JSON->DB, secrets/config) stores. Schema-only, no data absorption (0029) — and,
unlike every prior wave's own "schema" revision, this one adds NO new tables at all.

``auth``/``model_config``/``email_ingest`` each land as a NEW namespace row in the
EXISTING ``secret_config`` table (created by revision 0026, Wave 5D1) — the same
namespace-keyed singleton shape their fifteen connector-credential siblings already
use, sharing its SECRET + NON-RESTORABLE + checkpoint-EXCLUDED table-level status with
no new entry needed anywhere (``core.sensitive.SECRET_TABLES``/
``NON_RESTORABLE_TABLES``, ``ai.checkpoint._policy._EXCLUDED_TABLES`` all already name
``secret_config`` as a whole table).

``app_config`` lands as a NEW namespace row in the EXISTING ``runtime_state`` table
(created by revision 0024, Wave 5C) — NOT a secret, so it deliberately does NOT join
``secret_config``; ``runtime_state``'s existing checkpoint-EXCLUDED table-level status
(mirroring its one prior occupant, ``harness_state``) covers it the same way.

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-10
"""

revision = "202608110408_wave5d2a_schema"
down_revision = "202608110407_wave5d1_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: every Wave 5D2a store reuses a table an earlier wave already created —
    see this module's own docstring for which table and why. Kept as its own revision
    (rather than folded into 0029) purely to preserve this migration family's uniform
    one-schema-then-one-cutover shape per wave, matching every prior wave's pairing."""


def downgrade() -> None:
    """No-op for the same reason ``upgrade`` is — there is no DDL here to undo."""
