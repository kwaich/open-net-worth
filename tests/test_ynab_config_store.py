"""YnabConfigStore — the ``ynab`` connector row (RFC connectors-table) and its write-only
access-token discipline, plus the budget-selection fields no sibling store has
(budget_id/budget_name/budget_currency).

Connectors never read environment variables; the access token lives in ``connector_secrets``
(public() exposes only has_access_token); a blank access_token in an update keeps the saved
one. db_isolated gives each test a fresh, isolated sqlite file.

There is no delta-sync cursor any more (RFC connectors-as-data-sources): the fetch tool
passes an explicit since_date window on every call instead of a persisted
last_knowledge_of_server, so poll_interval_seconds/server_knowledge/account_map/stats and
the cursor_for_budget mid-sync guard are all gone — see docs/adr and the ynab.py module
docstring. record_status() replaces that old bookkeeping.
"""

from __future__ import annotations

import json

import pytest

from backend.db.ynab_config_store import YnabConfigStore


@pytest.fixture
def store(db_isolated):
    return YnabConfigStore()


def test_defaults_when_absent(store):
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["access_token"] == ""
    assert cfg["budget_id"] is None
    assert cfg["budget_name"] is None
    assert cfg["budget_currency"] is None
    assert cfg["last_synced_at"] is None
    assert cfg["last_status"] == "never_run"
    assert cfg["last_error"] is None
    assert store.is_configured() is False
    assert store.is_active() is False


def test_env_is_ignored(db_isolated, monkeypatch):
    monkeypatch.setenv("YNAB_ACCESS_TOKEN", "ynab-env-token")
    monkeypatch.setenv("YNAB_BUDGET_ID", "env-budget-id")

    cfg = YnabConfigStore().get()
    assert cfg["access_token"] == ""
    assert cfg["enabled"] is False
    assert cfg["budget_id"] is None


def test_public_hides_access_token(store):
    store.update({"enabled": True, "access_token": "TOPSECRET-ynab-token"})
    pub = store.public()
    assert pub["has_access_token"] is True
    assert "access_token" not in pub
    assert pub["enabled"] is True
    assert pub["configured"] is True
    assert "TOPSECRET" not in json.dumps(pub)


def test_blank_access_token_keeps_saved(store):
    store.update({"access_token": "keep-me"})
    store.update({"access_token": "", "enabled": True})
    assert store.access_token() == "keep-me"
    assert store.get()["enabled"] is True
    # An omitted token (None dropped) also keeps it.
    store.update({"budget_name": "Personal"})
    assert store.access_token() == "keep-me"


def test_is_active_requires_enabled_and_token(store):
    store.update({"access_token": "ynab-token"})
    assert store.is_active() is False  # configured but disabled
    store.update({"enabled": True})
    assert store.is_active() is True


def test_is_configured_does_not_require_a_budget(store):
    """Deliberately: budget resolution is a runtime step (auto-detect-if-one, or a clear
    refusal), not a config gate — so a fetch can still be attempted (and can complete
    auto-detection) as soon as a token is saved, before any budget is chosen."""
    store.update({"access_token": "ynab-token"})
    assert store.is_configured() is True
    assert store.get()["budget_id"] is None


def test_unknown_keys_are_ignored(store):
    store.update({"access_token": "ynab-token", "bogus_field": "nope"})
    assert "bogus_field" not in store.get()


def test_reset_clears_everything_back_to_defaults(store):
    store.update(
        {"access_token": "ynab-token", "enabled": True, "budget_id": "b1", "budget_name": "P"}
    )
    store.record_status("ok")
    store.reset()
    cfg = store.get()
    assert cfg["access_token"] == ""
    assert cfg["enabled"] is False
    assert cfg["budget_id"] is None
    assert cfg["last_status"] == "never_run"


# ── record_status: the one write a fetch call makes to this store ──────────────────────


def test_record_status_sets_last_fetched_fields(store):
    """Replaces the old stats/account-map/cursor bookkeeping the retired adapter used
    (RFC connectors-as-data-sources) — a fetch tool's own result carries counts; nothing
    here is a running total any more."""
    store.record_status("ok", error=None)
    pub = store.public()
    assert pub["last_status"] == "ok"
    assert pub["last_error"] is None
    assert pub["last_synced_at"] is not None


def test_record_status_persists_error(store):
    store.record_status("auth_error", error="bad token")
    pub = store.public()
    assert pub["last_status"] == "auth_error"
    assert pub["last_error"] == "bad token"


# ── budget selection (the one thing no sibling store has) ─────────────────────────────


def test_setting_a_budget_persists_name_and_currency(store):
    store.update({"budget_id": "b1", "budget_name": "Personal", "budget_currency": "USD"})
    cfg = store.get()
    assert cfg["budget_id"] == "b1"
    assert cfg["budget_name"] == "Personal"
    assert cfg["budget_currency"] == "USD"
    assert store.public()["budget_id"] == "b1"
    assert store.public()["budget_name"] == "Personal"


def test_blank_budget_id_clears_the_pick(store):
    """Unlike access_token (write-only: blank means 'keep the saved value'), budget_id is
    NOT a secret — a blank value is a real, deliberate instruction to clear the pick."""
    store.update({"budget_id": "b1", "budget_name": "Personal", "budget_currency": "USD"})
    store.update({"budget_id": ""})
    cfg = store.get()
    assert cfg["budget_id"] is None
    assert cfg["budget_name"] is None
    assert cfg["budget_currency"] is None


def test_omitted_budget_id_leaves_it_unchanged(store):
    store.update({"budget_id": "b1", "budget_name": "Personal"})
    store.update({"enabled": True})  # no budget_id key at all
    assert store.get()["budget_id"] == "b1"


def test_changing_the_budget_does_not_disturb_an_unrelated_field(store):
    """No cursor/account-map exists any more to reset on a budget switch (RFC
    connectors-as-data-sources) — switching budgets is a plain field update, nothing more."""
    store.update({"budget_id": "b1", "budget_name": "Personal", "budget_currency": "USD"})
    store.record_status("ok")
    store.update({"budget_id": "b2", "budget_name": "Family", "budget_currency": "EUR"})
    cfg = store.get()
    assert cfg["budget_id"] == "b2"
    assert cfg["budget_name"] == "Family"
    assert cfg["budget_currency"] == "EUR"
    assert cfg["last_status"] == "ok"  # untouched by the budget switch
