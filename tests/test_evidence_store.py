"""EvidenceStore — CRUD + the dedup-merge / freshness / GC mechanics (no network)."""

from __future__ import annotations

import pytest

from backend.db.evidence_store import EvidenceStore


@pytest.fixture
def store(db_isolated):
    return EvidenceStore()


def _row(rid, **kw):
    base = {
        "id": rid,
        "url": f"https://x/{rid}",
        "domain": "x",
        "title": "t",
        "snippet": "s",
        "published_at": "",
        "image": None,
        "provider": "p",
        "kind": "news",
        "subjects": ["a"],
        "queries": ["q1"],
        "first_seen": "2026-06-17T00:00:00+00:00",
        "last_seen": "2026-06-17T00:00:00+00:00",
        "seen_count": 1,
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    base.update(kw)
    return base


def test_merge_inserts_then_finds(store):
    store.merge_hits([_row("ev_1")])
    assert store.find_by_id("ev_1")["title"] == "t"
    assert len(store.all()) == 1


def test_merge_dedup_unions_and_preserves_first_seen(store):
    store.merge_hits(
        [_row("ev_1", subjects=["a"], queries=["q1"], first_seen="T0", last_seen="T0")]
    )
    store.merge_hits(
        [_row("ev_1", subjects=["b"], queries=["q2"], first_seen="T9", last_seen="T9")]
    )
    rows = store.all()
    assert len(rows) == 1  # same id → one row, not two
    r = rows[0]
    assert set(r["subjects"]) == {"a", "b"}  # subjects unioned
    assert set(r["queries"]) == {"q1", "q2"}  # queries unioned
    assert r["first_seen"] == "T0"  # earliest preserved (the "yesterday stays" anchor)
    assert r["last_seen"] == "T9"  # bumped to newest
    assert r["seen_count"] == 2  # corroboration count


def test_merge_backfills_source_url_onto_preexisting_row(store):
    # A row ingested before we captured the publisher homepage has no source_url. A later poll that
    # DOES carry one must backfill it, so the favicon/domain fix reaches existing Google-News rows.
    store.merge_hits([_row("ev_1")])  # no source_url key (pre-fix shape)
    assert store.find_by_id("ev_1").get("source_url", "") == ""
    store.merge_hits([_row("ev_1", source_url="https://www.benzinga.com")])
    assert store.find_by_id("ev_1")["source_url"] == "https://www.benzinga.com"


def test_for_subjects_fresh_only_gate(store):
    now = "2026-06-17T12:00:00+00:00"
    store.merge_hits(
        [
            _row("fresh", subjects=["a"], expires_at="2026-06-17T18:00:00+00:00"),
            _row("stale", subjects=["a"], expires_at="2026-06-17T06:00:00+00:00"),
        ]
    )
    assert {r["id"] for r in store.for_subjects(["a"], fresh_only=True, now=now)} == {"fresh"}
    assert len(store.for_subjects(["a"], fresh_only=False, now=now)) == 2
    assert store.for_subjects(["other"], now=now) == []  # subject not present


def test_prune_drops_expired_keeps_reference_domains(store):
    now = "2026-06-17T12:00:00+00:00"
    store.merge_hits(
        [
            _row("old", domain="cnbc.com", expires_at="2026-06-17T06:00:00+00:00"),
            _row("ref", domain="wikipedia.org", expires_at="2026-06-17T06:00:00+00:00"),
            _row("fresh", expires_at="2099-01-01T00:00:00+00:00"),
        ]
    )
    removed = store.prune(now, keep_domains=frozenset({"wikipedia.org"}))
    assert removed == 1
    assert {r["id"] for r in store.all()} == {"ref", "fresh"}  # reference domain exempt


def test_set_image_records_the_og_image_without_counting_a_scan(store):
    """The brief's full-text pass harvests og:image (owner ask 2026-09-22, Perplexity-style
    story cards) and writes it back — payload only: seen_count/last_seen are a scan's, and a
    full-text fetch is not one."""
    store.merge_hits([_row("a")])
    assert store.set_image("a", "https://cdn.x/hero.jpg") is True
    row = store.find_by_id("a")
    assert row["image"] == "https://cdn.x/hero.jpg"
    assert row["seen_count"] == 1
    assert row["last_seen"] == "2026-06-17T00:00:00+00:00"
    assert store.set_image("missing", "https://cdn.x/hero.jpg") is False
