"""intel_signals -> intel_stories + signal_timeline_entries + daily_digests.

RFC ``docs/rfc/intel-signals-data-model.md``. One multi-writer payload table (8 real `type`
values, an unbounded JSON `timeline` list, a `summary` key that meant two different things at
two different scopes) splits into three tables that match its three real shapes: story arcs
(7 types sharing one shape), each story's timeline entries (a real child table, one row per
dated development — direct structural copy of ``session_messages``, migration
``202608241937``), and `daily_digest` rows (structurally foreign to a story: no
ticker/timeline, a `daily-digest-<date>` id, always upserted never a list).

## Sequencing

One revision, big-bang (the ``session_messages`` precedent, RFC §3.1): table-create +
backfill + verify + old-table-drop, no dual-read transition window left to rot. This is the
single shared ``~/.open-net-worth/data/`` database — a silent partial migration is the one
outcome worth refusing over.

## Identity fix (RFC §2.4, §3.1 step 3)

Every story row's id gets a NEW ``instrument_id`` column populated where resolvable, and — for
the three known ticker-embedded id SHAPES (``crash-watch-<ticker>``,
``price-alert-<ticker>-<key>``, ``news-<ticker>``) — the id STRING itself is rewritten to embed
``instrument_id`` instead. Verified against a backup of the real production database (256
rows, 186 stories / 70 digests, 906 timeline entries) before writing this resolution logic, not
guessed:

  * ``news-*`` ids (30/30 checked) ALREADY embed the instrument's own id, not a ticker — the
    fragment after ``news-`` matches an existing ``instruments.id`` exactly, every time. This
    contradicts the RFC's own read of ``analyst.py:1829``'s rare ticker-fallback branch; on
    real data the normal create path already mints ``news-<instrument_id>``. Resolution
    therefore checks this FIRST (an id-fragment that is already a real instrument id needs no
    ticker lookup and is never rewritten — rewriting it to itself would be a no-op, and the
    comparison below naturally declines since ``ticker != instrument_id``).
  * ``crash-watch-*``/``price-alert-*`` ids (0/79 checked) are genuinely ticker-embedded —
    resolved via an exact, case-insensitive ``price_source_id`` match (the same rung
    ``services.instruments.resolve_by_ticker`` tries first), then rewritten.
  * 44 of 186 real story rows do NOT resolve (a price alert on a ticker not currently tracked
    as an instrument, e.g. MDB/CRM/CEG/RGTI/IONQ; a few crypto rows whose ticker, e.g. "BTC",
    doesn't match the instrument's coingecko-id-shaped ``price_source_id``, e.g. "bitcoin").
    Per RFC: kept under their LEGACY id, ``instrument_id`` stays NULL, logged — never dropped.
  * ``topic`` rows NEVER resolve an ``instrument_id`` (RFC: "NULL for topic rows — never a
    holding"), even the ~40% of real topic rows that happen to carry a `ticker` field (a
    topic's own primary linked ticker is a cross-reference, not the topic's identity).

## A real-data schema correction found while writing this (not in the RFC's own text)

``daily_digests.trigger``: the RFC's schema assumed ``'eod' | 'premarket'``. Every one of the
70 real digest rows carries ``"trigger": "scheduled"`` instead —
``monitoring/digest.py::_build_and_deliver(trigger)`` receives "eod"/"premarket" from its
caller but never forwards `trigger` into ``_write_inapp_digest(digest, message)``, which
hardcodes the literal "scheduled" regardless. That is a pre-existing bug in `digest.py` itself
(unrelated to this migration; a `monitoring/` writer-call-site fix, out of scope here) — the
``daily_digests.trigger`` CHECK constraint (``db/tables.py``) accepts ``'scheduled'`` too so
this migration carries every real row forward rather than rejecting it on a CHECK violation.

## Verify-before-drop (RFC §3.1 step 5)

Row/entry counts are asserted equal to what was actually inserted before ``intel_signals`` is
dropped — a mismatch RAISES and refuses, the same refuse-loudly contract ``session_messages``
enforces. A row shaped like neither a story (``"label" in row and "status" in row``) nor a
digest (``type == "daily_digest"``) is reported and SKIPPED, never silently absorbed or
dropped.

## Downgrade

Rebuilds ``intel_signals`` rows from the three new tables — genuinely reversible, every field
survives in one of them. Id rewrites are NOT reversed: the legacy ticker-embedded id is not
recoverable from the instrument_id-embedded one in the general case (a ticker can rename) —
a downgraded row keeps whichever id it had at the time of THIS revision's upgrade (rewritten or
not), same limitation ``session_messages``' own downgrade docstring calls out for its own scope.

Revision ID: 202608301500
Revises: 202608281125
Create Date: 2026-08-30
"""

