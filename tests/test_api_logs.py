"""
API smoke tests for GET /api/logs (P2-LOGS).

Mounts only the logs router in an isolated mini-app; the service's log_file is
redirected to a tmp_path fixture so tests never touch the live app.log.  No
network, no LLM, no full lifespan.

Run:
    pytest tests/test_api_logs.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def log_file(tmp_path):
    """A synthetic log file with a handful of mixed-level entries."""
    rows = [
        "2026-06-07T12:00:00 INFO     backend.main GET / -> 200  (5ms)",
        "2026-06-07T12:00:01 WARNING  backend.service watch limit",
        "2026-06-07T12:00:02 INFO     backend.ledger tx created",
        "2026-06-07T12:00:03 ERROR    backend.ingest uid jump: 42 -> 99",
        "2026-06-07T12:00:04 INFO     backend.main GET /api/portfolio -> 200  (10ms)",
    ]
    path = tmp_path / "app.log"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def client(log_file, monkeypatch):
    """FastAPI test client with the logs router + service redirected to tmp log."""
    import backend.api.logs as logs_api
    import backend.services.logs as svc

    monkeypatch.setattr(svc, "LOG_FILE", log_file)
    # Also patch the path imported directly inside the route handler.
    import backend.core.paths as p

    monkeypatch.setattr(p, "LOG_FILE", log_file)

    app = FastAPI()
    app.include_router(logs_api.router)
    return TestClient(app)


# ── basic shape ───────────────────────────────────────────────────────────────


def test_get_logs_returns_shape(client):
    r = client.get("/api/logs")
    assert r.status_code == 200
    data = r.json()
    assert "lines" in data
    assert "total" in data
    assert isinstance(data["lines"], list)
    assert data["total"] == len(data["lines"])


def test_get_logs_default_returns_all_five(client):
    data = client.get("/api/logs").json()
    assert data["total"] == 5


# ── lines param ───────────────────────────────────────────────────────────────


def test_lines_param_limits_output(client):
    data = client.get("/api/logs?lines=2").json()
    assert data["total"] == 2


def test_lines_param_zero_clamped_to_one(client):
    data = client.get("/api/logs?lines=0").json()
    assert data["total"] >= 1


def test_lines_param_beyond_max_clamped(client):
    from backend.services.logs import MAX_LINES

    data = client.get(f"/api/logs?lines={MAX_LINES + 99999}").json()
    assert data["total"] <= MAX_LINES


# ── level filter ─────────────────────────────────────────────────────────────


def test_level_filter_info(client):
    data = client.get("/api/logs?level=INFO").json()
    assert data["total"] == 3
    assert all("INFO" in line for line in data["lines"])


def test_level_filter_error(client):
    data = client.get("/api/logs?level=ERROR").json()
    assert data["total"] == 1
    assert "uid jump" in data["lines"][0]


def test_level_filter_unknown_falls_through(client):
    """An unrecognised level should not crash — returns all lines."""
    data = client.get("/api/logs?level=BOGUS").json()
    assert data["total"] == 5


# ── q filter ─────────────────────────────────────────────────────────────────


def test_q_filter(client):
    data = client.get("/api/logs?q=uid+jump").json()
    assert data["total"] == 1


def test_q_filter_case_insensitive(client):
    data_upper = client.get("/api/logs?q=GET").json()
    data_lower = client.get("/api/logs?q=get").json()
    assert data_upper["total"] == data_lower["total"]


def test_q_no_match_returns_empty(client):
    data = client.get("/api/logs?q=xyzzynotpresent").json()
    assert data["total"] == 0
    assert data["lines"] == []


# ── missing file ─────────────────────────────────────────────────────────────


def test_missing_log_file_returns_empty(tmp_path, monkeypatch):
    import backend.api.logs as logs_api
    import backend.core.paths as p
    import backend.services.logs as svc

    missing = tmp_path / "does_not_exist.log"
    monkeypatch.setattr(svc, "LOG_FILE", missing)
    monkeypatch.setattr(p, "LOG_FILE", missing)

    app = FastAPI()
    app.include_router(logs_api.router)
    c = TestClient(app)

    data = c.get("/api/logs").json()
    assert data["total"] == 0
    assert data["lines"] == []
