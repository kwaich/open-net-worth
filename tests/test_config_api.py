"""P3-ON / ON3 — GET/PUT /api/config + the auth-exempt GET.

Hermetic: points the AppConfigStore at a tmp file, resets the Settings singleton,
clears AUTH_*/APP_* env. "remote" is simulated by overriding the TestClient host to
a public address — used to prove GET /api/config is reachable WITHOUT auth (exempt),
the load-bearing white-label requirement (the login screen must brand itself).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config


@pytest.fixture
def cfg_env(monkeypatch, db_isolated):
    """AppConfigStore is DB-backed (runtime_state/"app_config") — db_isolated gives this
    test its own fresh database."""
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
    """A minimal app gated EXACTLY like main.py (the single app-wide require_auth
    dependency + the same exempt set) with only the config router mounted. This
    exercises the real gate/exemption without the SPA catch-all mount shadowing the
    freshly-added route. The orchestrator wires the router into main.py for real."""
    from fastapi import Depends, FastAPI

    from backend.api._deps import require_auth
    from backend.api.config import router as config_router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(config_router)

    if remote:
        return TestClient(app, client=("203.0.113.7", 9999))
    return TestClient(app)


def test_get_config_returns_defaults(cfg_env):
    r = _client().get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body == {"app_name": "Open Net Worth", "accent_color": "", "logo_url": ""}


def test_get_config_env_seeded(cfg_env):
    cfg_env.setenv("APP_NAME", "Acme Wealth")
    cfg_env.setenv("APP_ACCENT_COLOR", "#112233")
    config._settings = None
    body = _client().get("/api/config").json()
    assert body["app_name"] == "Acme Wealth"
    assert body["accent_color"] == "#112233"


def test_put_config_round_trip(cfg_env):
    client = _client()
    r = client.put("/api/config", json={"app_name": "My Money", "accent_color": "#abcdef"})
    assert r.status_code == 200
    assert r.json()["app_name"] == "My Money"
    # Read-back via a fresh GET.
    body = client.get("/api/config").json()
    assert body["app_name"] == "My Money"
    assert body["accent_color"] == "#abcdef"


def test_put_config_bad_accent_rejected(cfg_env):
    client = _client()
    for bad in ("red", "#zzz", "#12345", "3b86dd"):
        r = client.put("/api/config", json={"accent_color": bad})
        assert r.status_code == 422, bad
    # The empty string is a valid "reset to default accent".
    assert client.put("/api/config", json={"accent_color": ""}).status_code == 200


def test_get_config_reachable_unauthenticated_remote(cfg_env):
    """The crux: GET /api/config is exempt, so even a REMOTE request to an
    UNCONFIGURED instance (which the gate would otherwise 401) reaches it — the
    login screen needs the brand before any credential exists."""
    r = _client(remote=True).get("/api/config")
    assert r.status_code == 200
    assert r.json()["app_name"]


def test_put_config_gated_remote_unconfigured(cfg_env):
    """The WRITE is NOT exempt: a remote unconfigured request is refused."""
    r = _client(remote=True).put("/api/config", json={"app_name": "Hijack"})
    assert r.status_code == 401
