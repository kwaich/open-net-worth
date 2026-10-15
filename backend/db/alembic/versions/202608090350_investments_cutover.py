"""investments cutover: absorb all 8 investment JSON sources into their Tier-1/Tier-2
tables (built by revision 0011), with per-store round-trip verification

Plan 4b task 5. Mirrors 0010 (planning cutover) exactly — read every present source's
bytes ONCE (hash + parse the SAME bytes), import, then EXACT round-trip verify (export ==
original) for every absorbed store, aborting the WHOLE migration on any diff (the
fail-closed ``ensure_migrated`` wrapper then restores the pre-migrate backup) — SIMPLER,
like 0010: no cross-file consistency pre-check (no FKs BETWEEN investment tables — the
only FK on any of the 8 is ``book_id -> books.id``, already satisfied by the
``DEFAULT_BOOK_ID`` row revision 0001's identity seed creates before this revision ever
runs) and no embedded-child explosion (every investment table is either a flat payload
list or one of the two Tier-2/bespoke shapes — nothing here has a
``transactions.splits``-style nested list promoted to its own child table). Absorption
order across the eight is therefore COSMETIC, unlike the money core's FK ordering.

Money is GUARDED, not just round-tripped: ``investments_io.import_payload_list`` runs
``money_io._assert_finite_money`` on each store's net-worth-bearing fields
(positions/lots/vest_events/option_contracts/realized_events — grants/watchlists/
asset_brief carry none) BEFORE a row lands in ``payload`` — the plan-4a security-review
lesson (``budget_assignments``' M1 fix) applied PROACTIVELY here rather than as a
post-hoc patch, since investment money feeds net worth and ``/api/data/import`` is untrusted
(FR-M7). A non-finite/non-numeric value RAISES ``ValidationError``, aborting this whole
revision inside Alembic's one transaction — nothing partially absorbed.

``watchlist.json`` parses to a JSON **object** (dict) — checked with ``isinstance(data,
dict)`` (0005's/0010's own ``isinstance(..., list)`` guard, inverted for this one store)
— the other 7 sources (including ``asset_briefs.json``, the Tier-2 cache) parse to a flat
**list**. A shape mismatch RAISES rather than silently coercing or dropping data.
``asset_briefs.json`` may be absent (it is a derived cache, like ``instruments.json`` was
optional pre-0005) or unusually large — absorbed if present, skipped (not required) if
not, exactly like every other source here (``if not src.exists(): continue``).

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers``
renames each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds,
ONLY if the bytes still hash the same.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-09
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608090350_investments_cutover"
down_revision = "202608082245_investments_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Read ``src``'s bytes ONCE, import via ``import_fn``, verify ``export_fn(conn) ==
    original`` (a diff RAISES, aborting this whole revision inside Alembic's one
    transaction, which rolls back every store absorbed earlier in this same run too —
    the "all eight are one all-or-nothing unit" guarantee). Returns ``(raw_bytes,
    original)`` for the caller to stamp into ``json_cutovers``, or ``None`` when ``src``
    does not exist (fresh install, or an optional cache like asset_brief: nothing to
    absorb for this store). Mirrors 0010's identical helper verbatim."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"investments cutover: {src} is not {expected} ({shape.__name__} expected at "
            f"the root) — got {type(original).__name__}; aborting rather than silently "
            "dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"investments cutover: {store} round-trip verification FAILED — aborting the "
            "whole migration (all eight investment sources are one all-or-nothing unit); "
            "the fail-closed wrapper will restore the pre-migrate backup"
        )
    return raw, original


def _stamp_cutover(conn, store: str, src, raw: bytes, now: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {
            "s": store,
            "p": str(src),
            "h": hashlib.sha256(raw).hexdigest(),  # _finish_cutovers refuses a drifted rename
            "t": now,
        },
    )


def upgrade() -> None:
    from backend.db import investments_io as iio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import (
        asset_brief,
        grants,
        lots,
        option_contracts,
        positions,
        realized_events,
        vest_events,
    )

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    def _generic(store: str, table, id_field: str = "id"):
        return (
            lambda c, raw: iio.import_payload_list(c, table, raw, id_field=id_field, store=store),
            lambda c: iio.export_payload_list(c, table),
        )

    # (store, source path, expected root shape, import fn, export fn) — order is COSMETIC
    # (no FK between investment tables); grouped list-shaped-then-dict-shaped, like 0010.
    jobs = [
        ("positions", evsrc.POSITIONS_SOURCE, list, *_generic("positions", positions)),
        ("lots", evsrc.LOTS_SOURCE, list, *_generic("lots", lots)),
        ("grants", evsrc.GRANTS_SOURCE, list, *_generic("grants", grants)),
        ("vest_events", evsrc.VEST_EVENTS_SOURCE, list, *_generic("vest_events", vest_events)),
        (
            "option_contracts",
            evsrc.OPTION_CONTRACTS_SOURCE,
            list,
            *_generic("option_contracts", option_contracts),
        ),
        (
            "realized_events",
            evsrc.REALIZED_EVENTS_SOURCE,
            list,
            *_generic("realized_events", realized_events),
        ),
        (
            "asset_brief",
            evsrc.ASSET_BRIEF_SOURCE,
            list,
            *_generic("asset_brief", asset_brief, id_field="instrument_id"),
        ),
        ("watchlists", evsrc.WATCHLIST_SOURCE, dict, iio.import_watchlist, iio.export_watchlist),
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0011 owns the DDL for all eight investment tables and drops them
    when a caller downgrades past it — there is nothing for this data-only revision to
    undo beyond its own upgrade()'s row inserts, which vanish along with the tables. The
    renamed ``<name>.migrated-<ts>`` sources (once ``ensure_migrated()`` has run for real,
    not just a bare ``command.upgrade``) are the manual restore path, exactly as every
    other cutover revision in this family documents — an automatic, data-destructive
    downgrade here would be a lie."""
