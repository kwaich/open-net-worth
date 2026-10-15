"""Merge point: two independent revision chains branched off
``202609032128_flip_plan_event_amount_sign`` concurrently — one on the
``feature/connectors-as-data-sources`` branch (``202609051200_connector_snapshots`` ->
``202609051210_account_external_ids_from_config_maps``, RFC ``connectors-as-data-sources``
§C/§D) and one on ``main`` (``202609051200_derive_bond_quote_per_unit`` ->
``202609061000_owned_assets`` -> ``202609071200_seven_account_types``, ADR
``2026-09-06-assets-tab-faithful-port`` / ``2026-09-07-seven-account-types-one-axis``).

Both chains independently pick a ``202609051200`` timestamp for their own next revision —
two DIFFERENT full revision ids (``..._connector_snapshots`` vs. ``..._derive_bond_quote_
per_unit``), so Alembic does not collide on identity, but it does produce two heads once
both land in this directory. Verified data-safe in either order before merging: the
external_ids backfill only appends to ``accounts.payload["external_ids"]`` and strips four
unrelated provider-config keys; the seven-account-types remap only touches
``type``/``kind``/the four retired payload keys it strips. Neither reads or clears a key the
other writes, and both round-trip the full stored payload (load → targeted mutation → write
back), so nothing is clobbered regardless of which chain a fresh install applies first. This
merge therefore carries no operations of its own — a pure coalescing point, the same pattern
this repo already used for ``202608301845_merge_claims_and_asset_brief_news_drop``.

Revision ID: 202609071500_merge_external_ids_and_seven_account_types
Revises: 202609051210_account_external_ids_from_config_maps, 202609071200_seven_account_types  # pii-lint: ok (alembic revision timestamp)
Create Date: 2026-09-07
"""

from __future__ import annotations

revision = "202609071500_merge_external_ids_and_seven_account_types"
down_revision = (
    "202609051210_account_external_ids_from_config_maps",
    "202609071200_seven_account_types",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
