"""Desktop and browser share book auth without sharing native host privileges."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from backend.api._deps import require_auth
from backend.api.auth import router as auth_router
from backend.api.desktop_api import router as desktop_router
from backend.api.desktop_transport import SESSION_HEADER, DesktopSession, DesktopTransportMiddleware
from backend.core import config, security
from backend.db.auth_store import AuthStore

TOKEN = "x" * 48
ORIGIN = "http://127.0.0.1:8097"


@pytest.fixture
def app(db_isolated, monkeypatch, tmp_path):
    for key in ("AUTH_PASSWORD", "AUTH_PASSWORD_HASH", "AUTH_API_TOKEN", "AUTH_SESSION_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("AUTH_TRUST_LOOPBACK", "true")
    config._settings = None
    app = FastAPI(dependencies=[Depends(require_auth)])
    app.add_middleware(
        DesktopTransportMiddleware, session=DesktopSession(TOKEN, "test-instance", 8097)
    )
    app.include_router(auth_router)
    app.include_router(desktop_router)

    @app.get("/api/private")
    def private(request: Request):
        return {"desktop": request.state.desktop_authenticated}

    (tmp_path / "index.html").write_text("<html>Open Net Worth</html>")
    app.mount("/", StaticFiles(directory=tmp_path, html=True))
    yield app
    config._settings = None


def configured_book():
    store = AuthStore()
    store.set_password_hash(security.hash_password("synthetic-password"))
    store.update_auth_settings(trust_loopback=False)
    return store


def test_browser_loads_ui_and_uses_default_local_access(app):
    client = TestClient(app, base_url=ORIGIN)
    assert client.get("/").status_code == 200
    assert client.get("/api/auth/status").json() == {"required": False, "authenticated": True}
    assert client.get("/api/private").json() == {"desktop": False}
    assert client.get("/api/desktop/status").status_code == 403


def test_browser_existing_password_login_cookie_and_logout(app):
    configured_book()
    client = TestClient(app, base_url=ORIGIN)
    assert client.get("/").status_code == 200
    assert client.get("/api/auth/status").json() == {"required": True, "authenticated": False}
    assert client.get("/api/private").status_code == 401
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    response = client.post("/api/auth/login", json={"password": "synthetic-password"})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/private").json() == {"desktop": False}
    assert client.get("/api/desktop/status").status_code == 403
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/private").status_code == 401
    assert client.get("/api/private", headers={SESSION_HEADER: TOKEN}).json() == {"desktop": True}


def test_existing_bearer_is_accepted_without_native_privileges(app):
    store = configured_book()
    client = TestClient(
        app, base_url=ORIGIN, headers={"Authorization": f"Bearer {store.api_token()}"}
    )
    assert client.get("/api/private").json() == {"desktop": False}
    assert client.get("/api/desktop/status").status_code == 403
    assert client.get("/api/private", headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize("path", ["/", "/api/auth/status", "/api/private", "/api/desktop/status"])
@pytest.mark.parametrize(
    "headers",
    [
        {SESSION_HEADER: ""},
        {SESSION_HEADER: "wrong"},
        [(SESSION_HEADER, TOKEN), (SESSION_HEADER, TOKEN)],
    ],
)
def test_present_invalid_desktop_credential_never_falls_back(app, path, headers):
    AuthStore().update_auth_settings(auth_disabled=True)
    assert TestClient(app, base_url=ORIGIN).get(path, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "null"},
        {"Origin": "https://evil.test"},
        {"Host": "evil.test:8097"},
        {"Host": "127.0.0.1:8080"},
    ],
)
def test_browser_keeps_host_and_origin_guards(app, headers):
    assert TestClient(app, base_url=ORIGIN).get("/api/private", headers=headers).status_code == 403


def test_network_client_still_requires_book_auth_when_localhost_is_trusted(app):
    configured_book().update_auth_settings(trust_loopback=True)
    remote = TestClient(app, base_url=ORIGIN, client=("203.0.113.7", 9999))
    assert remote.get("/api/private").status_code == 401
    assert (
        remote.post("/api/auth/login", json={"password": "synthetic-password"}).status_code == 200
    )
    assert remote.get("/api/private").json() == {"desktop": False}