from __future__ import annotations

import logging
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.intel_stories_split")

revision = "202608301500_intel_stories_split"
down_revision = "202608281125_remove_self_harness"
branch_labels = None
depends_on = None

_STORY_TYPES = ("asset", "topic", "risk", "opportunity", "macro", "monitoring", "price_alert")
_ENTRY_FIELDS = (
    "date",
    "title",
    "summary",
    "sources",
    "superseded",
    "supersedes",
    "contradicts",
    "status",
    "confirmed_at",
)


def _classify(row: dict) -> str:
    """A row is a story if it carries BOTH `label` and `status` (the shape
    `_persist_story` always stamps) AND a `type` in `intel_stories`'s own CHECK-constraint
    vocabulary (`type` is NOT NULL at the target schema — a story-shaped row missing or
    misspelling it can never satisfy that constraint, so it must be treated the same as any
    other unrecognized shape, not crash the whole migration); a `daily_digest` row carries
    neither. Anything else is a genuinely unrecognized shape — reported and skipped, never
    silently absorbed."""
    if "label" in row and "status" in row and row.get("type") in _STORY_TYPES:
        return "story"
    if row.get("type") == "daily_digest":
        return "digest"
    return "unknown"


def _resolve_instrument_id(
    row_id: str,
    row_type: str,
    ticker: str,
    instrument_ids: set[str],
    by_price_source_id: dict[str, str],
) -> str | None:
    """Best-effort ticker -> instrument_id resolution (see this file's own module
    docstring for what was verified against real production data). `topic` rows never
    resolve one (RFC: "NULL for topic rows — never a holding"), even when a topic
    happens to carry a `ticker` cross-reference field."""
    if row_type == "topic":
        return None
    if row_id.startswith("news-") and not row_id.startswith("news-topic-"):
        frag = row_id[len("news-") :]
        if frag in instrument_ids:
            return frag
    ticker_lo = (ticker or "").strip().lower()
    if ticker_lo and ticker_lo in by_price_source_id:
        return by_price_source_id[ticker_lo]
    return None


def _rewrite_story_id(row_id: str, ticker: str, instrument_id: str | None) -> str:
    """Rewrite a ticker-embedded id (RFC §2.4) to embed `instrument_id` instead — ONLY
    when the row's own id matches one of the three known ticker-embedded shapes for
    THIS row's own ticker. `news-topic-*` ids are never ticker-keyed and pass through
    untouched. A row whose id does not match any known shape (already instrument_id-
    keyed — every real `news-*` id, or `risk_signals._synthetic_key`'s own
    already-correct shape — or something else entirely) also passes through
    untouched — never a guess."""
    if not instrument_id or row_id.startswith("news-topic-"):
        return row_id
    ticker_lo = (ticker or "").strip().lower()
    if not ticker_lo:
        return row_id
    if row_id == f"crash-watch-{ticker_lo}":
        return f"crash-watch-{instrument_id}"
    if row_id == f"news-{ticker_lo}":
        return f"news-{instrument_id}"
    price_alert_prefix = f"price-alert-{ticker_lo}-"
    if row_id.startswith(price_alert_prefix):
        safe_key = row_id[len(price_alert_prefix) :]
        return f"price-alert-{instrument_id}-{safe_key}"
    return row_id


