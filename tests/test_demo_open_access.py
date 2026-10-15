"""A public demo with ``DEMO_OPEN_ACCESS`` needs no password (ADR 2026-10-07).

Four things are pinned, each against the REAL app and a REMOTE client with no credentials:

* an open demo lets a visitor reach authenticated endpoints, and says so in ``/api/auth/status``;
* every other configuration — either flag alone, a typo, the gateway headers, a forged header —
  still answers 401, i.e. nothing but process env can open an instance;
* the demo lockdown still refuses, for that credential-less visitor, everything it refused when
  the visitor held a password;
* a visitor cannot set, change or remove a password, or touch any auth setting.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import backend.core.config as config
from backend.db.auth_store import AuthStore

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_ENV_KEYS = (
    "DEMO_MODE",
    "DEMO_OPEN_ACCESS",
    "AUTH_DISABLED",
    "AUTH_TRUST_LOOPBACK",
    "AUTH_PASSWORD",
    "AUTH_PASSWORD_HASH",
    "AUTH_API_TOKEN",
    "AUTH_SESSION_KEY",
    "ONW_GATEWAY_TOKEN",
)
_SECRET = "unpublished-random-secret-for-this-test"
_OPEN = {"DEMO_MODE": "1", "DEMO_OPEN_ACCESS": "1"}


@pytest.fixture
def configure(monkeypatch, db_isolated):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    def apply(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        config._settings = None

    config._settings = None
    yield apply
    config._settings = None


def _visitor(**headers) -> TestClient:
    """A remote browser with no cookie and no Bearer — the demo's whole audience."""
    from backend.main import app

    return TestClient(app, client=("203.0.113.7", 9999), headers=headers)


# ── (a) an open demo lets a visitor in ───────────────────────────────────────────


@pytest.mark.parametrize("password", [_SECRET, None], ids=["configured", "unconfigured"])
def test_an_open_demo_visitor_reaches_authenticated_endpoints(configure, password):
    env = {**_OPEN, "AUTH_TRUST_LOOPBACK": "false"}
    if password:
        env["AUTH_PASSWORD"] = password
    configure(**env)
    client = _visitor()
    assert client.get("/api/portfolio").status_code == 200
    assert client.get("/api/auth/settings").status_code == 200
    assert client.get("/api/auth/status").json() == {
        "required": False,
        "authenticated": True,
        "mode": "demo",
    }


def test_an_open_demo_never_serves_mcp_to_a_credentialless_visitor(configure):
    """The MCP endpoint has its own Bearer-only gate that open access must not reach — and a demo
    exposes no MCP server at all, so the lockdown refuses it before the Bearer check is asked."""
    configure(**_OPEN, AUTH_PASSWORD=_SECRET)
    r = _visitor().post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "demo_locked"


# ── (b) nothing else opens an instance ───────────────────────────────────────────

_STAYS_CLOSED = {
    "plain": {},
    "demo_mode_alone": {"DEMO_MODE": "1"},
    "open_access_alone": {"DEMO_OPEN_ACCESS": "1"},
    "demo_mode_off_explicit": {"DEMO_MODE": "0", "DEMO_OPEN_ACCESS": "1"},
    "open_access_off_explicit": {"DEMO_MODE": "1", "DEMO_OPEN_ACCESS": "0"},
    "open_access_typo": {"DEMO_MODE": "1", "DEMO_OPEN_ACCESS": "ture"},
    "demo_mode_typo": {"DEMO_MODE": "ture", "DEMO_OPEN_ACCESS": "1"},
    "auth_disabled_false": {"DEMO_OPEN_ACCESS": "1", "AUTH_DISABLED": "false"},
    "trust_loopback_on": {"DEMO_OPEN_ACCESS": "1", "AUTH_TRUST_LOOPBACK": "true"},
}


@pytest.mark.parametrize("env", _STAYS_CLOSED.values(), ids=_STAYS_CLOSED.keys())
def test_without_both_flags_a_credentialless_remote_gets_401(configure, env):
    configure(AUTH_PASSWORD=_SECRET, **env)
    client = _visitor()
    assert client.get("/api/portfolio").status_code == 401
    assert client.get("/api/auth/status").json() == {"required": True, "authenticated": False}


@pytest.mark.parametrize("env", _STAYS_CLOSED.values(), ids=_STAYS_CLOSED.keys())
def test_an_unconfigured_remote_stays_refused_too(configure, env):
    configure(**env)
    assert _visitor().get("/api/portfolio").status_code == 401


