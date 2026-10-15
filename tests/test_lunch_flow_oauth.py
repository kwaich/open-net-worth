"""Lunch Flow OAuth orchestration — begin() (authorize URL + staged state), complete() (the
callback's code exchange), and ensure_fresh_token() (proactive/reactive refresh + the
compare-and-swap write). All HTTP is mocked — no real network call.
"""

from __future__ import annotations

import threading
import time

import pytest

from backend.core.errors import ValidationError
from backend.db.lunch_flow_config_store import LunchFlowConfigStore
from backend.services.aggregation import lunch_flow_oauth as svc
from backend.services.aggregation import lunch_flow_pending as pending

_BASE_URL = "https://onw.example.test"


@pytest.fixture(autouse=True)
def _isolate(db_isolated):
    pending._reset_for_tests()
    yield
    pending._reset_for_tests()


def _configure_credentials(**over):
    changes = {"client_id": "cid-1", "client_secret": "csecret-1"}
    changes.update(over)
    LunchFlowConfigStore().update(changes)


# ── begin() ──────────────────────────────────────────────────────────────────────────


def test_begin_requires_credentials():
    result = svc.begin(request_base_url=_BASE_URL)
    assert result["ok"] is False
    assert "client" in result["error"].lower()


def test_begin_requires_a_secure_redirect():
    _configure_credentials()
    result = svc.begin(request_base_url="http://192.0.2.1")  # not https, not loopback
    assert result["ok"] is False
    assert "secure" in result["error"].lower()


def test_begin_allows_loopback_over_plain_http():
    _configure_credentials()
    result = svc.begin(request_base_url="http://127.0.0.1:8080")
    assert result["ok"] is True


def test_begin_returns_authorize_url_with_expected_params():
    _configure_credentials(email="me@example.com")
    result = svc.begin(request_base_url=_BASE_URL)
    assert result["ok"] is True
    url = result["authorize_url"]
    assert url.startswith("https://lunchflow.app/api/platform/oauth/authorize?")
    assert "client_id=cid-1" in url
    assert "redirect_uri=" in url
    assert "onw.example.test" in url
    assert "email=me%40example.com" in url
    assert f"state={result['flow_id']}" in url
    assert result["expires_in"] == pending.TTL_SECONDS
    # The staged flow is retrievable by the returned flow_id (= state).
    assert pending.get_status(result["flow_id"]) == {"status": "pending", "error": None}


def test_begin_omits_email_param_when_not_set():
    _configure_credentials()
    result = svc.begin(request_base_url=_BASE_URL)
    assert "email=" not in result["authorize_url"]


# ── complete() ───────────────────────────────────────────────────────────────────────


def test_complete_with_unknown_state_is_ok_false():
    result = svc.complete(state="never-issued", code="abc")
    assert result == {"ok": False}


def test_complete_with_vendor_error_records_status():
    _configure_credentials()
    begin = svc.begin(request_base_url=_BASE_URL)
    result = svc.complete(state=begin["flow_id"], code=None, error="access_denied")
    assert result == {"ok": False}
    assert pending.get_status(begin["flow_id"])["status"] == "error"


def test_complete_with_no_code_is_ok_false():
    _configure_credentials()
    begin = svc.begin(request_base_url=_BASE_URL)
    result = svc.complete(state=begin["flow_id"], code=None)
    assert result == {"ok": False}


def test_complete_state_is_single_use_a_replay_fails(monkeypatch):
    _configure_credentials()
    monkeypatch.setattr(
        svc.httpx,
        "post",
        lambda *a, **k: _FakeResp(
            200,
            {
                "access_token": "live-access",
                "refresh_token": "live-refresh",
                "expires_in": 3600,
                "user_id": "u1",
            },
        ),
    )
    begin = svc.begin(request_base_url=_BASE_URL)
    first = svc.complete(state=begin["flow_id"], code="code-1")
    assert first == {"ok": True}
    second = svc.complete(state=begin["flow_id"], code="code-1")
    assert second == {"ok": False}  # the staged flow was already popped


def test_complete_exchanges_code_and_persists_tokens(monkeypatch):
    _configure_credentials()
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return _FakeResp(
            200,
            {
                "access_token": "live-access",
                "refresh_token": "live-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "user_id": "lf-user-9",
            },
        )

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    begin = svc.begin(request_base_url=_BASE_URL)
    result = svc.complete(state=begin["flow_id"], code="auth-code-xyz")
    assert result == {"ok": True}

    assert captured["url"] == svc.TOKEN_URL
    # RFC 6749 §4.1.3: form-urlencoded body, not a JSON payload (security review, finding 3).
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "auth-code-xyz"
    assert captured["data"]["client_id"] == "cid-1"
    assert captured["data"]["client_secret"] == "csecret-1"

    store = LunchFlowConfigStore()
    assert store.access_token() == "live-access"
    assert store.refresh_token() == "live-refresh"
    assert store.get()["user_id"] == "lf-user-9"
    assert store.token_expires_at() is not None

    assert pending.get_status(begin["flow_id"]) == {"status": "ok", "error": None}


