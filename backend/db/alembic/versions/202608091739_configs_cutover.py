"""configs cutover: absorb the 12 JSON config sources into their revision-0015 tables
(plan 4d, task 5). Mirrors 0014 (logs cutover)/0010 (planning cutover) exactly: read
every present source's bytes ONCE, import, EXACT round-trip verify (export ==
original), aborting the WHOLE migration on any diff. No cross-file consistency check
— no FKs BETWEEN config tables; the only FK anywhere in this revision is each Tier-1
member's own ``book_id -> books.id`` (payees/automations/alerts/dashboard_layouts),
already satisfied by Plan 1's identity seed regardless of absorption order.

NO `tool_grants` JOB (removed 2026-08-14). This revision used to absorb `tool_grants.json`
as a tenth store. Revision 0040 folds `tool_grants` into `tool_permissions` and DROPS the
table, so that job could only ever matter to an instance still upgrading FROM FILES — and
carrying it would mean keeping a handle to a dropped table alive in `db.tables` (where it
made `metadata` disagree with the live schema, breaking `alembic check` and silently
aborting the test suite's metadata-driven cleanup), or a throwaway copy here. Owner's
ruling: no compatibility layers, and drop any migration step that only preserves something
they would re-set in one click. A permission that does not carry over costs one toggle;
losing one is the SAFE direction (more asking, never less). Grants already absorbed into
the `tool_grants` TABLE by this revision on a real instance are unaffected — revision 0040
still folds those forward.

SCOPE (controller ruling, plan 4d task 5 — see ``config_io.py``'s own docstring):
this revision absorbs ONLY the 12 config JsonStore subclasses that hold no secrets.
Connector config/secrets (Tasks 3/4: the Fernet encryption seam + the ~17 connector
stores) are DEFERRED to a separate post-terminal security track and untouched here.

Three of the twelve sources parse to a JSON **object** (dict), not a list:
``memory_settings.json``/``preferred_models.json`` (the two namespaced-settings
singletons, sharing ONE ``settings`` table) and ``dashboard_layout.json`` (the
book-keyed singleton) — checked with ``isinstance(data, dict)`` (0005's/0010's/
0012's/0014's own ``isinstance(..., list)`` guard, inverted for these three); the
other nine sources parse to a flat list. A shape mismatch RAISES rather than
silently coercing or dropping data.

ORDER FIX (the one real correctness trap this wave — the Wave C logs-cutover scar,
guarded against here from the start rather than discovered by a review): ``signals``
is the ONE prepend/ring source among these twelve — newest-first on disk, read back
via ``SignalsStore.query_signals``'s explicit ``ORDER BY seq DESC``. It passes
``reverse=True`` to ``config_io.import_payload_list`` so ascending seq means
oldest→newest (matching every append-order store's own convention and
``_next_seq``'s ``max(seq)+1`` for a post-migration append) — a plain file-order
import would have handed the newest pre-existing row seq=0, inverting chronological
order the first time ``seq DESC`` is read post-migration. The matching
``export_payload_list(..., reverse=True)`` reads ``seq DESC`` right back, so the
round-trip ``export == original`` check below still holds byte-exact — see
``config_io.import_payload_list``'s own docstring for the full mechanics, and
``logs_io.import_payload_list`` (Wave C) for the original fix this mirrors. Every
other flat-list source keeps the default ``reverse=False`` (append-order on disk
already). The round-trip check below is ORDER-SENSITIVE by construction — plain
``==`` on the parsed JSON list/dict is positional/element-by-element, never a
set/sorted/count comparison — so ANY store whose import order disagreed with its
export order would fail here even independent of the dedicated ``signals`` fix.

Only once every present store's import + round-trip verification has succeeded does
a ``json_cutovers`` row land per store (source hash included), so
``_finish_cutovers`` renames each source to ``<name>.migrated-<ts>`` after ``upgrade
head`` fully succeeds, ONLY if the bytes still hash the same. Sources are never
deleted.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-09
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608091739_configs_cutover"
down_revision = "202608090943_configs_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Verbatim port of 0012's/0014's own helper (see either revision for the full
    mechanics the sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"configs cutover: {src} is not {expected} ({shape.__name__} expected at "
            f"the root) — got {type(original).__name__}; aborting rather than "
            "silently dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"configs cutover: {store} round-trip verification FAILED — aborting the "
            "whole migration (all twelve config sources are one all-or-nothing unit); "
            "the fail-closed wrapper will restore the pre-migrate backup"
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
    from backend.db import config_io as cio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import (
        DEFAULT_BOOK_ID,
        alerts,
        automations,
        memory,
        news_sources,
        payees,
        statement_formats,
    )

    # `signals` and `intel_signals` are deliberately NOT imported from `backend.db.tables`
    # — `signals` was renamed to `notification_log` at revision 0044 (2026-08-20), and
    # `intel_signals` was split into `intel_stories`/`signal_timeline_entries`/
    # `daily_digests` (RFC `intel-signals-data-model`), both well after this revision
    # shipped. Local, UNREGISTERED `sa.table()` proxies (never touch the shared
    # `metadata`/`target_metadata`) are used instead, mirroring revision 0040's own
    # precedent and the exact `tool_grants` lesson recorded in this file's own module
    # docstring: importing a live `db.tables` symbol into a frozen migration makes
    # `metadata` disagree with the real schema the moment that symbol is renamed or
    # dropped, breaking `alembic check`. At the point THIS revision runs (upgrading from
    # 0015), the physical tables are still named "signals"/"intel_signals" — the
    # rename/split only happens later — so these proxies' names are correct for this
    # revision's own place in history.
    signals = sa.table(
        "signals",
        sa.column("seq", sa.Integer),
        sa.column("id", sa.Text),
        sa.column("payload", sa.JSON),
    )
    intel_signals = sa.table(
        "intel_signals",
        sa.column("seq", sa.Integer),
        sa.column("id", sa.Text),
        sa.column("payload", sa.JSON),
    )

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    def _generic(store: str, table, id_field: str = "id", *, book_id=None, reverse: bool = False):
        return (
            lambda c, raw: cio.import_payload_list(
                c, table, raw, id_field=id_field, book_id=book_id, reverse=reverse
            ),
            lambda c: cio.export_payload_list(c, table, reverse=reverse),
        )

    def _namespaced(namespace: str):
        return (
            lambda c, raw: cio.import_namespaced(c, namespace, raw),
            lambda c: cio.export_namespaced(c, namespace),
        )

    # (store, source path, expected root shape, import fn, export fn) — order is
    # COSMETIC (no FK between config tables; each Tier-1 member's book_id -> books.id
    # is satisfied by Plan 1's identity seed regardless of absorption order).
    #
    # book_id=DEFAULT_BOOK_ID (payees/automations/alerts ONLY): the 3 Tier-1 config
    # stores — every other store below omits book_id (Tier-2/Tier-3, no book concept).
    #
    # reverse=True (signals ONLY): see the module docstring's ORDER FIX section.
    jobs = [
        (
            "payees",
            evsrc.PAYEES_SOURCE,
            list,
            *_generic("payees", payees, book_id=DEFAULT_BOOK_ID),
        ),
        (
            "automations",
            evsrc.AUTOMATIONS_SOURCE,
            list,
            *_generic("automations", automations, book_id=DEFAULT_BOOK_ID),
        ),
        (
            "alerts",
            evsrc.ALERTS_SOURCE,
            list,
            *_generic("alerts", alerts, book_id=DEFAULT_BOOK_ID),
        ),
        (
            "memory",
            evsrc.MEMORY_SOURCE,
            list,
            *_generic("memory", memory),
        ),
        (
            "statement_formats",
            evsrc.STATEMENT_FORMATS_SOURCE,
            list,
            *_generic("statement_formats", statement_formats),
        ),
        (
            "intel_signals",
            evsrc.INTEL_SIGNALS_SOURCE,
            list,
            *_generic("intel_signals", intel_signals),
        ),
        (
            "news_sources",
            evsrc.NEWS_SOURCES_SOURCE,
            list,
            *_generic("news_sources", news_sources),
        ),
        (
            "signals",
            evsrc.SIGNALS_SOURCE,
            list,
            *_generic("signals", signals, reverse=True),
        ),
        (
            "memory_settings",
            evsrc.MEMORY_SETTINGS_SOURCE,
            dict,
            *_namespaced("memory_settings"),
        ),
        (
            "preferred_models",
            evsrc.PREFERRED_MODELS_SOURCE,
            dict,
            *_namespaced("preferred_models"),
        ),
        (
            "dashboard_layout",
            evsrc.DASHBOARD_LAYOUT_SOURCE,
            dict,
            cio.import_dashboard,
            cio.export_dashboard,
        ),
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0015 owns the DDL for all twelve config tables and drops them
    when a caller downgrades past it — there is nothing for this data-only revision to
    undo beyond its own upgrade()'s row inserts, which vanish along with the tables.
    The renamed ``<name>.migrated-<ts>`` sources (once ``ensure_migrated()`` has run
    for real, not just a bare ``command.upgrade``) are the manual restore path, exactly
    as every other cutover revision in this family documents — an automatic,
    data-destructive downgrade here would be a lie."""
