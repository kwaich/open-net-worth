"""wave5f cache-state cutover: absorb digest_state.json, news_screen_state.json and
memory_consolidate_state.json into runtime_state namespace rows, with round-trip
verification. Wave 5F (JSON->DB, the zero-`.json` finisher).

Mirrors 0029's (wave5d2a cutover) `_absorb_one`/`_stamp_cutover` exactly, three stores
into the SAME destination table: read each present source's bytes ONCE (hash + parse the
same bytes), import, EXACT round-trip verify (`export_fn(conn) == original`) — a diff
RAISES, aborting inside Alembic's one transaction, and the fail-closed `ensure_migrated`
wrapper then restores the pre-migrate backup. No cross-file consistency check (no FKs
between any of these three) — absorption order is COSMETIC. No money guard (none of the
three carries a net-worth-bearing amount — `wave5f_io.py`'s own docstring).

Every one of the three sources parses to a JSON **object** (dict) — each becomes ONE
namespace row. A shape mismatch RAISES rather than silently coercing or dropping data.

All three are watermarks a scheduler/consolidation loop writes lazily on first use, so
absence on a fresh or lightly-used instance is a correct no-op, exactly like every other
optional store this migration has absorbed.

A fourth CACHE_DIR file this wave's brief also named, `routing_decisions.json`, is
DELIBERATELY ABSENT here: investigation found it VESTIGIAL (the cross-provider
auto-routing engine that wrote it was removed in `5abdb183`, well before this wave — see
revision 0038, which retires it as dead data rather than migrating it into a table
nothing would ever read from).

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-10
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608110417_wave5f_cache_state_cutover"
down_revision = "202608110416_wave5f_cache_state_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Verbatim port of 0029's own helper (see that revision for the full mechanics the
    sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"wave5f cache-state cutover: {src} is not {expected} ({shape.__name__} "
            f"expected at the root) — got {type(original).__name__}; aborting rather "
            "than silently dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"wave5f cache-state cutover: {store} round-trip verification FAILED — "
            "aborting the whole migration (all present sources are one all-or-nothing "
            "unit); the fail-closed wrapper will restore the pre-migrate backup"
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
    from backend.db import wave5f_io as wio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import runtime_state

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    jobs_spec = [
        ("digest_state", evsrc.DIGEST_STATE_SOURCE),
        ("news_screen_state", evsrc.NEWS_SCREEN_STATE_SOURCE),
        ("memory_consolidate_state", evsrc.MEMORY_CONSOLIDATE_STATE_SOURCE),
    ]

    jobs = [
        (
            store,
            src,
            dict,
            lambda c, raw, ns=store: wio.import_namespaced(c, runtime_state, ns, raw),
            lambda c, ns=store: wio.export_namespaced(c, runtime_state, ns),
        )
        for store, src in jobs_spec
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0024 (Wave 5C) owns `runtime_state`'s DDL — this revision writes
    ROWS into a table it does not own and therefore does not drop it. Automatic
    data-destructive downgrades are deliberately not provided anywhere in this migration
    family (the renamed `<name>.migrated-<ts>` sources, once `ensure_migrated()` has run
    for real, are the manual restore path)."""
