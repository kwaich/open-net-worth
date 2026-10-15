"""TestClient smoke test for GET /api/ai/model-telemetry (backend/api/model_telemetry.py).

Offline + isolated: a minimal FastAPI app mounts only this router (auth is an app-wide
dependency on backend/main.py, not on the router itself — same pattern as
tests/test_product_docs_api.py), and backend.services.model_telemetry.model_telemetry is
monkeypatched so this never touches the real DB-backed store.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.services.model_telemetry as model_telemetry_service
from backend.api.model_telemetry import router

_ROW = {
    "model": "sonnet",
    "provider": "Claude Code CLI",
    "ttft_ms_p50": 100.0,
    "duration_ms_p50": 900.0,
    "tok_per_sec_p50": 20.0,
    "error_rate": 0.0,
    "errors": 0,
    "samples": 5,
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_returns_one_entry_per_model_with_the_five_ac1_keys(client, monkeypatch):
    monkeypatch.setattr(model_telemetry_service, "model_telemetry", lambda: [_ROW])

    resp = client.get("/api/ai/model-telemetry")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"models": [_ROW]}
    row = body["models"][0]
    for key in ("ttft_ms_p50", "duration_ms_p50", "tok_per_sec_p50", "error_rate", "samples"):
        assert key in row


def test_empty_store_returns_an_empty_list_not_an_error(client, monkeypatch):
    monkeypatch.setattr(model_telemetry_service, "model_telemetry", lambda: [])

    resp = client.get("/api/ai/model-telemetry")

    assert resp.status_code == 200
    assert resp.json() == {"models": []}
