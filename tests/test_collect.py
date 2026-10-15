"""gather_evidence collector — accumulation across runs (THE bug fix) + error/cache behaviour."""

from __future__ import annotations

import asyncio

import pytest

from backend.db.evidence_store import EvidenceStore
from backend.monitoring import collect
from backend.services import evidence as ev


@pytest.fixture
def store(db_isolated):
    return EvidenceStore()


class _FakeProvider:
    def __init__(self, by_query=None):
        self.by_query = by_query or {}
        self.calls: list[str] = []

    async def search(self, q, max_results=5):
        self.calls.append(q)
        return [dict(h) for h in self.by_query.get(q, [])]


def test_gather_records_and_returns_provider_shape(store, monkeypatch):
    fake = _FakeProvider(
        {"q1": [{"url": "https://e/a", "title": "A", "content": "b", "published_date": "just now"}]}
    )
    monkeypatch.setattr(collect, "get_search_provider", lambda: fake)
    out = asyncio.run(collect.gather_evidence(["q1"], ["scan:intel"], kind="news", store=store))
    assert any(r["url"] == "https://e/a" for r in out)
    r = out[0]
    assert r["content"] == "b" and r["published_date"] == "just now" and r["_query"] == "q1"
    assert store.for_subjects(["scan:intel"], now="")  # persisted to the cache


def test_gather_empty_search_still_returns_cached(store, monkeypatch):
    # THE BUG: an item found in an earlier scan must keep feeding analysis even when today's
    # live search returns nothing — instead of disappearing.
    ev.record_evidence(
        [
            {
                "url": "https://e/yesterday",
                "title": "Still valid",
                "content": "context",
                "published_date": "yesterday",
                "_query": "q1",
            }
        ],
        subjects=["scan:intel"],
        kind="news",
        store=store,
    )
    monkeypatch.setattr(collect, "get_search_provider", lambda: _FakeProvider({}))  # live → nothing
    out = asyncio.run(collect.gather_evidence(["q1"], ["scan:intel"], kind="news", store=store))
    assert any(r["url"] == "https://e/yesterday" for r in out)  # survived the empty live search


def test_gather_swallows_provider_error(store, monkeypatch):
    class _Boom:
        async def search(self, q, max_results=5):
            raise RuntimeError("network down")

    monkeypatch.setattr(collect, "get_search_provider", lambda: _Boom())
    out = asyncio.run(collect.gather_evidence(["q1"], ["scan:intel"], store=store))
    assert out == []  # error swallowed, no cache → empty, no raise


def test_prefer_cache_skips_live_search_when_covered(store, monkeypatch):
    ev.record_evidence(
        [{"url": "https://e/c", "title": "C", "content": "x", "_query": "q1"}],
        subjects=["scan:intel"],
        kind="news",
        store=store,
    )
    fake = _FakeProvider({"q1": [{"url": "https://e/new", "title": "N", "content": "y"}]})
    monkeypatch.setattr(collect, "get_search_provider", lambda: fake)
    out = asyncio.run(
        collect.gather_evidence(["q1"], ["scan:intel"], prefer_cache=True, store=store)
    )
    assert fake.calls == []  # fresh cache covered → no live search
    assert any(r["url"] == "https://e/c" for r in out)


# ── gather_rss (RSS collector) ────────────────────────────────────────────────────


def test_gather_rss_records_with_provenance_and_returns_union(store, monkeypatch):
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None: [
            {
                "url": "https://e/a",
                "title": "A",
                "content": "b",
                "published_date": "just now",
                "source": "Reuters",
                "guid": "g1",
            }
        ],
    )
    feeds_in = [
        {
            "url": "u1",
            "subjects": ["asset:i1", "scan:intel"],
            "kind": "news",
            "via": "rss:google_news",
        }
    ]
    out = asyncio.run(collect.gather_rss(feeds_in, store=store))
    assert any(r["url"] == "https://e/a" for r in out)
    rows = store.for_subjects(["asset:i1"], now="")
    assert rows, "recorded under the per-asset subject"
    r = rows[0]
    # the "store everything useful" fields landed
    assert r["source"] == "Reuters" and r["via"] == "rss:google_news" and r["guid"] == "g1"


def test_gather_rss_swallows_a_failing_feed(store, monkeypatch):
    def _boom(url, max_age=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(collect, "_fetch_feed", _boom)
    out = asyncio.run(
        collect.gather_rss([{"url": "u", "subjects": ["scan:intel"], "via": "rss"}], store=store)
    )
    assert out == []  # no raise, nothing recorded


def test_gather_rss_empty_feeds_noop(store):
    assert asyncio.run(collect.gather_rss([], store=store)) == []


def test_gather_rss_one_store_write_for_same_subject_feeds(store, monkeypatch):
    # PERF regression guard (2026-07-06 Plan-tab stall investigation): gather_rss used to call
    # record_evidence once PER FEED — each a full read-modify-write of the whole evidence file
    # (55 MB on a real book), all on the event loop. Feeds sharing (subjects, kind) — the shape
    # every per-holding poll produces — must fold into ONE merge_hits write.
    calls: list[int] = []
    real_merge = store.merge_hits

    def _counting_merge(records):
        calls.append(len(records))
        return real_merge(records)

    monkeypatch.setattr(store, "merge_hits", _counting_merge)
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None: [
            {"url": f"https://e/{url}", "title": url, "content": "c", "published_date": "just now"}
        ],
    )
    feeds_in = [
        {"url": f"u{i}", "subjects": ["asset:i1"], "kind": "news", "via": "rss"} for i in range(3)
    ]
    out = asyncio.run(collect.gather_rss(feeds_in, store=store))
    assert len(calls) == 1, f"expected ONE batched store write, got {len(calls)}"
    assert calls[0] == 3  # all three feeds' hits landed in that single write
    assert {r["url"] for r in out} == {"https://e/u0", "https://e/u1", "https://e/u2"}


def test_gather_rss_distinct_subjects_all_recorded(store, monkeypatch):
    # Batching is grouped by (subjects, kind) — feeds with DIFFERENT subjects must still each
    # land under their own subject tag.
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None: [
            {"url": f"https://e/{url}", "title": url, "content": "c", "published_date": "just now"}
        ],
    )
    feeds_in = [
        {"url": "ua", "subjects": ["asset:a"], "kind": "news", "via": "rss"},
        {"url": "ub", "subjects": ["asset:b"], "kind": "news", "via": "rss"},
    ]
    asyncio.run(collect.gather_rss(feeds_in, store=store))
    assert store.for_subjects(["asset:a"], now="")
    assert store.for_subjects(["asset:b"], now="")
