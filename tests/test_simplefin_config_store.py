"""SimpleFinConfigStore — the ``simplefin`` connector row (RFC connectors-table) and its
write-only access-URL discipline.

Connectors never read environment variables; the access URL lives in ``connector_secrets``
(``public()`` exposes only ``has_access_url``); a blank ``access_url`` in an update keeps the
saved one. There is no scheduled poll and no provider-account map (RFC
connectors-as-data-sources) — the provider-account -> app-account link lives on
``Account.external_ids`` instead, and the one status write a fetch makes is
``record_status(status, error=...)``.
"""

from __future__ import annotations

import json

import pytest

from backend.db.simplefin_config_store import SimpleFinConfigStore


@pytest.fixture
def store(db_isolated):
    """db_isolated gives the store a fresh, isolated sqlite file."""
    return SimpleFinConfigStore()


def test_defaults_when_absent(store):
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["access_url"] == ""
    assert cfg["last_synced_at"] is None
    assert cfg["last_status"] == "never_run"
    assert cfg["last_error"] is None
    assert store.is_configured() is False
    assert store.is_active() is False


def test_env_is_ignored(db_isolated, monkeypatch):
    monkeypatch.setenv("SIMPLEFIN_ACCESS_URL", "https://u:p@bridge.example/sf")

    cfg = SimpleFinConfigStore().get()
    assert cfg["access_url"] == ""
    assert cfg["enabled"] is False


def test_public_hides_access_url(store):
    store.update({"enabled": True, "access_url": "https://secret:creds@host/sf"})
    pub = store.public()
    assert pub["has_access_url"] is True
    assert "access_url" not in pub
    assert pub["enabled"] is True
    assert pub["configured"] is True
    # The raw URL (and its embedded creds) must never appear in the public view.
    assert "secret" not in json.dumps(pub)
    assert "creds" not in json.dumps(pub)


def test_blank_access_url_keeps_saved(store):
    store.update({"access_url": "https://keep@me/sf"})
    store.update({"access_url": "", "enabled": True})
    assert store.access_url() == "https://keep@me/sf"
    assert store.get()["enabled"] is True
    # An omitted URL (no key at all) also keeps it.
    store.update({"enabled": False})
    assert store.access_url() == "https://keep@me/sf"


def test_is_active_requires_enabled_and_url(store):
    store.update({"access_url": "https://x@y/sf"})
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
    store.record_status("auth_error", error="bad creds")
    pub = store.public()
    assert pub["last_status"] == "auth_error"
    assert pub["last_error"] == "bad creds"


def test_reset_clears_everything(store):
    store.update({"access_url": "https://x@y/sf", "enabled": True})
    store.record_status("ok")
    store.reset()
    cfg = store.get()
    assert cfg["access_url"] == ""
    assert cfg["enabled"] is False
    assert cfg["last_status"] == "never_run"
    assert cfg["last_synced_at"] is None