def _story_row_values(seq: int, row: dict, new_id: str, instrument_id: str | None) -> dict:
    """`payload` for the new row: the old row, id rewritten, `summary` renamed to
    `current_summary` (§2.1.1), `timeline` dropped (it now lives in
    `signal_timeline_entries` — keeping it in `payload` too would be a second, silently
    stale copy of exactly the data this split exists to stop duplicating)."""
    story = dict(row)
    story["id"] = new_id
    story["current_summary"] = story.pop("summary", "") or ""
    story.pop("timeline", None)
    if instrument_id:
        story["instrument_id"] = instrument_id
    return {
        "seq": seq,
        "id": new_id,
        "type": story.get("type"),
        "instrument_id": instrument_id,
        "ticker": story.get("ticker"),
        "label": story.get("label"),
        "status": story.get("status"),
        "current_summary": story.get("current_summary") or "",
        "portfolio_impact": story.get("portfolio_impact") or "",
        "action_items": story.get("action_items") or [],
        "affected_holdings": story.get("affected_holdings"),
        "analyzed": bool(story.get("analyzed")),
        "read": bool(story.get("read")),
        "dismissed": bool(story.get("dismissed")),
        "resolved": bool(story.get("resolved")),
        "resolved_reason": story.get("resolved_reason"),
        "archived": bool(story.get("archived")),
        "evidence_watermark": story.get("evidence_watermark"),
        "created_at": story.get("created_at") or "",
        "updated_at": story.get("updated_at") or "",
        "payload": story,
    }


def _entry_row_values(signal_id: str, entry: dict) -> dict:
    """An entry missing its own `id` (587 of 906 real entries — every entry minted
    before the ADR 2026-08-28-sourced-and-corroborated-intel-signal-updates id-carrying
    duplicate/contradicts/supersedes fields shipped) gets a freshly minted one — this is
    a NEW business identity nothing referenced before (an id-less entry could not have
    been a `duplicate_of_id`/`contradicts`/`supersedes` target), so minting one now loses
    nothing."""
    entry = dict(entry)
    entry_id = entry.get("id") or uuid4().hex
    entry["id"] = entry_id
    values = {"id": entry_id, "signal_id": signal_id, "payload": entry}
    for field in _ENTRY_FIELDS:
        if field == "sources":
            values[field] = entry.get(field) or []
        elif field == "superseded":
            values[field] = bool(entry.get(field))
        elif field in ("date", "title", "summary"):
            values[field] = entry.get(field) or ""
        else:
            values[field] = entry.get(field)
    return values


def _digest_row_values(row: dict) -> dict:
    date = row.get("id", "")
    prefix = "daily-digest-"
    if date.startswith(prefix):
        date = date[len(prefix) :]
    return {
        "date": date,
        "created_at": row.get("created_at") or "",
        "trigger": row.get("trigger") or "scheduled",
        "severity": row.get("severity") or "info",
        "title": row.get("title") or "Daily Digest",
        "message": row.get("summary") or "",
        "affected_tickers": row.get("affected_tickers") or [],
        "sources": row.get("sources") or [],
        "source_urls": row.get("source_urls") or [],
        "read": bool(row.get("read")),
        "payload": row,
    }


