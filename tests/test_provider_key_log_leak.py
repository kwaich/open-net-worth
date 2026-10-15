"""A saved API key must never reach app.log.

h11 refuses a header value holding a control byte with an error that QUOTES the value —
`Illegal header value b'Bearer <key>'` — and httpx/httpcore pass it through unchanged. Two
live-model-list routes logged that exception's text at INFO, and validation logged ``%r`` of it;
app.log is readable by Lucius, so a stored key with a stray control byte left through a log line.
Now a key a header cannot carry is refused before any request is built, and failed provider
calls are logged by type (or HTTP status) only. Reproduced against a real loopback listener.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.models as models_mod
from backend.db.model_config_store import ModelConfigStore

# Low-entropy and built at runtime: a synthetic sentinel, not something a secret scanner should see.
KEY = "sk-" + "sentinel" * 4


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for name in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(models_mod, "MODELS_CACHE_JSON", tmp_path / "models_cache.json")


def _leaky_get(url, headers=None, **kwargs):
    """What h11 does: a request CARRYING the key fails quoting it. A keyless request (OpenRouter's
    public capability catalog) has nothing to quote, so it just fails plainly."""
    if any(KEY in str(v) for v in (headers or {}).values()):
        raise httpx.LocalProtocolError(f"Illegal header value b'Bearer {KEY}\\x00'")
    raise httpx.ConnectError("offline in tests")


def test_a_failing_live_fetch_for_a_saved_key_is_not_logged_with_the_key(
    monkeypatch, caplog, db_isolated
):
    monkeypatch.setattr(models_mod.httpx, "get", _leaky_get)
    store = ModelConfigStore()
    store.update_provider("openai", {"api_key": KEY, "model": "gpt-test"})

    with caplog.at_level(logging.DEBUG):
        _models, source = models_mod._fetch_models_for_provider("openai", store)

    assert source == "fallback"
    assert KEY not in caplog.text


def test_the_default_model_list_route_does_not_log_the_saved_key(monkeypatch, caplog, db_isolated):
    monkeypatch.setattr(models_mod.httpx, "get", _leaky_get)
    store = ModelConfigStore()
    store.update_provider("openai", {"api_key": KEY, "model": "gpt-test"})
    store.update({"default_model": "openai:gpt-test"})
    app = FastAPI()
    app.include_router(models_mod.router)

    with caplog.at_level(logging.DEBUG):
        response = TestClient(app, raise_server_exceptions=False).get("/api/models")

    assert response.status_code == 200 and response.json()["source"] == "fallback"
    assert KEY not in caplog.text


@pytest.mark.parametrize(
    "bad", [KEY + "\x00", KEY + "\x0b", KEY + "\x7f", "sk-with space-1234", KEY + "\nx"]
)
def test_validation_never_builds_a_request_for_a_key_a_header_cannot_carry(
    monkeypatch, caplog, bad
):
    calls: list[str] = []
    monkeypatch.setattr(models_mod.httpx, "get", lambda url, **k: calls.append(str(url)))

    with caplog.at_level(logging.DEBUG):
        status, models, _error = models_mod._validate_credentials(
            "openai", "openai_compatible", "https://api.openai.com/v1", bad
        )

    assert status == "invalid" and models == []
    assert calls == []
    assert bad not in caplog.text


def test_an_http_failure_is_logged_by_status_not_by_text():
    request = httpx.Request("GET", "https://api.openai.com/v1/models")
    error = httpx.HTTPStatusError(
        f"boom {KEY}", request=request, response=httpx.Response(401, request=request)
    )

    assert models_mod._exc_label(error) == "HTTP 401"
    assert models_mod._exc_label(httpx.LocalProtocolError(KEY)) == "LocalProtocolError"
