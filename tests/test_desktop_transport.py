"""Desktop transport guards the owned origin and validates native credentials."""

from __future__ import annotations

import io
import json
import secrets

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from backend.api._deps import require_auth
from backend.api.desktop_transport import (
    SESSION_HEADER,
    DesktopSession,
    DesktopTransportMiddleware,
)
from backend.cli.desktop import configure_environment, read_bootstrap

TOKEN = "x" * 48
INSTANCE = "desktop-test-instance"
ORIGIN = "http://127.0.0.1:8097"


@pytest.fixture
def client(monkeypatch):
    import backend.api._deps as deps

    class ConfiguredAuth:
        def is_configured(self):
            return True

        def api_token(self):
            return None

        def session_key_bytes(self):
            return b"session-key"

        def auth_disabled(self):
            return False

        def trust_loopback(self):
            return False

    monkeypatch.setattr(deps, "AuthStore", ConfiguredAuth)
    monkeypatch.setenv("AUTH_TRUST_LOOPBACK", "false")
    app = FastAPI(dependencies=[Depends(require_auth)])
    app.add_middleware(DesktopTransportMiddleware, session=DesktopSession(TOKEN, INSTANCE, 8097))

    @app.get("/api/private")
    @app.get("/assets/app.js")
    def private(request: Request):
        return {"authenticated": request.state.desktop_authenticated}

    @app.get("/api/health")
    @app.get("/api/mcp-oauth/callback")
    @app.get("/api/lunch-flow/oauth/callback")
    def public():
        return {"ok": True}

    return TestClient(app, base_url=ORIGIN)


@pytest.mark.parametrize("path", ["/api/private", "/assets/app.js"])
def test_configured_routes_require_book_or_desktop_auth(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={SESSION_HEADER: "wrong"}).status_code == 401


def test_transport_credential_satisfies_configured_book_auth(client):
    response = client.get("/api/private", headers={SESSION_HEADER: TOKEN})
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.parametrize("origin", ["https://attacker.test", "null", "http://localhost:8097"])
def test_untrusted_origin_refused_even_with_valid_token(client, origin):
    response = client.get("/api/private", headers={SESSION_HEADER: TOKEN, "Origin": origin})
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("host", ["attacker.test:8097", "localhost:8097", "127.0.0.1:8080"])
def test_exact_owned_host_required(client, host):
    assert (
        client.get("/api/private", headers={SESSION_HEADER: TOKEN, "Host": host}).status_code == 403
    )


def test_duplicate_headers_are_rejected(client):
    response = client.get(
        "/api/private", headers=[(SESSION_HEADER, TOKEN), (SESSION_HEADER, TOKEN)]
    )
    assert response.status_code == 401
    response = client.get("/api/private", headers=[("Origin", ORIGIN), ("Origin", ORIGIN)])
    assert response.status_code == 403


def test_non_ascii_credential_is_unauthorized_instead_of_error(client):
    response = client.get("/api/private", headers=[(SESSION_HEADER.encode(), b"\xff")])
    assert response.status_code == 401


def test_invalid_cookie_and_bearer_do_not_authenticate_browser(client):
    response = client.get(
        "/api/private",
        headers={"Authorization": "Bearer browser-token", "Cookie": "onw_session=cookie"},
    )
    assert response.status_code == 401


def test_public_probes_and_callbacks_keep_origin_guard(client):
    for path in ("/api/health", "/api/mcp-oauth/callback", "/api/lunch-flow/oauth/callback"):
        assert client.get(path).status_code == 200
        assert client.post(path).status_code == 405
        assert client.get(path, headers={SESSION_HEADER: "wrong"}).status_code == 401
    assert (
        client.get(
            "/api/mcp-oauth/callback", headers={"Origin": "https://attacker.test"}
        ).status_code
        == 403
    )


def test_bootstrap_does_not_adopt_ambient_book(monkeypatch, tmp_path):
    for key in ("DATA_DIR", "CACHE_DIR", "LOGS_DIR", "ONW_DATABASE_URL", "AUTH_API_TOKEN"):
        monkeypatch.setenv(key, "do-not-adopt")
    for key in (
        "ONW_ROOT",
        "ONW_DESKTOP_MODE",
        "ONW_DESKTOP_INSTANCE_ID",
        "HOST",
        "PORT",
        "CORS_ORIGINS",
        "AUTH_DISABLED",
        "AUTH_TRUST_LOOPBACK",
        "ONW_QA_MODE",
    ):
        monkeypatch.setenv(key, "old")
    config = read_bootstrap(
        io.StringIO(
            json.dumps(
                {
                    "token": secrets.token_urlsafe(32),
                    "instance_id": INSTANCE,
                    "root": str(tmp_path),
                    "qa_mode": True,
                }
            )
            + "\n"
        )
    )
    configure_environment(config, 8097)
    import os

    assert os.environ["ONW_ROOT"] == str(tmp_path)
    assert os.environ["ONW_QA_MODE"] == "1"
    assert os.environ["PORT"] == "8097"
    assert os.environ["AUTH_TRUST_LOOPBACK"] == "true"
    assert "ONW_DATABASE_URL" not in os.environ
    assert "DATA_DIR" not in os.environ


@pytest.mark.parametrize("value", [{}, {"token": "short"}, [], None, {"root": "relative"}])
def test_invalid_bootstrap_fails_closed(value):
    with pytest.raises(ValueError, match="desktop"):
        read_bootstrap(io.StringIO(json.dumps(value) + "\n"))