def test_token_exchange_sends_form_urlencoded_not_json(monkeypatch):
    """Regression: the token request body must be application/x-www-form-urlencoded (RFC 6749
    §4.1.3), not JSON — httpx's ``data=`` kwarg, never ``json=``, is what makes that true."""
    _configure_credentials()
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return _FakeResp(200, {"access_token": "tok", "refresh_token": "rt", "expires_in": 3600})

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    begin = svc.begin(request_base_url=_BASE_URL)
    svc.complete(state=begin["flow_id"], code="c")

    assert len(calls) == 1
    assert "json" not in calls[0], "token exchange must not send a JSON body"
    assert "data" in calls[0], "token exchange must send a form-urlencoded body"


def test_complete_records_error_on_bad_token_exchange(monkeypatch):
    _configure_credentials()
    monkeypatch.setattr(
        svc.httpx, "post", lambda *a, **k: _FakeResp(400, {"error": "invalid_grant"})
    )
    begin = svc.begin(request_base_url=_BASE_URL)
    result = svc.complete(state=begin["flow_id"], code="bad-code")
    assert result == {"ok": False}
    status = pending.get_status(begin["flow_id"])
    assert status["status"] == "error"
    assert status["error"] is not None


def test_complete_never_touches_config_when_the_flow_is_bogus():
    before = LunchFlowConfigStore().get()
    svc.complete(state="bogus", code="c")
    after = LunchFlowConfigStore().get()
    assert before == after


# ── ensure_fresh_token() ────────────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _link(access_token="tok-1", refresh_token="rt-1", expires_at=None, user_id="u1"):
    _configure_credentials()
    store = LunchFlowConfigStore()
    store.set_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        user_id=user_id,
    )
    return store


def test_ensure_fresh_token_requires_a_link():
    with pytest.raises(ValidationError):
        svc.ensure_fresh_token()


def test_ensure_fresh_token_returns_saved_token_when_not_stale(monkeypatch):
    _link(expires_at=999_999_999_999.0)  # far future — never stale
    calls = []
    monkeypatch.setattr(svc.httpx, "post", lambda *a, **k: calls.append(1))
    result = svc.ensure_fresh_token()
    assert result == "tok-1"
    assert calls == []  # no network call — token was already fresh


def test_ensure_fresh_token_refreshes_a_stale_token(monkeypatch):
    import time

    _link(expires_at=time.time() - 10)  # already expired
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data
        return _FakeResp(
            200, {"access_token": "tok-2", "refresh_token": "rt-2", "expires_in": 3600}
        )

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    result = svc.ensure_fresh_token()
    assert result == "tok-2"
    assert captured["data"]["grant_type"] == "refresh_token"
    assert captured["data"]["refresh_token"] == "rt-1"

    store = LunchFlowConfigStore()
    assert store.access_token() == "tok-2"
    assert store.refresh_token() == "rt-2"


def test_ensure_fresh_token_force_refreshes_even_when_not_stale(monkeypatch):
    _link(expires_at=999_999_999_999.0)
    monkeypatch.setattr(
        svc.httpx,
        "post",
        lambda *a, **k: _FakeResp(200, {"access_token": "tok-forced", "expires_in": 3600}),
    )
    result = svc.ensure_fresh_token(force=True)
    assert result == "tok-forced"


def test_ensure_fresh_token_with_no_refresh_token_raises():
    _link(refresh_token="", expires_at=0.0)
    with pytest.raises(ValidationError):
        svc.ensure_fresh_token()


def test_ensure_fresh_token_keeps_the_old_refresh_token_if_none_returned(monkeypatch):
    import time

    _link(refresh_token="rt-original", expires_at=time.time() - 10)
    monkeypatch.setattr(
        svc.httpx,
        "post",
        lambda *a, **k: _FakeResp(200, {"access_token": "tok-2", "expires_in": 3600}),
    )
    svc.ensure_fresh_token()
    assert LunchFlowConfigStore().refresh_token() == "rt-original"


# ── the money-adjacent one: two concurrent refreshes make EXACTLY ONE network call ──────
# Mirrors tests/test_mcp_oauth_flow.py::test_two_concurrent_refreshes_make_exactly_one_network_call
# — coverage the security review noted was missing for this connector's own refresh path
# (the in-process `_refresh_lock` + the store's CAS were already correct; this pins it).


def test_two_concurrent_ensure_fresh_token_calls_make_exactly_one_network_call(monkeypatch):
    _link(expires_at=time.time() - 100)  # already stale

    call_count = {"n": 0}
    count_lock = threading.Lock()

    def fake_post(url, data=None, timeout=None):
        with count_lock:
            call_count["n"] += 1
            n = call_count["n"]
        time.sleep(0.15)  # long enough that a real race would show
        return _FakeResp(
            200, {"access_token": f"tok-{n}", "refresh_token": f"rt-{n}", "expires_in": 3600}
        )

    monkeypatch.setattr(svc.httpx, "post", fake_post)

    results: list[str] = []
    results_lock = threading.Lock()

    def worker():
        r = svc.ensure_fresh_token()
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert call_count["n"] == 1, "two racing refreshes must make exactly one network call"
    assert len(results) == 2
    assert results[0] == results[1] == "tok-1"
    store = LunchFlowConfigStore()
    assert store.access_token() == "tok-1"
    assert store.refresh_token() == "rt-1"
