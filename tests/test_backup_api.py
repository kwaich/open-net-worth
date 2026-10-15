"""TestClient smoke tests for backend/api/backup.py.

Routes covered:
  GET  /api/backup/settings
  PUT  /api/backup/settings
  GET  /api/backup
  POST /api/backup/run-now

Isolation: monkeypatches backend.api.backup's AppConfigStore + backend.services.backup's
DATA_DIR/BACKUPS_DIR to tmp_path so no live data is touched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.api.backup as backup_api
import backend.services.backup as backup_service
from backend.db.app_config_store import AppConfigStore
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    backups_dir = data_dir / "backups"
    data_dir.mkdir()
    backups_dir.mkdir()
    (data_dir / "accounts.json").write_text("[]")

    # app_config is DB-backed now (Wave 5D2a, JSON->DB) — conftest's autouse Tier-2 table
    # clear already isolates it per test, no path kwarg needed.
    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(backup_service, "_EXCLUDE_DIR_NAMES", {"backups", "checkpoints"})
    monkeypatch.setattr(backup_service, "AppConfigStore", AppConfigStore)
    monkeypatch.setattr(backup_api, "AppConfigStore", AppConfigStore)
    return {"data_dir": data_dir, "backups_dir": backups_dir}


def test_get_settings_defaults(client, isolated):
    res = client.get("/api/backup/settings")
    assert res.status_code == 200
    assert res.json() == {"enabled": True, "retention_days": 30}


def test_put_settings_round_trips(client, isolated):
    res = client.put("/api/backup/settings", json={"enabled": False, "retention_days": 7})
    assert res.status_code == 200
    assert res.json() == {"enabled": False, "retention_days": 7}
    assert client.get("/api/backup/settings").json() == {
        "enabled": False,
        "retention_days": 7,
    }


def test_put_settings_rejects_zero_retention(client, isolated):
    res = client.put("/api/backup/settings", json={"retention_days": 0})
    assert res.status_code == 422


def test_list_backups_empty(client, isolated):
    res = client.get("/api/backup")
    assert res.status_code == 200
    assert res.json() == {"backups": [], "last_backup_at": None}


def test_run_now_creates_a_backup_and_shows_up_in_list(client, isolated):
    res = client.post("/api/backup/run-now")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True

    listed = client.get("/api/backup").json()
    assert len(listed["backups"]) == 1
    assert listed["last_backup_at"] is not None


def test_run_now_ignores_disabled_toggle(client, isolated):
    client.put("/api/backup/settings", json={"enabled": False})
    res = client.post("/api/backup/run-now")
    assert res.status_code == 200
    assert res.json()["ok"] is True
