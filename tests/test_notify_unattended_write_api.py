"""Unattended-write notify toggle API — GET/PUT /api/config/notify-unattended-write.

Mirrors tests/test_script_skills_api.py's structure and hermeticity: AppConfigStore is
DB-backed (a "app_config" row in the runtime_state table) and tests/conftest.py's autouse
per-test table clear already gives every test a fresh row, so the live data dir is never
touched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config


@pytest.fixture
def cfg_env(monkeypatch):
    for key in (
        "APP_NAME",
        "APP_ACCENT_COLOR",
        "APP_LOGO_URL",
        "AUTH_DISABLED",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    yield monkeypatch
    config._settings = None


def _client(remote: bool = False) -> TestClient:
    from fastapi import Depends, FastAPI

    from backend.api._deps import require_auth
    from backend.api.config import router as config_router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(config_router)

    if remote:
        return TestClient(app, client=("203.0.113.7", 9999))
    return TestClient(app)


def test_get_notify_unattended_write_defaults_on(cfg_env):
    """A fresh instance returns enabled=true — default ON (preserves today's behavior)."""
    r = _client().get("/api/config/notify-unattended-write")
    assert r.status_code == 200
    assert r.json() == {"enabled": True}


def test_put_notify_unattended_write_disable(cfg_env):
    r = _client().put("/api/config/notify-unattended-write", json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


def test_put_notify_unattended_write_round_trips_via_get(cfg_env):
    client = _client()
    client.put("/api/config/notify-unattended-write", json={"enabled": False})
    r = client.get("/api/config/notify-unattended-write")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    client.put("/api/config/notify-unattended-write", json={"enabled": True})
    r2 = client.get("/api/config/notify-unattended-write")
    assert r2.json()["enabled"] is True


def test_notify_unattended_write_flag_absent_from_public_branding(cfg_env):
    """The flag must never ride the auth-exempt GET /api/config branding endpoint."""
    client = _client()
    client.put("/api/config/notify-unattended-write", json={"enabled": False})
    branding = client.get("/api/config").json()
    assert set(branding.keys()) == {"app_name", "accent_color", "logo_url"}


def test_get_notify_unattended_write_gated_remote(cfg_env):
    r = _client(remote=True).get("/api/config/notify-unattended-write")
    assert r.status_code == 401


def test_put_notify_unattended_write_gated_remote(cfg_env):
    r = _client(remote=True).put("/api/config/notify-unattended-write", json={"enabled": False})
    assert r.status_code == 401
