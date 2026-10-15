"""
TestClient endpoint tests for backend/api/email_ingest_api.py.

Email is ONE connector — one ``connectors`` row (``email``), one on/off, one save. Covers:
- GET /api/email/config  — the one flat shape, no password leak, has_password / configured flags
- PUT /api/email/config  — the whole form in one request, _applied echo, keep-on-blank password,
  single transaction (row + enabled + password), the alert channel re-registered on the seam
- GET /api/email/status  — required keys, no extra keys
- POST /api/email/test-connection — ok=True / ok=False / missing-creds short-circuit
- POST /api/email/test-send — the moved /api/channels/email/test

All tests are hermetic (no real IMAP/SMTP) — the network is monkeypatched.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.db.connector_store import ConnectorStore
from backend.db.email_config_store import EmailConfigStore
from backend.main import app

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def store(db_isolated):
    """A fresh database for this test (the ``email`` row does not exist yet)."""
    return EmailConfigStore()


def _save(store: EmailConfigStore, **extra) -> None:
    """A fully configured, enabled account (override any field)."""
    store.update(
        {
            "enabled": True,
            "address": "user@example.com",
            "password": "s3cr3t",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_folder": "INBOX",
            "lookback_days": 7,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            **extra,
        }
    )


# ── GET /api/email/config ─────────────────────────────────────────────────────

_FLAT_SHAPE = {
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


def test_get_email_config_is_one_flat_shape(client, store):
    _save(store)
    resp = client.get("/api/email/config")
    assert resp.status_code == 200
    assert set(resp.json()) == _FLAT_SHAPE


def test_get_email_config_values(client, store):
    _save(store, provider="gmail", use_tls=False)
    body = client.get("/api/email/config").json()
    assert body["address"] == "user@example.com"
    assert body["provider"] == "gmail"
    assert body["imap_host"] == "imap.example.com"
    assert body["smtp_host"] == "smtp.example.com"
    assert body["smtp_port"] == 587
    assert body["use_tls"] is False
    assert body["enabled"] is True


def test_get_email_config_never_returns_the_password(client, store):
    _save(store)
    raw = client.get("/api/email/config").text
    assert "s3cr3t" not in raw
    body = json.loads(raw)
    assert "password" not in body
    assert body["has_password"] is True


def test_get_email_config_has_password_false_when_never_set(client, store):
    _save(store, password="")
    assert client.get("/api/email/config").json()["has_password"] is False


def test_get_email_config_configured_when_credentials_saved(client, store):
    _save(store)
    assert client.get("/api/email/config").json()["configured"] is True


def test_configured_survives_a_pause(client, store):
    """`configured` means "set up", independent of `enabled`: a Paused mailbox with real
    saved credentials is not the same state as one never set up at all (finding #8). The
    OPERATIONAL gate — credentials() — is what refuses while Paused."""
    _save(store, enabled=False)
    body = client.get("/api/email/config").json()
    assert body["configured"] is True
    assert body["enabled"] is False
    assert store.credentials() is None
    assert store.credentials(require_enabled=False) is not None


def test_get_email_config_not_configured_without_a_password(client, store):
    _save(store, password="", enabled=False)
    assert client.get("/api/email/config").json()["configured"] is False


def test_get_email_config_defaults_when_never_set_up(client, store):
    assert ConnectorStore().get("email") is None
    resp = client.get("/api/email/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["enabled"] is False
    assert body["has_password"] is False
    assert body["imap_port"] == 993
    assert body["smtp_port"] == 587
    assert body["imap_folder"] == "INBOX"
    # A read never creates the row.
    assert ConnectorStore().get("email") is None


# ── PUT /api/email/config ─────────────────────────────────────────────────────


def test_put_email_config_saves_the_whole_form_in_one_request(client, store):
    resp = client.put(
        "/api/email/config",
        json={
            "enabled": True,
            "provider": "gmail",
            "address": "me@gmail.com",
            "password": "app-pw",
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "imap_folder": "Receipts",
            "lookback_days": 14,
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "use_tls": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _FLAT_SHAPE | {"_applied"}
    assert body["address"] == "me@gmail.com"
    assert body["has_password"] is True
    assert body["configured"] is True
    assert "app-pw" not in resp.text

    # IMAP and SMTP both read the ONE identity.
    assert store.credentials() == {
        "host": "imap.gmail.com",
        "port": 993,
        "user": "me@gmail.com",
        "password": "app-pw",
        "folder": "Receipts",
        "lookback_days": 14,
    }
    assert store.smtp_credentials() == {
        "host": "smtp.gmail.com",
        "port": 587,
        "user": "me@gmail.com",
        "password": "app-pw",
        "from_addr": "me@gmail.com",
        "to_addr": "me@gmail.com",
        "use_tls": True,
    }


def test_put_email_config_keeps_nothing_sensitive_in_the_connectors_row(client, store):
    client.put("/api/email/config", json={"address": "me@gmail.com", "password": "app-pw"})
    row = ConnectorStore().get("email")
    assert "app-pw" not in json.dumps(row)
    assert ConnectorStore().secrets("email") == {"password": "app-pw"}


def test_put_email_config_echoes_applied_keys(client, store):
    _save(store)
    resp = client.put("/api/email/config", json={"enabled": False, "lookback_days": 14})
    body = resp.json()
    applied = set(body["_applied"])
    assert applied == {"enabled", "lookback_days"}


def test_put_email_config_persists_enabled_change(client, store):
    _save(store, enabled=True)
    client.put("/api/email/config", json={"enabled": False})
    assert client.get("/api/email/config").json()["enabled"] is False


def test_put_email_config_blank_password_keeps_the_saved_one(client, store):
    _save(store, password="original_pass")
    client.put("/api/email/config", json={"password": ""})
    client.put("/api/email/config", json={"password": "   "})
    assert ConnectorStore().secrets("email")["password"] == "original_pass"


def test_put_email_config_new_password_is_stored(client, store):
    _save(store, password="old")
    client.put("/api/email/config", json={"password": "newpass"})
    assert ConnectorStore().secrets("email")["password"] == "newpass"


def test_put_email_config_response_has_no_password(client, store):
    _save(store)
    body = client.put("/api/email/config", json={"enabled": True}).json()
    assert "password" not in body


def test_put_email_config_partial_update_only_changes_specified(client, store):
    _save(store, imap_folder="Receipts", lookback_days=30)
    client.put("/api/email/config", json={"use_tls": False})
    body = client.get("/api/email/config").json()
    assert body["imap_folder"] == "Receipts"
    assert body["lookback_days"] == 30
    assert body["use_tls"] is False


def test_put_email_config_lookback_days_minimum(client, store):
    _save(store)
    client.put("/api/email/config", json={"lookback_days": 0})
    assert client.get("/api/email/config").json()["lookback_days"] >= 1


def test_put_email_config_blank_text_fields_are_ignored(client, store):
    _save(store)
    client.put("/api/email/config", json={"address": "", "imap_host": "  ", "smtp_host": ""})
    body = client.get("/api/email/config").json()
    assert body["address"] == "user@example.com"
    assert body["imap_host"] == "imap.example.com"
    assert body["smtp_host"] == "smtp.example.com"


def test_put_email_config_ignores_the_retired_split_fields(client, store):
    """imap_user / imap_password / smtp_user / from_addr / to_addr no longer exist: one
    address and one password."""
    _save(store)
    client.put(
        "/api/email/config",
        json={"imap_user": "x@y.z", "imap_password": "nope", "from_addr": "a@b.c"},
    )
    assert store.credentials()["user"] == "user@example.com"
    assert ConnectorStore().secrets("email") == {"password": "s3cr3t"}


def test_put_email_config_registers_and_unregisters_the_alert_channel(client, store):
    from backend.core import channels as seam

    saved = seam.registered_channels()
    seam._CHANNELS.clear()
    try:
        client.put(
            "/api/email/config",
            json={
                "enabled": True,
                "address": "me@x.com",
                "password": "pw",
                "smtp_host": "smtp.x.com",
            },
        )
        assert "email" in seam.registered_channels()
        client.put("/api/email/config", json={"enabled": False})
        assert "email" not in seam.registered_channels()
    finally:
        seam._CHANNELS.clear()
        seam._CHANNELS.update(saved)


# ── GET /api/email/status ─────────────────────────────────────────────────────


def test_get_email_status_required_keys(client, store):
    _save(store)
    body = client.get("/api/email/status").json()
    required = {"configured", "enabled", "last_status", "last_error"}
    assert required == set(body.keys()), f"Unexpected keys: {set(body.keys()) ^ required}"


def test_get_email_status_no_password_field(client, store):
    _save(store)
    body = client.get("/api/email/status").json()
    assert "password" not in body
    assert "has_password" not in body


def test_get_email_status_reflects_recorded_status(client, store):
    _save(store)
    store.record_status("auth_error", "bad credentials")
    body = client.get("/api/email/status").json()
    assert body["enabled"] is True
    assert body["last_status"] == "auth_error"
    assert body["last_error"] == "bad credentials"

    store.record_status("ok")
    body = client.get("/api/email/status").json()
    assert body["last_status"] == "ok"
    assert body["last_error"] is None


def test_record_status_redacts_a_credential_bearing_error(client, store):
    _save(store)
    store.record_status("connect_error", "failed https://u:pw@mail.example.com/?token=abc123")
    err = client.get("/api/email/status").json()["last_error"]
    assert "pw@" not in err
    assert "abc123" not in err


def test_record_status_before_the_row_exists_is_a_no_op(store):
    store.record_status("ok")
    assert ConnectorStore().get("email") is None


# ── POST /api/email/test-connection ──────────────────────────────────────────


def test_test_connection_ok_true(client, store, monkeypatch):
    """When IMAP credentials pass, endpoint returns {ok: true}."""
    _save(store)
    monkeypatch.setattr(
        "backend.ingest.imap_client.test_connection",
        lambda creds: (True, ""),
    )
    resp = client.post("/api/email/test-connection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "error" not in body


def test_test_connection_ok_false_with_error(client, store, monkeypatch):
    """When IMAP credentials fail, endpoint returns {ok: false, error: '...'}."""
    _save(store)
    monkeypatch.setattr(
        "backend.ingest.imap_client.test_connection",
        lambda creds: (False, "authentication failed"),
    )
    resp = client.post("/api/email/test-connection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "authentication failed"


def test_test_connection_works_while_paused(client, store, monkeypatch):
    """The saved credentials of a Paused mailbox can still be verified."""
    _save(store, enabled=False)
    monkeypatch.setattr("backend.ingest.imap_client.test_connection", lambda creds: (True, ""))
    assert client.post("/api/email/test-connection").json() == {"ok": True}


@pytest.mark.parametrize(
    "fields",
    [
        {"address": "u@x.com", "password": "p"},  # no host
        {"imap_host": "imap.x.com", "password": "p"},  # no address
        {"imap_host": "imap.x.com", "address": "u@x.com"},  # no password
    ],
)
def test_test_connection_incomplete_short_circuits(client, store, fields):
    """A missing host / address / password: no IMAP connection is attempted."""
    store.update({"enabled": True, **fields})
    resp = client.post("/api/email/test-connection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "not fully configured" in body["error"].lower()


def test_test_connection_no_config_short_circuit(client, store):
    resp = client.post("/api/email/test-connection")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_test_connection_passes_correct_creds_to_imap(client, store, monkeypatch):
    """The creds dict forwarded to test_connection has the right host/port/user/password."""
    _save(
        store,
        imap_host="mail.host.com",
        imap_port=143,
        address="alice@host.com",
        password="alicepass",
    )
    captured: dict = {}

    def _capture(creds):
        captured.update(creds)
        return (True, "")

    monkeypatch.setattr("backend.ingest.imap_client.test_connection", _capture)
    client.post("/api/email/test-connection")
    assert captured["host"] == "mail.host.com"
    assert captured["port"] == 143
    assert captured["user"] == "alice@host.com"
    assert captured["password"] == "alicepass"


# ── POST /api/email/test-send ────────────────────────────────────────────────


def test_test_send_requires_a_saved_account(client, store):
    assert client.post("/api/email/test-send").status_code == 400


def test_test_send_delivers_to_the_accounts_own_address(client, store, monkeypatch):
    from backend.connectors.email_out import channel as email_chan

    sent = {}

    class _SMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, msg):
            sent["to"] = msg["To"]

    monkeypatch.setattr(email_chan.smtplib, "SMTP", _SMTP)
    _save(store, enabled=False)  # a saved-but-paused account can still be tested
    resp = client.post("/api/email/test-send")
    assert resp.status_code == 200
    assert resp.json() == {"name": "email", "result": {"sent": 1}}
    assert sent == {
        "host": "smtp.example.com",
        "login": ("user@example.com", "s3cr3t"),
        "to": "user@example.com",
    }


def test_test_send_reports_a_send_failure_without_raising(client, store, monkeypatch):
    from backend.connectors.email_out import channel as email_chan

    def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(email_chan.smtplib, "SMTP", _boom)
    _save(store)
    body = client.post("/api/email/test-send").json()
    assert "connection refused" in body["result"]["error"]


def test_the_retired_email_channel_routes_are_gone(client, store):
    assert client.put("/api/channels/email", json={"enabled": True}).status_code == 404
    assert client.post("/api/channels/email/test").status_code == 404
