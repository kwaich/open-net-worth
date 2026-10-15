"""ClaimsStore — DB-CRUD round trip for the cross-subject push-dedup registry (owner override
of RFC intel-signals-data-model §4's "NO-GO"; see
docs/adr/2026-08-30-claims-registry-cross-subject-push-dedup.md).
"""

from __future__ import annotations

from backend.db.claims_store import ClaimsStore


def _push(store: ClaimsStore, group_key: str = "news-fact:inst_abc", **overrides) -> dict:
    kwargs = {
        "story_id": "story_1",
        "statement": "Company announces a formal tender offer.",
        "sources": [{"url": "https://example.com/a", "title": "x", "domain": "example.com"}],
        "materiality": "new",
        "decision_relevant": True,
        "confirmed_at": None,
        "now": "2026-08-30T20:00:00+00:00",
    }
    kwargs.update(overrides)
    return store.record_push(group_key, **kwargs)


def test_record_push_creates_a_new_claim(db_engine):
    store = ClaimsStore(engine=db_engine)
    claim = _push(store)
    assert claim["group_key"] == "news-fact:inst_abc"
    assert claim["status"] == "active"
    assert claim["story_ids"] == ["story_1"]
    assert claim["pushed_at"] == "2026-08-30T20:00:00+00:00"
    stored = store.find_by_id(claim["id"])
    assert stored["statement"] == "Company announces a formal tender offer."


def test_find_active_by_group_key_returns_none_when_absent(db_engine):
    store = ClaimsStore(engine=db_engine)
    assert store.find_active_by_group_key("news-fact:nope") is None


def test_record_push_second_time_updates_same_claim_not_a_second_row(db_engine):
    store = ClaimsStore(engine=db_engine)
    first = _push(store, statement="Tender offer announced.", now="2026-08-30T10:00:00+00:00")
    second = _push(
        store,
        story_id="story_2",
        statement="Tender offer confirmed by the target's board.",
        now="2026-08-30T20:00:00+00:00",
    )
    assert second["id"] == first["id"]  # same claim, not a new row
    assert second["story_ids"] == ["story_1", "story_2"]
    assert second["statement"] == "Tender offer confirmed by the target's board."
    assert second["pushed_at"] == "2026-08-30T20:00:00+00:00"
    assert len(store.all()) == 1


def test_record_push_repeat_story_id_not_duplicated_in_story_ids(db_engine):
    store = ClaimsStore(engine=db_engine)
    _push(store, story_id="story_1", now="2026-08-30T10:00:00+00:00")
    claim = _push(store, story_id="story_1", now="2026-08-30T11:00:00+00:00")
    assert claim["story_ids"] == ["story_1"]


def test_record_push_confirmed_at_only_advances(db_engine):
    store = ClaimsStore(engine=db_engine)
    _push(store, confirmed_at="2026-08-29T00:00:00+00:00", now="2026-08-30T10:00:00+00:00")
    # A later push with no confirmed evidence of its own must not erase the earlier one.
    claim = _push(store, confirmed_at=None, now="2026-08-30T11:00:00+00:00")
    assert claim["confirmed_at"] == "2026-08-29T00:00:00+00:00"


def test_record_push_different_group_keys_are_independent_rows(db_engine):
    store = ClaimsStore(engine=db_engine)
    _push(store, group_key="news-fact:inst_a")
    _push(store, group_key="news-fact:inst_b")
    assert len(store.all()) == 2


def test_all_returns_insertion_order(db_engine):
    store = ClaimsStore(engine=db_engine)
    a = _push(store, group_key="news-fact:inst_a")
    b = _push(store, group_key="news-fact:inst_b")
    assert [c["id"] for c in store.all()] == [a["id"], b["id"]]
