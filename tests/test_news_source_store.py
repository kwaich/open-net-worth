"""INT9 Phase A — db.news_source_store.NewsSourceStore + services.news_sources.

Covers: store CRUD + validation, builtin seed idempotency (double-ensure, a user-disabled
builtin surviving re-ensure, a new builtin appearing on a simulated upgrade), and
active_sources() filtering (enabled / cadence / the master news_freshness gate).

Fully isolated: the store path is monkeypatched into a pytest tmp_path, mirroring
test_category_store.py's convention.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.news_source_store import NewsSourceStore

_FT = {
    "id": "src_ft_companies",
    "label": "FT Companies",
    "kind": "outlet",
    "transport": "poll",
    "url": "https://www.ft.com/companies?format=rss",
    "resolver": None,
    "scope": "firehose",
    "match": "server_tokens",
    "cadence": "hot",
    "via": "rss:ft_companies",
    "enabled": True,
    "min_interval_sec": 60,
    "markets": [],
    "provenance": "builtin",
    "notes": "",
    "added_at": "2026-07-16T00:00:00+00:00",
}


@pytest.fixture
def store(db_isolated):
    # NewsSourceStore is DB-backed (plan 4d task 2) — db_isolated redirects the process-wide
    # singleton to a fresh, empty database; no more per-instance `_path` to repoint.
    return NewsSourceStore()


# ── Store CRUD + validation ─────────────────────────────────────────────────────────


def test_create_source_inserts_a_valid_row(store):
    row = store.create_source(dict(_FT))
    assert row["id"] == "src_ft_companies"
    assert store.get("src_ft_companies")["label"] == "FT Companies"


def test_create_source_refuses_duplicate_id(store):
    store.create_source(dict(_FT))
    with pytest.raises(ConflictError):
        store.create_source(dict(_FT))


def test_create_source_refuses_missing_id_prefix(store):
    bad = {**_FT, "id": "ft_companies"}
    with pytest.raises(ValidationError):
        store.create_source(bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "bogus"),
        ("transport", "carrier_pigeon"),
        ("scope", "bogus"),
        ("match", "bogus"),
        ("cadence", "bogus"),
        ("provenance", "bogus"),
    ],
)
def test_create_source_refuses_invalid_enum(store, field, value):
    bad = {**_FT, field: value}
    with pytest.raises(ValidationError):
        store.create_source(bad)


def test_create_source_refuses_both_url_and_resolver(store):
    bad = {**_FT, "resolver": "yahoo_ticker"}  # _FT already carries a url
    with pytest.raises(ValidationError):
        store.create_source(bad)


def test_create_source_refuses_neither_url_nor_resolver(store):
    bad = {**_FT, "url": None, "resolver": None}
    with pytest.raises(ValidationError):
        store.create_source(bad)


def test_create_source_refuses_min_interval_below_floor(store):
    bad = {**_FT, "min_interval_sec": 10}
    with pytest.raises(ValidationError):
        store.create_source(bad)


def test_create_source_refuses_non_list_markets(store):
    bad = {**_FT, "markets": "DE"}
    with pytest.raises(ValidationError):
        store.create_source(bad)


def test_set_enabled_toggles_a_row(store):
    store.create_source(dict(_FT))
    updated = store.set_enabled("src_ft_companies", False)
    assert updated["enabled"] is False
    assert store.get("src_ft_companies")["enabled"] is False
    store.set_enabled("src_ft_companies", True)
    assert store.get("src_ft_companies")["enabled"] is True


def test_set_enabled_not_found_raises(store):
    with pytest.raises(NotFoundError):
        store.set_enabled("src_nope", False)


def test_delete_source_removes_a_user_row(store):
    # example.com — the RFC 2606 reserved test domain (also used by test_watchlist_custom_
    # feeds.py) — is guaranteed DNS-resolvable, so create_source's Phase B check_reachable
    # SSRF probe (triggered because provenance="user") passes without hitting a real feed.
    user_row = {
        **_FT,
        "id": "src_deadbeef0001",
        "provenance": "user",
        "url": "https://example.com/feed.xml",
    }
    store.create_source(user_row)
    store.delete_source("src_deadbeef0001")
    assert store.get("src_deadbeef0001") is None


def test_delete_source_refuses_a_builtin_row(store):
    store.create_source(dict(_FT))
    with pytest.raises(ConflictError):
        store.delete_source("src_ft_companies")
    assert store.get("src_ft_companies") is not None  # untouched


def test_delete_source_not_found_raises(store):
    with pytest.raises(NotFoundError):
        store.delete_source("src_nope")


def test_where_filters_by_kind_and_cadence(store):
    store.create_source(dict(_FT))
    store.create_source({**_FT, "id": "src_edgar_8k", "kind": "regulatory", "via": "atom:edgar"})
    assert len(store.where(kind="outlet")) == 1
    assert len(store.where(cadence="hot")) == 2
    assert store.where(kind="regulatory")[0]["id"] == "src_edgar_8k"


# ── Builtin seed idempotency ─────────────────────────────────────────────────────────


@pytest.fixture
def isolated_ns_store(store, monkeypatch):
    """Point services.news_sources's NewsSourceStore() constructor at the isolated fixture
    store so ensure_builtin_sources()/active_sources() operate on the tmp_path file."""
    import backend.services.news_sources as ns_mod

    monkeypatch.setattr(ns_mod, "NewsSourceStore", lambda: store)
    return store


def test_ensure_builtin_sources_seeds_every_builtin(isolated_ns_store):
    from backend.services.news_sources import _all_builtin_defs, ensure_builtin_sources

    result = ensure_builtin_sources()
    expected_ids = {d["id"] for d in _all_builtin_defs()}
    assert result["seeded"] == len(expected_ids)
    stored_ids = {r["id"] for r in isolated_ns_store.all()}
    assert stored_ids == expected_ids
    assert all(r["enabled"] for r in isolated_ns_store.all())
    assert all(r["provenance"] == "builtin" for r in isolated_ns_store.all())


def test_every_seeded_resolver_row_is_covered_by_feeds_registry(isolated_ns_store):
    """SF-1 (code review): a row's `resolver` name must actually resolve through
    services.feeds_registry.RESOLVERS — a row pointing at an unregistered name would silently
    fetch nothing forever. A concrete (url-carrying) row carries no resolver at all."""
    from backend.services.feeds_registry import RESOLVERS
    from backend.services.news_sources import ensure_builtin_sources

    ensure_builtin_sources()
    for row in isolated_ns_store.all():
        resolver = row.get("resolver")
        if resolver is None:
            assert row.get("url"), f"{row['id']!r} has neither url nor resolver"
        else:
            assert resolver in RESOLVERS, (
                f"{row['id']!r} references unregistered resolver {resolver!r}"
            )


def test_ensure_builtin_sources_is_idempotent_no_dupes(isolated_ns_store):
    from backend.services.news_sources import ensure_builtin_sources

    ensure_builtin_sources()
    n_before = len(isolated_ns_store.all())
    result = ensure_builtin_sources()
    assert result == {"seeded": 0}
    assert len(isolated_ns_store.all()) == n_before


def test_ensure_builtin_sources_never_overwrites_a_user_disabled_builtin(isolated_ns_store):
    from backend.services.news_sources import ensure_builtin_sources

    ensure_builtin_sources()
    isolated_ns_store.set_enabled("src_ft_companies", False)

    ensure_builtin_sources()  # re-ensure (simulates a later boot)

    assert isolated_ns_store.get("src_ft_companies")["enabled"] is False


def test_ensure_builtin_sources_new_builtin_appears_on_upgrade(isolated_ns_store, monkeypatch):
    from backend.services import news_sources as ns_mod

    ensure = ns_mod.ensure_builtin_sources
    all_defs_original = ns_mod._all_builtin_defs

    # Simulate a pre-upgrade boot: one fewer builtin existed in the code tables.
    def _defs_missing_techcrunch():
        return [d for d in all_defs_original() if d["id"] != "src_techcrunch"]

    monkeypatch.setattr(ns_mod, "_all_builtin_defs", _defs_missing_techcrunch)
    ensure()
    assert isolated_ns_store.get("src_techcrunch") is None

    # Simulate the upgrade: the real (fuller) code table is back.
    monkeypatch.setattr(ns_mod, "_all_builtin_defs", all_defs_original)
    result = ensure()
    assert result["seeded"] == 1
    assert isolated_ns_store.get("src_techcrunch") is not None
    assert isolated_ns_store.get("src_techcrunch")["provenance"] == "builtin"


# ── active_sources() filtering ────────────────────────────────────────────────────────


def _stub_freshness(monkeypatch, value):
    import backend.services.news_sources as ns_mod

    monkeypatch.setattr(ns_mod, "read_watchlist", lambda: {"config": {"news_freshness": value}})


def test_active_sources_filters_by_cadence(store, monkeypatch):
    from backend.services.news_sources import active_sources

    store.create_source({**_FT, "cadence": "hot"})
    store.create_source(
        {
            **_FT,
            "id": "src_yahoo_ticker",
            "cadence": "fast",
            "url": None,
            "resolver": "yahoo_ticker",
        }
    )
    _stub_freshness(monkeypatch, "fast")

    hot = active_sources("hot", store=store)
    fast = active_sources("fast", store=store)
    assert {r["id"] for r in hot} == {"src_ft_companies"}
    assert {r["id"] for r in fast} == {"src_yahoo_ticker"}


def test_active_sources_excludes_disabled_rows(store, monkeypatch):
    from backend.services.news_sources import active_sources

    store.create_source(dict(_FT))
    store.set_enabled("src_ft_companies", False)
    _stub_freshness(monkeypatch, "fast")

    assert active_sources("hot", store=store) == []


def test_active_sources_empty_when_news_freshness_off(store, monkeypatch):
    from backend.services.news_sources import active_sources

    store.create_source(dict(_FT))
    _stub_freshness(monkeypatch, "off")

    assert active_sources("hot", store=store) == []


def test_active_sources_defaults_enabled_true_when_key_absent(store, monkeypatch):
    from backend.services.news_sources import active_sources

    row = dict(_FT)
    del row["enabled"]
    store.insert(row)
    _stub_freshness(monkeypatch, "fast")

    assert {r["id"] for r in active_sources("hot", store=store)} == {"src_ft_companies"}


# ── active_custom_firehose_sources() — INT9 Phase B review fix, cadence-blind by design ──

_CUSTOM_ROW = {
    **_FT,
    "id": "src_custom0001",
    "kind": "custom",
    "provenance": "user",
    "via": "rss:custom_aaaaaaaa",
    "url": "https://example.com/custom.xml",
}


def test_active_custom_firehose_sources_ignores_cadence(store, monkeypatch):
    """The whole point of the review fix: a 'slow'-cadence custom row is STILL returned — only
    active_sources("hot") cadence-filters; this reader never does."""
    from backend.services.news_sources import active_custom_firehose_sources

    store.create_source({**_CUSTOM_ROW, "cadence": "slow"})
    _stub_freshness(monkeypatch, "fast")

    rows = active_custom_firehose_sources(store=store)
    assert {r["id"] for r in rows} == {"src_custom0001"}


def test_active_custom_firehose_sources_excludes_non_custom_kind(store, monkeypatch):
    from backend.services.news_sources import active_custom_firehose_sources

    store.create_source(dict(_FT))  # kind=outlet, not custom
    _stub_freshness(monkeypatch, "fast")

    assert active_custom_firehose_sources(store=store) == []


def test_active_custom_firehose_sources_excludes_per_entity_scope(store, monkeypatch):
    from backend.services.news_sources import active_custom_firehose_sources

    store.create_source({**_CUSTOM_ROW, "scope": "per_entity"})
    _stub_freshness(monkeypatch, "fast")

    assert active_custom_firehose_sources(store=store) == []


def test_active_custom_firehose_sources_excludes_disabled_rows(store, monkeypatch):
    from backend.services.news_sources import active_custom_firehose_sources

    store.create_source(dict(_CUSTOM_ROW))
    store.set_enabled("src_custom0001", False)
    _stub_freshness(monkeypatch, "fast")

    assert active_custom_firehose_sources(store=store) == []


def test_active_custom_firehose_sources_empty_when_news_freshness_off(store, monkeypatch):
    from backend.services.news_sources import active_custom_firehose_sources

    store.create_source(dict(_CUSTOM_ROW))
    _stub_freshness(monkeypatch, "off")

    assert active_custom_firehose_sources(store=store) == []


# ── INT9 Phase B: SSRF at the user-row write choke ─────────────────────────────────

_USER_ROW = {
    **_FT,
    "id": "src_deadbeef0002",
    "provenance": "user",
    "url": "https://example.com/feed.xml",
}


def test_create_source_accepts_a_safe_user_url(store):
    row = store.create_source(dict(_USER_ROW))
    assert row["url"] == "https://example.com/feed.xml"


def test_create_source_rejects_a_private_ip_user_url(store):
    bad = {**_USER_ROW, "url": "http://127.0.0.1/feed.xml"}
    with pytest.raises(ValidationError):
        store.create_source(bad)


def test_create_source_rejects_a_metadata_ip_user_url(store):
    bad = {**_USER_ROW, "url": "http://169.254.169.254/latest/meta-data"}
    with pytest.raises(ValidationError):
        store.create_source(bad)


def test_create_source_rejects_a_non_http_scheme_user_url(store):
    bad = {**_USER_ROW, "url": "file:///etc/passwd"}
    with pytest.raises(ValidationError):
        store.create_source(bad)


# ── INT9 Phase B (security review F1): label/url length caps ───────────────────────


def test_create_source_rejects_a_label_over_200_chars(store):
    bad = {**_FT, "id": "src_deadbeef0003", "label": "x" * 201}
    with pytest.raises(ValidationError, match="^label:"):
        store.create_source(bad)


def test_create_source_accepts_a_label_at_exactly_200_chars(store):
    row = {**_FT, "id": "src_deadbeef0004", "label": "x" * 200}
    store.create_source(row)  # must not raise


def test_create_source_rejects_a_url_over_2048_chars(store):
    long_path = "a" * 2040
    bad = {**_USER_ROW, "id": "src_deadbeef0005", "url": f"https://example.com/{long_path}"}
    with pytest.raises(ValidationError, match="^url:"):
        store.create_source(bad)


def test_create_source_never_probes_a_builtin_url(store, monkeypatch):
    """A builtin row's `url` is never SSRF-probed (Phase A's trusted-code-table posture,
    unchanged) — asserted by making the guard raise if it's ever called for a builtin insert."""
    import backend.db.news_source_store as ns_store_mod

    def _boom(url):
        raise AssertionError("assert_public_url must never run for a builtin row")

    monkeypatch.setattr(ns_store_mod, "assert_public_url", _boom)
    store.create_source(dict(_FT))  # _FT is provenance="builtin" — must not raise


