"""NotificationLogStore on Core (renamed 2026-08-20 from SignalsStore, revision 0044) —
DB-CRUD round trip, the capped-prepend newest-first ring (the exact bug class Wave C's logs
cutover fixed for the 4 prepend-order log stores), and checkpoint exclusion (a /rewind must
not resurrect or erase a notification).
"""

from __future__ import annotations

from backend.db.notification_log_store import MAX_NOTIFICATION_LOG_ROWS, NotificationLogStore


def test_append_and_query_round_trip(db_engine):
    store = NotificationLogStore(engine=db_engine)
    row = store.append({"title": "AAPL down 5%", "type": "price", "affected_tickers": ["AAPL"]})
    assert row["id"].startswith("sig_")
    assert row["read"] is False
    got = store.query_signals(limit=10)
    assert len(got) == 1 and got[0]["id"] == row["id"]


def test_mark_read_and_unread_count(db_engine):
    store = NotificationLogStore(engine=db_engine)
    a = store.append({"title": "a", "type": "news"})
    store.append({"title": "b", "type": "news"})
    assert store.unread_count() == 2
    store.mark_read(a["id"])
    assert store.unread_count() == 1
    assert store.query_signals(unread_only=True, limit=10)[0]["title"] == "b"


def test_mark_all_read(db_engine):
    store = NotificationLogStore(engine=db_engine)
    store.append({"title": "a", "type": "news"})
    store.append({"title": "b", "type": "news"})
    store.mark_all_read()
    assert store.unread_count() == 0


def test_filter_by_type_and_ticker(db_engine):
    store = NotificationLogStore(engine=db_engine)
    store.append({"title": "price move", "type": "price", "affected_tickers": ["AAPL"]})
    store.append({"title": "headline", "type": "news", "affected_tickers": ["MSFT"]})
    assert len(store.query_signals(type="price", limit=10)) == 1
    assert store.query_signals(ticker="aapl", limit=10)[0]["title"] == "price move"
    assert store.count_signals(type="news") == 1


def test_clear_drops_everything(db_engine):
    store = NotificationLogStore(engine=db_engine)
    store.append({"title": "a", "type": "news"})
    store.clear()
    assert store.all() == []


# ── Security-critical: capped prepend, newest-first (the brief's own named test) ───────────


def test_signals_capped_prepend_newest_first(db_engine):
    s = NotificationLogStore(engine=db_engine)
    for i in range(MAX_NOTIFICATION_LOG_ROWS + 5):
        s.append({"title": f"n{i}", "type": "news"})
    got = s.query_signals(limit=1)
    assert (
        len(s.query_signals(limit=1000)) == MAX_NOTIFICATION_LOG_ROWS
        and got[0]["title"] == f"n{MAX_NOTIFICATION_LOG_ROWS + 4}"
    )


def test_cap_drops_the_oldest_not_the_newest(db_engine):
    """Complements the brief's own test: the SURVIVING rows after the cap trips are exactly
    the newest MAX_NOTIFICATION_LOG_ROWS titles — n5..n{MAX_NOTIFICATION_LOG_ROWS+4} — not
    some other MAX_NOTIFICATION_LOG_ROWS-sized slice, and they still read back newest-first."""
    s = NotificationLogStore(engine=db_engine)
    for i in range(MAX_NOTIFICATION_LOG_ROWS + 5):
        s.append({"title": f"n{i}", "type": "news"})
    titles = [row["title"] for row in s.query_signals(limit=1000)]
    expected_newest_first = [f"n{i}" for i in range(MAX_NOTIFICATION_LOG_ROWS + 4, 4, -1)]
    assert titles == expected_newest_first


# ── Security-critical: checkpoint exclusion ─────────────────────────────────────────────────


def test_signals_writes_produce_no_checkpoint(db_isolated):
    """A /rewind must not resurrect a dismissed notification or erase a fresh one — proven
    end to end: a turn that only appends a notification mints NO checkpoint at all."""
    from backend.ai import checkpoint as cp

    store = NotificationLogStore()
    ctx, token = cp.begin_turn("s1", 0, "monitoring poll")
    try:
        store.append({"title": "poll result", "type": "news"})
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is None
    assert len(store.all()) == 1
