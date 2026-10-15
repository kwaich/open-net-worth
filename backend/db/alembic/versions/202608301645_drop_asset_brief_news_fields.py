"""Drop the ``news``/``news_checked_at`` keys from every ``asset_brief`` payload.

RFC ``intel-signals-data-model`` §5: the "Stories & Analysis"/"Latest Developments" feed on
the Asset Detail page moves from an independently-fetched, independently-cached, per-item
LLM-rewritten `asset_brief.news[]` snapshot to a LIVE query over the shared ``evidence`` table
(``services.asset_detail._live_news_for_instrument``) — no fetch, no cache, no LLM rewrite.
``monitoring.asset_brief._do_refresh`` no longer writes ``news``/``news_checked_at`` at all
(the article data it used to snapshot there already lands in ``evidence`` via
``record_evidence``, which this migration does not touch).

``asset_brief`` is a ``payload_table`` (seq/id/payload, no ``book_id``) — the two keys live
inside the JSON ``payload`` column, so this is a payload rewrite, not a schema change, same
shape as 0058 (``drop_profile_tax_config``) and 0061 (the automations `allowed` rename). A
payload that cannot be parsed is skipped rather than failing the boot — asset_brief is a
derived, rebuildable cache (checkpoint-EXCLUDED, not bridged/portable — the news it held was
already redundant with `evidence` before this migration), so an unreadable row is not worth
blocking every other instrument's migration over.

Downgrade restores the keys with empty/null placeholder values (``news: []``,
``news_checked_at: None``) rather than the original stored articles — those were never a
book fact, and `_do_refresh` never touched-then-forgot data other than this cache, so there is
nothing genuine to reconstruct (mirrors 0058's `downgrade()`: "nothing to reconstruct"). A
downgrade only needs to make the OLD code's `brief.get("news")` reads not KeyError.

Revision ID: 202608301645_drop_asset_brief_news_fields
Revises: 202608301500_intel_stories_split
Create Date: 2026-08-30
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.drop_asset_brief_news_fields")

revision = "202608301645_drop_asset_brief_news_fields"
down_revision = "202608301500_intel_stories_split"
branch_labels = None
depends_on = None

_SELECT = sa.text("SELECT id, payload FROM asset_brief")
_UPDATE = sa.text("UPDATE asset_brief SET payload = :payload WHERE id = :id")
_KEYS = ("news", "news_checked_at")


def upgrade() -> None:
    conn = op.get_bind()
    touched = 0
    for row_id, raw in conn.execute(_SELECT).fetchall():
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable brief must not stop the rest
            log.warning("asset_brief payload for %s is unreadable; skipped", row_id)
            continue
        if not isinstance(payload, dict) or not any(k in payload for k in _KEYS):
            continue
        for key in _KEYS:
            payload.pop(key, None)
        conn.execute(_UPDATE, {"payload": json.dumps(payload), "id": row_id})
        touched += 1
    if touched:
        log.info("dropped news/news_checked_at from %d asset_brief payload(s)", touched)


def downgrade() -> None:
    """Restore the keys as empty placeholders — see the module docstring for why the
    original article snapshots aren't reconstructable (nor worth reconstructing: `evidence`
    still holds the real underlying articles)."""
    conn = op.get_bind()
    touched = 0
    for row_id, raw in conn.execute(_SELECT).fetchall():
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001
            log.warning("asset_brief payload for %s is unreadable; skipped", row_id)
            continue
        if not isinstance(payload, dict) or any(k in payload for k in _KEYS):
            continue
        payload["news"] = []
        payload["news_checked_at"] = None
        conn.execute(_UPDATE, {"payload": json.dumps(payload), "id": row_id})
        touched += 1
    if touched:
        log.info("restored empty news/news_checked_at on %d asset_brief payload(s)", touched)
