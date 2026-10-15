"""Friendly-error mapping for the chat SSE stream (backend/api/chat.py).

Verifies that common "no/invalid AI key" and other provider failures are turned
into clear, user-readable messages — never raw SDK dicts or stack traces.

Hermetic: no network, no LLM.  ``assistant.run`` is monkeypatched to raise the
exception under test; the test reads the SSE stream and checks the emitted error
event.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.api.chat as chat_mod
import backend.db.model_config_store as model_config_store
from backend.api.chat import (
    _extract_provider_message,
    _friendly_error,
    _is_auth_error,
    _strip_sdk_dict,
)

# ── Unit tests for _friendly_error ────────────────────────────────────────────


class _FakeAuthError(Exception):
    """Simulates anthropic.AuthenticationError (status_code=401)."""

    status_code = 401


class _FakeRateLimitError(Exception):
    """Simulates anthropic.RateLimitError."""

    status_code = 429


class _FakeConnectionError(Exception):
    """Simulates anthropic.APIConnectionError."""


def test_auth_error_produces_setup_hint():
    # Patch the isinstance check to treat _FakeAuthError as an Anthropic auth error.
    import backend.api.chat as mod

    orig = mod._is_auth_error

    def patched(exc):
        if isinstance(exc, _FakeAuthError):
            return True
        return orig(exc)

    mod._is_auth_error = patched
    try:
        msg, hint = _friendly_error(_FakeAuthError("invalid x-api-key"))
        assert hint is True, "setup_hint must be True for auth errors"
        assert "Settings" in msg or "key" in msg.lower(), (
            "message should mention the key or Settings"
        )
        assert "{" not in msg, "must not leak raw SDK dict"
    finally:
        mod._is_auth_error = orig


def test_connection_error_no_setup_hint():
    import backend.api.chat as mod

    orig_conn = mod._is_connection_error

    def patched(exc):
        if isinstance(exc, _FakeConnectionError):
            return True
        return orig_conn(exc)

    mod._is_connection_error = patched
    try:
        msg, hint = _friendly_error(_FakeConnectionError("connection refused"))
        assert hint is False
        assert "network" in msg.lower() or "reach" in msg.lower() or "connect" in msg.lower()
        assert "{" not in msg
    finally:
        mod._is_connection_error = orig_conn


def test_rate_limit_error_no_setup_hint():
    import backend.api.chat as mod

    orig_rl = mod._is_rate_limit_error

    def patched(exc):
        if isinstance(exc, _FakeRateLimitError):
            return True
        return orig_rl(exc)

    mod._is_rate_limit_error = patched
    try:
        msg, hint = _friendly_error(_FakeRateLimitError("rate limit exceeded"))
        assert hint is False
        assert "rate" in msg.lower() or "limit" in msg.lower() or "wait" in msg.lower()
        assert "{" not in msg
    finally:
        mod._is_rate_limit_error = orig_rl


def test_generic_error_strips_sdk_dict():
    raw = (
        "Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', "
        "'message': 'invalid x-api-key'}}"
    )
    msg, hint = _friendly_error(ValueError(raw))
    # A 401 string is detected as an auth error → setup_hint=True and a friendly message.
    # The important thing is that the raw dict is NOT echoed back to the user.
    assert "{" not in msg, f"SDK dict leaked into message: {msg!r}"
    # The 401 string detector marks this as an auth error.
    assert hint is True


def test_strip_sdk_dict_removes_body():
    """_strip_sdk_dict isolates the human-readable prefix from the SDK body."""
    raw = "Error code: 401 - {'type': 'error', 'error': {'message': 'bad key'}}"
    assert _strip_sdk_dict(raw) == "Error code: 401"


def test_strip_sdk_dict_passthrough_when_no_dict():
    plain = "Network timeout"
    assert _strip_sdk_dict(plain) == plain


def test_extract_provider_message_ollama_context_size():
    """Ollama double-encodes the real reason under error.message — surface it."""
    raw = (
        "Error code: 400 - {'error': {'message': '{\"error\":{\"code\":400,"
        '"message":"request (11070 tokens) exceeds the available context size '
        '(4096 tokens), try increasing it","type":"exceed_context_size_error",'
        "\"param\":null}}', 'type': None, 'param': None, 'code': None}}"
    )
    detail = _extract_provider_message(raw)
    assert detail == (
        "request (11070 tokens) exceeds the available context size (4096 tokens), try increasing it"
    )


def test_extract_provider_message_flat_openai_shape():
    raw = "Error code: 400 - {'error': {'message': 'model not found', 'type': 'invalid_request_error'}}"
    assert _extract_provider_message(raw) == "model not found"


_OPENROUTER_403 = (
    "Error code: 403 - {'error': {'message': 'Provider returned error', 'code': 403, "
    "'metadata': {'raw': 'This model is only available in the United States.', "
    "'provider_name': 'Meta', 'is_byok': False}}}"
)


def test_extract_provider_message_openrouter_metadata_raw():
    """OpenRouter passthrough: the upstream reason in metadata.raw beats the
    generic 'Provider returned error' message, and names the upstream provider."""
    detail = _extract_provider_message(_OPENROUTER_403)
    assert detail == (
        "This model is only available in the United States. (upstream provider: Meta)"
    )


def test_extract_provider_message_openrouter_raw_json_recurses():
    """A JSON-encoded metadata.raw still yields its inner message."""
    raw = (
        "Error code: 429 - {'error': {'message': 'Provider returned error', "
        "'metadata': {'raw': '{\"error\":{\"message\":\"overloaded, retry later\"}}', "
        "'provider_name': 'Meta'}}}"
    )
    detail = _extract_provider_message(raw)
    assert "overloaded, retry later" in detail
    assert "(upstream provider: Meta)" in detail


def test_friendly_error_surfaces_openrouter_geo_block():
    """End-to-end: the geo-block reason reaches the user, never the raw dict."""
    msg, hint = _friendly_error(ValueError(_OPENROUTER_403))
    assert hint is False
    assert "{" not in msg, f"raw SDK dict leaked: {msg!r}"
    assert "Error code: 403" in msg
    assert "only available in the United States" in msg
    assert "Meta" in msg


def test_rate_limit_error_includes_upstream_reason():
    """A 429 with an upstream reason (OpenRouter shared-pool throttle) shows it."""
    raw = (
        "Error code: 429 - {'error': {'message': 'Provider returned error', 'code': 429, "
        "'metadata': {'raw': 'moonshotai/kimi-k3 is temporarily rate-limited upstream. "
        "Please retry shortly, or add your own key to accumulate your rate limits', "
        "'provider_name': 'Moonshot AI', 'is_byok': False}}}"
    )
    msg, hint = _friendly_error(ValueError(raw))
    assert hint is False
    assert "{" not in msg
    assert "rate limit" in msg.lower()
    assert "temporarily rate-limited upstream" in msg
    assert "Moonshot AI" in msg


def test_extract_provider_message_none_when_absent():
    assert (
        _extract_provider_message("Error code: 500 - {'error': {'type': 'server_error'}}") is None
    )


def test_friendly_error_surfaces_ollama_context_size():
    """End-to-end: a context-size 400 reaches the user with the actionable reason."""
    raw = (
        "Error code: 400 - {'error': {'message': '{\"error\":{\"code\":400,"
        '"message":"request (11070 tokens) exceeds the available context size '
        '(4096 tokens), try increasing it","type":"exceed_context_size_error"}}'
        "'}}"
    )
    msg, hint = _friendly_error(ValueError(raw))
    assert hint is False
    assert "{" not in msg, f"raw SDK dict leaked: {msg!r}"
    assert "Error code: 400" in msg
    assert "exceeds the available context size" in msg
    assert "try increasing it" in msg


def test_is_auth_error_detects_string_401():
    """String-based detection catches OpenAI or other providers not yet installed."""
    assert _is_auth_error(Exception("Error code: 401 - invalid api_key")) is True
    assert _is_auth_error(Exception("401 authentication error")) is True
    assert _is_auth_error(Exception("500 internal server error")) is False


# ── Integration: SSE error event shape ────────────────────────────────────────


def _make_client(tmp_path, monkeypatch, exc: Exception) -> TestClient:
    from backend.db.model_config_store import ModelConfigStore

    ModelConfigStore().update({"provider": "anthropic", "model": "test-model"})
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")

    async def fake_run(*a, **kw) -> AsyncGenerator[dict, None]:
        raise exc
        yield  # make it an async generator

    monkeypatch.setattr(assistant, "run", fake_run)
    # Phase 3: POST /api/chat spawns the turn instead of streaming it. Inline mode (test-
    # only) makes the route await it before responding, so the failed turn — and the
    # error event backend.ai.turns published to live_sessions on the way down — has
    # already landed by the time the test reads it back via GET /live.
    import backend.ai.turns as turns_mod

    monkeypatch.setattr(turns_mod, "_inline", True)

    app = FastAPI()
    app.include_router(chat_mod.router)
    return TestClient(app)


def _parse_events(text: str) -> list[dict]:
    """Parse SSE data lines from a raw response body (POST 202's own body is JSON, not
    SSE — this is used on the GET /live body the tests below attach to instead)."""
    events = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return events


def _make_model_resolution_client(tmp_path, monkeypatch) -> TestClient:
    # ModelConfigStore is DB-backed now (secret_config/"model_config") — the caller's
    # db_isolated fixture already gives this test its own fresh database.
    for key in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")

    import backend.ai.turns as turns_mod

    monkeypatch.setattr(turns_mod, "_inline", True)
    app = FastAPI()
    app.include_router(chat_mod.router)
    return TestClient(app, raise_server_exceptions=False)


def test_unconfigured_default_is_a_streamed_error(tmp_path, monkeypatch, db_isolated):
    client = _make_model_resolution_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/chat",
        json={"session_id": "unconfigured", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 202
    events = _parse_events(client.get("/api/sessions/unconfigured/live").text)
    errors = [event for event in events if event.get("type") == "error"]
    assert len(errors) == 1
    assert "AI provider is not configured" in errors[0]["message"]


def test_disabled_stored_provider_is_a_streamed_setup_error(tmp_path, monkeypatch, db_isolated):
    client = _make_model_resolution_client(tmp_path, monkeypatch)
    store = model_config_store.ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-test"})
    store.update_provider("openai", {"model": "gpt-test", "enabled": False})
    assistant.upsert_session_meta("disabled-provider", model="openai:gpt-test")

    response = client.post(
        "/api/chat",
        json={
            "session_id": "disabled-provider",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 202
    events = _parse_events(client.get("/api/sessions/disabled-provider/live").text)
    errors = [event for event in events if event.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0].get("setup_hint") is True
    assert "disabled" in errors[0]["message"].lower()


def test_auth_error_sse_has_setup_hint(tmp_path, monkeypatch, db_isolated):
    """A 401 auth error emits setup_hint=true and a clean message, replayed on attach."""
    import backend.api.chat as mod

    # Patch _is_auth_error so our fake exception is treated as an auth error.
    orig = mod._is_auth_error
    mod._is_auth_error = lambda exc: isinstance(exc, _FakeAuthError) or orig(exc)

    c = _make_client(tmp_path, monkeypatch, _FakeAuthError("invalid x-api-key"))
    try:
        r = c.post(
            "/api/chat",
            json={"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 202
        events = _parse_events(c.get("/api/sessions/s1/live").text)
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1, "exactly one error event expected"
        ev = error_events[0]
        assert ev.get("setup_hint") is True
        msg = ev.get("message", "")
        assert "{" not in msg, f"raw SDK dict leaked: {msg!r}"
        assert "key" in msg.lower() or "Settings" in msg
    finally:
        mod._is_auth_error = orig


def test_generic_error_sse_no_setup_hint(tmp_path, monkeypatch, db_isolated):
    """An unexpected exception emits a clean message without setup_hint."""
    c = _make_client(tmp_path, monkeypatch, RuntimeError("something exploded"))
    r = c.post(
        "/api/chat",
        json={"session_id": "s2", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 202
    events = _parse_events(c.get("/api/sessions/s2/live").text)
    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) == 1
    ev = error_events[0]
    assert ev.get("setup_hint") is not True
    assert ev.get("message") == "something exploded"


def test_sdk_dict_error_sse_stripped(tmp_path, monkeypatch, db_isolated):
    """A raw non-auth SDK error string with a dict body is stripped before emission."""
    # Use a 500 error string (no auth tokens) so it goes through the generic branch.
    raw = "Error code: 500 - {'type': 'error', 'error': {'type': 'internal_server_error'}}"
    c = _make_client(tmp_path, monkeypatch, ValueError(raw))
    r = c.post(
        "/api/chat",
        json={"session_id": "s3", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 202
    events = _parse_events(c.get("/api/sessions/s3/live").text)
    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events, "expected at least one error event"
    msg = error_events[0].get("message", "")
    assert "{" not in msg, f"raw SDK dict should have been stripped: {msg!r}"
    # Not an auth error, so no setup_hint.
    assert error_events[0].get("setup_hint") is not True
