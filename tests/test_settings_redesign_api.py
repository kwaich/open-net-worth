"""Endpoints the Settings overlay redesign added or extended.

Covers the memory-facts routes, the new profile fields (nickname + display formats), the
base-currency preview, the cost by-provider breakdown, and the extension enable/disable flag.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


# ── Memory facts ──────────────────────────────────────────────────────────────


@pytest.fixture
def memory_store(db_isolated):
    from backend.db import memory_store as module

    return module.MemoryStore()


def _seed(store, *contents):
    for i, c in enumerate(contents):
        store.insert(
            {
                "id": f"mem_{i}",
                "content": c,
                "tier": "durable",
                "source": "implicit",
                "created_at": "2026-08-01T00:00:00",
                "updated_at": f"2026-08-0{i + 1}T00:00:00",
            }
        )


def test_list_memory_facts_returns_facts_and_total(memory_store, monkeypatch):
    import backend.api.memory as api

    _seed(memory_store, "prefers dividend payers", "relocating in 2028")
    monkeypatch.setattr(api, "MemoryStore", lambda: memory_store)

    r = client.get("/api/memory/facts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {f["content"] for f in body["facts"]} == {
        "prefers dividend payers",
        "relocating in 2028",
    }


def test_forget_one_fact_removes_only_that_fact(memory_store, monkeypatch):
    import backend.api.memory as api

    _seed(memory_store, "a", "b")
    monkeypatch.setattr(api, "MemoryStore", lambda: memory_store)

    r = client.delete("/api/memory/facts/mem_0")
    assert r.status_code == 200
    assert [f["content"] for f in memory_store.all_facts()] == ["b"]


def test_forget_an_unknown_fact_is_404(memory_store, monkeypatch):
    import backend.api.memory as api

    monkeypatch.setattr(api, "MemoryStore", lambda: memory_store)
    assert client.delete("/api/memory/facts/mem_nope").status_code == 404


def test_forget_everything_empties_the_store(memory_store, monkeypatch):
    import backend.api.memory as api

    _seed(memory_store, "a", "b", "c")
    monkeypatch.setattr(api, "MemoryStore", lambda: memory_store)

    r = client.delete("/api/memory/facts")
    assert r.status_code == 200
    assert r.json()["deleted"] == 3
    assert memory_store.all_facts() == []


def test_forgetting_works_while_memory_is_switched_off(memory_store, monkeypatch):
    """The toggle stops extraction and injection. A user who turned memory off and now wants
    the stored facts GONE must not have to turn it back on to do that."""
    import backend.api.memory as api
    from backend.db.memory_settings_store import MemorySettingsStore

    _seed(memory_store, "a")
    monkeypatch.setattr(api, "MemoryStore", lambda: memory_store)
    monkeypatch.setattr(MemorySettingsStore, "enabled", lambda self: False)

    assert client.delete("/api/memory/facts/mem_0").status_code == 200
    assert memory_store.all_facts() == []


# ── Profile: new fields ───────────────────────────────────────────────────────


def test_profile_accepts_nickname_and_display_formats():
    r = client.put(
        "/api/profile",
        json={
            "nickname": "Jane",
            "date_format": "iso",
            "number_format": "space",
            "week_start": "sun",
        },
    )
    assert r.status_code == 200
    saved = r.json()["profile"]
    assert saved["nickname"] == "Jane"
    assert saved["date_format"] == "iso"
    assert saved["number_format"] == "space"
    assert saved["week_start"] == "sun"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("date_format", "ymd"),
        ("number_format", "apostrophe"),
        ("week_start", "wed"),
    ],
)
def test_profile_refuses_an_unknown_display_format(field, bad):
    """An unrecognised format would silently fall back to the browser locale, so the setting
    would read as saved while doing nothing."""
    assert client.put("/api/profile", json={field: bad}).status_code == 422


def test_blank_display_format_clears_rather_than_422s():
    r = client.put("/api/profile", json={"date_format": ""})
    assert r.status_code == 200


def test_blank_display_format_returns_to_the_browser_default():
    """Browser default is stored as no value; a blank must actually unset a saved choice."""
    client.put("/api/profile", json={"date_format": "mdy", "number_format": "dot"})
    r = client.put("/api/profile", json={"date_format": "", "number_format": ""})
    assert r.status_code == 200
    saved = r.json()["profile"]
    assert saved["date_format"] is None
    assert saved["number_format"] is None
    assert client.get("/api/profile").json()["date_format"] is None


def test_an_unrelated_profile_save_leaves_display_formats_alone():
    client.put("/api/profile", json={"date_format": "iso"})
    client.put("/api/profile", json={"nickname": "Jane"})
    assert client.get("/api/profile").json()["date_format"] == "iso"


def test_base_currency_preview_lists_the_amounts_that_will_not_convert():
    client.put(
        "/api/profile",
        json={"monthly_net_income": 8000, "monthly_expenses": 3800},
    )
    r = client.get("/api/profile/base-currency-preview")
    assert r.status_code == 200
    labels = {f["label"] for f in r.json()["fields"]}
    assert "Monthly net income" in labels
    assert "Monthly expenses" in labels


# ── Cost: by-provider breakdown ───────────────────────────────────────────────


def test_cost_summary_exposes_a_by_provider_breakdown():
    r = client.get("/api/cost/summary?days=30")
    assert r.status_code == 200
    assert "by_provider" in r.json()


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-sonnet-4-6", "Anthropic"),
        ("gpt-5.2", "OpenAI"),
        ("gemini-3-pro", "Google Gemini"),
        ("llama3.3:70b", "Ollama (local)"),
        ("anthropic:claude-haiku-4-5", "Anthropic"),
    ],
)
def test_provider_attribution_places_known_families(model, expected):
    from backend.services.cost import provider_of_model

    assert provider_of_model(model) == expected


def test_provider_attribution_never_guesses_for_an_unknown_model():
    """The model field takes free text. Folding a model we've never heard of into some
    provider would misreport spend; "Unknown" is the honest bucket."""
    from backend.services.cost import provider_of_model

    assert provider_of_model("some-private-gateway-model") == "Unknown"
    assert provider_of_model("") == "Unknown"


# ── Extensions: enable / disable ──────────────────────────────────────────────


def test_a_pack_with_no_flag_is_enabled(monkeypatch, tmp_path):
    """Absence means enabled — a freshly installed pack works without anyone writing a
    record for it, and a lost config file cannot switch every extension off."""
    from backend.db.app_config_store import AppConfigStore
    from backend.services.extensions import registry

    # app_config is DB-backed now (Wave 5D2a, JSON->DB) — no path= to isolate; conftest's
    # autouse Tier-2 table clear isolates the row per test instead.
    store = AppConfigStore()
    monkeypatch.setattr("backend.db.app_config_store.AppConfigStore", lambda *a, **k: store)
    assert registry.is_pack_enabled("data_pack", "never-seen") is True


def test_disabling_a_pack_is_readable_back(monkeypatch, tmp_path):
    from backend.db.app_config_store import AppConfigStore
    from backend.services.extensions import registry

    store = AppConfigStore()
    monkeypatch.setattr("backend.db.app_config_store.AppConfigStore", lambda *a, **k: store)

    registry.set_pack_enabled("data_pack", "sg-cpf", False)
    assert registry.is_pack_enabled("data_pack", "sg-cpf") is False

    registry.set_pack_enabled("data_pack", "sg-cpf", True)
    assert registry.is_pack_enabled("data_pack", "sg-cpf") is True


def test_patching_an_unknown_extension_is_404():
    r = client.patch("/api/extensions/data_pack/not-installed", json={"enabled": False})
    assert r.status_code == 404


def test_extension_list_reports_enabled():
    r = client.get("/api/extensions")
    assert r.status_code == 200
    for pack in r.json():
        assert "enabled" in pack


# ── Backup download / restore: the CSRF gate ──────────────────────────────────
#
# AUTH_TRUST_LOOPBACK defaults TRUE, so on a local install the auth gate returns early for
# any request from 127.0.0.1. Without a custom-header requirement, a plain <form> POST from
# any page the operator has open — a CORS *simple* request, no preflight — reaches
# /api/backup/{name}/restore and rolls their data back. The attacker cannot read the
# response; the side effect IS the attack. Same guard GET /api/data/export already has.


def test_restore_without_the_csrf_header_is_403():
    r = client.post(
        "/api/backup/20260101T000000Z/restore",
        headers={"X-Requested-With": ""},
    )
    assert r.status_code == 403, "a form-POST-shaped request must not reach the restore"
    assert "X-Requested-With" in r.json()["detail"]


def test_download_without_the_csrf_header_is_403():
    r = client.get(
        "/api/backup/20260101T000000Z/download",
        headers={"X-Requested-With": ""},
    )
    assert r.status_code == 403


def test_restore_with_the_csrf_header_gets_past_the_gate():
    """It then 404s on the unknown backup — which is the point: the gate is what changed,
    not the resolution."""
    r = client.post(
        "/api/backup/20260101T000000Z/restore",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 404


def test_download_with_the_csrf_header_gets_past_the_gate():
    r = client.get(
        "/api/backup/20260101T000000Z/download",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 404