def test_nothing_a_request_carries_can_open_a_closed_instance(configure):
    configure(AUTH_PASSWORD=_SECRET, DEMO_MODE="1")
    forged = {
        "X-Demo-Mode": "1",
        "X-Demo-Open-Access": "1",
        "DEMO_OPEN_ACCESS": "1",
        "X-Forwarded-For": "127.0.0.1",
        "X-Forwarded-Host": "localhost",
        "X-Onw-Gateway-Auth": "session",
        "X-Onw-User-Email": "operator@example.com",
        "Origin": "http://localhost",
    }
    client = _visitor(**forged)
    assert client.get("/api/portfolio").status_code == 401
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_gateway_headers_do_not_open_a_closed_instance(configure):
    token = "t" * 40
    configure(AUTH_PASSWORD=_SECRET, DEMO_OPEN_ACCESS="1", ONW_GATEWAY_TOKEN=token)
    for gateway_auth in ("none", "session", "garbage"):
        client = _visitor(**{"X-Onw-Gateway-Token": token, "X-Onw-Gateway-Auth": gateway_auth})
        r = client.get("/api/portfolio")
        assert (r.status_code == 200) == (gateway_auth == "session"), gateway_auth


def test_a_stored_auth_setting_cannot_open_a_closed_instance(configure):
    """Open access is not a stored setting: writing every auth knob leaves a non-demo gated."""
    configure(AUTH_PASSWORD=_SECRET, AUTH_TRUST_LOOPBACK="false")
    AuthStore().update_auth_settings(session_ttl=120, trust_loopback=False, auth_disabled=False)
    assert _visitor().get("/api/portfolio").status_code == 401


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"DEMO_MODE": "1"}, False),
        ({"DEMO_OPEN_ACCESS": "1"}, False),
        ({"DEMO_MODE": "1", "DEMO_OPEN_ACCESS": "1"}, True),
        ({"DEMO_MODE": "true", "DEMO_OPEN_ACCESS": "yes"}, True),
    ],
)
def test_the_pair_is_read_through_one_function(configure, env, expected):
    configure(**env)
    assert config.demo_open_access_active() is expected


def test_the_desktop_app_discards_both_flags():
    from pathlib import Path

    src = (Path(config.__file__).parent.parent / "cli" / "desktop.py").read_text()
    assert '"DEMO_MODE",' in src and '"DEMO_OPEN_ACCESS",' in src


# ── (c) the lockdown holds for a visitor with no credentials ─────────────────────

_LOCKED = [
    ("GET", "/api/model-config/providers/openrouter/reveal-key"),
    ("GET", "/api/connectors/telegram/secret/bot_token"),
    ("PUT", "/api/model-config"),
    ("PUT", "/api/model-config/providers/openrouter"),
    ("DELETE", "/api/model-config/providers/openrouter/key"),
    ("POST", "/api/mcp-clients"),
    ("POST", "/api/mcp-clients/probe"),
    ("PUT", "/api/auth/settings"),
    ("GET", "/api/auth/api-token"),
    ("POST", "/api/auth/api-token/rotate"),
    ("POST", "/api/shortcuts/prepare"),
    ("GET", "/api/shortcuts/setup"),
    ("GET", "/api/shortcuts/file"),
    ("GET", "/api/shortcuts/setup-code"),
    ("GET", "/api/terminal/status"),
    ("POST", "/api/move/begin"),
    ("POST", "/api/move/export-nonce"),
]


@pytest.mark.parametrize(("method", "path"), _LOCKED)
def test_the_lockdown_still_refuses_a_credentialless_visitor(configure, method, path):
    configure(**_OPEN, AUTH_PASSWORD=_SECRET)
    r = _visitor(**_XHR).request(method, path, json={})
    assert r.status_code == 403, (method, path, r.text)
    assert r.json()["detail"]["code"] == "demo_locked"


