"""Security pass F2 / H2 — loopback trust is bound to a served Host and same-origin writes.

A DNS-rebound page reaches the app from the user's own machine (peer == loopback) with
``Host: attacker.example``; loopback trust must not extend to it. A cross-site browser POST
authenticated only by ambient trust (loopback or cookie) is refused; Bearer callers, which
are not browsers, are exempt.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.core.security import hash_password, sign_session
from backend.db.auth_store import AuthStore

_TOKEN = "tok-abc"
_KEY = "key-xyz"


@pytest.fixture
def configured(monkeypatch, db_isolated):
    for key in ("AUTH_DISABLED", "AUTH_TRUST_LOOPBACK", "ONW_ALLOWED_HOSTS", "CORS_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    AuthStore().save(
        {
            "password_hash": hash_password("pw-123456"),
            "session_signing_key": _KEY,
            "api_token": _TOKEN,
        }
    )
    yield monkeypatch
    config._settings = None


def _client() -> TestClient:
    from backend.main import app

    return TestClient(app)


def _cookie() -> dict:
    return {"onw_session": sign_session({"sub": "user"}, _KEY.encode(), ttl=60)}


def test_rebound_host_refused_on_loopback(configured):
    r = _client().get("/api/accounts", headers={"Host": "attacker.example"})
    assert r.status_code == 421


def test_rebound_host_refused_even_when_unconfigured(db_isolated, monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    config._settings = None
    r = _client().get("/api/accounts", headers={"Host": "attacker.example:8080"})
    assert r.status_code == 421


@pytest.mark.parametrize("host", ["localhost:8080", "127.0.0.1:8080", "[::1]:8080", "localhost"])
def test_served_loopback_hosts_still_trusted(configured, host):
    assert _client().get("/api/accounts", headers={"Host": host}).status_code == 200


def test_configured_public_host_is_allowed(configured):
    configured.setenv("ONW_ALLOWED_HOSTS", "Money.Example.com")
    config._settings = None
    r = _client().get("/api/accounts", headers={"Host": "money.example.com"})
    assert r.status_code == 200


def test_rebound_host_with_bearer_still_works(configured):
    r = _client().get(
        "/api/accounts",
        headers={"Host": "attacker.example", "Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 200


def test_status_does_not_report_rebound_host_as_trusted(configured):
    body = _client().get("/api/auth/status", headers={"Host": "attacker.example"}).json()
    assert body == {"required": True, "authenticated": False}


def test_cross_site_post_refused_under_loopback_trust(configured):
    r = _client().post(
        "/api/auth/api-token/rotate",
        headers={"Origin": "https://attacker.example", "Host": "localhost:8080"},
    )
    assert r.status_code == 403


def test_sec_fetch_site_cross_site_refused(configured):
    r = _client().post(
        "/api/auth/api-token/rotate",
        headers={"Sec-Fetch-Site": "cross-site", "Host": "localhost:8080"},
    )
    assert r.status_code == 403


def test_cross_site_post_refused_with_cookie(configured):
    c = _client()
    c.cookies.update(_cookie())
    r = c.post(
        "/api/auth/api-token/rotate",
        headers={"Origin": "https://attacker.example", "Host": "money.example.com"},
    )
    assert r.status_code == 403


def test_same_origin_post_passes_the_guard(configured):
    r = _client().post(
        "/api/auth/api-token/rotate",
        headers={
            "Origin": "http://localhost:8080",
            "Host": "localhost:8080",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    assert r.status_code not in (401, 403, 421)


def test_bearer_post_exempt_from_origin_check(configured):
    r = _client().post(
        "/api/auth/api-token/rotate",
        headers={"Origin": "https://attacker.example", "Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code not in (401, 403, 421)


def test_originless_post_is_allowed_curl_style(configured):
    r = _client().post("/api/auth/api-token/rotate", headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code not in (401, 403, 421)
