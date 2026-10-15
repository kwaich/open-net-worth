"""LunchMoneyConfigStore — the ``lunch_money`` connector row (RFC connectors-table) and its
write-only access-token discipline.

Connectors never read environment variables; the token lives in ``connector_secrets``
(public() exposes only has_access_token); a blank access_token in an update keeps the saved
one. db_isolated gives each test a fresh, isolated sqlite file.

No poll_interval_seconds/account_map/cursor/stats (RFC connectors-as-data-sources retired the
scheduler-driven sync pipeline) — the one write a fetch call makes is record_status(status, error).
"""

from __future__ import annotations

import json

import pytest

from backend.db.lunch_money_config_store import LunchMoneyConfigStore


@pytest.fixture
def store(db_isolated):
    return LunchMoneyConfigStore()


def test_defaults_when_absent(store):
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["access_token"] == ""
    assert cfg["last_synced_at"] is None
    assert cfg["last_status"] == "never_run"
    assert cfg["last_error"] is None
    assert store.is_configured() is False
    assert store.is_active() is False


def test_env_is_ignored(db_isolated, monkeypatch):
    monkeypatch.setenv("LUNCH_MONEY_ACCESS_TOKEN", "lm-env-token")

    cfg = LunchMoneyConfigStore().get()
    assert cfg["access_token"] == ""
    assert cfg["enabled"] is False


def test_public_hides_access_token(store):
    store.update({"enabled": True, "access_token": "TOPSECRET-lm-token"})
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
    store.update({"enabled": False})
    assert store.access_token() == "keep-me"


def test_is_active_requires_enabled_and_token(store):
    store.update({"access_token": "lm-token"})
    assert store.is_active() is False  # configured but disabled
    store.update({"enabled": True})
    assert store.is_active() is True


def test_record_status_sets_last_fetched_fields(store):
    """The uniform record_status(status, error) surface a fetch call makes — replaces the
    old stats/account-map bookkeeping the retired adapter used (RFC
    connectors-as-data-sources)."""
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


def test_unknown_keys_are_ignored(store):
    store.update({"access_token": "lm-token", "bogus_field": "nope"})
    assert "bogus_field" not in store.get()


def test_reset_clears_everything_back_to_defaults(store):
    store.update({"access_token": "lm-token", "enabled": True})
    store.record_status("ok")
    store.reset()
    cfg = store.get()
    assert cfg["access_token"] == ""
    assert cfg["enabled"] is False
    assert cfg["last_status"] == "never_run"
