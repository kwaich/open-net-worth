"""NewsSourceStore on Core (plan 4d, task 2) — DB-CRUD round trip, the user-source cap, and
the SSRF guard preserved on `create_source` (security review flag — see task-2-report.md).
"""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.news_source_store import NewsSourceStore

_BUILTIN = {
    "id": "src_ft_companies",
    "label": "FT Companies",
    "kind": "outlet",
    "transport": "poll",
    "url": "https://example.com/feed.xml",
    "scope": "firehose",
    "match": "server_tokens",
    "cadence": "fast",
    "via": "rss:ft_companies",
    "enabled": True,
    "min_interval_sec": 300,
    "markets": [],
    "provenance": "builtin",
}


def test_create_and_get_round_trip(db_engine):
    store = NewsSourceStore(engine=db_engine)
    row = store.create_source(dict(_BUILTIN))
    assert row["id"] == "src_ft_companies"
    assert store.get("src_ft_companies")["label"] == "FT Companies"
    assert store.where(kind="outlet") == [row]


def test_create_source_refuses_duplicate_id(db_engine):
    store = NewsSourceStore(engine=db_engine)
    store.create_source(dict(_BUILTIN))
    with pytest.raises(ConflictError):
        store.create_source(dict(_BUILTIN))


def test_set_enabled_and_update_policy(db_engine):
    store = NewsSourceStore(engine=db_engine)
    store.create_source(dict(_BUILTIN))
    store.set_enabled("src_ft_companies", False)
    assert store.get("src_ft_companies")["enabled"] is False
    updated = store.update_policy("src_ft_companies", {"cadence": "slow"})
    assert updated["cadence"] == "slow"
    with pytest.raises(ValidationError):
        store.update_policy("src_ft_companies", {"url": "https://evil.example.com"})
    with pytest.raises(NotFoundError):
        store.update_policy("src_missing", {"cadence": "hot"})


def test_delete_source_refuses_builtin_allows_user(db_engine):
    store = NewsSourceStore(engine=db_engine)
    store.create_source(dict(_BUILTIN))
    with pytest.raises(ConflictError):
        store.delete_source("src_ft_companies")

    user_row = {
        **_BUILTIN,
        "id": "src_userone",
        "provenance": "user",
        "url": "https://1.1.1.1/feed",
    }
    store.create_source(user_row)
    # 1.1.1.1 is a public IP, so SSRF validation should accept it (not asserted further here —
    # see test_create_source_calls_ssrf_guard_for_user_urls for the guard-invocation proof).
    deleted = store.delete_source("src_userone")
    assert deleted["id"] == "src_userone"
    assert store.get("src_userone") is None


def test_max_user_sources_cap_enforced(db_engine, monkeypatch):
    import backend.db.news_source_store as mod

    monkeypatch.setattr(mod, "_MAX_USER_SOURCES", 2)
    store = NewsSourceStore(engine=db_engine)
    for i in range(2):
        store.create_source(
            {**_BUILTIN, "id": f"src_user{i}", "provenance": "user", "url": f"https://1.1.1.{i}/f"}
        )
    with pytest.raises(ValidationError):
        store.create_source(
            {**_BUILTIN, "id": "src_userN", "provenance": "user", "url": "https://1.1.1.9/f"}
        )


# ── Security-critical: the SSRF guard is preserved on create_source ────────────────────────


def test_create_source_calls_ssrf_guard_for_user_urls(db_engine, monkeypatch):
    """`create_source` must still invoke `assert_public_url` (check_reachable=True) for a
    user-supplied URL — the ONE moment a fresh URL is accepted. Proven by making the guard
    refuse and checking the store write is refused too, not just that the module still
    imports the guard."""
    import backend.db.news_source_store as mod
    from backend.core.net_guard import UnsafeUrlError

    def _refuse(url):
        raise UnsafeUrlError(f"refused: {url}")

    monkeypatch.setattr(mod, "assert_public_url", _refuse)
    store = NewsSourceStore(engine=db_engine)
    with pytest.raises(ValidationError):
        store.create_source(
            {**_BUILTIN, "id": "src_user1", "provenance": "user", "url": "https://169.254.169.254/"}
        )
    assert store.get("src_user1") is None  # refused before any row was written
