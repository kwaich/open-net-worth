"""EmailConfigStore — the Email connector's ONE row.

One address (IMAP login, SMTP login, From and alert recipient), one password, one ``enabled``
flag. Settings live in ``connectors.config``, the password in ``connector_secrets``, the latest
IMAP outcome in ``connectors.state``. Isolated per test by conftest's autouse table clear.
"""

from __future__ import annotations

import json

import pytest

from backend.db.connector_store import ConnectorStore
from backend.db.email_config_store import EmailConfigStore


@pytest.fixture
def store():
    return EmailConfigStore()


def _account(**overrides) -> dict:
    return {
        "address": "me@example.com",
        "password": "pw",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        **overrides,
    }


def test_defaults_before_the_row_exists(store):
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["imap_port"] == 993
    assert cfg["smtp_port"] == 587
    assert cfg["imap_folder"] == "INBOX"
    assert cfg["lookback_days"] == 7
    assert cfg["use_tls"] is True
    assert cfg["provider"] is None
    assert cfg["last_status"] == "never_run"
    assert ConnectorStore().get("email") is None
    assert store.credentials() is None
    assert store.smtp_credentials() is None


def test_update_creates_the_row_in_one_go(store):
    store.update(_account(enabled=True, provider="gmail"))
    row = ConnectorStore().get("email")
    assert row["kind"] == "email"
    assert row["enabled"] is True
    assert row["config"]["address"] == "me@example.com"
    assert row["config"]["provider"] == "gmail"
    assert "password" not in row["config"]
    assert ConnectorStore().secrets("email") == {"password": "pw"}


def test_update_coerces_and_clamps(store):
    store.update(
        {
            "imap_port": "143",
            "smtp_port": "465",
            "lookback_days": 0,
            "use_tls": 0,
            "enabled": 1,
        }
    )
    cfg = store.get()
    assert cfg["imap_port"] == 143
    assert cfg["smtp_port"] == 465
    assert cfg["lookback_days"] == 1
    assert cfg["use_tls"] is False
    assert cfg["enabled"] is True


def test_update_drops_unreadable_numbers_and_unknown_keys(store):
    store.update({"imap_port": "abc", "lookback_days": None, "imap_user": "x", "stats": {}})
    cfg = store.get()
    assert cfg["imap_port"] == 993
    assert cfg["lookback_days"] == 7
    assert "imap_user" not in cfg
    assert "stats" not in cfg


def test_blank_password_keeps_the_saved_one(store):
    store.update(_account(password="keepme"))
    store.update({"password": ""})
    store.update({"password": "   "})
    assert ConnectorStore().secrets("email") == {"password": "keepme"}
    store.update({"password": "new"})
    assert ConnectorStore().secrets("email") == {"password": "new"}


def test_get_and_public_never_return_the_password(store):
    store.update(_account(password="supersecret"))
    assert "password" not in store.get()
    pub = store.public()
    assert "password" not in pub
    assert pub["has_password"] is True
    assert "supersecret" not in json.dumps(pub)
    assert "supersecret" not in json.dumps(store.get())


def test_credentials_are_gated_on_the_one_enabled_flag(store):
    store.update(_account(enabled=False, imap_folder="Receipts", lookback_days=3))
    assert store.credentials() is None
    assert store.smtp_credentials() is None

    store.update({"enabled": True})
    assert store.credentials() == {
        "host": "imap.example.com",
        "port": 993,
        "user": "me@example.com",
        "password": "pw",
        "folder": "Receipts",
        "lookback_days": 3,
    }
    assert store.smtp_credentials() == {
        "host": "smtp.example.com",
        "port": 587,
        "user": "me@example.com",
        "password": "pw",
        "from_addr": "me@example.com",
        "to_addr": "me@example.com",
        "use_tls": True,
    }


def test_require_enabled_false_reads_a_paused_account(store):
    store.update(_account(enabled=False))
    assert store.credentials(require_enabled=False)["user"] == "me@example.com"
    assert store.smtp_credentials(require_enabled=False)["host"] == "smtp.example.com"


@pytest.mark.parametrize("missing", ["address", "password"], ids=["no-address", "no-password"])
def test_credentials_need_the_address_and_the_password(store, missing):
    fields = _account(enabled=True)
    del fields[missing]
    store.update(fields)
    assert store.credentials() is None
    assert store.smtp_credentials() is None


def test_receive_and_send_each_need_their_own_host(store):
    store.update(_account(enabled=True, smtp_host=""))
    assert store.credentials() is not None
    assert store.smtp_credentials() is None

    store.update({"smtp_host": "smtp.example.com", "imap_host": ""})
    assert store.credentials() is None
    assert store.smtp_credentials() is not None


def test_public_configured_is_independent_of_enabled(store):
    assert store.public()["configured"] is False
    store.update(_account(enabled=False))
    pub = store.public()
    assert pub["configured"] is True
    assert pub["enabled"] is False


def test_public_shape_is_flat(store):
    store.update(_account(provider="gmail"))
    assert set(store.public()) == {
        "enabled",
        "configured",
        "has_password",
        "provider",
        "address",
        "imap_host",
        "imap_port",
        "imap_folder",
        "lookback_days",
        "smtp_host",
        "smtp_port",
        "use_tls",
        "last_status",
        "last_error",
    }


def test_record_status_lives_in_state_and_is_redacted(store):
    store.update(_account())
    store.record_status("connect_error", "login failed for https://u:hunter2@mail.test/?token=zzz")
    state = ConnectorStore().get("email")["state"]
    assert state["last_status"] == "connect_error"
    assert "hunter2" not in state["last_error"]
    assert "zzz" not in state["last_error"]
    assert store.get()["last_status"] == "connect_error"

    store.record_status("ok")
    assert store.get()["last_status"] == "ok"
    assert store.get()["last_error"] is None


def test_a_settings_save_never_wipes_the_recorded_status(store):
    store.update(_account())
    store.record_status("auth_error", "bad password")
    store.update({"lookback_days": 30})
    assert store.get()["last_status"] == "auth_error"


def test_reset_erases_the_row_and_password(store):
    store.update(_account(enabled=True))
    store.reset()
    assert ConnectorStore().get("email") is None
    assert ConnectorStore().secrets("email") == {}
    assert store.public()["configured"] is False
    assert store.credentials() is None


def test_environment_never_seeds_the_connector(monkeypatch):
    monkeypatch.setenv("EMAIL_INGEST_ENABLED", "true")
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.env.test")
    monkeypatch.setenv("EMAIL_IMAP_USER", "env@example.com")
    monkeypatch.setenv("EMAIL_IMAP_PASSWORD", "env-pw")
    monkeypatch.setenv("CHANNEL_EMAIL_SMTP_HOST", "smtp.env.test")
    store = EmailConfigStore()
    assert store.get()["imap_host"] == ""
    assert store.get()["enabled"] is False
    assert store.credentials() is None
    assert ConnectorStore().get("email") is None
