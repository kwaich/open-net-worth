"""Evidence service — canonical key, record/union, freshness, TTL (no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.evidence_store import EvidenceStore
from backend.services import evidence as ev


@pytest.fixture
def store(db_isolated):
    return EvidenceStore()


def test_canonical_key_strips_tracking_www_case_fragment():
    a = ev.canonical_key("https://WWW.Example.com/Article?utm_source=x&id=5#frag")
    b = ev.canonical_key("https://example.com/Article?id=5")
    assert a == b  # tracking param + www + casing + fragment all normalised away
    assert ev.canonical_key("https://example.com/other") != a  # genuinely different URL


def test_ttl_by_kind():
    # Scan-time TTL applies to opportunity/macro/filing only. news+asset use PUBLISH retention
    # (ttl_hours is never called for them) — see test_news_retention_keyed_to_publish_date_not_scan_time.
    assert ev.ttl_hours("opportunity") == 12
    assert ev.ttl_hours("macro") == 48
    assert ev.ttl_hours("unknown") == 24  # default


def test_record_evidence_builds_row_and_unions_same_url_queries(store):
    now = "2026-06-17T00:00:00+00:00"
    hits = [
        {
            "url": "https://e.com/a?utm_source=z",
            "title": "A",
            "content": "body " * 200,  # long → must be truncated to <=500
            "published_date": "1 hour ago",
            "_query": "q1",
        },
        {"url": "https://www.e.com/a", "title": "A", "content": "x", "_query": "q2"},  # same canon
    ]
    rows = ev.record_evidence(hits, subjects=["asset:1"], kind="news", store=store, now=now)
    assert len(rows) == 1  # two hits, same canonical URL → one row
    r = rows[0]
    assert set(r["queries"]) == {"q1", "q2"}  # both surfacing queries kept
    assert len(r["snippet"]) <= 500
    assert r["subjects"] == ["asset:1"]
    assert r["expires_at"] > r["first_seen"]  # TTL applied


def test_record_evidence_drops_urlless_hits(store):
    assert ev.record_evidence([{"title": "no url"}], subjects=["a"], store=store) == []


def test_read_evidence_sorts_newest_first(store):
    now = "2026-06-17T12:00:00+00:00"
    ev.record_evidence(
        [{"url": "https://e/old", "title": "old", "published_date": "5 hours ago", "_query": "q"}],
        subjects=["a"],
        kind="news",
        store=store,
        now=now,
    )
    ev.record_evidence(
        [{"url": "https://e/new", "title": "new", "published_date": "just now", "_query": "q"}],
        subjects=["a"],
        kind="news",
        store=store,
        now=now,
    )
    rows = ev.read_evidence(subjects=["a"], store=store, now=now)
    assert [r["title"] for r in rows] == ["new", "old"]  # newest article first


def test_read_evidence_since_watermark_filters_to_newer_first_seen(store):
    """INT14b: `since` (typically a prior Intel signal's `updated_at`) drops any row whose
    `first_seen` is not strictly newer — "everything that arrived since we last looked"."""
    now = "2026-06-17T12:00:00+00:00"
    ev.record_evidence(
        [
            {
                "url": "https://e/before",
                "title": "before",
                "published_date": "5 hours ago",
                "_query": "q",
            }
        ],
        subjects=["a"],
        kind="news",
        store=store,
        now="2026-06-17T08:00:00+00:00",  # first_seen stamped at record time
    )
    ev.record_evidence(
        [{"url": "https://e/after", "title": "after", "published_date": "just now", "_query": "q"}],
        subjects=["a"],
        kind="news",
        store=store,
        now="2026-06-17T11:00:00+00:00",
    )
    watermark = "2026-06-17T10:00:00+00:00"
    rows = ev.read_evidence(subjects=["a"], store=store, now=now, since=watermark)
    assert [r["title"] for r in rows] == ["after"]  # the "before" row predates the watermark


def test_read_evidence_no_since_is_byte_identical_to_pre_watermark_behavior(store):
    """`since=None` (the default) never filters — every existing caller's behavior is unchanged."""
    now = "2026-06-17T12:00:00+00:00"
    ev.record_evidence(
        [{"url": "https://e/old", "title": "old", "published_date": "5 hours ago", "_query": "q"}],
        subjects=["a"],
        kind="news",
        store=store,
        now=now,
    )
    rows_default = ev.read_evidence(subjects=["a"], store=store, now=now)
    rows_explicit_none = ev.read_evidence(subjects=["a"], store=store, now=now, since=None)
    assert len(rows_default) == 1
    assert rows_default == rows_explicit_none


def test_read_evidence_excludes_ttl_expired(store):
    # opportunity TTL = 12h; record at T, read 24h later → expired.
    ev.record_evidence(
        [{"url": "https://e/x", "title": "x", "_query": "q"}],
        subjects=["a"],
        kind="opportunity",
        store=store,
        now="2026-06-16T00:00:00+00:00",
    )
    later = "2026-06-17T00:00:00+00:00"
    assert ev.read_evidence(subjects=["a"], store=store, now=later) == []  # TTL-expired hidden
    assert len(ev.read_evidence(subjects=["a"], fresh_only=False, store=store, now=later)) == 1


def test_prune_evidence_drops_expired(store):
    ev.record_evidence(
        [{"url": "https://e/x", "title": "x", "_query": "q"}],
        subjects=["a"],
        kind="opportunity",
        store=store,
        now="2026-06-16T00:00:00+00:00",
    )
    removed = ev.prune_evidence(now="2026-06-17T00:00:00+00:00", store=store)
    assert removed == 1 and store.all() == []


# ---------------------------------------------------------------------------
# Publish-date retention tests (news/asset keyed to article publish date)
# ---------------------------------------------------------------------------


def test_parse_published_iso_with_z():
    dt = ev._parse_published("2026-06-19T10:00:00Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026 and dt.month == 6 and dt.day == 19 and dt.hour == 10


def test_parse_published_iso_with_offset():
    dt = ev._parse_published("2026-06-19T10:00:00+00:00")
    assert dt is not None and dt.tzinfo is not None


def test_parse_published_rfc2822():
    dt = ev._parse_published("Wed, 27 May 2026 09:00:00 GMT")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 5 and dt.day == 27


def test_parse_published_garbage_returns_none():
    assert ev._parse_published("not a date at all") is None
    assert ev._parse_published("") is None
    assert ev._parse_published(None) is None
    assert ev._parse_published("Wed, 27 Ma…") is None  # truncated / garbage


def test_parse_published_far_future_returns_none():
    # A date > now + 2 days is implausible bad data
    far = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert ev._parse_published(far) is None


def test_news_retention_keyed_to_publish_date_not_scan_time(store):
    """CORE FIX: a news article published 2 days ago, recorded once, must still be fresh
    48 h after recording WITHOUT any re-record. Old behaviour (now+24h) would have dropped it."""
    # "now" for the recording scan is T0
    t0 = datetime(2026, 6, 17, 0, 0, 0, tzinfo=timezone.utc)
    published_iso = (t0 - timedelta(days=2)).isoformat()  # published 2 days before the scan

    ev.record_evidence(
        [{"url": "https://e/article", "title": "Fresh article", "published_date": published_iso}],
        subjects=["a"],
        kind="news",
        store=store,
        now=t0.isoformat(),
    )

    # Read 48 h later WITHOUT re-recording (no new scan has run)
    t_later = (t0 + timedelta(hours=48)).isoformat()
    rows = ev.read_evidence(subjects=["a"], store=store, now=t_later)
    assert len(rows) == 1, "article should still be fresh 48h after scan (no re-record needed)"

    # Sanity-check: expires_at is ~publish+90d, NOT ~t0+24h
    r = rows[0]
    expires = datetime.fromisoformat(r["expires_at"])
    publish_dt = datetime.fromisoformat(published_iso)
    if publish_dt.tzinfo is None:
        publish_dt = publish_dt.replace(tzinfo=timezone.utc)
    assert expires > publish_dt + timedelta(days=89)  # ~90d from publish, not 24h from scan


def test_news_old_article_91_days_not_retained(store):
    """An article published 91 days ago has already expired at record time — not cached."""
    now_dt = datetime(2026, 6, 19, 0, 0, 0, tzinfo=timezone.utc)
    published_91d_ago = (now_dt - timedelta(days=91)).isoformat()

    ev.record_evidence(
        [
            {
                "url": "https://e/old-article",
                "title": "Stale article",
                "published_date": published_91d_ago,
            }
        ],
        subjects=["a"],
        kind="news",
        store=store,
        now=now_dt.isoformat(),
    )

    rows = ev.read_evidence(subjects=["a"], store=store, now=now_dt.isoformat(), fresh_only=True)
    assert rows == [], "91-day-old article should already be expired (publish+90d < now)"


def test_already_expired_item_is_skipped_at_ingest_not_merely_filtered_at_read(store):
    """Data-hygiene fix: an item already past its own expires_at at record time must never be
    WRITTEN at all — not just hidden by read_evidence's fresh_only gate. Pre-fix, a scan
    could churn ~1.5K such rows every cycle (real Intel-scan cadence, owner's box) that were
    dead on arrival: written, then immediately eligible for the next prune, never read fresh
    even once."""
    now_dt = datetime(2026, 6, 19, 0, 0, 0, tzinfo=timezone.utc)
    published_91d_ago = (now_dt - timedelta(days=91)).isoformat()

    rows = ev.record_evidence(
        [
            {
                "url": "https://e/old-article",
                "title": "Stale article",
                "published_date": published_91d_ago,
            }
        ],
        subjects=["a"],
        kind="news",
        store=store,
        now=now_dt.isoformat(),
    )

    assert rows == []  # nothing merged/returned
    assert store.all() == []  # and nothing landed in the store either — no prune needed


def test_a_fresh_item_alongside_an_already_expired_one_is_still_recorded(store):
    """The skip is per-item, not a whole-batch bail — a same-call mix of a dead-on-arrival
    hit and a genuinely fresh one must still record the fresh one."""
    now_dt = datetime(2026, 6, 19, 0, 0, 0, tzinfo=timezone.utc)
    published_91d_ago = (now_dt - timedelta(days=91)).isoformat()

    rows = ev.record_evidence(
        [
            {"url": "https://e/old", "title": "Stale", "published_date": published_91d_ago},
            {"url": "https://e/new", "title": "Fresh", "published_date": now_dt.isoformat()},
        ],
        subjects=["a"],
        kind="news",
        store=store,
        now=now_dt.isoformat(),
    )

    assert [r["url"] for r in rows] == ["https://e/new"]
    assert len(store.all()) == 1


def test_news_undated_row_falls_back_to_first_seen_plus_90d(store):
    """When published_at is absent or garbage the row must not crash; it retains ~90d from first_seen."""
    now_dt = datetime(2026, 6, 17, 0, 0, 0, tzinfo=timezone.utc)

    for bad_date, url in [
        (None, "https://e/no-date"),
        ("Wed, 27 Ma…", "https://e/garbage-date"),  # truncated garbage
    ]:
        hit: dict = {"url": url, "title": f"Undated {url}"}
        if bad_date is not None:
            hit["published_date"] = bad_date
        ev.record_evidence([hit], subjects=["a"], kind="news", store=store, now=now_dt.isoformat())

    rows = ev.read_evidence(subjects=["a"], store=store, now=now_dt.isoformat())
    assert len(rows) == 2  # both recorded without crashing

    # Each expires ~90d from now (first_seen fallback)
    for r in rows:
        expires = datetime.fromisoformat(r["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        assert expires > now_dt + timedelta(days=89)


def test_opportunity_macro_retention_unchanged(store):
    """opportunity (12h) and macro (48h) must still use scan-time TTL, not publish date."""
    t0 = datetime(2026, 6, 17, 0, 0, 0, tzinfo=timezone.utc)
    yesterday = (t0 - timedelta(days=1)).isoformat()

    ev.record_evidence(
        [{"url": "https://e/opp", "title": "opp", "published_date": yesterday}],
        subjects=["a"],
        kind="opportunity",
        store=store,
        now=t0.isoformat(),
    )
    ev.record_evidence(
        [{"url": "https://e/mac", "title": "mac", "published_date": yesterday}],
        subjects=["a"],
        kind="macro",
        store=store,
        now=t0.isoformat(),
    )

    # 13h later: opportunity (12h) is expired, macro (48h) is fresh
    t_13h = (t0 + timedelta(hours=13)).isoformat()
    rows = ev.read_evidence(subjects=["a"], store=store, now=t_13h)
    titles = {r["title"] for r in rows}
    assert "opp" not in titles, "opportunity should expire after 12h (scan-time TTL unchanged)"
    assert "mac" in titles, "macro should still be fresh at 13h"

    # 50h later: both expired
    t_50h = (t0 + timedelta(hours=50)).isoformat()
    assert ev.read_evidence(subjects=["a"], store=store, now=t_50h) == []


def test_dedup_merge_preserves_min_first_seen_and_stable_publish_expires(store):
    """Re-recording the same article yields the same publish-based expires (idempotent under merge)
    and preserves the earliest first_seen."""
    published = "2026-06-15T08:00:00+00:00"
    t0 = "2026-06-17T00:00:00+00:00"
    t1 = "2026-06-18T00:00:00+00:00"  # second scan, one day later

    ev.record_evidence(
        [{"url": "https://e/a", "title": "A", "published_date": published, "_query": "q1"}],
        subjects=["s"],
        kind="news",
        store=store,
        now=t0,
    )
    ev.record_evidence(
        [{"url": "https://e/a", "title": "A updated", "published_date": published, "_query": "q2"}],
        subjects=["s"],
        kind="news",
        store=store,
        now=t1,
    )

    rows = store.all()
    assert len(rows) == 1
    r = rows[0]
    assert r["first_seen"] == t0  # earliest preserved
    assert r["seen_count"] == 2
    # Both scans produce the same expires_at (published+90d), so max(e1, e2) == either
    expected_expires = (datetime.fromisoformat(published) + timedelta(days=90)).isoformat()
    assert r["expires_at"] == expected_expires
