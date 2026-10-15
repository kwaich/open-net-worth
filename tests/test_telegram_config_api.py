"""REST smoke for the Telegram config endpoint (GET/PUT /api/telegram/config).

TestClient on a bare app with ONLY the integrations router mounted. The store is isolated
to tmp_path and ONW_DISABLE_TELEGRAM=1 so the PUT's hot-reload is a no-op (never starts a
competing poller in CI). Verifies: the bot token is write-only (never returned), a blank
token keeps the saved one, and a config change persists.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(paths, "TELEGRAM_CONFIG_JSON", tmp_path / "telegram_config.json")
    # Make the hot-reload a no-op (a non-primary boot never starts the poller).
    monkeypatch.setenv("ONW_DISABLE_TELEGRAM", "1")

    app = FastAPI()
    from backend.api.integrations import router

    app.include_router(router)
    return TestClient(app)


def test_get_default_config_no_token(client):
    res = client.get("/api/telegram/config")
    assert res.status_code == 200
    body = res.json()
    assert body["has_bot_token"] is False
    assert body["enabled"] is False
    assert body["configured"] is False
    assert "bot_token" not in body


def test_put_sets_config_and_hides_token(client):
    res = client.put(
        "/api/telegram/config",
        json={
            "enabled": True,
            "bot_token": "123:abc",
            "allowed_chat_ids": "111, 222",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["has_bot_token"] is True
    assert body["enabled"] is True
    assert body["allowed_chat_ids"] == ["111", "222"]
    assert "webhook_url" not in body  # removed 2026-08-22 — was never a working feature
    assert "bot_token" not in body
    # The raw token NEVER comes back.
    assert "123:abc" not in json.dumps(body)


def test_put_blank_token_keeps_saved(client):
    client.put("/api/telegram/config", json={"bot_token": "keep:me", "enabled": True})
    # A subsequent save with no token (or blank) must keep the saved one.
    res = client.put("/api/telegram/config", json={"allowed_chat_ids": ["9"]})
    assert res.status_code == 200
    assert res.json()["has_bot_token"] is True
    assert res.json()["allowed_chat_ids"] == ["9"]


def test_status_reflects_store(client):
    client.put("/api/telegram/config", json={"bot_token": "t", "enabled": True})
    res = client.get("/api/integrations/telegram/status")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["enabled"] is True
    assert "open_access" not in body  # removed 2026-08-22 — meant "open" under the old
    # fail-open contract; an empty allowlist is now closed, not open, and nothing consumed it
    assert body["allowed_count"] == 0