def test_create_source_enforces_the_user_row_cap(store):
    for i in range(20):
        store.create_source({**_USER_ROW, "id": f"src_user{i:04d}"})
    with pytest.raises(ValidationError):
        store.create_source({**_USER_ROW, "id": "src_user_over_cap"})
    assert len([r for r in store.all() if r.get("provenance") == "user"]) == 20


def test_create_source_cap_does_not_count_builtin_rows(store):
    store.create_source(dict(_FT))  # a builtin row
    for i in range(20):
        store.create_source({**_USER_ROW, "id": f"src_user{i:04d}"})
    # 20 user rows + 1 builtin must NOT trip the user-only cap
    assert len(store.all()) == 21


# ── INT9 Phase B: update_policy (PATCH choke) ───────────────────────────────────────


def test_update_policy_patches_enabled_cadence_and_min_interval(store):
    store.create_source(dict(_FT))
    updated = store.update_policy(
        "src_ft_companies", {"enabled": False, "cadence": "fast", "min_interval_sec": 300}
    )
    assert updated["enabled"] is False
    assert updated["cadence"] == "fast"
    assert updated["min_interval_sec"] == 300
    assert store.get("src_ft_companies")["cadence"] == "fast"


def test_update_policy_never_reprobes_an_unchanged_url(store, monkeypatch):
    """A policy-only PATCH must never re-run the SSRF network probe — only create_source's
    check_reachable=True opts into that."""
    import backend.db.news_source_store as ns_store_mod

    store.create_source(dict(_USER_ROW))

    def _boom(url):
        raise AssertionError("assert_public_url must never run on a policy-only PATCH")

    monkeypatch.setattr(ns_store_mod, "assert_public_url", _boom)
    store.update_policy(_USER_ROW["id"], {"enabled": False})


