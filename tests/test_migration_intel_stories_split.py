"""The intel_signals -> intel_stories + signal_timeline_entries + daily_digests migration
(``202608301500_intel_stories_split``), driven end to end from a real pre-migration database.

Mirrors ``tests/db/test_session_messages_id_migration.py``'s shape: seed the OLD table
directly via a local, throwaway ``sa.table()`` proxy (never ``backend.db.tables`` — that
module reflects the schema AFTER this migration), upgrade one revision, read back through the
live, typed table objects. RFC §7's acceptance: one row of EACH of the 8 real shapes plus a
deliberately-malformed 9th, asserting the right row lands in the right table, timeline entry
counts match, an unresolvable-ticker row keeps its legacy id (never dropped), and the
malformed row is reported + skipped (never silently absorbed).
"""

from __future__ import annotations

import logging

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine

from backend.db.alembic_config import make_config
from tests.db.conftest import patch_all_cutover_sources_missing

_PRE_MIGRATION_HEAD = "202608281125_remove_self_harness"
_POST_MIGRATION_HEAD = "202608301500_intel_stories_split"

# LOCAL, throwaway description of `intel_signals` AS IT ACTUALLY IS immediately before this
# migration — a plain (seq, id, payload) payload table. Never imported from
# `backend.db.tables`: that module no longer defines it as of this revision.
_intel_signals_pre = sa.table(
    "intel_signals",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)
_instruments_pre = sa.table(
    "instruments",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)


def _asset_story(iid: str) -> dict:
    return {
        "id": f"news-{iid}",
        "type": "asset",
        "ticker": "AAPL",
        "label": "AAPL supply chain",
        "status": "action_needed",
        "summary": "Running summary of the AAPL story",
        "portfolio_impact": "Held position, moderate exposure",
        "action_items": ["Watch Q3 guidance"],
        "timeline": [
            {
                "id": "e1",
                "date": "2026-08-01T00:00:00Z",
                "title": "New development",
                "summary": "First development",
                "sources": [],
            },
            # No `id` — real production data has this on the majority of entries (pre-dates
            # the duplicate/contradicts/supersedes id-carrying fields); the migration mints
            # one rather than dropping the entry.
            {"date": "2026-08-02T00:00:00Z", "title": "Update", "summary": "Second development"},
        ],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
    }


