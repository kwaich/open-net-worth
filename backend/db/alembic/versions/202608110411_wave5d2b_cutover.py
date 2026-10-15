"""wave5d2b cutover: absorb pending_plans.json, mcp_pending_calls.json and
chat_pairing.json into their revision-0030 tables, with round-trip verification. Wave
5D2b (JSON->DB, permission/nonce machinery — security-review gate).

Mirrors 0025's (wave5c cutover) ``_absorb_one``/``_stamp_cutover`` exactly, three stores:
read each present source's bytes ONCE (hash + parse the SAME bytes), import, EXACT
round-trip verify (``export_fn(conn) == original``) — a diff RAISES, aborting inside
Alembic's one transaction, and the fail-closed ``ensure_migrated`` wrapper then restores
the pre-migrate backup. No cross-file consistency check (no FKs between these three, or to
anything else) — absorption order is COSMETIC. No money guard (none of the three carries a
net-worth-bearing amount of its own — ``wave5d2b_io.py``'s own docstring).

Every one of the three sources parses to a JSON **object** (dict), keyed by a token /
tool_use_id / nonce / "<connector>:<sender_id>" composite — never a flat list. A shape
mismatch RAISES rather than silently coercing or dropping data.

``pending_plans.json`` has one wrinkle the other two don't: it moved from ``CACHE_DIR`` to
``DATA_DIR`` on 2026-07-30 (``db.pending_plan_store``'s former ``_migrate_from_cache_dir``,
retired by this same wave — see that store's module docstring), adopted in place the first
time a ``PendingPlanStore`` was ever constructed after that date. An instance that never
happened to reconstruct one between 2026-07-30 and this cutover (verified concretely: this
very worktree's own dev data is exactly this case — ``~/.open-net-worth/cache/
pending_plans.json`` exists, ``~/.open-net-worth/pending_plans.json`` does not) still has
its queue sitting at the legacy path. ``_pending_plans_source`` below is PURE DISCOVERY —
it picks whichever path actually holds data and never touches the filesystem itself — so
the normal ``_absorb_one``/``_finish_cutovers`` machinery (which only renames a source to
``.migrated-<ts>`` AFTER the whole migration has verifiably succeeded) does the rename
in-place at whichever path was actually used, exactly like ``pending_analysis.json``'s own
pre-existing CACHE_DIR source already works. Nothing moves before success is confirmed.

On a fresh/self-hosted instance every source may be absent (the confirm gate has never
staged a call, no external-MCP server configured, no chat channel paired) — an absent
source is a correct no-op, exactly like every other optional store this migration has
absorbed.

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers``
renames each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds,
ONLY if the bytes still hash the same.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-10
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "202608110411_wave5d2b_cutover"
down_revision = "202608110410_wave5d2b_schema"
branch_labels = None
depends_on = None

# `mcp_pending_calls` was DROPPED at revision 0047 (2026-08-22, external-MCP's confirm→apply
# migrated onto `pending_plans` — see that revision's own docstring), so the live
# `backend.db.tables` module no longer carries that symbol. This migration must still be able
# to replay from scratch against the table revision 0030 (the schema half of this same wave)
# creates via plain DDL — so this is a FROZEN local reference to that table's shape
# (`seq`/`id`/`payload`, exactly `payload_table()`'s own shape), not a live import, mirroring
# revision 0040's own `_grants` table clause for the same reason (a migration must not depend
# on live application code that a LATER migration is free to remove). `pending_plans`/
# `chat_pairing` are unaffected by that later removal and stay imported live, unchanged, below.
_mcp_pending_calls = sa.table(
    "mcp_pending_calls",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)


def _pending_plans_source(evsrc) -> Path:
    """Which path actually holds the pending_plans queue: the current DATA_DIR location,
    or — for an instance that never reconstructed a ``PendingPlanStore`` since the
    2026-07-30 CACHE_DIR->DATA_DIR move — the legacy CACHE_DIR file. Read-only discovery,
    no filesystem mutation: whichever path this returns becomes ``source_path`` in the
    ``json_cutovers`` row, so ``_finish_cutovers`` (``db/migrate.py`` — which only runs
    AFTER the whole migration has verifiably succeeded) is what actually renames it to
    ``.migrated-<ts>``, exactly like every other absorbed source. Never move a file before
    success is confirmed."""
    if evsrc.PENDING_PLANS_SOURCE.exists():
        return evsrc.PENDING_PLANS_SOURCE
    from backend.core.paths import CACHE_DIR

    legacy = CACHE_DIR / evsrc.PENDING_PLANS_SOURCE.name
    return legacy if legacy.exists() else evsrc.PENDING_PLANS_SOURCE


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Verbatim port of 0025's own helper (see that revision for the full mechanics the
    sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"wave5d2b cutover: {src} is not {expected} ({shape.__name__} expected at "
            f"the root) — got {type(original).__name__}; aborting rather than silently "
            "dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"wave5d2b cutover: {store} round-trip verification FAILED — aborting the "
            "whole migration (all three sources are one all-or-nothing unit); the "
            "fail-closed wrapper will restore the pre-migrate backup"
        )
    return raw, original


def _stamp_cutover(conn, store: str, src, raw: bytes, now: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {"s": store, "p": str(src), "h": hashlib.sha256(raw).hexdigest(), "t": now},
    )


def upgrade() -> None:
    from backend.db import wave5d2b_io as wio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import chat_pairing, pending_plans

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    jobs = [
        (
            "pending_plans",
            _pending_plans_source(evsrc),
            dict,
            lambda c, raw: wio.import_keyed(c, pending_plans, raw),
            lambda c: wio.export_keyed(c, pending_plans),
        ),
        (
            "mcp_pending_calls",
            evsrc.MCP_PENDING_CALLS_SOURCE,
            dict,
            lambda c, raw: wio.import_keyed(c, _mcp_pending_calls, raw),
            lambda c: wio.export_keyed(c, _mcp_pending_calls),
        ),
        (
            "chat_pairing",
            evsrc.CHAT_PAIRING_SOURCE,
            dict,
            lambda c, raw: wio.import_keyed(c, chat_pairing, raw),
            lambda c: wio.export_keyed(c, chat_pairing),
        ),
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0030 owns the DDL for pending_plans/mcp_pending_calls/chat_pairing
    and drops them when a caller downgrades past it. Automatic data-destructive downgrades
    are deliberately not provided anywhere in this migration family (0010/0021/0023/0025/
    0027/0029's own precedent: the renamed ``<name>.migrated-<ts>`` sources, once
    ``ensure_migrated()`` has run for real, are the manual restore path)."""
