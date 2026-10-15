"""wave5d2a cutover: absorb auth.json, model_config.json, email_ingest.json into
secret_config namespace rows, and app_config.json into a runtime_state namespace row,
with round-trip verification. Wave 5D2a (JSON->DB, secrets/config).

Mirrors 0027's (wave5d1 cutover) ``_absorb_one``/``_stamp_cutover`` exactly, four
stores across TWO destination tables: read each present source's bytes ONCE (hash +
parse the SAME bytes), import, EXACT round-trip verify (``export_fn(conn) ==
original``) — a diff RAISES, aborting inside Alembic's one transaction, and the
fail-closed ``ensure_migrated`` wrapper then restores the pre-migrate backup. No
cross-file consistency check (no FKs between any of these four, or to anything else)
— absorption order is COSMETIC. No money guard (none of the four carries a
net-worth-bearing amount — ``wave5d2a_io.py``'s own docstring).

Every one of the four sources parses to a JSON **object** (dict) — each becomes ONE
namespace row. A shape mismatch RAISES rather than silently coercing or dropping data.

On a fresh/self-hosted instance ``app_config.json``/``email_ingest.json`` may be
absent (never configured) — an absent source is a correct no-op, exactly like every
other optional store this migration has absorbed. ``auth.json``/``model_config.json``
are present on nearly every real instance (auth materialises on first boot; model
config is required to use the assistant at all), but absence is equally a correct
no-op — a truly fresh, never-booted instance re-materialises/re-seeds through the
store's own ``get()``/``_read_raw()`` on first access instead.

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers``
renames each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds,
ONLY if the bytes still hash the same.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-10
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608110409_wave5d2a_cutover"
down_revision = "202608110408_wave5d2a_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Verbatim port of 0027's own helper (see that revision for the full mechanics the
    sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"wave5d2a cutover: {src} is not {expected} ({shape.__name__} expected at "
            f"the root) — got {type(original).__name__}; aborting rather than silently "
            "dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"wave5d2a cutover: {store} round-trip verification FAILED — aborting the "
            "whole migration (all four sources are one all-or-nothing unit); the "
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
    from backend.db import wave5d2a_io as wio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import runtime_state, secret_config

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    # (store name, source path, table) — three land in secret_config, one in runtime_state.
    jobs_spec = [
        ("auth", evsrc.AUTH_SOURCE, secret_config),
        ("model_config", evsrc.MODEL_CONFIG_SOURCE, secret_config),
        ("email_ingest", evsrc.EMAIL_INGEST_SOURCE, secret_config),
        ("app_config", evsrc.APP_CONFIG_SOURCE, runtime_state),
    ]

    jobs = [
        (
            store,
            src,
            dict,
            lambda c, raw, ns=store, t=table: wio.import_namespaced(c, t, ns, raw),
            lambda c, ns=store, t=table: wio.export_namespaced(c, t, ns),
        )
        for store, src, table in jobs_spec
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0026 (Wave 5D1) owns the DDL for secret_config, revision 0024
    (Wave 5C) owns runtime_state — this revision writes ROWS into tables it does not
    own and therefore does not drop them. Automatic data-destructive downgrades are
    deliberately not provided anywhere in this migration family (0010/0021/0023/0025/
    0027's own precedent: the renamed ``<name>.migrated-<ts>`` sources, once
    ``ensure_migrated()`` has run for real, are the manual restore path)."""