def _topic_story() -> dict:
    return {
        "id": "news-topic-abc123",
        "type": "topic",
        "ticker": "",
        "label": "AI export controls",
        "status": "monitoring",
        "summary": "Tracking export-control developments",
        "portfolio_impact": "",
        "action_items": [],
        "timeline": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _risk_story() -> dict:
    return {
        "id": "risk:concentration:inst_xyz",
        "type": "risk",
        "ticker": "",
        "label": "Sector concentration",
        "status": "monitoring",
        "summary": "Tech sector concentration above target",
        "status_reason": "auto-detected on rebalance sweep",  # risk-only extra field
        "portfolio_impact": "",
        "action_items": [],
        "timeline": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _opportunity_story() -> dict:
    return {
        "id": "opp-1",
        "type": "opportunity",
        "ticker": "MSFT",
        "label": "MSFT valuation gap",
        "status": "active",
        "summary": "Valuation gap vs peers",
        "portfolio_impact": "",
        "action_items": [],
        "timeline": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _macro_story() -> dict:
    return {
        "id": "macro-1",
        "type": "macro",
        "ticker": "",
        "label": "Rate decision",
        "status": "monitoring",
        "summary": "Fed rate decision impact",
        "portfolio_impact": "",
        "action_items": [],
        "affected_holdings": ["inst_a", "inst_b"],
        "timeline": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _monitoring_story() -> dict:
    return {
        "id": "mon-1",
        "type": "monitoring",
        "ticker": "TSLA",
        "label": "TSLA watch",
        "status": "monitoring",
        "summary": "Under routine monitoring",
        "portfolio_impact": "",
        "action_items": [],
        "timeline": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _price_alert_resolvable(iid: str) -> dict:
    return {
        "id": "price-alert-googl-lte336_0",
        "type": "price_alert",
        "ticker": "GOOGL",
        "label": "GOOGL price alert",
        "status": "active",
        "summary": "GOOGL <= 336",
        "portfolio_impact": "",
        "action_items": [],
        "timeline": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _price_alert_unresolvable() -> dict:
    """A ticker not tracked as any instrument — must keep its LEGACY id, never dropped."""
    return {
        "id": "price-alert-mdb-lte355_0",
        "type": "price_alert",
        "ticker": "MDB",
        "label": "MDB price alert",
        "status": "active",
        "summary": "MDB <= 355",
        "portfolio_impact": "",
        "action_items": [],
        "timeline": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _digest_row() -> dict:
    return {
        "id": "daily-digest-2026-08-01",
        "type": "daily_digest",
        "severity": "info",
        "title": "Daily Digest",
        "summary": "Portfolio up 1.2% today.",
        "portfolio_impact": "",
        "action_items": [],
        "affected_tickers": [],
        "sources": [],
        "source_urls": [],
        "read": False,
        "trigger": "scheduled",
        "created_at": "2026-08-01T09:00:00Z",
        "period": "1M",
        "pl_base": 391.42,
        "pl_pct": 0.0019,
    }


def _malformed_row() -> dict:
    """Neither shape: no label+status (not a story), type != daily_digest (not a digest)."""
    return {"id": "mystery-row-1", "type": "something_else", "foo": "bar"}


def _typeless_story_row() -> dict:
    """Story-SHAPED (has label+status, this migration's original classification test) but no
    recognized `type` — the exact row shape that used to crash the upgrade on the NOT NULL
    `intel_stories.type` constraint before `_classify`'s fix added the `_STORY_TYPES` gate.
    Deliberately reuses a real topic story's fields minus `type`, not a wholly synthetic shape,
    so this proves the fix against a row a production book could plausibly contain (e.g. a
    pre-`type`-column-era row that never got backfilled)."""
    row = _topic_story()
    del row["type"]
    row["id"] = "mystery-typeless-story-1"
    return row


@pytest.fixture
def at_pre_migration(db_url, monkeypatch, tmp_path):
    from backend.db import engine as eng

    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    eng.dispose_engine()
    command.upgrade(make_config(db_url), _PRE_MIGRATION_HEAD)
    yield db_url
    eng.dispose_engine()


def _seed_instrument(url: str, iid: str, price_source_id: str) -> None:
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(
            _instruments_pre.insert().values(
                seq=0,
                id=iid,
                payload={
                    "id": iid,
                    "symbol": price_source_id.upper(),
                    "price_source": "yahoo",
                    "price_source_id": price_source_id,
                    "asset_class": "equity",
                    "quote_currency": "USD",
                },
            )
        )
    engine.dispose()


def _seed_signals(url: str, rows: list[dict]) -> None:
    engine = create_engine(url)
    with engine.begin() as c:
        for i, row in enumerate(rows):
            c.execute(_intel_signals_pre.insert().values(seq=i, id=row["id"], payload=row))
    engine.dispose()


def _run_migration(url: str) -> None:
    command.upgrade(make_config(url), _POST_MIGRATION_HEAD)


def test_migration_partitions_every_real_shape_into_the_right_table(at_pre_migration, caplog):
    iid = "inst_googl001xyz"
    _seed_instrument(at_pre_migration, iid, "googl")
    rows = [
        _asset_story(iid),
        _topic_story(),
        _risk_story(),
        _opportunity_story(),
        _macro_story(),
        _monitoring_story(),
        _price_alert_resolvable(iid),
        _price_alert_unresolvable(),
        _digest_row(),
        _malformed_row(),
    ]
    _seed_signals(at_pre_migration, rows)

    with caplog.at_level(logging.INFO, logger="alembic.intel_stories_split"):
        _run_migration(at_pre_migration)

    from backend.db.tables import daily_digests, intel_stories, signal_timeline_entries

    engine = create_engine(at_pre_migration)
    with engine.connect() as c:
        stories = c.execute(sa.select(intel_stories)).fetchall()
        entries = c.execute(sa.select(signal_timeline_entries)).fetchall()
        digests = c.execute(sa.select(daily_digests)).fetchall()
    engine.dispose()

    # 8 story-shaped rows land in intel_stories; the digest lands separately; the
    # malformed row lands NOWHERE (reported and skipped, never silently absorbed).
    assert len(stories) == 8
    assert len(digests) == 1
    story_ids = {s.id for s in stories}
    assert "mystery-row-1" not in story_ids
    assert digests[0].date == "2026-08-01"

    # Timeline entries: only the asset row seeded any (2 entries).
    assert len(entries) == 2
    assert {e.summary for e in entries} == {"First development", "Second development"}
    # An entry missing its own `id` in the source got one minted, not dropped.
    assert all(e.id for e in entries)

    # The malformed row was logged, not silently dropped.
    assert "mystery-row-1" in caplog.text or "1 unrecognized" in caplog.text


def test_resolvable_ticker_gets_instrument_id_and_id_rewrite(at_pre_migration):
    iid = "inst_googl001xyz"
    _seed_instrument(at_pre_migration, iid, "googl")
    _seed_signals(at_pre_migration, [_price_alert_resolvable(iid)])

    _run_migration(at_pre_migration)

    from backend.db.tables import intel_stories

    engine = create_engine(at_pre_migration)
    with engine.connect() as c:
        row = c.execute(sa.select(intel_stories)).first()
    engine.dispose()

    assert row.instrument_id == iid
    assert row.id == f"price-alert-{iid}-lte336_0"
    assert row.payload["id"] == row.id, "payload id must match the rewritten row id"


def test_unresolvable_ticker_keeps_legacy_id_and_null_instrument_id(at_pre_migration):
    """No matching instrument exists at all — MDB never resolves. Per RFC: kept under its
    LEGACY id, instrument_id stays NULL, never dropped."""
    _seed_signals(at_pre_migration, [_price_alert_unresolvable()])

    _run_migration(at_pre_migration)

    from backend.db.tables import intel_stories

    engine = create_engine(at_pre_migration)
    with engine.connect() as c:
        row = c.execute(sa.select(intel_stories)).first()
    engine.dispose()

    assert row.id == "price-alert-mdb-lte355_0"
    assert row.instrument_id is None


def test_topic_row_never_resolves_instrument_id_even_with_a_ticker(at_pre_migration):
    """RFC §2.1: NULL for topic rows — never a holding — even if a topic happens to carry
    a `ticker` cross-reference field."""
    topic = _topic_story()
    topic["ticker"] = "AAPL"  # a real, resolvable ticker — must still stay NULL
    iid = "inst_aapl001xyz"
    _seed_instrument(at_pre_migration, iid, "aapl")
    _seed_signals(at_pre_migration, [topic])

    _run_migration(at_pre_migration)

    from backend.db.tables import intel_stories

    engine = create_engine(at_pre_migration)
    with engine.connect() as c:
        row = c.execute(sa.select(intel_stories)).first()
    engine.dispose()

    assert row.type == "topic"
    assert row.instrument_id is None
    assert row.id == "news-topic-abc123", "topic ids are never ticker-keyed, never rewritten"


def test_summary_renamed_to_current_summary(at_pre_migration):
    _seed_signals(at_pre_migration, [_topic_story()])
    _run_migration(at_pre_migration)

    from backend.db.tables import intel_stories

    engine = create_engine(at_pre_migration)
    with engine.connect() as c:
        row = c.execute(sa.select(intel_stories)).first()
    engine.dispose()

    assert row.current_summary == "Tracking export-control developments"
    assert "summary" not in row.payload
    assert "timeline" not in row.payload


def test_digest_row_id_becomes_the_date_pk_and_summary_becomes_message(at_pre_migration):
    _seed_signals(at_pre_migration, [_digest_row()])
    _run_migration(at_pre_migration)

    from backend.db.tables import daily_digests

    engine = create_engine(at_pre_migration)
    with engine.connect() as c:
        row = c.execute(sa.select(daily_digests)).first()
    engine.dispose()

    assert row.date == "2026-08-01"
    assert row.message == "Portfolio up 1.2% today."
    assert row.trigger == "scheduled"  # real-data finding — see the revision's own docstring


def test_unrecognized_shape_is_reported_and_skipped_not_dropped_silently(at_pre_migration, caplog):
    """RFC §3.1 step 2: a row shaped like neither a story nor a digest is the "silent
    partial migration" case to refuse loudly on — reported (logged) and skipped, landing
    in NEITHER new table, never silently absorbed into either."""
    _seed_signals(at_pre_migration, [_topic_story(), _malformed_row()])

    with caplog.at_level(logging.INFO, logger="alembic.intel_stories_split"):
        _run_migration(at_pre_migration)

    from backend.db.tables import daily_digests, intel_stories

    engine = create_engine(at_pre_migration)
    with engine.connect() as c:
        stories = c.execute(sa.select(intel_stories)).fetchall()
        digests = c.execute(sa.select(daily_digests)).fetchall()
    engine.dispose()

    assert len(stories) == 1  # only the topic row
    assert len(digests) == 0
    assert "mystery-row-1" in caplog.text


def test_typeless_story_shaped_row_is_reported_and_skipped_not_crashed_on(at_pre_migration, caplog):
    """Regression test for a migration-robustness fix found while fixing
    tests/db/test_configs_cutover.py's collection-blocking ImportError: `_classify` originally
    checked only `"label" in row and "status" in row`, so a story-SHAPED-but-typeless row (see
    `_typeless_story_row`'s own docstring) passed classification and then crashed the upgrade
    on `intel_stories.type`'s NOT NULL constraint — turning one bad legacy row into a
    fail-closed migration for the WHOLE book. The fix added `and row.get("type") in
    _STORY_TYPES` to the same gate, so this row is now reported+skipped (this test's own
    proof) exactly like `_malformed_row` above, rather than aborting the upgrade."""
    _seed_signals(at_pre_migration, [_topic_story(), _typeless_story_row()])

    with caplog.at_level(logging.INFO, logger="alembic.intel_stories_split"):
        _run_migration(at_pre_migration)

    from backend.db.tables import daily_digests, intel_stories

    engine = create_engine(at_pre_migration)
    with engine.connect() as c:
        stories = c.execute(sa.select(intel_stories)).fetchall()
        digests = c.execute(sa.select(daily_digests)).fetchall()
    engine.dispose()

    assert len(stories) == 1  # only the real topic row — the typeless row never lands
    assert len(digests) == 0
    assert "mystery-typeless-story-1" in caplog.text
