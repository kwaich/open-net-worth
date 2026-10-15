"""planning cutover: absorb all 12 planning JSON sources into their Tier-1 tables
(built by revision 0009), with per-store round-trip verification

Plan 4a task 6. Mirrors 0007 (money core) — read every present source's bytes ONCE
(hash + parse the SAME bytes), import, then EXACT round-trip verify (export ==
original) for every absorbed store, aborting the WHOLE migration on any diff (spec D6
step 4; the fail-closed ``ensure_migrated`` wrapper then restores the pre-migrate
backup) — but SIMPLER: no cross-file consistency pre-check (no FKs BETWEEN planning
tables, unlike the money core's ``verify_cross_file_consistency`` — the only FK on any
of these twelve is ``book_id -> books.id``, already satisfied by the ``DEFAULT_BOOK_ID``
row revision 0001's identity seed creates before this revision ever runs) and no
embedded-child explosion (every planning table is either a flat payload list or one of
the two bespoke dict-shaped stores — nothing here has a ``transactions.splits``-style
nested list promoted to its own child table). Absorption order across the twelve is
therefore COSMETIC, unlike the money core's accounts-before-transactions FK ordering.

``budget_assignments``/``plan_state`` parse to a JSON **object** (dict), not a list —
their shape is checked with ``isinstance(data, dict)`` (0005's own ``isinstance(...,
list)`` guard, inverted for these two) and a mismatch RAISES rather than silently
coercing or dropping data, exactly like every other shape guard in this cutover family.

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers``
renames each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds,
ONLY if the bytes still hash the same.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608081826_planning_cutover"
down_revision = "202608081119_planning_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Read ``src``'s bytes ONCE, import via ``import_fn``, verify ``export_fn(conn) ==
    original`` (spec D6 step 4 — a diff RAISES, aborting this whole revision inside
    Alembic's one transaction, which rolls back every store absorbed earlier in this
    same run too — the "all twelve are one all-or-nothing unit" guarantee). Returns
    ``(raw_bytes, original)`` for the caller to stamp into ``json_cutovers``, or
    ``None`` when ``src`` does not exist (fresh install: nothing to absorb for this
    store)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"planning cutover: {src} is not {expected} ({shape.__name__} expected at "
            f"the root) — got {type(original).__name__}; aborting rather than silently "
            "dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"planning cutover: {store} round-trip verification FAILED — aborting the "
            "whole migration (all twelve planning sources are one all-or-nothing unit); "
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
    from backend.db import planning_io as pio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import (
        categories,
        commitments,
        corrections,
        envelopes,
        income_events,
        match_decisions,
        match_reviews,
        recurring_streams,
        targets,
    )

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    # This revision runs BEFORE the table was renamed ``envelope_groups`` (revision 202610031200),
    # so it targets the name the schema has at this point in history, via a throwaway Table.
    category_groups = sa.Table(
        "category_groups",
        sa.MetaData(),
        sa.Column("book_id", sa.Text, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )

    def _generic(table):
        return (
            lambda c, raw: pio.import_payload_list(c, table, raw),
            lambda c: pio.export_payload_list(c, table),
        )

    _, match_decisions_export = _generic(match_decisions)  # export is generic; import is bespoke

    # (store, source path, expected root shape, import fn, export fn) — order is
    # COSMETIC (no FK between planning tables); grouped list-shaped-then-dict-shaped.
    jobs = [
        ("category_groups", evsrc.CATEGORY_GROUPS_SOURCE, list, *_generic(category_groups)),
        ("categories", evsrc.CATEGORIES_SOURCE, list, *_generic(categories)),
        ("commitments", evsrc.COMMITMENTS_SOURCE, list, *_generic(commitments)),
        ("envelopes", evsrc.ENVELOPES_SOURCE, list, *_generic(envelopes)),
        ("income_events", evsrc.INCOME_EVENTS_SOURCE, list, *_generic(income_events)),
        ("recurring_streams", evsrc.RECURRING_STREAMS_SOURCE, list, *_generic(recurring_streams)),
        ("targets", evsrc.TARGETS_SOURCE, list, *_generic(targets)),
        ("corrections", evsrc.CORRECTIONS_SOURCE, list, *_generic(corrections)),
        ("match_reviews", evsrc.MATCH_REVIEWS_SOURCE, list, *_generic(match_reviews)),
        (
            "match_decisions",
            evsrc.MATCH_DECISIONS_SOURCE,
            list,
            pio.import_match_decisions,
            match_decisions_export,
        ),
        (
            "budget_assignments",
            evsrc.BUDGET_ASSIGNMENTS_SOURCE,
            dict,
            pio.import_budget_assignments,
            pio.export_budget_assignments,
        ),
        ("plan_state", evsrc.PLAN_STATE_SOURCE, dict, pio.import_plan_state, pio.export_plan_state),
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0009 owns the DDL for all twelve planning tables and drops them
    when a caller downgrades past it — there is nothing for this data-only revision to
    undo beyond its own upgrade()'s row inserts, which vanish along with the tables.
    The renamed ``<name>.migrated-<ts>`` sources (once ``ensure_migrated()`` has run for
    real, not just this bare ``command.upgrade``) are the manual restore path, exactly
    as every other cutover revision in this family documents — an automatic,
    data-destructive downgrade here would be a lie."""
