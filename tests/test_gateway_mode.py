"""Gateway mode — the app behind a trusted authenticating reverse proxy (ONW_GATEWAY_TOKEN).

Hermetic like test_auth.py: env via monkeypatch, Settings singleton busted around every test,
and the client is "remote" with auth otherwise ENABLED, so a 200 can only come from the token.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.api import gateway
from backend.core.errors import ConfigurationError

TOKEN = "t" * 40
TOKEN_ONLY = {"X-Onw-Gateway-Token": TOKEN}
HEADER = {**TOKEN_ONLY, "X-Onw-Gateway-Auth": "session"}
PASSTHROUGH = {**TOKEN_ONLY, "X-Onw-Gateway-Auth": "none"}


@pytest.fixture
def gateway_env(monkeypatch, db_isolated):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.setenv("AUTH_TRUST_LOOPBACK", "false")
    monkeypatch.delenv("ONW_GATEWAY_TOKEN", raising=False)

    def configure(token: str | None = TOKEN):
        if token is not None:
            monkeypatch.setenv("ONW_GATEWAY_TOKEN", token)
        config._settings = None

    config._settings = None
    yield configure
    config._settings = None


def _client() -> TestClient:
    from backend.main import app

    return TestClient(app, client=("203.0.113.7", 9999))


def test_missing_token_is_403(gateway_env):
    gateway_env()
    r = _client().get("/api/portfolio")
    assert r.status_code == 403
    assert r.json() == {"detail": "gateway token required"}


def test_wrong_token_is_403(gateway_env):
    gateway_env()
    r = _client().get("/api/portfolio", headers={"X-Onw-Gateway-Token": "x" * 40})
    assert r.status_code == 403
    assert r.json() == {"detail": "gateway token required"}


def test_right_token_reaches_a_gated_endpoint(gateway_env):
    gateway_env()
    assert _client().get("/api/portfolio", headers=HEADER).status_code == 200


def test_health_is_exempt_but_only_for_get(gateway_env):
    gateway_env()
    c = _client()
    assert c.get("/api/health").status_code == 200
    assert c.post("/api/health").status_code == 403


def test_static_spa_and_status_are_gated(gateway_env):
    gateway_env()
    c = _client()
    assert c.get("/").status_code == 403
    assert c.get("/api/auth/status").status_code == 403
    assert c.get("/", headers=HEADER).status_code != 403


def test_status_reports_gateway_mode_and_email(gateway_env):
    gateway_env()
    r = _client().get("/api/auth/status", headers={**HEADER, "X-Onw-User-Email": "a@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "gateway"
    assert body["authenticated"] is True
    assert body["required"] is False
    assert body["gateway"] == {"email": "a@example.com", "portal_url": None}


def test_status_email_is_optional(gateway_env):
    gateway_env()
    body = _client().get("/api/auth/status", headers=HEADER).json()
    assert body["gateway"] == {"email": None, "portal_url": None}


def test_status_reports_the_portal_url(gateway_env):
    gateway_env()
    headers = {**HEADER, "X-Onw-Portal-Url": "https://cloud.example.com"}
    body = _client().get("/api/auth/status", headers=headers).json()
    assert body["gateway"]["portal_url"] == "https://cloud.example.com"


@pytest.mark.parametrize(
    "bad",
    ["javascript:alert(1)", "//evil.example", "/relative", "https://u:p@evil.example", "x" * 3000],
)
def test_a_portal_url_that_is_not_a_plain_web_link_is_dropped(gateway_env, bad):
    gateway_env()
    body = _client().get("/api/auth/status", headers={**HEADER, "X-Onw-Portal-Url": bad}).json()
    assert body["gateway"]["portal_url"] is None


def test_passthrough_ignores_the_portal_url_and_session_started(gateway_env):
    gateway_env()
    headers = {**PASSTHROUGH, "X-Onw-Portal-Url": "https://evil.example"}
    body = _client().get("/api/auth/status", headers=headers).json()
    assert "gateway" not in body


def test_health_reports_version_and_gateway_flag(gateway_env):
    gateway_env()
    body = _client().get("/api/health").json()
    assert body["status"] == "ok" and body["version"] and body["gateway"] is True


def test_health_gateway_flag_is_false_when_the_mode_is_off(gateway_env):
    gateway_env(token=None)
    assert _client().get("/api/health").json()["gateway"] is False


def test_email_header_alone_grants_nothing(gateway_env):
    gateway_env()
    r = _client().get("/api/portfolio", headers={"X-Onw-User-Email": "a@example.com"})
    assert r.status_code == 403


def test_unset_env_is_unchanged(gateway_env):
    gateway_env(token=None)
    c = _client()
    assert c.get("/api/health").status_code == 200
    body = c.get("/api/auth/status").json()
    assert "mode" not in body
    # The header is ignored entirely when the mode is off: auth still decides.
    assert c.get("/api/portfolio", headers=HEADER).status_code == 401


def test_short_token_refuses_to_boot(gateway_env):
    with pytest.raises(ConfigurationError, match="at least 32"):
        gateway_env("short")
        config.get_settings()


def test_comparison_is_constant_time():
    assert "compare_digest" in inspect.getsource(gateway.token_matches)
    assert gateway.token_matches(TOKEN, TOKEN)
    assert not gateway.token_matches(TOKEN, TOKEN[:-1] + "x")
    assert not gateway.token_matches("é" * 40, TOKEN)


# ── pass-through: valid token, the proxy did NOT sign the owner in ─────────────────────────


def _api_token() -> str:
    from backend.db.auth_store import AuthStore

    return AuthStore().api_token()


@pytest.mark.parametrize(
    "extra",
    [
        {"X-Onw-Gateway-Auth": "none"},
        {},
        {"X-Onw-Gateway-Auth": ""},
        {"X-Onw-Gateway-Auth": "SESSION"},
        {"X-Onw-Gateway-Auth": "session "},
        {"X-Onw-Gateway-Auth": "true"},
        {"X-Onw-Gateway-Auth": "session, session"},
        {"X-Onw-Gateway-Auth": "session, none"},
    ],
)
def test_anything_but_exact_session_fails_closed(gateway_env, extra):
    gateway_env()
    c = _client()
    assert c.get("/api/portfolio", headers={**TOKEN_ONLY, **extra}).status_code == 401


def test_duplicated_auth_header_with_a_mixed_value_fails_closed(gateway_env):
    gateway_env()
    r = _client().get(
        "/api/portfolio",
        headers=[
            ("X-Onw-Gateway-Token", TOKEN),
            ("X-Onw-Gateway-Auth", "session"),
            ("X-Onw-Gateway-Auth", "none"),
        ],
    )
    assert r.status_code == 401


def test_passthrough_without_a_bearer_is_401(gateway_env):
    gateway_env()
    assert _client().get("/api/portfolio", headers=PASSTHROUGH).status_code == 401


def test_passthrough_with_a_bad_bearer_is_401(gateway_env):
    gateway_env()
    r = _client().get("/api/portfolio", headers={**PASSTHROUGH, "Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_passthrough_with_the_api_token_is_200(gateway_env):
    gateway_env()
    r = _client().get(
        "/api/portfolio", headers={**PASSTHROUGH, "Authorization": f"Bearer {_api_token()}"}
    )
    assert r.status_code == 200


def test_passthrough_from_loopback_on_an_unconfigured_instance_is_still_401(gateway_env):
    """The critical one: the proxy runs on this machine, so the peer is loopback, and an
    unconfigured instance is open to loopback in ordinary mode. Pass-through must not inherit it."""
    from backend.db.auth_store import AuthStore
    from backend.main import app

    gateway_env()
    assert not AuthStore().is_configured()
    local = TestClient(app, client=("127.0.0.1", 9999))
    assert local.get("/api/portfolio", headers=PASSTHROUGH).status_code == 401
    assert local.get("/mcp", headers=PASSTHROUGH).status_code == 401


def test_passthrough_ignores_loopback_trust_and_disabled_auth(gateway_env, monkeypatch):
    from backend.main import app

    gateway_env()
    monkeypatch.setenv("AUTH_TRUST_LOOPBACK", "true")
    monkeypatch.setenv("AUTH_DISABLED", "true")
    config._settings = None
    local = TestClient(app, client=("127.0.0.1", 9999))
    assert local.get("/api/portfolio", headers=PASSTHROUGH).status_code == 401
    assert local.get("/api/portfolio", headers=HEADER).status_code == 200


def test_passthrough_status_is_never_authenticated_without_a_credential(gateway_env):
    gateway_env()
    body = _client().get("/api/auth/status", headers=PASSTHROUGH).json()
    assert body == {"required": True, "authenticated": False}
    with_token = _client().get(
        "/api/auth/status", headers={**PASSTHROUGH, "Authorization": f"Bearer {_api_token()}"}
    )
    assert with_token.json() == {"required": True, "authenticated": True}


def test_passthrough_ignores_the_email_header(gateway_env):
    gateway_env()
    body = (
        _client()
        .get("/api/auth/status", headers={**PASSTHROUGH, "X-Onw-User-Email": "a@example.com"})
        .json()
    )
    assert "gateway" not in body and "mode" not in body


def test_shortcuts_setup_ticket_works_through_the_gateway(gateway_env):
    from backend.services.shortcuts import tickets

    gateway_env()
    tickets.revoke_all()
    c = _client()
    prepared = c.post("/api/shortcuts/prepare", json={}, headers=HEADER).json()
    ticket = prepared["setup_url"].split("t=")[1].split("&")[0]
    ok = c.get("/api/shortcuts/setup", params={"t": ticket}, headers=PASSTHROUGH)
    assert ok.status_code == 200
    assert (
        c.get("/api/shortcuts/setup", params={"t": "made-up"}, headers=PASSTHROUGH).status_code
        == 404
    )
    assert c.get("/api/shortcuts/setup", params={"t": ticket}).status_code == 403


def test_mcp_without_a_bearer_is_its_normal_401(gateway_env):
    gateway_env()
    r = _client().post("/mcp", json={}, headers=PASSTHROUGH)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
