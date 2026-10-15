"""email-out + webhook channel adapters — stubbed SMTP / HTTP, error isolation.

No real email is sent and no real HTTP request is made: ``smtplib.SMTP`` and
``pinned_urlopen`` are monkeypatched. Verifies the adapter:
  * builds + sends correctly (right recipient / payload / header) and returns {sent:1},
  * catches a send failure and returns {"error": ...} (never raises into the Notifier),
  * refuses when not configured,
  * register_if_active only registers when enabled + configured.

Email's sender is the Email connector's single account: the address is the SMTP login, the
From and the alert recipient; one password; one ``enabled`` flag.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.connectors.email_out import channel as email_chan
from backend.connectors.webhook import channel as web_chan
from backend.core import channels as seam
from backend.db.channel_config_store import ChannelConfigStore
from backend.db.email_config_store import EmailConfigStore


def _account(**overrides) -> dict:
    return {
        "address": "me@example.com",
        "password": "pw",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        **overrides,
    }


@pytest.fixture
def email_store():
    return EmailConfigStore()


@pytest.fixture
def cfg_store():
    return ChannelConfigStore()


@pytest.fixture
def clean_seam():
    saved = seam.registered_channels()
    seam._CHANNELS.clear()
    yield
    seam._CHANNELS.clear()
    seam._CHANNELS.update(saved)


# ── email-out ────────────────────────────────────────────────────────────────


class _FakeSMTP:
    """Records what was sent; stands in for smtplib.SMTP as a context manager."""

    last = {}

    def __init__(self, host, port, timeout=None):
        _FakeSMTP.last = {"host": host, "port": port, "started_tls": False, "logged_in": None}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        _FakeSMTP.last["started_tls"] = True

    def login(self, user, password):
        _FakeSMTP.last["logged_in"] = (user, password)

    def send_message(self, msg):
        _FakeSMTP.last["to"] = msg["To"]
        _FakeSMTP.last["from"] = msg["From"]
        # Emails are now multipart/alternative (plain fallback + rendered HTML). Capture both.
        if msg.is_multipart():
            plain = msg.get_body(preferencelist=("plain",))
            html = msg.get_body(preferencelist=("html",))
            _FakeSMTP.last["body"] = plain.get_content().strip() if plain else ""
            _FakeSMTP.last["html"] = html.get_content().strip() if html else ""
        else:
            _FakeSMTP.last["body"] = msg.get_content().strip()
            _FakeSMTP.last["html"] = ""


def test_email_send_builds_and_sends(email_store, monkeypatch):
    email_store.update(_account(enabled=True, use_tls=True))
    monkeypatch.setattr(email_chan.smtplib, "SMTP", _FakeSMTP)
    result = asyncio.run(email_chan._send("**hello** €100 [FT](https://ft.com)"))
    assert result == {"sent": 1}
    # One identity: the address is the recipient, the From and the SMTP login.
    assert _FakeSMTP.last["to"] == "me@example.com"
    assert _FakeSMTP.last["from"] == "me@example.com"
    assert _FakeSMTP.last["started_tls"] is True
    assert _FakeSMTP.last["logged_in"] == ("me@example.com", "pw")
    # Plain part is the verbatim markdown (text-client fallback)
    assert _FakeSMTP.last["body"] == "**hello** €100 [FT](https://ft.com)"
    # HTML alternative renders the markdown
    assert "<strong>hello</strong>" in _FakeSMTP.last["html"]
    assert '<a href="https://ft.com">FT</a>' in _FakeSMTP.last["html"]


def test_email_send_isolates_error(email_store, monkeypatch):
    email_store.update(_account(enabled=True))

    def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(email_chan.smtplib, "SMTP", _boom)
    result = asyncio.run(email_chan._send("x"))  # must NOT raise
    assert "error" in result
    assert "connection refused" in result["error"]


def test_email_refuses_when_unconfigured(email_store):
    result = asyncio.run(email_chan._send("x"))
    assert result == {"error": "email channel not configured"}


def test_email_send_needs_the_password(email_store):
    email_store.update(_account(password=""))
    assert asyncio.run(email_chan._send("x")) == {"error": "email channel not configured"}


def test_email_send_probes_a_saved_but_paused_connector(email_store, monkeypatch):
    """The test-send endpoint calls ``_send`` directly: a saved-but-paused account is
    verifiable (configured, not enabled), while the seam stays unregistered."""
    email_store.update(_account(enabled=False))
    monkeypatch.setattr(email_chan.smtplib, "SMTP", _FakeSMTP)
    assert asyncio.run(email_chan._send("x")) == {"sent": 1}


def test_send_email_succeeds_when_configured_and_enabled(email_store, monkeypatch):
    """``send_email`` (the arbitrary-recipient tool path, distinct from ``_send``'s
    fixed-recipient notification) sends once BOTH configured and enabled."""
    email_store.update(_account(enabled=True))
    monkeypatch.setattr(email_chan.smtplib, "SMTP", _FakeSMTP)
    result = email_chan.send_email("boss@x.com", "Update", "body")
    assert result == {"ok": True, "sent": 1, "to": "boss@x.com"}
    assert _FakeSMTP.last["to"] == "boss@x.com"
    assert _FakeSMTP.last["from"] == "me@example.com"


def test_send_email_refuses_when_unconfigured(email_store):
    result = email_chan.send_email("boss@x.com", "Update", "body")
    assert result == {"ok": False, "error": "email send (SMTP) is not configured"}


def test_send_email_refuses_gracefully_when_paused(email_store, monkeypatch):
    """SEC: tool_permissions decides whether Lucius may ATTEMPT this call; the connector's
    own Pause must still stop it from actually sending — otherwise an `always_allow` grant
    on `send_email` could deliver mail through a connector the user believes is fully off.
    Fully configured but ``enabled: False`` (Paused) must refuse BEFORE touching SMTP."""
    email_store.update(_account(enabled=False))

    def _boom(*a, **k):
        raise AssertionError("SMTP must not be contacted while the connector is paused")

    monkeypatch.setattr(email_chan.smtplib, "SMTP", _boom)
    monkeypatch.setattr(email_chan.smtplib, "SMTP_SSL", _boom)
    result = email_chan.send_email("boss@x.com", "Update", "body")
    assert result["ok"] is False
    assert "paused" in result["error"].lower()


def test_email_register_if_active(email_store, clean_seam):
    # Configured but not enabled → not registered.
    email_store.update(_account())
    assert email_chan.register_if_active() is False
    assert "email" not in seam.registered_channels()
    # Enable → registered.
    email_store.update({"enabled": True})
    assert email_chan.register_if_active() is True
    assert "email" in seam.registered_channels()


def test_pausing_the_connector_unregisters_the_seam_so_notify_wont_deliver(
    email_store, clean_seam, monkeypatch
):
    """SEC (finding #2): `services.connectors.set_connector_enabled("email", False)` — the
    choke `PATCH /api/connectors/email` and the AI `set_connector_enabled` tool both run —
    must ALSO unregister the seam, not just flip the store flag. Before the fix, pausing the
    connector this way stopped `send_email` (which checks the store itself) but left
    `notify`'s SEPARATE email leg registered, so `notify(channels=["email"])` still sent
    real mail through a connector the operator believed was fully off. Mirrors
    `test_send_email_refuses_gracefully_when_paused`'s SMTP-must-not-be-contacted shape."""
    from backend.services import connectors as connectors_service
    from backend.services.notify import notify

    email_store.update(_account())
    connectors_service.set_connector_enabled("email", True)
    assert "email" in seam.registered_channels()

    connectors_service.set_connector_enabled("email", False)
    assert "email" not in seam.registered_channels()

    def _boom(*a, **k):
        raise AssertionError("SMTP must not be contacted while the connector is paused")

    monkeypatch.setattr(email_chan.smtplib, "SMTP", _boom)
    monkeypatch.setattr(email_chan.smtplib, "SMTP_SSL", _boom)

    result = asyncio.run(notify("hi there", channels=["email"]))
    assert result == {"email": {"skipped": "unknown_channel"}}

    # Resume — the seam re-registers and notify reaches it again.
    connectors_service.set_connector_enabled("email", True)
    assert "email" in seam.registered_channels()


def test_saving_the_email_form_reconciles_the_seam(email_store, clean_seam):
    """The ONE save re-registers (or unregisters) the alert channel — no second request."""
    from backend.services import connectors as connectors_service

    connectors_service.save_email_config(_account(enabled=True))
    assert "email" in seam.registered_channels()

    connectors_service.save_email_config({"enabled": False})
    assert "email" not in seam.registered_channels()


def test_removing_the_connector_unregisters_the_seam(email_store, clean_seam):
    from backend.services import connectors as connectors_service

    connectors_service.save_email_config(_account(enabled=True))
    assert "email" in seam.registered_channels()

    connectors_service.remove_connector("email")
    assert "email" not in seam.registered_channels()
    assert email_store.smtp_credentials(require_enabled=False) is None


# ── webhook ──────────────────────────────────────────────────────────────────


def test_pausing_resuming_and_removing_the_webhook_follows_the_seam(cfg_store, clean_seam):
    """Pause used to leave the webhook registered on the seam until the next restart, so alerts
    kept reaching a URL the operator had switched off."""
    from backend.services import connectors as connectors_service

    cfg_store.update({"url": "https://x.test/hook"})
    connectors_service.set_connector_enabled("webhook", True)
    assert "webhook" in seam.registered_channels()

    connectors_service.set_connector_enabled("webhook", False)
    assert "webhook" not in seam.registered_channels()

    connectors_service.set_connector_enabled("webhook", True)
    assert "webhook" in seam.registered_channels()

    connectors_service.remove_connector("webhook")
    assert "webhook" not in seam.registered_channels()


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return 200


def test_webhook_send_posts_payload_and_header(cfg_store, monkeypatch):
    cfg_store.update(
        {
            "url": "https://x.test/hook",
            "secret": "shh",
            "secret_header_name": "X-Token",
            "enabled": True,
        },
    )
    captured = {}

    def _fake_urlopen(url, *, headers=None, data=None, timeout=None, owner_configured=False):
        import json as _json

        captured["url"] = url
        captured["data"] = _json.loads(data.decode())
        captured["header"] = (headers or {}).get("X-Token")
        captured["owner"] = owner_configured
        return _FakeResp()

    monkeypatch.setattr(web_chan, "pinned_urlopen", _fake_urlopen)
    result = asyncio.run(web_chan._send("net worth up €5"))
    assert result == {"sent": 1, "status": 200}
    assert captured["url"] == "https://x.test/hook"
    assert captured["data"] == {"text": "net worth up €5"}  # verbatim body
    assert captured["header"] == "shh"
    assert captured["owner"] is True  # owner-typed URL: pinned and SSRF-validated, LAN allowed


def test_webhook_send_isolates_error(cfg_store, monkeypatch):
    cfg_store.update({"url": "https://x.test/hook", "enabled": True})

    def _boom(url, **kwargs):
        raise OSError("dns failure")

    monkeypatch.setattr(web_chan, "pinned_urlopen", _boom)
    result = asyncio.run(web_chan._send("x"))  # must NOT raise
    assert "error" in result
    assert "dns failure" in result["error"]


def test_webhook_refuses_when_unconfigured(cfg_store):
    result = asyncio.run(web_chan._send("x"))
    assert result == {"error": "webhook channel not configured"}


def test_webhook_register_if_active(cfg_store, clean_seam):
    cfg_store.update({"url": "https://x.test/hook"})
    assert web_chan.register_if_active() is False
    cfg_store.update({"enabled": True})
    assert web_chan.register_if_active() is True
    assert "webhook" in seam.registered_channels()
