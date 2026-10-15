"""AI tools for INT9 Phase B (backend/ai/tools/intel.py): get_news_sources (headless-ok read),
set_news_sources_enabled / add_news_sources / remove_news_sources (NOT headless writes).

Batch-pluralized 2026-07-19: set_news_source_enabled -> set_news_sources_enabled,
add_news_source -> add_news_sources, remove_news_source -> remove_news_sources. The former
singular names are kept as registry aliases (a tool name is persisted data). Dispatched via
execute_tool (not the module function directly) so the alias-resolution path is exercised
the same way a real chat turn would use it.

Param-arity follow-up (2026-07-19d): set_news_sources_enabled's/remove_news_sources' batch
param renamed source_id -> source_ids (the old singular key still resolves — read-side
fallback, no alias table needed for a param rename).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── get_news_sources ────────────────────────────────────────────────────────────────


def test_get_news_sources_returns_joined_rows(monkeypatch):
    from backend.services import news_sources as ns_service

    monkeypatch.setattr(
        ns_service, "list_sources", lambda *, days=7, store=None: [{"id": "src_x", "via": "rss:x"}]
    )

    import backend.monitoring.rss_poller_hot as hot_mod

    monkeypatch.setattr(
        hot_mod, "source_health_snapshot", lambda: {"rss:x": {"consecutive_failures": 1}}
    )

    result, proposed = call("get_news_sources")
    assert proposed is None
    assert result["sources"][0]["id"] == "src_x"
    assert result["sources"][0]["failure_streak"] == {"consecutive_failures": 1}


def test_get_news_sources_is_registered():
    from backend.ai.tools import registered_tool_names

    names = registered_tool_names()
    assert "get_news_sources" in names
    assert "set_news_sources_enabled" in names
    assert "add_news_sources" in names
    assert "remove_news_sources" in names


def test_former_singular_names_are_aliases_not_registered_tools():
    """The pre-2026-07-19 singular names must still resolve (via alias) but never appear
    as live schemas — the same contract every other renamed tool follows."""
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    renamed = {
        "set_news_source_enabled": "set_news_sources_enabled",
        "add_news_source": "add_news_sources",
        "remove_news_source": "remove_news_sources",
    }
    for old, new in renamed.items():
        assert old not in names, f"{old} must not be offered — only {new} is live"


# ── set_news_sources_enabled ──────────────────────────────────────────────────────────


def test_set_news_sources_enabled_toggles(monkeypatch):
    """Also exercises the pre-2026-07-19d singular ``source_id`` key — must keep
    resolving as a read-side fallback."""
    from backend.services import news_sources as ns_service

    calls = {}

    def _fake_update(source_id, changes):
        calls["args"] = (source_id, changes)
        return {"id": source_id, "enabled": changes["enabled"]}

    monkeypatch.setattr(ns_service, "update_source_policy", _fake_update)

    result, proposed = call("set_news_sources_enabled", source_id="src_x", enabled=False)
    assert proposed is None
    assert result["ok"] is True
    assert result["source"]["enabled"] is False
    assert calls["args"] == ("src_x", {"enabled": False})


def test_set_news_sources_enabled_toggles_via_plural_key(monkeypatch):
    """The current schema key (2026-07-19d param-arity fixup): ``source_ids``."""
    from backend.services import news_sources as ns_service

    calls = {}

    def _fake_update(source_id, changes):
        calls["args"] = (source_id, changes)
        return {"id": source_id, "enabled": changes["enabled"]}

    monkeypatch.setattr(ns_service, "update_source_policy", _fake_update)

    result, proposed = call("set_news_sources_enabled", source_ids="src_x", enabled=False)
    assert proposed is None
    assert result["ok"] is True
    assert result["source"]["enabled"] is False
    assert calls["args"] == ("src_x", {"enabled": False})


def test_set_news_sources_enabled_not_found_returns_error_not_raise(monkeypatch):
    from backend.core.errors import NotFoundError
    from backend.services import news_sources as ns_service

    def _boom(source_id, changes):
        raise NotFoundError(f"News source {source_id!r} not found")

    monkeypatch.setattr(ns_service, "update_source_policy", _boom)

    result, _ = call("set_news_sources_enabled", source_id="src_nope", enabled=True)
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_set_news_source_enabled_alias_resolves(monkeypatch):
    from backend.services import news_sources as ns_service

    monkeypatch.setattr(
        ns_service,
        "update_source_policy",
        lambda source_id, changes: {"id": source_id, **changes},
    )
    result, _ = call("set_news_sources_enabled", source_id="src_x", enabled=False)
    assert result["ok"] is True
    assert result["source"]["enabled"] is False


def test_set_news_sources_enabled_batch_broadcasts_to_several(monkeypatch):
    from backend.services import news_sources as ns_service

    seen = []

    def _fake_update(source_id, changes):
        seen.append((source_id, changes))
        return {"id": source_id, **changes}

    monkeypatch.setattr(ns_service, "update_source_policy", _fake_update)

    result, proposed = call("set_news_sources_enabled", source_id=["src_a", "src_b"], enabled=False)
    assert proposed is None
    assert result["ok"] is True
    assert result["updated"] == 2
    assert seen == [("src_a", {"enabled": False}), ("src_b", {"enabled": False})]


def test_set_news_sources_enabled_batch_broadcasts_via_plural_key(monkeypatch):
    from backend.services import news_sources as ns_service

    seen = []

    def _fake_update(source_id, changes):
        seen.append((source_id, changes))
        return {"id": source_id, **changes}

    monkeypatch.setattr(ns_service, "update_source_policy", _fake_update)

    result, proposed = call(
        "set_news_sources_enabled", source_ids=["src_a", "src_b"], enabled=False
    )
    assert proposed is None
    assert result["ok"] is True
    assert result["updated"] == 2
    assert seen == [("src_a", {"enabled": False}), ("src_b", {"enabled": False})]


def test_set_news_sources_enabled_batch_one_not_found_does_not_block_the_rest(monkeypatch):
    from backend.core.errors import NotFoundError
    from backend.services import news_sources as ns_service

    def _fake_update(source_id, changes):
        if source_id == "src_ghost":
            raise NotFoundError("nope")
        return {"id": source_id, **changes}

    monkeypatch.setattr(ns_service, "update_source_policy", _fake_update)

    result, _ = call(
        "set_news_sources_enabled",
        updates=[
            {"source_id": "src_ghost", "enabled": True},
            {"source_id": "src_ok", "enabled": False},
        ],
    )
    assert result["ok"] is False
    assert result["updated"] == 1
    assert "error" in result["results"][0]
    assert result["results"][1]["ok"] is True


# ── add_news_sources ──────────────────────────────────────────────────────────────────


def test_add_news_sources_creates_a_row(monkeypatch):
    from backend.services import news_sources as ns_service

    def _fake_create(*, label, url, cadence):
        return {"id": "src_new", "label": label, "url": url, "cadence": cadence}

    monkeypatch.setattr(ns_service, "create_user_source", _fake_create)

    result, proposed = call("add_news_sources", label="My feed", url="https://example.com/f.xml")
    assert proposed is None
    assert result["ok"] is True
    assert result["source"]["cadence"] == "slow"  # tool default mirrors the service default


def test_add_news_sources_validation_error_returns_error_not_raise(monkeypatch):
    from backend.core.errors import ValidationError
    from backend.services import news_sources as ns_service

    def _boom(*, label, url, cadence):
        raise ValidationError("url: is not a safe http(s) URL")

    monkeypatch.setattr(ns_service, "create_user_source", _boom)

    result, _ = call("add_news_sources", label="Evil", url="http://127.0.0.1/x")
    assert result["ok"] is False
    assert result["error"].startswith("url:")


def test_add_news_source_alias_resolves(monkeypatch):
    from backend.services import news_sources as ns_service

    monkeypatch.setattr(
        ns_service,
        "create_user_source",
        lambda *, label, url, cadence: {
            "id": "src_new",
            "label": label,
            "url": url,
            "cadence": cadence,
        },
    )
    result, _ = call("add_news_sources", label="My feed", url="https://example.com/f.xml")
    assert result["ok"] is True
    assert result["source"]["id"] == "src_new"


def test_add_news_sources_batch_creates_all_independently(monkeypatch):
    from backend.services import news_sources as ns_service

    created = []

    def _fake_create(*, label, url, cadence):
        row = {"id": f"src_{len(created)}", "label": label, "url": url, "cadence": cadence}
        created.append(row)
        return row

    monkeypatch.setattr(ns_service, "create_user_source", _fake_create)

    result, proposed = call(
        "add_news_sources",
        sources=[
            {"label": "Feed A", "url": "https://a.example.com/f.xml"},
            {"label": "Feed B", "url": "https://b.example.com/f.xml", "cadence": "fast"},
        ],
    )
    assert proposed is None
    assert result["ok"] is True
    assert result["created"] == 2
    assert len(created) == 2


def test_add_news_sources_batch_enforces_cap_across_the_whole_batch(monkeypatch):
    """SEC landmine: the 20-user-row cap must hold across the WHOLE batch, not per item.
    The real store choke (NewsSourceStore.create_source) re-reads its own current count on
    every call, so a batch that would push past the cap fails only the items beyond it."""
    from backend.core.errors import ValidationError
    from backend.services import news_sources as ns_service

    existing_count = {"n": 19}

    def _fake_create(*, label, url, cadence):
        if existing_count["n"] >= 20:
            raise ValidationError(
                f"sources: at most 20 user-added sources allowed (already have {existing_count['n']})"
            )
        existing_count["n"] += 1
        return {"id": f"src_{existing_count['n']}", "label": label, "url": url, "cadence": cadence}

    monkeypatch.setattr(ns_service, "create_user_source", _fake_create)

    result, proposed = call(
        "add_news_sources",
        sources=[
            {"label": "Fits", "url": "https://a.example.com/f.xml"},
            {"label": "Over cap", "url": "https://b.example.com/f.xml"},
        ],
    )
    assert proposed is None
    assert result["ok"] is False
    assert result["created"] == 1
    assert result["results"][0]["ok"] is True
    assert "at most 20" in result["results"][1]["error"]


# ── remove_news_sources ───────────────────────────────────────────────────────────────


def test_remove_news_sources_deletes(monkeypatch):
    """Also exercises the pre-2026-07-19d singular ``source_id`` key — must keep
    resolving as a read-side fallback."""
    from backend.services import news_sources as ns_service

    monkeypatch.setattr(ns_service, "delete_user_source", lambda source_id: {"id": source_id})

    result, proposed = call("remove_news_sources", source_id="src_x")
    assert proposed is None
    assert result["ok"] is True
    assert result["deleted_id"] == "src_x"


def test_remove_news_sources_deletes_via_plural_key(monkeypatch):
    """The current schema key (2026-07-19d param-arity fixup): ``source_ids``."""
    from backend.services import news_sources as ns_service

    monkeypatch.setattr(ns_service, "delete_user_source", lambda source_id: {"id": source_id})

    result, proposed = call("remove_news_sources", source_ids="src_x")
    assert proposed is None
    assert result["ok"] is True
    assert result["deleted_id"] == "src_x"


def test_remove_news_sources_refuses_builtin_returns_error(monkeypatch):
    from backend.core.errors import ConflictError
    from backend.services import news_sources as ns_service

    def _boom(source_id):
        raise ConflictError(f"News source {source_id!r} is a builtin")

    monkeypatch.setattr(ns_service, "delete_user_source", _boom)

    result, _ = call("remove_news_sources", source_id="src_ft_companies")
    assert result["ok"] is False
    assert "builtin" in result["error"]


def test_remove_news_source_alias_resolves(monkeypatch):
    from backend.services import news_sources as ns_service

    monkeypatch.setattr(ns_service, "delete_user_source", lambda source_id: {"id": source_id})
    result, _ = call("remove_news_sources", source_id="src_x")
    assert result["ok"] is True
    assert result["deleted_id"] == "src_x"


def test_remove_news_sources_batch_one_builtin_refusal_does_not_block_the_rest(monkeypatch):
    from backend.core.errors import ConflictError
    from backend.services import news_sources as ns_service

    def _fake_delete(source_id):
        if source_id == "src_ft_companies":
            raise ConflictError("News source is a builtin")
        return {"id": source_id}

    monkeypatch.setattr(ns_service, "delete_user_source", _fake_delete)

    result, proposed = call("remove_news_sources", source_id=["src_ft_companies", "src_custom_1"])
    assert proposed is None
    assert result["ok"] is False
    assert result["deleted_count"] == 1
    assert "error" in result["results"][0]
    assert result["results"][1]["ok"] is True


def test_remove_news_sources_batch_via_plural_key(monkeypatch):
    from backend.services import news_sources as ns_service

    monkeypatch.setattr(ns_service, "delete_user_source", lambda source_id: {"id": source_id})

    result, proposed = call("remove_news_sources", source_ids=["src_a", "src_b"])
    assert proposed is None
    assert result["ok"] is True
    assert result["deleted_count"] == 2


# ── add_news_sources: REAL store, cap enforced across the whole batch ────────────────


def test_add_news_sources_batch_enforces_the_20_row_cap_against_the_real_store(db_isolated):
    """End-to-end (no mocking of the choke): the 20-user-source cap must hold ACROSS a
    single batch call, not just per item — this is the exact landmine the task calls out.
    Seed the store to 19 user rows, then add 3 more in ONE add_news_sources call: only 1
    should fit under the cap, and the other 2 must fail independently (not abort the batch).

    NewsSourceStore is DB-backed (plan 4d task 2) — db_isolated redirects the process-wide
    singleton to a fresh, empty database, so the real store choke used by add_news_sources
    sees exactly the 19 seeded rows below, no file path to repoint."""
    from backend.db import news_source_store as ns_store_mod

    store = ns_store_mod.NewsSourceStore()
    # Seed 19 pre-existing user rows directly (mirrors ensure_builtin_sources' own
    # bulk_insert use — these are code-controlled synthetic rows, not user input, so
    # skipping validate_source here is fine for test setup).
    store.bulk_insert(
        [
            {
                "id": f"src_seed_{i}",
                "label": f"Seed {i}",
                "kind": "custom",
                "transport": "poll",
                "url": f"https://example.com/seed{i}.xml",
                "resolver": None,
                "scope": "firehose",
                "match": "server_tokens",
                "cadence": "slow",
                "via": f"rss:custom_seed{i}",
                "enabled": True,
                "min_interval_sec": 1800,
                "markets": [],
                "provenance": "user",
                "notes": "",
                "added_at": "2026-01-01T00:00:00+00:00",
            }
            for i in range(19)
        ]
    )
    assert sum(1 for r in store.all() if r.get("provenance") == "user") == 19

    result, proposed = call(
        "add_news_sources",
        sources=[
            {"label": "Fits", "url": "https://example.com/fits.xml"},
            {"label": "Over 1", "url": "https://example.com/over1.xml"},
            {"label": "Over 2", "url": "https://example.com/over2.xml"},
        ],
    )
    assert proposed is None
    assert result["ok"] is False
    assert result["created"] == 1
    assert result["results"][0]["ok"] is True
    assert "at most 20" in result["results"][1]["error"]
    assert "at most 20" in result["results"][2]["error"]
    # Exactly 20 user rows survive — the cap held across the whole batch, not per-item-blind.
    assert sum(1 for r in store.all() if r.get("provenance") == "user") == 20


# ── mutates domain tag (frontend freshness refresh) ─────────────────────────────────


@pytest.mark.parametrize(
    "name", ["set_news_sources_enabled", "add_news_sources", "remove_news_sources"]
)
def test_write_tools_declare_a_mutates_domain(name):
    from backend.ai.tools.registry import mutated_domains

    assert mutated_domains(name) == ("news_sources",)