def test_update_policy_refuses_unpatchable_fields(store):
    store.create_source(dict(_FT))
    with pytest.raises(ValidationError):
        store.update_policy("src_ft_companies", {"label": "New label"})


def test_update_policy_refuses_invalid_cadence(store):
    store.create_source(dict(_FT))
    with pytest.raises(ValidationError):
        store.update_policy("src_ft_companies", {"cadence": "bogus"})


def test_update_policy_refuses_min_interval_below_floor(store):
    store.create_source(dict(_FT))
    with pytest.raises(ValidationError):
        store.update_policy("src_ft_companies", {"min_interval_sec": 10})


def test_update_policy_not_found_raises(store):
    with pytest.raises(NotFoundError):
        store.update_policy("src_nope", {"enabled": False})


def test_update_policy_works_on_a_builtin_row(store):
    """Builtin rows: enabled/cadence/min_interval only — exactly the fields update_policy
    already restricts to, so no extra branch is needed for the builtin/user distinction."""
    store.create_source(dict(_FT))
    updated = store.update_policy("src_ft_companies", {"enabled": False})
    assert updated["enabled"] is False


# ── INT9 Phase B: services/news_sources.py's REST/AI pass-throughs ─────────────────────


def test_list_sources_embeds_latency_and_last_ok_at(isolated_ns_store, db_isolated):
    """db_isolated: an unrelated test elsewhere in the session may have written a real
    rss:ft_companies evidence row into the shared store, which would flip this assertion
    depending on test order."""
    from backend.services.news_sources import ensure_builtin_sources, list_sources

    ensure_builtin_sources()
    rows = list_sources(store=isolated_ns_store)
    row = next(r for r in rows if r["id"] == "src_ft_companies")
    assert row["latency"] is None  # no evidence rows in this isolated test
    assert row["last_ok_at"] is None


