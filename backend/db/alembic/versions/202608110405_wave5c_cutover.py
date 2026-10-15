"""wave5c cutover: absorb dedup_config.json, harness_state.json, skills_state.json,
subscriptions.json and actions.json into their revision-0024 tables (dedup_config into
the pre-existing 0015 ``settings`` table), with round-trip verification. Wave 5C
(JSON->DB).

Mirrors 0023's (wave5b logs cutover) ``_absorb_one``/``_stamp_cutover`` exactly, five
stores: read each present source's bytes ONCE (hash + parse the SAME bytes), import,
EXACT round-trip verify (``export_fn(conn) == original``) — a diff RAISES, aborting
inside Alembic's one transaction, and the fail-closed ``ensure_migrated`` wrapper then
restores the pre-migrate backup. No cross-file consistency check (no FKs between these
five, or to anything else) — absorption order is COSMETIC. No money guard (none of the
five carries a net-worth-bearing amount — ``wave5c_io.py``'s own docstring).

``dedup_config.json``/``harness_state.json`` parse to a JSON **object** (dict) — each
becomes ONE namespace row (``settings``/``runtime_state`` respectively).
``skills_state.json`` ALSO parses to a JSON object, but keyed by skill id (many rows, one
per key) rather than one namespace singleton. ``subscriptions.json``/``actions.json``
parse to a flat JSON **list**. A shape mismatch RAISES rather than silently coercing or
dropping data.

``subscriptions.json`` is VESTIGIAL (see ``db/subscription_store.py``) but absorbed
anyway — real historical user data (dismiss/confirm decisions), not garbage, mirroring
``agent_findings``' own Wave 5B precedent exactly. ``connector_sessions.json`` is
DELIBERATELY NOT part of this cutover — see
``docs/adr/2026-08-10-connector-sessions-deferred-to-secrets-wave.md``.

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers``
renames each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds,
ONLY if the bytes still hash the same.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-10
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608110405_wave5c_cutover"
down_revision = "202608110404_wave5c_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Verbatim port of 0023's own helper (see that revision for the full mechanics
    the sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"wave5c cutover: {src} is not {expected} ({shape.__name__} expected at "
            f"the root) — got {type(original).__name__}; aborting rather than silently "
            "dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"wave5c cutover: {store} round-trip verification FAILED — aborting the "
            "whole migration (all five sources are one all-or-nothing unit); the "
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
    from backend.db import wave5c_io as wio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import actions, runtime_state, settings, skills_state, subscriptions

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    jobs = [
        (
            "dedup_config",
            evsrc.DEDUP_CONFIG_SOURCE,
            dict,
            lambda c, raw: wio.import_namespaced(c, settings, "dedup_config", raw),
            lambda c: wio.export_namespaced(c, settings, "dedup_config"),
        ),
        (
            "harness_state",
            evsrc.HARNESS_STATE_SOURCE,
            dict,
            lambda c, raw: wio.import_namespaced(c, runtime_state, "harness_state", raw),
            lambda c: wio.export_namespaced(c, runtime_state, "harness_state"),
        ),
        (
            "skills_state",
            evsrc.SKILLS_STATE_SOURCE,
            dict,
            lambda c, raw: wio.import_keyed(c, skills_state, raw),
            lambda c: wio.export_keyed(c, skills_state),
        ),
        (
            "subscriptions",
            evsrc.SUBSCRIPTIONS_SOURCE,
            list,
            lambda c, raw: wio.import_payload_list(c, subscriptions, raw),
            lambda c: wio.export_payload_list(c, subscriptions),
        ),
        (
            "actions",
            evsrc.ACTIONS_SOURCE,
            list,
            lambda c, raw: wio.import_actions(c, actions, raw),
            lambda c: wio.export_actions(c, actions),
        ),
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0024 owns the DDL for runtime_state/skills_state/subscriptions/
    actions and drops them when a caller downgrades past it; dedup_config's row lives in
    the pre-existing (revision 0015) ``settings`` table, which this revision does not own
    and therefore does not drop — an explicit DELETE would be needed to undo JUST that
    row, but automatic data-destructive downgrades are deliberately not provided anywhere
    in this migration family (0010/0021/0023's own precedent: the renamed
    ``<name>.migrated-<ts>`` sources, once ``ensure_migrated()`` has run for real, are the
    manual restore path)."""
