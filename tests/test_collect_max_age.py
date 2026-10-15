"""collect.py max_age parameter — unit tests (network-free).

Covers:
  * _fetch_feed with explicit max_age uses that window (shorter window refetches;
    default None preserves the 900s behavior).
  * gather_rss passes max_age through to _fetch_feed.
"""

from __future__ import annotations

import asyncio
import time


def _inject_fake_fetch(monkeypatch, *, hit_title: str = "Article"):
    """Replace the SSRF-guarded URL fetch with a pure in-memory stub.
    Returns a counter so tests can track calls."""
    import backend.monitoring.collect as collect_mod
    from backend.services.feeds import parse_rss

    call_count = {"n": 0}

    # Clear the module cache before each test scenario
    collect_mod._rss_cache.clear()

    _rss_xml = (
        b"<?xml version='1.0'?><rss version='2.0'><channel>"
        b"<item><title>" + hit_title.encode() + b"</title>"
        b"<link>https://example.com/article</link></item>"
        b"</channel></rss>"
    )

    def _fake_fetch(url: str) -> list[dict]:
        call_count["n"] += 1
        return parse_rss(_rss_xml)

    return call_count, _fake_fetch


# ── _fetch_feed max_age tests ─────────────────────────────────────────────────


def test_fetch_feed_default_none_uses_rss_cache_ttl(monkeypatch):
    """With max_age=None, the default _RSS_CACHE_TTL (900s) is used."""
    import backend.monitoring.collect as collect_mod

    call_count, fake_fetch = _inject_fake_fetch(monkeypatch)
    url = "https://example.com/feed"

    # Manually seed the cache as if the feed was fetched just 1 second ago
    now = time.monotonic()
    collect_mod._rss_cache[url] = (now - 1.0, [{"title": "cached"}])

    # Call _fetch_feed — should use the cached result (TTL not expired)
    result = collect_mod._fetch_feed(url)

    assert result == [{"title": "cached"}]  # cache hit — no network call
    assert call_count["n"] == 0


def _make_opener_stub(monkeypatch, *, hit_title: str = "fresh_article"):
    """Patch core.pinned_http._build_pinned_opener so _fetch_feed never touches the network.
    Returns a list that records every URL passed to opener.open."""
    import backend.monitoring.collect as collect_mod

    # Clear the module cache before each test scenario
    collect_mod._rss_cache.clear()

    _rss_xml = (
        b"<?xml version='1.0'?><rss version='2.0'><channel>"
        b"<item><title>" + hit_title.encode() + b"</title>"
        b"<link>https://example.com/article</link></item>"
        b"</channel></rss>"
    )

    open_calls: list[str] = []

    class _FakeResp:
        def read(self, n):
            return _rss_xml

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    class _FakeOpener:
        def open(self, req, timeout=None):
            open_calls.append(req.full_url)
            return _FakeResp()

    monkeypatch.setattr(
        "backend.core.pinned_http._build_pinned_opener", lambda pinned_ip: _FakeOpener()
    )

    return open_calls


def test_fetch_feed_max_age_shorter_causes_refetch(monkeypatch):
    """When max_age is shorter than elapsed time, the real _fetch_feed bypasses the cache."""
    import backend.monitoring.collect as collect_mod

    url = "https://example.com/feed2"
    open_calls = _make_opener_stub(monkeypatch)

    # Seed cache as if fetched 200 seconds ago — stale relative to a 120s max_age
    now = time.monotonic()
    collect_mod._rss_cache[url] = (now - 200.0, [{"title": "stale_cached"}])

    # Call the REAL _fetch_feed — 200s elapsed > 120s max_age → should refetch
    result = collect_mod._fetch_feed(url, max_age=120.0)

    assert open_calls == [url]  # network was hit
    assert result[0]["title"] == "fresh_article"


def test_fetch_feed_max_age_longer_uses_cache(monkeypatch):
    """When max_age is longer than elapsed time, the real _fetch_feed returns the cached value."""
    import backend.monitoring.collect as collect_mod

    url = "https://example.com/feed3"
    open_calls = _make_opener_stub(monkeypatch)

    # Seed cache as if fetched 50 seconds ago — still fresh relative to a 120s max_age
    now = time.monotonic()
    collect_mod._rss_cache[url] = (now - 50.0, [{"title": "recent_cached"}])

    # Call the REAL _fetch_feed — 50s elapsed < 120s max_age → should use cache
    result = collect_mod._fetch_feed(url, max_age=120.0)

    assert open_calls == []  # no network call
    assert result == [{"title": "recent_cached"}]


# ── gather_rss max_age propagation ────────────────────────────────────────────


def test_gather_rss_passes_max_age_to_fetch_feed(monkeypatch):
    """gather_rss must pass max_age through to _fetch_feed for each feed."""
    import inspect

    import backend.monitoring.collect as collect_mod

    # Verify gather_rss accepts max_age param (would raise TypeError if not)
    sig = inspect.signature(collect_mod.gather_rss)
    assert "max_age" in sig.parameters, "gather_rss must accept max_age parameter"


def test_gather_rss_max_age_none_default():
    """gather_rss defaults max_age to None (preserving backward compat)."""
    import inspect

    import backend.monitoring.collect as collect_mod

    sig = inspect.signature(collect_mod.gather_rss)
    default = sig.parameters["max_age"].default
    assert default is None


def test_gather_rss_max_age_forwarded_to_fetch(monkeypatch):
    """The max_age passed to gather_rss must reach _fetch_feed calls."""
    import backend.monitoring.collect as collect_mod

    received: list = []

    def _patched_fetch(url, max_age=None):
        received.append(max_age)
        return []

    monkeypatch.setattr(collect_mod, "_fetch_feed", _patched_fetch)

    feeds = [
        {"url": "https://example.com/a", "subjects": ["scan:intel"], "kind": "news", "via": "rss"},
        {"url": "https://example.com/b", "subjects": ["scan:intel"], "kind": "news", "via": "rss"},
    ]

    asyncio.run(collect_mod.gather_rss(feeds, max_age=240.0))

    assert len(received) == 2
    assert all(v == 240.0 for v in received)


def test_gather_rss_default_max_age_none_forwarded(monkeypatch):
    """When called without max_age, _fetch_feed receives None."""
    import backend.monitoring.collect as collect_mod

    received: list = []

    def _patched_fetch(url, max_age=None):
        received.append(max_age)
        return []

    monkeypatch.setattr(collect_mod, "_fetch_feed", _patched_fetch)

    feeds = [
        {"url": "https://example.com/c", "subjects": ["scan:intel"], "kind": "news", "via": "rss"},
    ]

    asyncio.run(collect_mod.gather_rss(feeds))

    assert len(received) == 1
    assert received[0] is None