def test_list_sources_last_ok_at_is_unbounded_unlike_latency(db_isolated):
    """A `via`'s evidence hit OUTSIDE the trailing-days window still surfaces via `last_ok_at`
    (unbounded), even though `latency` (bounded, 7d default) is null for it — the whole point
    of adding `last_ok_at` (UI contract item 4): distinguishes "dead beyond the window" from
    "never worked"."""
    from datetime import datetime, timedelta, timezone

    from backend.db.evidence_store import EvidenceStore
    from backend.db.news_source_store import NewsSourceStore
    from backend.services.news_sources import ensure_builtin_sources, list_sources

    old_hit = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    EvidenceStore().merge_hits(
        [
            {
                "id": "e1",
                "url": "https://ft.com/x",
                "title": "t",
                "via": "rss:ft_companies",
                "published_at": old_hit,
                "first_seen": old_hit,
                "subjects": ["asset:i1"],
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            }
        ]
    )

    # tests/conftest.py's shared autouse seed runs BEFORE db_isolated activates (autouse
    # fixtures set up before explicitly-requested ones at the same scope), so it landed in
    # the old, now-orphaned default engine — reseed builtins explicitly here.
    ensure_builtin_sources()
    ns_store = NewsSourceStore()
    rows = list_sources(store=ns_store)
    row = next(r for r in rows if r["id"] == "src_ft_companies")
    assert row["latency"] is None  # outside the default 7d window
    assert row["last_ok_at"] == old_hit  # unbounded — still visible