def _create_intel_stories_table() -> sa.Table:
    table = op.create_table(
        "intel_stories",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.Text, nullable=True),
        sa.Column("ticker", sa.Text, nullable=True),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("current_summary", sa.Text, nullable=False, server_default=""),
        sa.Column("portfolio_impact", sa.Text, nullable=False, server_default=""),
        sa.Column("action_items", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("affected_holdings", sa.JSON, nullable=True),
        sa.Column("analyzed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("read", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dismissed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolved", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolved_reason", sa.Text, nullable=True),
        sa.Column("archived", sa.Integer, nullable=False, server_default="0"),
        sa.Column("evidence_watermark", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.CheckConstraint(
            "type IN ('asset','topic','risk','opportunity','macro','monitoring','price_alert')",
            name=sa.schema.conv("ck_intel_stories_type"),
        ),
        sa.CheckConstraint(
            "analyzed IN (0,1)", name=sa.schema.conv("ck_intel_stories_analyzed_bool")
        ),
        sa.CheckConstraint("read IN (0,1)", name=sa.schema.conv("ck_intel_stories_read_bool")),
        sa.CheckConstraint(
            "dismissed IN (0,1)", name=sa.schema.conv("ck_intel_stories_dismissed_bool")
        ),
        sa.CheckConstraint(
            "resolved IN (0,1)", name=sa.schema.conv("ck_intel_stories_resolved_bool")
        ),
        sa.CheckConstraint(
            "archived IN (0,1)", name=sa.schema.conv("ck_intel_stories_archived_bool")
        ),
    )
    op.create_index("ix_intel_stories_seq", "intel_stories", ["seq"], unique=True)
    op.create_index("ix_intel_stories_type", "intel_stories", ["type"])
    op.create_index("ix_intel_stories_instrument", "intel_stories", ["instrument_id"])
    op.create_index("ix_intel_stories_status", "intel_stories", ["status"])
    return table


def _create_signal_timeline_entries_table() -> sa.Table:
    table = op.create_table(
        "signal_timeline_entries",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("id", sa.Text, nullable=False),
        sa.Column(
            "signal_id",
            sa.Text,
            sa.ForeignKey("intel_stories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("sources", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("superseded", sa.Integer, nullable=False, server_default="0"),
        sa.Column("supersedes", sa.Text, nullable=True),
        sa.Column("contradicts", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=True),
        sa.Column("confirmed_at", sa.Text, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.CheckConstraint(
            "superseded IN (0,1)", name=sa.schema.conv("ck_signal_timeline_entries_superseded_bool")
        ),
    )
    op.create_index(
        "ix_signal_timeline_entries_signal_seq", "signal_timeline_entries", ["signal_id", "seq"]
    )
    op.create_index("ix_signal_timeline_entries_id", "signal_timeline_entries", ["id"], unique=True)
    return table


def _create_daily_digests_table() -> sa.Table:
    table = op.create_table(
        "daily_digests",
        sa.Column("date", sa.Text, primary_key=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False, server_default="info"),
        sa.Column("title", sa.Text, nullable=False, server_default="Daily Digest"),
        sa.Column("message", sa.Text, nullable=False, server_default=""),
        sa.Column("affected_tickers", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("sources", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("source_urls", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("read", sa.Integer, nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.CheckConstraint(
            "trigger IN ('eod','premarket','scheduled')",
            name=sa.schema.conv("ck_daily_digests_trigger"),
        ),
        sa.CheckConstraint("read IN (0,1)", name=sa.schema.conv("ck_daily_digests_read_bool")),
    )
    return table


# Local, UNREGISTERED sa.table() proxies for the two EXISTING tables this revision reads from
# — never imported from backend.db.tables (that module no longer defines `intel_signals` as of
# THIS revision; see config_io.py's own docstring for the identical `tool_grants`/`signals`
# lesson this mirrors). `instruments` is read-only here and stable in shape, but kept as a
# local proxy for the same reason.
_intel_signals_src = sa.table(
    "intel_signals",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)
_instruments_src = sa.table("instruments", sa.column("id", sa.Text), sa.column("payload", sa.JSON))


def _load_instrument_index(conn) -> tuple[set[str], dict[str, str]]:
    """(every instrument id, {lowercased price_source_id: instrument id}) — the two lookups
    `_resolve_instrument_id` needs, read once up front."""
    instrument_ids: set[str] = set()
    by_price_source_id: dict[str, str] = {}
    for iid, payload in conn.execute(sa.select(_instruments_src.c.id, _instruments_src.c.payload)):
        instrument_ids.add(iid)
        psid = ((payload or {}).get("price_source_id") or "").strip().lower()
        if psid:
            by_price_source_id[psid] = iid
    return instrument_ids, by_price_source_id


def _partition_signal_rows(
    conn, instrument_ids: set[str], by_price_source_id: dict[str, str]
) -> tuple[list[dict], list[dict], list[dict], list[tuple[str, str]], list[str]]:
    """Read every `intel_signals` row and partition it into (story_rows, entry_rows,
    digest_rows, unresolved ticker->instrument_id pairs, unrecognized row ids)."""
    rows = conn.execute(
        sa.select(
            _intel_signals_src.c.seq, _intel_signals_src.c.id, _intel_signals_src.c.payload
        ).order_by(_intel_signals_src.c.seq)
    ).fetchall()

    story_rows: list[dict] = []
    entry_rows: list[dict] = []
    digest_rows: list[dict] = []
    unresolved: list[tuple[str, str]] = []
    unknown_shapes: list[str] = []
    unresolvable_types = ("asset", "risk", "opportunity", "monitoring", "price_alert")

    for seq, old_id, payload in rows:
        row = dict(payload or {})
        shape = _classify(row)
        if shape == "unknown":
            unknown_shapes.append(old_id)
            continue
        if shape == "digest":
            digest_rows.append(_digest_row_values(row))
            continue

        ticker = row.get("ticker") or ""
        row_type = row.get("type") or ""
        instrument_id = _resolve_instrument_id(
            old_id, row_type, ticker, instrument_ids, by_price_source_id
        )
        new_id = _rewrite_story_id(old_id, ticker, instrument_id)
        if not instrument_id and row_type in unresolvable_types:
            unresolved.append((old_id, ticker))

        story_rows.append(_story_row_values(seq, row, new_id, instrument_id))
        for entry in row.get("timeline") or []:
            if isinstance(entry, dict):
                entry_rows.append(_entry_row_values(new_id, entry))

    return story_rows, entry_rows, digest_rows, unresolved, unknown_shapes


def _verify_backfill(
    conn,
    tables: tuple[sa.Table, sa.Table, sa.Table],
    rows: tuple[list[dict], list[dict], list[dict]],
) -> tuple[int, int, int]:
    """Counts actually in the three new tables must equal what was inserted — a silent
    partial migration of real story/digest history is the one outcome worth refusing over
    (session_messages' own contract). Returns the three counts for the log line."""
    counts = tuple(
        conn.execute(sa.select(sa.func.count()).select_from(t)).scalar() or 0 for t in tables
    )
    if counts != tuple(len(r) for r in rows):
        raise RuntimeError(
            "intel_stories_split backfill verification failed: "
            f"stories inserted={len(rows[0])} table={counts[0]}, "
            f"entries inserted={len(rows[1])} table={counts[1]}, "
            f"digests inserted={len(rows[2])} table={counts[2]} — "
            "refusing to drop intel_signals"
        )
    return counts


def upgrade() -> None:
    intel_stories_t = _create_intel_stories_table()
    entries_t = _create_signal_timeline_entries_table()
    digests_t = _create_daily_digests_table()

    conn = op.get_bind()
    instrument_ids, by_price_source_id = _load_instrument_index(conn)
    story_rows, entry_rows, digest_rows, unresolved, unknown_shapes = _partition_signal_rows(
        conn, instrument_ids, by_price_source_id
    )

    if story_rows:
        conn.execute(sa.insert(intel_stories_t), story_rows)
    if entry_rows:
        conn.execute(sa.insert(entries_t), entry_rows)
    if digest_rows:
        conn.execute(sa.insert(digests_t), digest_rows)

    story_count, entry_count, digest_count = _verify_backfill(
        conn,
        (intel_stories_t, entries_t, digests_t),
        (story_rows, entry_rows, digest_rows),
    )

    log.info(
        "intel_stories_split: %d stories, %d timeline entries, %d digests migrated "
        "(%d unresolved ticker->instrument_id: %s)%s",
        story_count,
        entry_count,
        digest_count,
        len(unresolved),
        unresolved[:10],
        f" ({len(unknown_shapes)} unrecognized row(s) skipped: {unknown_shapes[:5]})"
        if unknown_shapes
        else "",
    )

    op.drop_table("intel_signals")


def _rebuild_intel_signals_rows(conn) -> list[dict]:
    """(seq, id, payload) rows to reinsert into a recreated `intel_signals`, folding
    `signal_timeline_entries` back onto each story's `timeline[]` and reversing the
    `current_summary` rename. Renumbered from 0 across BOTH sources combined (stories by
    their own `seq`, then digests by date) — the original `intel_signals.seq` values are
    NOT reusable as-is: they were scattered across the full 0..N-1 range shared with
    digest rows before the split, so replaying only the story subset's original values
    here would leave gaps a later digest seq could collide with. `seq` was always just an
    insertion-order proxy (never a value anything else keys off), so a fresh, gap-free
    renumbering is exactly as valid — same posture as `PayloadTableStore.replace_all`'s
    own renumbering."""
    stories_t = sa.table(
        "intel_stories",
        sa.column("seq", sa.Integer),
        sa.column("id", sa.Text),
        sa.column("payload", sa.JSON),
    )
    entries_t = sa.table(
        "signal_timeline_entries", sa.column("signal_id", sa.Text), sa.column("payload", sa.JSON)
    )
    digests_t = sa.table("daily_digests", sa.column("payload", sa.JSON))

    entries_by_signal: dict[str, list[dict]] = {}
    for signal_id, payload in conn.execute(sa.select(entries_t.c.signal_id, entries_t.c.payload)):
        entries_by_signal.setdefault(signal_id, []).append(payload)

    payloads: list[tuple[str, dict]] = []
    for story_id, payload in conn.execute(sa.select(stories_t.c.id, stories_t.c.payload)):
        row = dict(payload or {})
        row["summary"] = row.pop("current_summary", "") or ""
        row["timeline"] = entries_by_signal.get(story_id, [])
        payloads.append((story_id, row))
    for (payload,) in conn.execute(sa.select(digests_t.c.payload)):
        payloads.append((payload.get("id"), payload))

    return [
        {"seq": seq, "id": row_id, "payload": row} for seq, (row_id, row) in enumerate(payloads)
    ]


def downgrade() -> None:
    """Rebuild `intel_signals` rows from the three new tables. Id rewrites are NOT
    reversed — see this file's own module docstring for why (a renamed ticker means the
    legacy id is not recoverable from the instrument_id-embedded one in general)."""
    op.create_table(
        "intel_signals",
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_intel_signals_seq", "intel_signals", ["seq"], unique=True)
    conn = op.get_bind()

    intel_signals = sa.table(
        "intel_signals",
        sa.column("seq", sa.Integer),
        sa.column("id", sa.Text),
        sa.column("payload", sa.JSON),
    )
    rows_to_insert = _rebuild_intel_signals_rows(conn)
    if rows_to_insert:
        conn.execute(sa.insert(intel_signals), rows_to_insert)

    op.drop_table("daily_digests")
    op.drop_index("ix_signal_timeline_entries_id", table_name="signal_timeline_entries")
    op.drop_index("ix_signal_timeline_entries_signal_seq", table_name="signal_timeline_entries")
    op.drop_table("signal_timeline_entries")
    op.drop_index("ix_intel_stories_status", table_name="intel_stories")
    op.drop_index("ix_intel_stories_instrument", table_name="intel_stories")
    op.drop_index("ix_intel_stories_type", table_name="intel_stories")
    op.drop_index("ix_intel_stories_seq", table_name="intel_stories")
    op.drop_table("intel_stories")
