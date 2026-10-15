"""REST /api/channels — list / configure / test round-trip + Notifier fan-out.

TestClient on a bare app with just the router mounted (the app-wide require_auth is
exercised elsewhere). The webhook is a row of the ``connectors`` table, isolated per test.
HTTP is stubbed — no real send. Also verifies the Notifier still fans out to a registered
webhook channel WITHOUT breaking the in-app / telegram channels. Email's alert channel is the
Email connector's (tests/test_email_ingest_api.py, tests/test_outbound_channels.py).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    app = FastAPI()
    from backend.api.channels import router

    app.include_router(router)
    return TestClient(app)


# ── list / configure / secret-absent round-trip ──────────────────────────────


def test_list_defaults(client):
    res = client.get("/api/channels")
    assert res.status_code == 200
    names = {c["name"] for c in res.json()["channels"]}
    assert names == {"webhook"}
    for c in res.json()["channels"]:
        assert c["active"] is False
        assert c["configured"] is False


def test_configure_and_url_and_secret_never_returned(client):
    res = client.put(
        "/api/channels/webhook",
        json={"url": "https://hooks.example.com/T0/B0/xyz", "secret": "supersecret"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["has_url"] is True
    assert body["has_secret"] is True
    assert body["url_display"] == "hooks.example.com"
    assert "url" not in body
    assert "secret" not in body
    assert "supersecret" not in json.dumps(body)
    assert "xyz" not in json.dumps(body)
    assert body["configured"] is True

    # GET still hides both.
    listed = client.get("/api/channels").json()["channels"]
    web = next(c for c in listed if c["name"] == "webhook")
    assert "url" not in web
    assert "secret" not in web
    assert web["has_url"] is True
    assert web["has_secret"] is True
    assert "xyz" not in json.dumps(web)


def test_blank_secret_and_url_on_update_are_ignored(client):
    client.put("/api/channels/webhook", json={"url": "https://x.test/h", "secret": "saved"})
    # Re-save with a blank url and secret — both stay; another field is updated.
    client.put(
        "/api/channels/webhook",
        json={"url": "", "secret": "", "secret_header_name": "X-Sig"},
    )
    listed = client.get("/api/channels").json()["channels"]
    web = next(c for c in listed if c["name"] == "webhook")
    assert web["has_secret"] is True
    assert web["has_url"] is True
    assert web["url_display"] == "x.test"
    assert web["secret_header_name"] == "X-Sig"


def test_unknown_channel_404(client):
    assert client.put("/api/channels/slack", json={"enabled": True}).status_code == 404
    assert client.post("/api/channels/slack/test").status_code == 404
    # Email is the Email connector's now, not a managed channel.
    assert client.put("/api/channels/email", json={"enabled": True}).status_code == 404
    assert client.post("/api/channels/email/test").status_code == 404


def test_test_endpoint_requires_configured(client):
    # Not configured yet → 400.
    assert client.post("/api/channels/webhook/test").status_code == 400


def test_test_endpoint_sends(client, monkeypatch):
    from backend.connectors.webhook import channel as web_chan

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getcode(self):
            return 200

    monkeypatch.setattr(web_chan, "pinned_urlopen", lambda url, **kw: _Resp())
    client.put("/api/channels/webhook", json={"url": "https://x.test/hook"})
    res = client.post("/api/channels/webhook/test")
    assert res.status_code == 200
    assert res.json()["result"] == {"sent": 1, "status": 200}


# ── Notifier fan-out unaffected (in-app + telegram still work) ────────────────


def test_notifier_fans_out_to_registered_webhook(monkeypatch, db_isolated):
    """A registered webhook channel delivers via notify() without breaking others."""
    from backend.core import channels as seam
    from backend.services.notify import notify

    # Isolate the registry: keep the import-registered in_app channel, add a fake telegram.
    saved = seam.registered_channels()
    seam._CHANNELS.clear()
    try:
        # Re-register in_app (the real one is registered at notify import; reinstall it).
        from backend.services.notify import _in_app_send

        seam.register_channel("in_app", _in_app_send)

        telegram_calls = []

        async def fake_telegram(msg):
            telegram_calls.append(msg)
            return {"sent": 1}

        seam.register_channel("telegram", fake_telegram)

        # Configure + register the webhook channel via its adapter.
        from backend.connectors.webhook import channel as web_chan
        from backend.db.channel_config_store import ChannelConfigStore

        ChannelConfigStore().update({"url": "https://x.test/h", "enabled": True})

        web_calls = []

        class _Resp:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def getcode(self):
                return 202

        def _urlopen(url, **kwargs):
            web_calls.append(url)
            return _Resp()

        monkeypatch.setattr(web_chan, "pinned_urlopen", _urlopen)
        assert web_chan.register_if_active() is True

        result = asyncio.run(notify("portfolio nudge"))

        # All three channels delivered; none broke the others.
        assert result["in_app"]["sent"] == 1
        assert result["telegram"] == {"sent": 1}
        assert result["webhook"] == {"sent": 1, "status": 202}
        assert telegram_calls == ["portfolio nudge"]
        assert web_calls == ["https://x.test/h"]
    finally:
        seam._CHANNELS.clear()
        seam._CHANNELS.update(saved)


# ── GET /api/notify-channels — the live fan-out targets (notify "Send to" picker) ──


def test_notify_channels_lists_live_registry(client):
    """The endpoint mirrors core.channels.registered_channels() (sorted) — the actual
    targets a notify task reaches, NOT the managed-config set from /api/channels."""
    from backend.core import channels as seam

    saved = seam.registered_channels()
    seam._CHANNELS.clear()
    try:

        async def _noop(_msg):
            return {"sent": 0}

        # in_app is always live in the real app; telegram only when the connector boots.
        seam.register_channel("in_app", _noop)
        seam.register_channel("telegram", _noop)

        res = client.get("/api/notify-channels")
        assert res.status_code == 200
        names = res.json()["channels"]
        assert names == ["in_app", "telegram"]  # sorted, deterministic

        # A channel that goes away (connector disabled) drops off the list.
        seam.unregister_channel("telegram")
        assert client.get("/api/notify-channels").json()["channels"] == ["in_app"]
    finally:
        seam._CHANNELS.clear()
        seam._CHANNELS.update(saved)


def test_notify_module_import_registers_in_app():
    """Regression for the boot wiring: importing services.notify registers the always-on
    in_app channel. main.py imports it up front so /api/notify-channels lists in_app on a
    fresh boot — before any notification has fired (else the 'Send to' picker hides it)."""
    import importlib

    from backend.core import channels as seam

    saved = seam.registered_channels()
    seam.unregister_channel("in_app")
    try:
        import backend.services.notify as notify_mod

        importlib.reload(notify_mod)
        assert "in_app" in seam.registered_channels()
    finally:
        seam._CHANNELS.clear()
        seam._CHANNELS.update(saved)