def test_create_user_source_mints_a_distinct_via_and_defaults(isolated_ns_store):
    from backend.services.news_sources import create_user_source

    row = create_user_source(label="My feed", url="https://example.com/feed.xml")
    assert row["kind"] == "custom"
    assert row["scope"] == "firehose"
    assert row["match"] == "server_tokens"
    assert row["provenance"] == "user"
    assert row["cadence"] == "slow"
    assert row["via"].startswith("rss:custom_")
    assert row["via"] != "rss:custom"


def test_create_user_source_hot_cadence_gets_60s_default_interval(isolated_ns_store):
    from backend.services.news_sources import create_user_source

    row = create_user_source(label="Fast one", url="https://example.com/f.xml", cadence="hot")
    assert row["min_interval_sec"] == 60


def test_update_source_policy_rejects_empty_changes(isolated_ns_store):
    with pytest.raises(ValidationError):
        from backend.services.news_sources import update_source_policy

        update_source_policy("src_ft_companies", {})


def test_update_source_policy_cadence_change_refreshes_min_interval(isolated_ns_store):
    """Review fix: a cadence PATCH with no explicit min_interval_sec must refresh the interval
    to the NEW cadence's default — otherwise the row keeps its old tier's interval and the
    Phase C UI's "no more than every X" hint goes stale."""
    from backend.services.news_sources import create_user_source, update_source_policy

    row = create_user_source(label="Refresh me", url="https://example.com/refresh.xml")
    assert row["cadence"] == "slow"
    assert row["min_interval_sec"] == 1800

    updated = update_source_policy(row["id"], {"cadence": "hot"})
    assert updated["cadence"] == "hot"
    assert updated["min_interval_sec"] == 60  # the hot-tier default, not the stale 1800


def test_update_source_policy_explicit_min_interval_wins_over_cadence_default(isolated_ns_store):
    from backend.services.news_sources import create_user_source, update_source_policy

    row = create_user_source(label="Explicit wins", url="https://example.com/explicit.xml")
    updated = update_source_policy(row["id"], {"cadence": "hot", "min_interval_sec": 120})
    assert updated["cadence"] == "hot"
    assert updated["min_interval_sec"] == 120


def test_delete_user_source_removes_the_row(isolated_ns_store):
    from backend.services.news_sources import create_user_source, delete_user_source

    row = create_user_source(label="Gone soon", url="https://example.com/g.xml")
    delete_user_source(row["id"])
    assert isolated_ns_store.get(row["id"]) is None


def test_delete_user_source_refuses_a_builtin(isolated_ns_store):
    from backend.services.news_sources import delete_user_source, ensure_builtin_sources

    ensure_builtin_sources()
    with pytest.raises(ConflictError):
        delete_user_source("src_ft_companies")