def test_a_visitor_cannot_move_or_read_the_models_key(configure):
    from backend.db.model_config_store import ModelConfigStore

    configure()
    store = ModelConfigStore()
    store.update_provider(
        "openrouter",
        {"api_key": "sk-or-test-not-a-real-key", "model": "anthropic/claude-sonnet-5.5"},
    )
    store.update({"default_model": "openrouter:anthropic/claude-sonnet-5.5"})
    configure(**_OPEN, AUTH_PASSWORD=_SECRET)
    client = _visitor(**_XHR)
    r = client.put(
        "/api/model-config/providers/openrouter", json={"base_url": "https://evil.example/v1"}
    )
    assert r.status_code == 403
    assert client.get("/api/model-config/providers/openrouter/reveal-key").status_code == 403
    assert store.resolved()["base_url"] == "https://openrouter.ai/api/v1"
    assert store.resolved()["api_key"] == "sk-or-test-not-a-real-key"


def test_the_demo_itself_stays_usable_without_a_login(configure):
    configure(**_OPEN, AUTH_PASSWORD=_SECRET)
    client = _visitor(**_XHR)
    assert client.get("/api/model-config").status_code == 200
    assert client.get("/api/mcp-clients").status_code == 200


# ── (d) a visitor cannot take over the password or the auth settings ─────────────

_ATTACKER = "attacker-chosen-password"


@pytest.mark.parametrize("password", [_SECRET, None], ids=["configured", "unconfigured"])
def test_a_visitor_cannot_set_change_or_remove_the_password(configure, password):
    env = dict(_OPEN)
    if password:
        env["AUTH_PASSWORD"] = password
    configure(**env)
    store = AuthStore()
    before = (store.is_password_set(), store.password_hash(), store.session_key_bytes())
    client = _visitor(**_XHR)

    for path, body in (
        ("/api/auth/change-password", {"new_password": _ATTACKER, "old_password": password}),
        ("/api/auth/change-password", {"new_password": _ATTACKER}),
        ("/api/auth/remove-password", {"old_password": password}),
    ):
        r = client.post(path, json=body)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "demo_locked", path

    after = AuthStore()
    assert (after.is_password_set(), after.password_hash(), after.session_key_bytes()) == before
    assert client.post("/api/auth/login", json={"password": _ATTACKER}).status_code == 401


def test_a_visitor_cannot_change_auth_settings(configure):
    configure(**_OPEN, AUTH_PASSWORD=_SECRET)
    store = AuthStore()
    before = dict(store.get())
    r = _visitor(**_XHR).put(
        "/api/auth/settings",
        json={"auth_disabled": True, "trust_loopback": True, "session_ttl": 60},
    )
    assert r.status_code == 403
    after = AuthStore()
    assert after.get() == before


def test_a_visitor_never_holds_a_login_session(configure):
    """No login session is issued. The only cookie is the anonymous identity that scopes a
    visitor's conversations (``demo_visitors``); it authenticates nothing."""
    configure(**_OPEN, AUTH_PASSWORD=_SECRET)
    client = _visitor(**_XHR)
    client.get("/api/portfolio")
    client.get("/api/auth/status")
    assert set(client.cookies.keys()) == {"onw_demo_visitor"}


# ── (e) per-visitor Lucius limits key on the network, never on a login ───────────


def _request(forwarded: str | None, cookie: str = "") -> Request:
    headers = [(b"host", b"demo.opennetworth.ai")]
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
        }
    )


def test_visitors_are_told_apart_without_a_session(configure):
    """Behind one trusted proxy the address is what that proxy appended (the right of the header)."""
    from backend.api import demo_visitor

    configure(**_OPEN, AUTH_PASSWORD=_SECRET, DEMO_TRUSTED_PROXY_HOPS="1")
    assert demo_visitor.client_ip(_request("10.0.0.1, 198.51.100.1")) == "198.51.100.1"
    assert demo_visitor.client_ip(_request("198.51.100.2")) == "198.51.100.2"
    # No proxy header: the peer address, not a shared "anonymous" bucket per cookie.
    assert demo_visitor.client_ip(_request(None)) == "127.0.0.1"
    # A cookie, signed or not, never changes the quota key.
    assert demo_visitor.client_ip(_request("198.51.100.1", "onw_session=x")) == "198.51.100.1"


def test_the_quota_is_charged_per_visitor_for_an_open_demo(configure):
    from backend.services import demo_quota

    configure(**_OPEN, AUTH_PASSWORD=_SECRET, DEMO_TURNS_PER_WINDOW="2")
    demo_quota.reset()
    try:
        for _ in range(2):
            demo_quota.record_turn("198.51.100.1", "s1")
        assert not demo_quota.check("198.51.100.1").allowed
        assert demo_quota.check("198.51.100.2").allowed
    finally:
        demo_quota.reset()
